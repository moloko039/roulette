"""Подключение к SQLite: путь к базе, замеры времени запроса, режим журнала (WAL), повтор BEGIN при занятой базе, _connect."""

import contextvars
import logging
import os
import sqlite3
import time


logger = logging.getLogger("depnaya.db")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "players.db")


# SQLite рассчитана на ОДИН экземпляр сервиса: файл базы лежит на одном диске
# (на Railway это том), и два экземпляра не смогут безопасно писать в него.
# Число реплик должно быть 1.
def _resolve_path(db_path):
    """Путь к файлу: аргумент, затем переменная DB_PATH, затем файл рядом с кодом."""
    path = db_path or os.environ.get("DB_PATH") or DB_PATH
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)  # папки для файла может не быть (например, свежий том)
    return path


# Замеры времени базы: middleware запроса кладёт сюда словарь {"begin": секунды, "commit": секунды}, а
# соединение прибавляет время BEGIN (ожидание блокировки записи) и COMMIT. Вне запроса (бот, фоновые задачи)
# значения нет, замеров нет. Словарь изменяемый, поэтому значения видны и из потоков, где идёт работа с базой.
request_timing = contextvars.ContextVar("request_timing", default=None)
BUSY_TIMEOUT_SECONDS = 10     # сколько соединение ждёт снятия чужой блокировки записи
BUSY_RETRY_DELAY = 0.05       # пауза перед единственным повтором BEGIN, если блокировка не снялась


def is_busy_error(exc):
    """Ошибка SQLite «база занята» (database is locked / busy), а не поломка запроса."""
    return isinstance(exc, sqlite3.OperationalError) and ("locked" in str(exc).lower() or "busy" in str(exc).lower())


class TimedConnection(sqlite3.Connection):
    """Соединение, которое измеряет BEGIN и COMMIT и один раз повторяет BEGIN, если база занята: на этом шаге в базе
    ещё ничего не изменено, повтор безопасен. Остальные запросы не меняются."""

    def execute(self, sql, *args):
        head = sql.lstrip()[:6].upper()
        if head.startswith("BEGIN"):
            try:
                return self._timed_execute(sql, head, args)
            except sqlite3.OperationalError as exc:
                if not is_busy_error(exc):
                    raise
                time.sleep(BUSY_RETRY_DELAY)
                return self._timed_execute(sql, head, args)
        return self._timed_execute(sql, head, args)

    def _timed_execute(self, sql, head, args):
        timing = request_timing.get()
        if timing is None:
            return super().execute(sql, *args)
        key = "begin" if head == "BEGIN" or head.startswith("BEGIN") else "commit" if head == "COMMIT" else None
        if key is None:
            return super().execute(sql, *args)
        started = time.perf_counter()
        try:
            return super().execute(sql, *args)
        finally:
            timing[key] += time.perf_counter() - started


# Не задан: поведение прежнее, ничего не переключается. "wal" или "delete": режим переключается ОДИН раз при
# старте в init_db (пока нет других соединений), а не при каждом соединении. Каждое соединение при WAL ставит
# synchronous=NORMAL (при FULL каждый COMMIT делает fsync; в WAL при NORMAL fsync на контрольной точке: после
# сбоя питания последние транзакции могут откатиться, база остаётся целой).
JOURNAL_MODE_ENV = "SQLITE_JOURNAL_MODE"
_wal_by_path = {}      # абсолютный путь файла базы -> включён ли WAL (заполняет init_db, иначе читается один раз)
_journal_warned = set()


def wal_version_safe(version):
    """Версии SQLite без ошибки «WAL-reset» (см. https://www.sqlite.org/wal.html): 3.44.6 и новее в ветке 3.44,
    3.50.7 и новее в ветке 3.50, 3.51.3 и новее."""
    v = tuple(version)[:3]
    return v >= (3, 51, 3) or (3, 50, 7) <= v < (3, 51, 0) or (3, 44, 6) <= v < (3, 45, 0)


def _journal_warn(key, text, *args):
    if key not in _journal_warned:      # одно предупреждение за запуск
        _journal_warned.add(key)
        logger.warning(text, *args)


def journal_mode_flag():
    """Значение флага: "wal", "delete" или None (не задан или неверен: предупреждение, поведение прежнее)."""
    raw = (os.environ.get(JOURNAL_MODE_ENV) or "").strip().lower()
    if raw in ("wal", "delete"):
        return raw
    if raw:
        _journal_warn("flag", "Неверное значение %s (допустимо wal или delete): режим журнала не меняется", JOURNAL_MODE_ENV)
    return None


def _pragma_value(conn, name):
    row = conn.execute("PRAGMA " + name).fetchone()
    return str(row[0]).lower() if row is not None else ""


def _apply_journal_mode(conn, path):
    """Переключает режим журнала по флагу (один раз при старте) и запоминает итоговый режим базы."""
    flag = journal_mode_flag()
    try:
        if flag == "wal":
            version = sqlite3.sqlite_version_info
            if not wal_version_safe(version):
                _journal_warn("version", "WAL не включён: версия SQLite %s содержит ошибку WAL-reset "
                              "(исправлена в 3.44.6, 3.50.7, 3.51.3 и новее); остаюсь в прежнем режиме", sqlite3.sqlite_version)
            elif conn.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
                _journal_warn("wal-failed", "Режим WAL не включился, остаюсь в прежнем")
        elif flag == "delete":
            if conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower() != "delete":
                _journal_warn("delete-failed", "Режим DELETE не включился, остаюсь в прежнем")
    except sqlite3.Error as exc:
        _journal_warn("switch-error", "Не удалось переключить режим журнала SQLite (%s), остаюсь в прежнем", type(exc).__name__)
    is_wal = _pragma_value(conn, "journal_mode") == "wal"
    _wal_by_path[os.path.abspath(path)] = is_wal
    if is_wal:
        conn.execute("PRAGMA synchronous=NORMAL")


def _is_wal(conn, path):
    key = os.path.abspath(path)
    if key not in _wal_by_path:          # init_db для этого файла ещё не вызывался: режим читаем один раз
        _wal_by_path[key] = _pragma_value(conn, "journal_mode") == "wal"
    return _wal_by_path[key]


def log_sqlite_mode(db_path=None):
    """Одна строка INFO при старте: версия SQLite, режим журнала, synchronous, значение флага (без путей)."""
    conn = _connect(db_path)
    try:
        flag = (os.environ.get(JOURNAL_MODE_ENV) or "").strip().lower() or "не задан"
        logger.info("SQLite %s: журнал=%s synchronous=%s флаг %s=%s", sqlite3.sqlite_version,
                    _pragma_value(conn, "journal_mode"),
                    {"0": "off", "1": "normal", "2": "full", "3": "extra"}.get(_pragma_value(conn, "synchronous"), "?"),
                    JOURNAL_MODE_ENV, flag if flag in ("wal", "delete", "не задан") else "неверное значение")
    finally:
        conn.close()


def _connect(db_path):
    path = _resolve_path(db_path)
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_SECONDS, factory=TimedConnection)
    conn.row_factory = sqlite3.Row
    # транзакциями управляем вручную (BEGIN IMMEDIATE), а не автоматически
    conn.isolation_level = None
    if _is_wal(conn, path):
        conn.execute("PRAGMA synchronous=NORMAL")   # только при WAL; иначе остаётся FULL, как раньше
    return conn

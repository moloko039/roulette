import contextvars
import json
import logging
import os
import unicodedata
import secrets
import sqlite3
import time

import antiabuse
from antiabuse import COOLDOWN_SECONDS, TombstoneUnavailable
from economy import START_BALANCE, BASE_RATE, accrue
import blackjack
import crash
import farm
import keno
import mines
import wallet
import xp
from levels import profile_level
from roulette import BalanceLimit, InsufficientFunds, MAX_SAFE_INT, max_payout, settle

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


class TimedConnection(sqlite3.Connection):
    """Обычное соединение, которое измеряет только BEGIN и COMMIT (поведение запросов не меняется)."""

    def execute(self, sql, *args):
        timing = request_timing.get()
        if timing is None:
            return super().execute(sql, *args)
        head = sql.lstrip()[:6].upper()
        key = "begin" if head == "BEGIN" or head.startswith("BEGIN") else "commit" if head == "COMMIT" else None
        if key is None:
            return super().execute(sql, *args)
        started = time.perf_counter()
        try:
            return super().execute(sql, *args)
        finally:
            timing[key] += time.perf_counter() - started


# ---------- режим журнала SQLite (необязательный флаг SQLITE_JOURNAL_MODE) ----------
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
    conn = sqlite3.connect(path, factory=TimedConnection)
    conn.row_factory = sqlite3.Row
    # транзакциями управляем вручную (BEGIN IMMEDIATE), а не автоматически
    conn.isolation_level = None
    if _is_wal(conn, path):
        conn.execute("PRAGMA synchronous=NORMAL")   # только при WAL; иначе остаётся FULL, как раньше
    return conn


def init_db(db_path=None):
    conn = _connect(db_path)
    try:
        _apply_journal_mode(conn, _resolve_path(db_path))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                telegram_id  INTEGER PRIMARY KEY,
                balance      INTEGER NOT NULL,
                rate         INTEGER NOT NULL,
                last_accrual INTEGER NOT NULL,
                created_at   INTEGER NOT NULL,
                total_staked INTEGER NOT NULL DEFAULT 0,
                xp           INTEGER NOT NULL DEFAULT 0,
                income_level INTEGER NOT NULL DEFAULT 0,
                storage_level INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # раунды рулетки: ключ (игрок, request_id) защищает от повторного списания при повторе запроса
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS roulette_rounds (
                telegram_id  INTEGER NOT NULL,
                request_id   TEXT    NOT NULL,
                number       INTEGER NOT NULL,
                stake_total  INTEGER NOT NULL,
                payout_total INTEGER NOT NULL,
                bets_json    TEXT    NOT NULL,
                created_at   INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
        # раунды кено: ключ (игрок, request_id) защищает от повторного списания при повторе запроса
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS keno_rounds (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                request_id  TEXT    NOT NULL,
                bet         INTEGER NOT NULL,
                picks_json  TEXT    NOT NULL,
                draw_json   TEXT    NOT NULL,
                hit_count   INTEGER NOT NULL,
                payout      INTEGER NOT NULL,
                created_at  INTEGER NOT NULL,
                UNIQUE (telegram_id, request_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_keno_created ON keno_rounds(created_at)")
        # покупки улучшений фермы: ключ (игрок, request_id) защищает от повторного списания при повторе запроса
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS farm_purchases (
                telegram_id INTEGER NOT NULL,
                request_id  TEXT    NOT NULL,
                kind        TEXT    NOT NULL,
                level_after INTEGER NOT NULL,
                cost        INTEGER NOT NULL,
                created_at  INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
        # игры в мины: раскладка (mine_mask) хранится только здесь и никогда не уходит клиенту до конца игры
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mines_games (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id    INTEGER NOT NULL,
                bet            INTEGER NOT NULL,
                mines_count    INTEGER NOT NULL,
                mine_mask      INTEGER NOT NULL,
                revealed_mask  INTEGER NOT NULL DEFAULT 0,
                status         TEXT    NOT NULL,
                payout         INTEGER NOT NULL DEFAULT 0,
                staked_counted INTEGER NOT NULL DEFAULT 0,
                created_at     INTEGER NOT NULL,
                updated_at     INTEGER NOT NULL,
                finished_at    INTEGER
            )
            """
        )
        # не больше одной активной игры на игрока
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_mines_active ON mines_games(telegram_id) WHERE status = 'active'"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mines_history ON mines_games(telegram_id, finished_at)"
        )
        # действия в игре: ключ (игрок, request_id) даёт идемпотентность повторов
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mines_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
        # блэкджек: порядок колоды (deck_json) хранится только здесь и только пока игра идёт (после конца очищается)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blackjack_games (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                bet         INTEGER NOT NULL,
                wager       INTEGER NOT NULL,
                deck_json   TEXT    NOT NULL,
                deck_pos    INTEGER NOT NULL,
                player_json TEXT    NOT NULL,
                dealer_json TEXT    NOT NULL,
                status      TEXT    NOT NULL,
                result      TEXT,
                payout      INTEGER,
                auto        INTEGER NOT NULL DEFAULT 0,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL,
                finished_at INTEGER
            )
            """
        )
        # не больше одной активной раздачи на игрока
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_blackjack_active ON blackjack_games(telegram_id) WHERE status = 'active'"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_blackjack_history ON blackjack_games(telegram_id, finished_at)"
        )
        # действия: ключ (игрок, request_id) даёт идемпотентность повторов
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blackjack_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
        # краш: точка краха (crash_x100) хранится только здесь и никогда не уходит клиенту, пока раунд идёт
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crash_games (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id   INTEGER NOT NULL,
                bet           INTEGER NOT NULL,
                mode          TEXT    NOT NULL,
                target_x100   INTEGER,
                crash_x100    INTEGER NOT NULL,
                started_at_ms INTEGER NOT NULL,
                status        TEXT    NOT NULL,
                result        TEXT,
                mult_x100     INTEGER,
                payout        INTEGER,
                auto          INTEGER NOT NULL DEFAULT 0,
                created_at    INTEGER NOT NULL,
                finished_at   INTEGER
            )
            """
        )
        # не больше одного активного раунда на игрока
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_crash_active ON crash_games(telegram_id) WHERE status = 'active'"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_crash_history ON crash_games(telegram_id, finished_at)")
        # действия: ключ (игрок, request_id) даёт идемпотентность повторов
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crash_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
        # участники бесед для рейтинга: chat_instance — глобальный id чата из подписанных данных
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_members (
                chat_instance TEXT    NOT NULL,
                telegram_id   INTEGER NOT NULL,
                first_name    TEXT    NOT NULL,
                first_seen    INTEGER NOT NULL,
                last_seen     INTEGER NOT NULL,
                PRIMARY KEY (chat_instance, telegram_id)
            )
            """
        )
        # «надгробия» после удаления данных: только хэш идентификатора и дата удаления
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deletion_tombstones (
                key_hash   TEXT    PRIMARY KEY,
                deleted_at INTEGER NOT NULL
            )
            """
        )
        # служебные отметки (время последних уведомлений владельцу); личных данных здесь нет
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        _migrate_total_staked(conn)
        _migrate_xp(conn)
        _migrate_farm_levels(conn)
        # записи старше срока защиты не нужны
        conn.execute(
            "DELETE FROM deletion_tombstones WHERE deleted_at + ? <= ?",
            (COOLDOWN_SECONDS, int(time.time())),
        )
    finally:
        conn.close()


def _migrate_total_staked(conn):
    """Добавляет players.total_staked в старую базу (идемпотентно, одной транзакцией).

    Начальное значение игрока = сумма stake_total из roulette_rounds. Учитывается только то, что ещё
    хранится: раунды старше срока хранения уже удалены очисткой, поэтому у старых игроков значение
    может быть меньше фактической суммы ставок. Дальше счётчик растёт в spin_roulette и очисткой
    раундов не уменьшается.
    """
    def has_column():
        return any(r["name"] == "total_staked" for r in conn.execute("PRAGMA table_info(players)"))

    if has_column():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not has_column():  # повторная проверка внутри транзакции
            conn.execute("ALTER TABLE players ADD COLUMN total_staked INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                "UPDATE players SET total_staked = MIN(COALESCE("
                "(SELECT SUM(r.stake_total) FROM roulette_rounds r WHERE r.telegram_id = players.telegram_id), 0), ?)",
                (MAX_SAFE_INT,),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_xp(conn):
    """Добавляет players.xp в старую базу (идемпотентно, одной транзакцией).

    У существующих игроков xp = total_staked, чтобы уровни профиля не изменились; дальше опыт растёт по правилам
    xp.py. Новые игроки (и после удаления данных) начинают с 0."""
    def has_column():
        return any(r["name"] == "xp" for r in conn.execute("PRAGMA table_info(players)"))

    if has_column():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not has_column():  # повторная проверка внутри транзакции
            conn.execute("ALTER TABLE players ADD COLUMN xp INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE players SET xp = total_staked")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _migrate_farm_levels(conn):
    """Добавляет players.income_level и players.storage_level в старую базу (идемпотентно).

    Новые столбцы 0: потолок накопления прежний (30 часов). Столбец rate не трогаем: у старых игроков он
    уже равен базовой ставке, а при покупке дохода обновляется до farm.income_rate(уровень)."""
    def columns():
        return {r["name"] for r in conn.execute("PRAGMA table_info(players)")}

    if {"income_level", "storage_level"} <= columns():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        present = columns()  # повторная проверка внутри транзакции
        if "income_level" not in present:
            conn.execute("ALTER TABLE players ADD COLUMN income_level INTEGER NOT NULL DEFAULT 0")
        if "storage_level" not in present:
            conn.execute("ALTER TABLE players ADD COLUMN storage_level INTEGER NOT NULL DEFAULT 0")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _accrue_player(row, now):
    """Начисление по часам для строки игрока: единственное место, где вызывается economy.accrue.
    Ставка берётся из players.rate, потолок часов из уровня хранилища игрока."""
    return accrue(row["last_accrual"], now, row["rate"], max_hours=farm.storage_hours(row["storage_level"]))


def get_meta(key, db_path=None):
    """Значение служебной отметки или None."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT value FROM service_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_meta(key, value, db_path=None):
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO service_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
    finally:
        conn.close()


def _register_player(conn, telegram_id, now):
    """Единственное место, где создаётся строка в players (get_player, spin_roulette, chat_top).

    Новый игрок получает START_BALANCE, кроме случая, когда его данные удалили меньше
    REGISTRATION_COOLDOWN_DAYS назад: тогда баланс 0, скорость и время начисления обычные.
    Если игрок уже есть, ничего не меняется. Без TOMBSTONE_SECRET защита не работает,
    поведение прежнее.
    """
    if conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone() is not None:
        return
    balance = START_BALANCE
    secret = antiabuse.tombstone_secret()
    if secret is not None:
        row = conn.execute(
            "SELECT deleted_at FROM deletion_tombstones WHERE key_hash = ?",
            (antiabuse.key_hash(telegram_id, secret),),
        ).fetchone()
        if row is not None and row["deleted_at"] + COOLDOWN_SECONDS > now:
            balance = 0
    conn.execute(
        "INSERT OR IGNORE INTO players "
        "(telegram_id, balance, rate, last_accrual, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (telegram_id, balance, BASE_RATE, now, now),
    )


def get_player(telegram_id, now=None, db_path=None):
    """Находит игрока (или регистрирует), начисляет фишки, возвращает словарь."""
    if now is None:
        now = int(time.time())

    conn = _connect(db_path)
    try:
        # IMMEDIATE сразу берёт блокировку на запись: два одновременных запроса
        # одного игрока выполняются по очереди, а не читают одно и то же старое значение
        conn.execute("BEGIN IMMEDIATE")
        try:
            # новый игрок получает стартовый баланс (или 0 в период защиты); существующего не трогаем
            _register_player(conn, telegram_id, now)
            row = conn.execute(
                "SELECT balance, rate, last_accrual, total_staked, xp, income_level, storage_level "
                "FROM players WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()

            # начисление по часам: единственная правка баланса вне wallet (это не игровое списание или выплата)
            earned, new_last = _accrue_player(row, now)
            balance = row["balance"] + earned

            if earned or new_last != row["last_accrual"]:
                conn.execute(
                    "UPDATE players SET balance = ?, last_accrual = ? WHERE telegram_id = ?",
                    (balance, new_last, telegram_id),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    return {
        "telegram_id": telegram_id,
        "balance": balance,
        "rate": row["rate"],
        "last_accrual": new_last,
        "total_staked": row["total_staked"],
        "xp": row["xp"],
        "income_level": row["income_level"],
        "storage_level": row["storage_level"],
    }


def _round_result(number, stake_total, payout_total, balance, replayed):
    return {
        "number": number,
        "stake_total": stake_total,
        "payout_total": payout_total,
        "net": payout_total - stake_total,
        "balance": balance,
        "replayed": replayed,
    }


def spin_roulette(telegram_id, request_id, bets, now=None, db_path=None, rng=None):
    """Один спин рулетки в одной транзакции.

    bets уже проверены validate_bets(). rng(n) возвращает число 0..n-1 (по умолчанию
    secrets.randbelow); параметр нужен, чтобы тесты подставляли числа.
    Бросает InsufficientFunds или BalanceLimit; тогда в базе ничего не меняется.
    """
    if now is None:
        now = int(time.time())
    if rng is None:
        rng = secrets.randbelow

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # (а) повторный запрос: отдаём сохранённый результат, ничего не меняем.
            # balance в ответе — текущий баланс игрока, а не баланс на момент раунда
            old = conn.execute(
                "SELECT number, stake_total, payout_total FROM roulette_rounds "
                "WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                cur = conn.execute(
                    "SELECT balance FROM players WHERE telegram_id = ?", (telegram_id,)
                ).fetchone()
                result = _round_result(old["number"], old["stake_total"], old["payout_total"],
                                       cur["balance"] if cur else 0, True)
                conn.execute("COMMIT")
                return result

            # (б) игрок и начисление: потратить можно и только что начисленное.
            # Начисление по часам записывается сразу (при ошибке ниже транзакция откатится целиком)
            _register_player(conn, telegram_id, now)
            row = conn.execute(
                "SELECT balance, rate, last_accrual, storage_level FROM players WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            earned, new_last = _accrue_player(row, now)
            conn.execute(
                "UPDATE players SET balance = ?, last_accrual = ? WHERE telegram_id = ?",
                (row["balance"] + earned, new_last, telegram_id),
            )

            # (в) списание ставки через кошелёк (InsufficientFunds, если фишек не хватает) и проверка,
            # не упрётся ли баланс в предел точных чисел JavaScript при самой большой выплате
            stake_total = sum(b["amount"] for b in bets)
            wallet.debit(conn, telegram_id, stake_total)
            if wallet.get_balance(conn, telegram_id) + max_payout(bets) > MAX_SAFE_INT:
                raise BalanceLimit()

            # (г) число, (д) выигрыш
            number = rng(37)
            if type(number) is not int or not 0 <= number <= 36:
                raise RuntimeError("bad random number")
            stake_total, payout_total = settle(bets, number)

            # (е) выплата через кошелёк, запись раунда и счётчик ставок (в той же транзакции)
            if payout_total > 0:
                wallet.credit(conn, telegram_id, payout_total)
            new_balance = wallet.get_balance(conn, telegram_id)
            conn.execute(
                "UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                (stake_total, MAX_SAFE_INT, telegram_id),
            )
            _add_xp(conn, telegram_id, xp.roulette_xp(stake_total, bets))
            conn.execute(
                "INSERT INTO roulette_rounds "
                "(telegram_id, request_id, number, stake_total, payout_total, bets_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, number, stake_total, payout_total,
                 json.dumps(bets, separators=(",", ":")), now),
            )
            # (ж)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    return _round_result(number, stake_total, payout_total, new_balance, False)


# ---------- кено ----------

def _keno_result(bet, picks, draw, payout, balance, xp_total, replayed):
    hits = keno.play(picks, draw)
    return {
        "bet": bet,
        "picks": list(picks),
        "draw": list(draw),
        "hits": hits,
        "hit_count": len(hits),
        "multiplier": keno.multiplier_text(keno.multiplier_x100(len(picks), len(hits))),
        "payout": payout,
        "balance": balance,
        "level": profile_level(xp_total),
        "xp": xp_total,
        "replayed": replayed,
    }


def play_keno(telegram_id, request_id, bet, picks, now=None, db_path=None, rng=None):
    """Один раунд кено в одной транзакции BEGIN IMMEDIATE (по образцу spin_roulette).

    bet и picks уже проверены (keno.validate_picks, 1 <= bet <= KENO_MAX_BET). rng: объект с sample (тесты).
    Повтор с тем же request_id и теми же bet и picks возвращает сохранённый раунд (balance текущий),
    с другими: keno.RequestConflict. Бросает InsufficientFunds или BalanceLimit; тогда в базе ничего не меняется.
    """
    if type(bet) is not int or not 1 <= bet <= keno.KENO_MAX_BET:
        raise ValueError("bet out of range")
    picks = keno.validate_picks(picks)
    if now is None:
        now = int(time.time())

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT bet, picks_json, draw_json, payout FROM keno_rounds WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                if old["bet"] != bet or json.loads(old["picks_json"]) != picks:
                    raise keno.RequestConflict()
                cur = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
                result = _keno_result(bet, picks, json.loads(old["draw_json"]), old["payout"],
                                      cur["balance"] if cur else 0, cur["xp"] if cur else 0, True)
                conn.execute("COMMIT")
                return result

            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            wallet.debit(conn, telegram_id, bet)   # InsufficientFunds, если фишек не хватает
            if wallet.get_balance(conn, telegram_id) + bet * keno.MAX_MULT_X100 // 100 > MAX_SAFE_INT:
                raise BalanceLimit()
            draw = keno.draw_numbers(rng)
            hits = keno.play(picks, draw)
            payout = keno.payout(bet, len(picks), len(hits))
            if payout > 0:
                wallet.credit(conn, telegram_id, payout)
            conn.execute(
                "UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                (bet, MAX_SAFE_INT, telegram_id),
            )
            _add_xp(conn, telegram_id, xp.keno_xp(bet, len(picks)))
            conn.execute(
                "INSERT INTO keno_rounds (telegram_id, request_id, bet, picks_json, draw_json, hit_count, payout, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, bet, json.dumps(picks, separators=(",", ":")),
                 json.dumps(draw, separators=(",", ":")), len(hits), payout, now),
            )
            cur = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
            result = _keno_result(bet, picks, draw, payout, cur["balance"], cur["xp"], False)
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


# ---------- блэкджек ----------

def _bj_active(conn, telegram_id):
    return conn.execute(
        "SELECT * FROM blackjack_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,)
    ).fetchone()


def _bj_state(row):
    """Состояние движка из строки базы (у завершённой игры колоды нет: она очищена)."""
    return {
        "bet": row["bet"], "wager": row["wager"],
        "shoe": json.loads(row["deck_json"]) if row["deck_json"] else [], "pos": row["deck_pos"],
        "player": json.loads(row["player_json"]), "dealer": json.loads(row["dealer_json"]),
        "status": row["status"], "result": row["result"], "payout": row["payout"],
    }


def _bj_save(conn, game_id, state, now):
    """Записывает состояние. После конца раздачи колода очищается и больше не хранится."""
    finished = state["status"] == "finished"
    conn.execute(
        "UPDATE blackjack_games SET wager = ?, deck_json = ?, deck_pos = ?, player_json = ?, dealer_json = ?, "
        "status = ?, result = ?, payout = ?, updated_at = ? WHERE id = ?",
        (state["wager"], "" if finished else json.dumps(state["shoe"], separators=(",", ":")), state["pos"],
         json.dumps(state["player"], separators=(",", ":")), json.dumps(state["dealer"], separators=(",", ":")),
         state["status"], state["result"], state["payout"], now, game_id),
    )


def _bj_finish(conn, telegram_id, game_id, state, now, auto=False):
    """Единственное место окончания раздачи: выплата через wallet, XP за раздачу, отметка времени. Вызывается один раз
    (раздача уже не active, повторно её никто не закроет)."""
    _bj_save(conn, game_id, state, now)
    conn.execute("UPDATE blackjack_games SET finished_at = ?, auto = ? WHERE id = ?", (now, 1 if auto else 0, game_id))
    if state["payout"] > 0:
        _credit_capped(conn, telegram_id, state["payout"])
    _add_xp(conn, telegram_id, xp.blackjack_xp(state["wager"]))


def _bj_response(conn, telegram_id, row, replayed=False):
    pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return blackjack.view(_bj_state(row), pl["balance"], profile_level(pl["xp"]), pl["xp"],
                          auto=bool(row["auto"]), replayed=replayed)


def _bj_settle_expired_in(conn, telegram_id, now):
    """Просроченная активная раздача (24 часа без действий) закрывается автоматическим stand. True, если закрыла."""
    row = _bj_active(conn, telegram_id)
    if row is None or now - row["updated_at"] < blackjack.BLACKJACK_IDLE_SECONDS:
        return False
    state = blackjack.act(_bj_state(row), "stand")
    _bj_finish(conn, telegram_id, row["id"], state, now, auto=True)
    return True


def settle_expired_blackjack(telegram_id, now=None, db_path=None):
    """Закрывает просроченную раздачу игрока отдельной транзакцией (идемпотентно). True, если закрыла."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            closed = _bj_settle_expired_in(conn, telegram_id, now)
            conn.execute("COMMIT")
            return closed
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


BLACKJACK_CLOSE_BATCH = 200


def close_expired_blackjack(now=None, db_path=None, batch=BLACKJACK_CLOSE_BATCH):
    """Фоновое закрытие просроченных раздач всех игроков (не больше batch за проход). Возвращает число."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'blackjack_games'").fetchone() is None:
            return 0
        owners = [r["telegram_id"] for r in conn.execute(
            "SELECT telegram_id FROM blackjack_games WHERE status = 'active' AND ? - updated_at >= ? LIMIT ?",
            (now, blackjack.BLACKJACK_IDLE_SECONDS, batch),
        )]
    finally:
        conn.close()
    closed = sum(1 for owner in owners if settle_expired_blackjack(owner, now=now, db_path=db_path))
    if closed:
        logger.info("Закрыто просроченных раздач блэкджека: %d", closed)  # только количество
    return closed


def _run_blackjack_action(telegram_id, request_id, action, params, body, now, db_path):
    """Общий порядок действия: закрытие просроченной раздачи, повтор по request_id, начисление по часам, тело
    действия, запись ответа. Один request_id с другим действием или параметрами даёт RequestConflict."""
    if now is None:
        now = int(time.time())
    settle_expired_blackjack(telegram_id, now=now, db_path=db_path)
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT action, params, response_json FROM blackjack_actions WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                if old["action"] != action or old["params"] != params_json:
                    raise blackjack.RequestConflict()
                response = json.loads(old["response_json"])
                response["replayed"] = True
                conn.execute("COMMIT")
                return response
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            response = body(conn, now)
            response["replayed"] = False
            conn.execute(
                "INSERT INTO blackjack_actions (telegram_id, request_id, action, params, response_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, action, params_json, json.dumps(response, separators=(",", ":")), now),
            )
            conn.execute("COMMIT")
            return response
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def blackjack_start(telegram_id, request_id, bet, now=None, db_path=None, rng=None):
    """Новая раздача: нет активной, ставка списывается через wallet и сразу идёт в total_staked, колода тасуется заново.
    Блэкджек (у игрока и/или дилера) решается тут же. rng: объект с shuffle (тесты)."""
    if type(bet) is not int or not 1 <= bet <= blackjack.BLACKJACK_MAX_BET:
        raise ValueError("bet out of range")

    def body(conn, now_):
        if _bj_active(conn, telegram_id) is not None:
            raise blackjack.ActiveGameExists()
        wallet.debit(conn, telegram_id, bet)   # InsufficientFunds, если фишек не хватает
        conn.execute("UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                     (bet, MAX_SAFE_INT, telegram_id))
        state = blackjack.start(bet, blackjack.new_shoe(rng))
        cur = conn.execute(
            "INSERT INTO blackjack_games (telegram_id, bet, wager, deck_json, deck_pos, player_json, dealer_json, "
            "status, created_at, updated_at) VALUES (?, ?, ?, '', 0, '[]', '[]', 'active', ?, ?)",
            (telegram_id, bet, bet, now_, now_),
        )
        game_id = cur.lastrowid
        if state["status"] == "finished":
            _bj_finish(conn, telegram_id, game_id, state, now_)
        else:
            _bj_save(conn, game_id, state, now_)
        return _bj_response(conn, telegram_id, conn.execute("SELECT * FROM blackjack_games WHERE id = ?", (game_id,)).fetchone())

    return _run_blackjack_action(telegram_id, request_id, "start", {"bet": bet}, body, now, db_path)


def blackjack_action(telegram_id, request_id, action, now=None, db_path=None):
    """hit, stand или double. double: только на первых двух картах (InvalidAction), списывается вторая ставка
    (InsufficientFunds), она тоже идёт в total_staked."""
    if action not in blackjack.ACTIONS:
        raise ValueError("bad action")

    def body(conn, now_):
        row = _bj_active(conn, telegram_id)
        if row is None:
            raise blackjack.NoActiveGame()
        state = _bj_state(row)
        if action == "double":
            if not blackjack.can_double(state):
                raise blackjack.InvalidAction()
            wallet.debit(conn, telegram_id, state["bet"])
            conn.execute("UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                         (state["bet"], MAX_SAFE_INT, telegram_id))
        blackjack.act(state, action)
        if state["status"] == "finished":
            _bj_finish(conn, telegram_id, row["id"], state, now_)
        else:
            _bj_save(conn, row["id"], state, now_)
        return _bj_response(conn, telegram_id, conn.execute("SELECT * FROM blackjack_games WHERE id = ?", (row["id"],)).fetchone())

    return _run_blackjack_action(telegram_id, request_id, action, {}, body, now, db_path)


def blackjack_state(telegram_id, now=None, db_path=None):
    """Активная раздача, иначе последняя завершённая, иначе status none (только чтение, плюс закрытие просроченной)."""
    if now is None:
        now = int(time.time())
    settle_expired_blackjack(telegram_id, now=now, db_path=db_path)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)   # баланс с начислением, как /api/me
            row = conn.execute(
                "SELECT * FROM blackjack_games WHERE telegram_id = ? ORDER BY (status = 'active') DESC, id DESC LIMIT 1",
                (telegram_id,),
            ).fetchone()
            pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
            level = profile_level(pl["xp"])
            if row is None:
                result = blackjack.none_view(pl["balance"], level, pl["xp"])
            else:
                result = blackjack.view(_bj_state(row), pl["balance"], level, pl["xp"], auto=bool(row["auto"]))
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


# ---------- краш ----------

def _now_ms():
    return int(time.time() * 1000)


def _crash_active(conn, telegram_id):
    return conn.execute(
        "SELECT * FROM crash_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,)
    ).fetchone()


def _crash_view(row, now_ms, balance, level, xp_total, replayed=False):
    """Ответ API (одна форма). Пока раунд идёт, точки краха в ответе нет; elapsed_ms по эффективному времени."""
    active = row["status"] == "active"
    return {
        "status": row["status"],
        "mode": row["mode"],
        "bet": row["bet"],
        "target": crash.text(row["target_x100"]) if row["target_x100"] is not None else None,
        "elapsed_ms": crash.effective_ms(now_ms, row["started_at_ms"]) if active else None,
        "doubling_ms": crash.DOUBLING_MS,
        "cap": crash.text(crash.CAP_X100),
        "crash_multiplier": None if active else crash.text(row["crash_x100"]),
        "result": row["result"],
        "multiplier": None if active else crash.text(row["mult_x100"]),
        "payout": row["payout"],
        "balance": balance,
        "level": level,
        "xp": xp_total,
        "auto": bool(row["auto"]),
        "replayed": replayed,
    }


def _crash_none_view(balance, level, xp_total):
    return {"status": "none", "mode": None, "bet": None, "target": None, "elapsed_ms": None, "doubling_ms": crash.DOUBLING_MS,
            "cap": crash.text(crash.CAP_X100), "crash_multiplier": None, "result": None, "multiplier": None, "payout": None,
            "balance": balance, "level": level, "xp": xp_total, "auto": False, "replayed": False}


def _crash_response(conn, telegram_id, game_id, now_ms, replayed=False):
    row = conn.execute("SELECT * FROM crash_games WHERE id = ?", (game_id,)).fetchone()
    pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return _crash_view(row, now_ms, pl["balance"], profile_level(pl["xp"]), pl["xp"], replayed)


def _crash_finish(conn, telegram_id, row, result, mult_x100, now, auto):
    """Единственное место окончания раунда: выплата через wallet, XP, отметка времени. Раунд закрывается один раз
    (условие status = 'active'), значит выплата и опыт тоже один раз."""
    paid = crash.payout(row["bet"], mult_x100) if result == "win" else 0
    changed = conn.execute(
        "UPDATE crash_games SET status = 'finished', result = ?, mult_x100 = ?, payout = ?, auto = ?, finished_at = ? "
        "WHERE id = ? AND status = 'active'", (result, mult_x100, paid, 1 if auto else 0, now, row["id"])).rowcount
    if changed == 0:
        return
    if paid > 0:
        _credit_capped(conn, telegram_id, paid)
    _add_xp(conn, telegram_id, xp.crash_xp(row["bet"], crash.xp_multiplier(row["mode"], result, mult_x100, row["target_x100"])))


def _crash_settle_in(conn, telegram_id, now_ms):
    """Закрывает активный ручной раунд, если он разбился по времени, достиг предела или брошен. Возвращает True, если закрыла."""
    row = _crash_active(conn, telegram_id)
    if row is None:
        return False
    verdict = crash.settle(row["crash_x100"], row["started_at_ms"], now_ms)
    if verdict is None:
        return False
    _crash_finish(conn, telegram_id, row, verdict[0], verdict[1], now_ms // 1000, True)
    return True


def settle_expired_crash(telegram_id, now_ms=None, db_path=None):
    """Закрывает раунд игрока отдельной транзакцией (идемпотентно). Без изменений только читает. True, если закрыла."""
    if now_ms is None:
        now_ms = _now_ms()
    conn = _connect(db_path)
    try:
        row = _crash_active(conn, telegram_id)
        if row is None or crash.settle(row["crash_x100"], row["started_at_ms"], now_ms) is None:
            return False
        conn.execute("BEGIN IMMEDIATE")
        try:
            closed = _crash_settle_in(conn, telegram_id, now_ms)
            conn.execute("COMMIT")
            return closed
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


CRASH_CLOSE_BATCH = 200


def close_expired_crash(now_ms=None, db_path=None, batch=CRASH_CLOSE_BATCH):
    """Фоновое закрытие брошенных раундов всех игроков (не больше batch за проход). Возвращает число."""
    if now_ms is None:
        now_ms = _now_ms()
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'crash_games'").fetchone() is None:
            return 0
        owners = [r["telegram_id"] for r in conn.execute(
            "SELECT telegram_id FROM crash_games WHERE status = 'active' AND started_at_ms <= ? LIMIT ?",
            (now_ms - crash.ABANDON_MS, batch))]
    finally:
        conn.close()
    closed = sum(1 for owner in owners if settle_expired_crash(owner, now_ms=now_ms, db_path=db_path))
    if closed:
        logger.info("Закрыто брошенных раундов краша: %d", closed)  # только количество
    return closed


def _run_crash_action(telegram_id, request_id, action, params, body, now_ms, db_path, presettle=True):
    """Общий порядок действия: закрытие просроченного раунда (кроме cashout: он закрывает сам), повтор по request_id,
    начисление по часам, тело действия, запись ответа. Один request_id с другими параметрами: RequestConflict."""
    if now_ms is None:
        now_ms = _now_ms()
    now = now_ms // 1000
    if presettle:
        settle_expired_crash(telegram_id, now_ms=now_ms, db_path=db_path)
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT action, params, response_json FROM crash_actions WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                if old["action"] != action or old["params"] != params_json:
                    raise crash.RequestConflict()
                response = json.loads(old["response_json"])
                response["replayed"] = True
                conn.execute("COMMIT")
                return response
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            response = body(conn, now_ms, now)
            response["replayed"] = False
            conn.execute(
                "INSERT INTO crash_actions (telegram_id, request_id, action, params, response_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, action, params_json, json.dumps(response, separators=(",", ":")), now),
            )
            conn.execute("COMMIT")
            return response
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def crash_start(telegram_id, request_id, bet, target_x100=None, now_ms=None, db_path=None, rng=None):
    """Новый раунд: нет активного, ставка списывается через wallet и идёт в total_staked, точка краха выбирается и прячется.
    Режим авто (задан target_x100): раунд решается сразу. Ручной: раунд активен, множитель растёт по времени сервера."""
    if type(bet) is not int or not 1 <= bet <= crash.CRASH_MAX_BET:
        raise ValueError("bet out of range")
    if target_x100 is not None and (type(target_x100) is not int
                                    or not crash.MIN_TARGET_X100 <= target_x100 <= crash.CAP_X100):
        raise ValueError("target out of range")

    def body(conn, now_ms_, now):
        if _crash_active(conn, telegram_id) is not None:
            raise crash.ActiveGameExists()
        wallet.debit(conn, telegram_id, bet)   # InsufficientFunds, если фишек не хватает
        conn.execute("UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                     (bet, MAX_SAFE_INT, telegram_id))
        crash_x100 = crash.new_crash(rng)
        mode = "manual" if target_x100 is None else "auto"
        cur = conn.execute(
            "INSERT INTO crash_games (telegram_id, bet, mode, target_x100, crash_x100, started_at_ms, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active', ?)",
            (telegram_id, bet, mode, target_x100, crash_x100, now_ms_, now))
        game_id = cur.lastrowid
        if mode == "auto":
            result, mult = crash.decide_auto(target_x100, crash_x100)
            _crash_finish(conn, telegram_id, conn.execute("SELECT * FROM crash_games WHERE id = ?", (game_id,)).fetchone(),
                          result, mult, now, False)
        return _crash_response(conn, telegram_id, game_id, now_ms_)

    return _run_crash_action(telegram_id, request_id, "start", {"bet": bet, "target_x100": target_x100}, body, now_ms, db_path)


def crash_cashout(telegram_id, request_id, now_ms=None, db_path=None):
    """Вывод на текущем множителе. Если раунд к этому моменту уже разбился (или достиг предела), отвечает итогом раунда.
    TooEarly (множитель меньше 1.01): раунд остаётся активным."""
    def body(conn, now_ms_, now):
        row = _crash_active(conn, telegram_id)
        if row is None:
            raise crash.NoActiveGame()
        if _crash_settle_in(conn, telegram_id, now_ms_):
            return _crash_response(conn, telegram_id, row["id"], now_ms_)
        c = crash.cashout_multiplier(row["started_at_ms"], now_ms_)   # TooEarly: ничего не меняется (откат)
        _crash_finish(conn, telegram_id, row, "win", c, now, False)
        return _crash_response(conn, telegram_id, row["id"], now_ms_)

    return _run_crash_action(telegram_id, request_id, "cashout", {}, body, now_ms, db_path, presettle=False)


def crash_state(telegram_id, now_ms=None, db_path=None):
    """Активный раунд, иначе последний завершённый, иначе status none. Во время полёта только читает (без начисления)."""
    if now_ms is None:
        now_ms = _now_ms()
    settle_expired_crash(telegram_id, now_ms=now_ms, db_path=db_path)
    conn = _connect(db_path)
    try:
        row = _crash_active(conn, telegram_id)
        if row is not None:
            pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return _crash_view(row, now_ms, pl["balance"], profile_level(pl["xp"]), pl["xp"])
    finally:
        conn.close()
    now = now_ms // 1000
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)   # баланс с начислением, как /api/me
            row = conn.execute(
                "SELECT * FROM crash_games WHERE telegram_id = ? ORDER BY (status = 'active') DESC, id DESC LIMIT 1",
                (telegram_id,)).fetchone()
            pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
            level = profile_level(pl["xp"])
            result = _crash_none_view(pl["balance"], level, pl["xp"]) if row is None else _crash_view(
                row, now_ms, pl["balance"], level, pl["xp"])
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


# ---------- ферма: улучшения дохода и хранилища ----------

def buy_upgrade(telegram_id, request_id, kind, now=None, db_path=None):
    """Покупка улучшения (kind "income" или "storage") в одной транзакции BEGIN IMMEDIATE.

    Порядок: повтор по request_id; начисление накопленного по старой ставке и старому потолку; проверки
    (максимальный уровень, лимит по уровню профиля, достаточно ли фишек); списание через wallet.debit;
    повышение уровня (для дохода и players.rate); запись покупки. Бросает farm.MaxLevel, farm.LevelLocked,
    wallet.InsufficientFunds; при любой ошибке в базе ничего не меняется.
    """
    if kind not in farm.KINDS:
        raise ValueError("unknown kind")
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # (а) повтор: тот же ответ, без списания (balance, как у spin, текущий)
            old = conn.execute(
                "SELECT kind, level_after, cost FROM farm_purchases WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                cur = conn.execute("SELECT balance FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
                result = {"kind": old["kind"], "level_after": old["level_after"], "cost": old["cost"],
                          "balance": cur["balance"] if cur else 0, "replayed": True}
                conn.execute("COMMIT")
                return result

            # (б) начисление по СТАРОЙ ставке и СТАРОМУ потолку: новые значения на прошлое не действуют
            _register_player(conn, telegram_id, now)
            row = conn.execute(
                "SELECT balance, rate, last_accrual, xp, income_level, storage_level "
                "FROM players WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            earned, new_last = _accrue_player(row, now)
            conn.execute(
                "UPDATE players SET balance = ?, last_accrual = ? WHERE telegram_id = ?",
                (row["balance"] + earned, new_last, telegram_id),
            )

            # (в) проверки по порядку
            level = row["income_level"] if kind == "income" else row["storage_level"]
            cost = farm.income_cost(level) if kind == "income" else farm.storage_cost(level)
            if cost is None:
                raise farm.MaxLevel()
            used = row["income_level"] + row["storage_level"]
            if used >= profile_level(row["xp"]):   # уровень профиля по опыту
                raise farm.LevelLocked(used + 1)

            # (г) списание, повышение уровня, запись покупки
            wallet.debit(conn, telegram_id, cost)   # wallet.InsufficientFunds, если фишек не хватает
            if kind == "income":
                conn.execute(
                    "UPDATE players SET income_level = ?, rate = ? WHERE telegram_id = ?",
                    (level + 1, farm.income_rate(level + 1), telegram_id),
                )
            else:
                conn.execute("UPDATE players SET storage_level = ? WHERE telegram_id = ?", (level + 1, telegram_id))
            conn.execute(
                "INSERT INTO farm_purchases (telegram_id, request_id, kind, level_after, cost, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, kind, level + 1, cost, now),
            )
            result = {"kind": kind, "level_after": level + 1, "cost": cost,
                      "balance": wallet.get_balance(conn, telegram_id), "replayed": False}
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def farm_status(telegram_id, now=None, db_path=None):
    """Данные экрана фермы (GET /api/farm). Баланс с начислением, как в /api/me."""
    player = get_player(telegram_id, now=now, db_path=db_path)
    return farm.status(player["balance"], player["xp"], player["total_staked"], player["income_level"],
                       player["storage_level"])


# ---------- мины ----------
# Все изменения баланса идут через wallet. Раскладка мин активной игры не попадает ни в ответы, ни в лог,
# ни в response_json, ни в выгрузку данных. Баланс игрока с активной игрой не включает ставку, лежащую в игре
# (она возвращается при завершении), в рейтинге беседы это так же.

def _accrue_write(conn, telegram_id, now):
    """Начисление по часам как в spin_roulette: пишет баланс и last_accrual (единая _accrue_player)."""
    row = conn.execute(
        "SELECT balance, rate, last_accrual, storage_level FROM players WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    earned, new_last = _accrue_player(row, now)
    conn.execute(
        "UPDATE players SET balance = ?, last_accrual = ? WHERE telegram_id = ?",
        (row["balance"] + earned, new_last, telegram_id),
    )


def _credit_capped(conn, telegram_id, amount):
    """Зачисляет min(amount, MAX_SAFE_INT - баланс) (на практике недостижимо). Возвращает зачисленное."""
    amount = min(amount, MAX_SAFE_INT - wallet.get_balance(conn, telegram_id))
    if amount > 0:
        wallet.credit(conn, telegram_id, amount)
        return amount
    return 0


def _active_game(conn, telegram_id):
    return conn.execute(
        "SELECT * FROM mines_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,)
    ).fetchone()


def _game_view(row):
    """Активная игра для клиента. Раскладки мин здесь нет."""
    m = row["mines_count"]
    k = mines.popcount(row["revealed_mask"])
    left = mines.FIELD_CELLS - m - k
    return {
        "bet": row["bet"],
        "mines": m,
        "revealed": mines.cells_of(row["revealed_mask"]),
        "safe_left": left,
        "multiplier": mines.multiplier_text(m, k),
        "payout_now": mines.payout(row["bet"], m, k),
        "next_multiplier": mines.multiplier_text(m, k + 1) if left > 0 else None,
        "next_payout": mines.payout(row["bet"], m, k + 1) if left > 0 else None,
        "expires_at": row["updated_at"] + mines.MINES_IDLE_SECONDS,
    }


def _last_view(row):
    """Завершённая игра: раскладка мин раскрывается только здесь."""
    return {
        "status": row["status"],
        "bet": row["bet"],
        "mines": row["mines_count"],
        "revealed": mines.cells_of(row["revealed_mask"]),
        "mine_cells": mines.cells_of(row["mine_mask"]),
        "payout": row["payout"],
        "finished_at": row["finished_at"],
    }


def _add_xp(conn, telegram_id, amount):
    """Опыт игрока (в той же транзакции, что и результат), не выше MAX_SAFE_INT."""
    if amount > 0:
        conn.execute("UPDATE players SET xp = MIN(xp + ?, ?) WHERE telegram_id = ?", (amount, MAX_SAFE_INT, telegram_id))


def _finish_game(conn, game_id, status, payout, now):
    """Закрывает активную игру и начисляет опыт: единственное место для всех путей закрытия (мина, очистка поля,
    cashout, автозакрытие). Игра закрывается один раз (условие status = 'active'), значит и опыт один раз.
    Возврат ставки (refunded, auto_refunded) опыта не даёт."""
    game = conn.execute(
        "SELECT telegram_id, bet, mines_count, revealed_mask FROM mines_games WHERE id = ? AND status = 'active'",
        (game_id,),
    ).fetchone()
    if game is None:
        return
    conn.execute(
        "UPDATE mines_games SET status = ?, payout = ?, finished_at = ?, updated_at = ? WHERE id = ?",
        (status, payout, now, now, game_id),
    )
    if status in ("lost", "cashed", "auto_cashed"):
        _add_xp(conn, game["telegram_id"],
                xp.mines_xp(game["bet"], game["mines_count"], mines.popcount(game["revealed_mask"]), status == "lost"))


def _settle_expired_in(conn, telegram_id, now):
    """Закрывает просроченную активную игру игрока (внутри открытой транзакции). True, если закрыла."""
    game = _active_game(conn, telegram_id)
    if game is None or now - game["updated_at"] < mines.MINES_IDLE_SECONDS:
        return False
    k = mines.popcount(game["revealed_mask"])
    if k == 0:
        # автоматический возврат отличается от ручного (cashout при k = 0 остаётся «refunded»)
        _finish_game(conn, game["id"], "auto_refunded", _credit_capped(conn, telegram_id, game["bet"]), now)
    else:
        owed = mines.payout(game["bet"], game["mines_count"], k)
        _finish_game(conn, game["id"], "auto_cashed", _credit_capped(conn, telegram_id, owed), now)
    return True


def settle_expired_mines(telegram_id, now=None, db_path=None):
    """Закрывает просроченную игру игрока отдельной транзакцией (идемпотентно). True, если закрыла."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            closed = _settle_expired_in(conn, telegram_id, now)
            conn.execute("COMMIT")
            return closed
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


MINES_CLOSE_BATCH = 200


def close_expired_mines(now=None, db_path=None, batch=MINES_CLOSE_BATCH):
    """Фоновое закрытие просроченных активных игр всех игроков (не больше batch за проход). Возвращает число."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'mines_games'").fetchone() is None:
            return 0
        owners = [r["telegram_id"] for r in conn.execute(
            "SELECT telegram_id FROM mines_games WHERE status = 'active' AND ? - updated_at >= ? LIMIT ?",
            (now, mines.MINES_IDLE_SECONDS, batch),
        )]
    finally:
        conn.close()
    closed = sum(1 for owner in owners if settle_expired_mines(owner, now=now, db_path=db_path))
    if closed:
        logger.info("Закрыто просроченных игр в мины: %d", closed)  # только количество
    return closed


def _run_mines_action(telegram_id, request_id, action, params, body, now, db_path):
    """Общий порядок действия: закрытие просроченной игры, повтор по request_id, начисление по часам, тело
    действия, запись ответа. Один request_id с другим действием или параметрами даёт RequestConflict."""
    if now is None:
        now = int(time.time())
    settle_expired_mines(telegram_id, now=now, db_path=db_path)
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute(
                "SELECT action, params, response_json FROM mines_actions WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                if old["action"] != action or old["params"] != params_json:
                    raise mines.RequestConflict()
                response = json.loads(old["response_json"])
                response["replayed"] = True
                conn.execute("COMMIT")
                return response
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            response = body(conn, now)
            response["balance"] = wallet.get_balance(conn, telegram_id)
            response["replayed"] = False
            conn.execute(
                "INSERT INTO mines_actions (telegram_id, request_id, action, params, response_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, action, params_json, json.dumps(response, separators=(",", ":")), now),
            )
            conn.execute("COMMIT")
            return response
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def mines_start(telegram_id, request_id, bet, mines_count, now=None, db_path=None, rng=None):
    """Старт игры: нет активной игры, списание ставки через wallet, раскладка мин. Ставка в total_staked
    на этом шаге не засчитывается."""
    if type(bet) is not int or not 1 <= bet <= mines.MINES_MAX_BET:
        raise ValueError("bet out of range")
    if type(mines_count) is not int or not mines.MINES_MIN_COUNT <= mines_count <= mines.MINES_MAX_COUNT:
        raise ValueError("mines out of range")

    def body(conn, now_):
        if _active_game(conn, telegram_id) is not None:
            raise mines.ActiveGameExists()
        wallet.debit(conn, telegram_id, bet)   # wallet.InsufficientFunds, если фишек не хватает
        conn.execute(
            "INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, revealed_mask, status, payout, "
            "staked_counted, created_at, updated_at) VALUES (?, ?, ?, ?, 0, 'active', 0, 0, ?, ?)",
            (telegram_id, bet, mines_count, mines.new_layout(mines_count, rng), now_, now_),
        )
        return {"game": _game_view(_active_game(conn, telegram_id))}

    return _run_mines_action(telegram_id, request_id, "start", {"bet": bet, "mines": mines_count}, body, now, db_path)


def mines_reveal(telegram_id, request_id, cell, now=None, db_path=None):
    """Открытие клетки. При первом открытии ставка добавляется к total_staked (один раз за игру)."""
    if type(cell) is not int or not 0 <= cell < mines.FIELD_CELLS:
        raise ValueError("cell out of range")

    def body(conn, now_):
        game = _active_game(conn, telegram_id)
        if game is None:
            raise mines.NoActiveGame()
        bit = 1 << cell
        if game["revealed_mask"] & bit:
            raise mines.AlreadyRevealed()
        if not game["staked_counted"]:
            conn.execute(
                "UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                (game["bet"], MAX_SAFE_INT, telegram_id),
            )
            conn.execute("UPDATE mines_games SET staked_counted = 1 WHERE id = ?", (game["id"],))
        if game["mine_mask"] & bit:
            _finish_game(conn, game["id"], "lost", 0, now_)
            last = conn.execute("SELECT * FROM mines_games WHERE id = ?", (game["id"],)).fetchone()
            return {"result": "mine", "game": None, "last": _last_view(last)}
        revealed = game["revealed_mask"] | bit
        k = mines.popcount(revealed)
        conn.execute("UPDATE mines_games SET revealed_mask = ?, updated_at = ? WHERE id = ?", (revealed, now_, game["id"]))
        if k == mines.FIELD_CELLS - game["mines_count"]:
            owed = mines.payout(game["bet"], game["mines_count"], k)
            _finish_game(conn, game["id"], "cashed", _credit_capped(conn, telegram_id, owed), now_)
            last = conn.execute("SELECT * FROM mines_games WHERE id = ?", (game["id"],)).fetchone()
            return {"result": "cleared", "game": None, "last": _last_view(last)}
        return {"result": "safe", "game": _game_view(_active_game(conn, telegram_id))}

    return _run_mines_action(telegram_id, request_id, "reveal", {"cell": cell}, body, now, db_path)


def mines_cashout(telegram_id, request_id, now=None, db_path=None):
    """Забрать выигрыш: при нуле открытых клеток возвращается ставка (refunded, в total_staked не идёт)."""
    def body(conn, now_):
        game = _active_game(conn, telegram_id)
        if game is None:
            raise mines.NoActiveGame()
        k = mines.popcount(game["revealed_mask"])
        if k == 0:
            _finish_game(conn, game["id"], "refunded", _credit_capped(conn, telegram_id, game["bet"]), now_)
        else:
            owed = mines.payout(game["bet"], game["mines_count"], k)
            _finish_game(conn, game["id"], "cashed", _credit_capped(conn, telegram_id, owed), now_)
        last = conn.execute("SELECT * FROM mines_games WHERE id = ?", (game["id"],)).fetchone()
        return {"last": _last_view(last)}

    return _run_mines_action(telegram_id, request_id, "cashout", {}, body, now, db_path)


def mines_state(telegram_id, now=None, db_path=None):
    """Активная игра или None, последняя завершённая или None, баланс (с начислением, как /api/me)."""
    if now is None:
        now = int(time.time())
    settle_expired_mines(telegram_id, now=now, db_path=db_path)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _register_player(conn, telegram_id, now)
            _accrue_write(conn, telegram_id, now)
            active = _active_game(conn, telegram_id)
            last = conn.execute(
                "SELECT * FROM mines_games WHERE telegram_id = ? AND status != 'active' "
                "ORDER BY finished_at DESC, id DESC LIMIT 1", (telegram_id,),
            ).fetchone()
            result = {
                "game": _game_view(active) if active is not None else None,
                "last": _last_view(last) if last is not None else None,
                "balance": wallet.get_balance(conn, telegram_id),
            }
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


# ---------- рейтинг беседы ----------
NAME_MAX = 32
DEFAULT_NAME = "Игрок"
TOUCH_INTERVAL = 60       # запись участника обновляется не чаще раза в 60 секунд
TOP_SIZE = 10
# Ограничение для больших чатов: в рейтинге учитываются не больше 1000 участников одной
# беседы, самые недавно активные (по last_seen). Остальные в рейтинг не попадают, и
# me.total считается по этим же участникам, поэтому ранг и total всегда согласованы.
MAX_CHAT_MEMBERS = 1000

# управляющие направления текста (могут перевернуть соседний текст на экране)
_BIDI = set("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def clean_name(name):
    """Убирает управляющие символы, обрезает пробелы по краям и длину до 32 символов."""
    if not isinstance(name, str):
        return DEFAULT_NAME
    kept = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cs") and ch not in _BIDI)
    kept = kept.strip()[:NAME_MAX].strip()
    return kept or DEFAULT_NAME


def _touch_member(conn, chat_instance, telegram_id, first_name, now):
    """Запись или обновление участника внутри открытой транзакции. True, если база изменена."""
    name = clean_name(first_name)
    row = conn.execute(
        "SELECT last_seen FROM chat_members WHERE chat_instance = ? AND telegram_id = ?",
        (chat_instance, telegram_id),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_instance, telegram_id, name, now, now),
        )
        return True
    if now - row["last_seen"] < TOUCH_INTERVAL:
        return False
    conn.execute(
        "UPDATE chat_members SET first_name = ?, last_seen = ? WHERE chat_instance = ? AND telegram_id = ?",
        (name, now, chat_instance, telegram_id),
    )
    return True


def touch_chat_member(chat_instance, telegram_id, first_name, now=None, db_path=None):
    """Записывает или обновляет участника беседы (не чаще раза в 60 секунд). Возвращает True, если записал."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            changed = _touch_member(conn, chat_instance, telegram_id, first_name, now)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return changed


def chat_top(chat_instance, telegram_id, first_name, now=None, db_path=None):
    """Рейтинг беседы: до 10 лучших и позиция вызвавшего.

    Вызвавший записывается как участник (как в touch_chat_member). Баланс для показа =
    хранимый + то, что начислилось бы по accrue() на время now; в базе из-за этого
    расчёта ничего не меняется. Сортировка: баланс по убыванию, затем first_seen, затем
    telegram_id. Игроки без записи в players пропускаются.
    """
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # вызвавший должен быть в players, чтобы попасть в список без вызова /api/me;
            # начисление ему при этом не применяется (строка создаётся только для новых)
            _register_player(conn, telegram_id, now)
            _touch_member(conn, chat_instance, telegram_id, first_name, now)
            rows = conn.execute(
                "SELECT m.telegram_id, m.first_name, m.first_seen, p.balance, p.rate, p.last_accrual, "
                "       p.total_staked, p.storage_level, p.xp "
                "FROM (SELECT telegram_id, first_name, first_seen FROM chat_members "
                "      WHERE chat_instance = ? ORDER BY last_seen DESC, telegram_id LIMIT ?) m "
                "JOIN players p ON p.telegram_id = m.telegram_id",
                (chat_instance, MAX_CHAT_MEMBERS),
            ).fetchall()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    entries = []
    for r in rows:
        earned, _ = _accrue_player(r, now)
        entries.append((-(r["balance"] + earned), r["first_seen"], r["telegram_id"], r["first_name"],
                        r["total_staked"], r["xp"]))
    entries.sort(key=lambda e: (e[0], e[1], e[2]))

    top = [
        {"rank": i + 1, "name": e[3], "balance": -e[0], "is_me": e[2] == telegram_id, "staked": e[4],
         "level": profile_level(e[5])}   # уровень по опыту, поле staked остаётся информацией
        for i, e in enumerate(entries[:TOP_SIZE])
    ]
    me = None
    for i, e in enumerate(entries):
        if e[2] == telegram_id:
            # total здесь число участников рейтинга (не сумма ставок); сумма ставок: staked и chat_staked
            me = {"rank": i + 1, "balance": -e[0], "total": len(entries), "staked": e[4],
                  "level": profile_level(e[5])}
    # сумма ставок всех участников того же набора, по которому строится рейтинг (без разбивки по людям)
    chat_staked = min(sum(e[4] for e in entries), MAX_SAFE_INT)
    return {"scope": "chat", "top": top, "me": me, "chat_staked": chat_staked}


# ---------- права на данные: выгрузка и удаление ----------

def get_player_export(telegram_id, rounds_limit=100, db_path=None):
    """Данные игрока для /mydata (только чтение). None, если о нём вообще ничего нет.

    В выгрузку не входят идентификаторы чатов и данные других игроков.
    """
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN")  # один снимок для всех запросов
        try:
            player = conn.execute(
                "SELECT telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level "
                "FROM players WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            rounds = conn.execute(
                "SELECT created_at, bets_json, number, stake_total, payout_total FROM roulette_rounds "
                "WHERE telegram_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            purchases = conn.execute(
                "SELECT created_at, kind, level_after, cost FROM farm_purchases "
                "WHERE telegram_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            games = conn.execute(
                "SELECT created_at, bet, mines_count, revealed_mask, status, payout, finished_at FROM mines_games "
                "WHERE telegram_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            keno_rounds = conn.execute(
                "SELECT created_at, bet, picks_json, draw_json, hit_count, payout FROM keno_rounds "
                "WHERE telegram_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            bj_games = conn.execute(
                "SELECT created_at, bet, wager, player_json, dealer_json, result, payout, finished_at FROM blackjack_games "
                "WHERE telegram_id = ? AND status = 'finished' ORDER BY created_at DESC, id DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            bj_active = conn.execute(
                "SELECT 1 FROM blackjack_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,)
            ).fetchone() is not None
            crash_games = conn.execute(
                "SELECT created_at, bet, mode, target_x100, crash_x100, result, payout, finished_at FROM crash_games "
                "WHERE telegram_id = ? AND status = 'finished' ORDER BY created_at DESC, id DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            crash_active = conn.execute(
                "SELECT 1 FROM crash_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,)
            ).fetchone() is not None
            chats = conn.execute(
                "SELECT first_seen, last_seen, first_name FROM chat_members "
                "WHERE telegram_id = ? ORDER BY first_seen, last_seen",
                (telegram_id,),
            ).fetchall()
        finally:
            conn.execute("COMMIT")
    finally:
        conn.close()
    if player is None and not rounds and not chats and not purchases and not games and not keno_rounds and not bj_games and not bj_active and not crash_games and not crash_active:
        return None
    return {
        "player": dict(player) if player is not None else None,
        "rounds": [
            {"time": r["created_at"], "bets": json.loads(r["bets_json"]), "number": r["number"],
             "stake_total": r["stake_total"], "payout_total": r["payout_total"]}
            for r in rounds
        ],
        # раскладка мин (mine_mask) в выгрузку не входит никогда: она раскрыла бы поле текущей игры
        "mines_games": [
            {"created_at": g["created_at"], "bet": g["bet"], "mines": g["mines_count"],
             "opened": mines.popcount(g["revealed_mask"]), "status": g["status"], "payout": g["payout"],
             "finished_at": g["finished_at"]}
            for g in games
        ],
        # колода и карты незавершённой раздачи в выгрузку не входят: они раскрыли бы скрытую карту дилера
        "blackjack_games": [
            {"created_at": g["created_at"], "bet": g["bet"], "wager": g["wager"],
             "player_cards": json.loads(g["player_json"]), "dealer_cards": json.loads(g["dealer_json"]),
             "result": g["result"], "payout": g["payout"], "finished_at": g["finished_at"]}
            for g in bj_games
        ],
        "blackjack_active": bj_active,
        # точка краха активного раунда в выгрузку не входит: она раскрыла бы исход
        "crash_games": [
            {"created_at": g["created_at"], "bet": g["bet"], "mode": g["mode"], "target_x100": g["target_x100"],
             "crash_x100": g["crash_x100"], "result": g["result"], "payout": g["payout"], "finished_at": g["finished_at"]}
            for g in crash_games
        ],
        "crash_active": crash_active,
        "keno_rounds": [
            {"time": k["created_at"], "bet": k["bet"], "picks": json.loads(k["picks_json"]),
             "draw": json.loads(k["draw_json"]), "hits": k["hit_count"], "payout": k["payout"]}
            for k in keno_rounds
        ],
        "farm_purchases": [
            {"time": p["created_at"], "kind": p["kind"], "level": p["level_after"], "cost": p["cost"]}
            for p in purchases
        ],
        "chats": [{"first_seen": c["first_seen"], "last_seen": c["last_seen"], "name": c["first_name"]}
                  for c in chats],
    }


def delete_player_data(telegram_id, db_path=None, now=None):
    """Удаляет все данные игрока в одной транзакции. Возвращает число удалённых строк по таблицам.

    В той же транзакции, если у игрока была строка в players, записывается «надгробие»:
    HMAC-хэш идентификатора и время удаления (идентификатор не хранится). Просроченные
    надгробия удаляются. Без TOMBSTONE_SECRET ничего не удаляется: бросается
    TombstoneUnavailable.
    """
    secret = antiabuse.tombstone_secret()
    if secret is None:
        raise TombstoneUnavailable()
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            counts = {
                "players": conn.execute("DELETE FROM players WHERE telegram_id = ?", (telegram_id,)).rowcount,
                "roulette_rounds": conn.execute(
                    "DELETE FROM roulette_rounds WHERE telegram_id = ?", (telegram_id,)).rowcount,
                "chat_members": conn.execute(
                    "DELETE FROM chat_members WHERE telegram_id = ?", (telegram_id,)).rowcount,
                "farm_purchases": conn.execute(
                    "DELETE FROM farm_purchases WHERE telegram_id = ?", (telegram_id,)).rowcount,
                # незавершённая игра удаляется вместе со ставкой
                "mines_games": conn.execute(
                    "DELETE FROM mines_games WHERE telegram_id = ?", (telegram_id,)).rowcount,
            }
            conn.execute("DELETE FROM mines_actions WHERE telegram_id = ?", (telegram_id,))
            counts["blackjack_games"] = conn.execute(
                "DELETE FROM blackjack_games WHERE telegram_id = ?", (telegram_id,)).rowcount   # и незавершённая вместе со ставкой
            conn.execute("DELETE FROM blackjack_actions WHERE telegram_id = ?", (telegram_id,))
            counts["crash_games"] = conn.execute(
                "DELETE FROM crash_games WHERE telegram_id = ?", (telegram_id,)).rowcount   # и незавершённый вместе со ставкой
            conn.execute("DELETE FROM crash_actions WHERE telegram_id = ?", (telegram_id,))
            counts["keno_rounds"] = conn.execute(
                "DELETE FROM keno_rounds WHERE telegram_id = ?", (telegram_id,)).rowcount
            if counts["players"] > 0:
                conn.execute(
                    "INSERT OR REPLACE INTO deletion_tombstones (key_hash, deleted_at) VALUES (?, ?)",
                    (antiabuse.key_hash(telegram_id, secret), now),
                )
            conn.execute(
                "DELETE FROM deletion_tombstones WHERE deleted_at + ? <= ?", (COOLDOWN_SECONDS, now)
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return counts


# ---------- очистка старых данных ----------
PURGE_BATCH = 1000


def purge_old_data(now=None, db_path=None, rounds_days=30, member_days=90, batch=PURGE_BATCH):
    """Удаляет старые данные пачками (каждая пачка отдельной короткой транзакцией).

    roulette_rounds старше rounds_days (не меньше 2 суток: на это время нужна защита от
    повторов request_id), chat_members с last_seen старше member_days (не меньше 7) и
    просроченные deletion_tombstones. Таблицу players не трогает никогда.
    Возвращает число удалённых строк по таблицам; в лог идут только количества.
    """
    if now is None:
        now = int(time.time())
    rounds_days = max(int(rounds_days), 2)
    member_days = max(int(member_days), 7)
    conn = _connect(db_path)
    deleted = {"roulette_rounds": 0, "farm_purchases": 0, "mines_games": 0, "mines_actions": 0,
               "keno_rounds": 0, "blackjack_games": 0, "blackjack_actions": 0, "crash_games": 0, "crash_actions": 0, "chat_members": 0,
               "deletion_tombstones": 0}
    try:
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

        def batches(sql, params):
            total = 0
            while True:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    n = conn.execute(sql, params).rowcount
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                total += n
                if n < batch:
                    return total
                time.sleep(0.01)  # между пачками даём пройти другим записям

        if "roulette_rounds" in present:
            deleted["roulette_rounds"] = batches(
                "DELETE FROM roulette_rounds WHERE rowid IN "
                "(SELECT rowid FROM roulette_rounds WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "farm_purchases" in present:  # тот же срок хранения, что у раундов
            deleted["farm_purchases"] = batches(
                "DELETE FROM farm_purchases WHERE rowid IN "
                "(SELECT rowid FROM farm_purchases WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "mines_games" in present:  # завершённые старше срока раундов; активные не удаляются никогда
            deleted["mines_games"] = batches(
                "DELETE FROM mines_games WHERE id IN "
                "(SELECT id FROM mines_games WHERE status != 'active' AND finished_at IS NOT NULL "
                "AND finished_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "mines_actions" in present:
            deleted["mines_actions"] = batches(
                "DELETE FROM mines_actions WHERE rowid IN "
                "(SELECT rowid FROM mines_actions WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "keno_rounds" in present:  # тот же срок хранения, что у раундов рулетки
            deleted["keno_rounds"] = batches(
                "DELETE FROM keno_rounds WHERE id IN (SELECT id FROM keno_rounds WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "blackjack_games" in present:  # завершённые старше срока раундов; активные не удаляются никогда
            deleted["blackjack_games"] = batches(
                "DELETE FROM blackjack_games WHERE id IN "
                "(SELECT id FROM blackjack_games WHERE status != 'active' AND finished_at IS NOT NULL "
                "AND finished_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "blackjack_actions" in present:
            deleted["blackjack_actions"] = batches(
                "DELETE FROM blackjack_actions WHERE rowid IN "
                "(SELECT rowid FROM blackjack_actions WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "crash_games" in present:  # завершённые старше срока раундов; активные не удаляются никогда
            deleted["crash_games"] = batches(
                "DELETE FROM crash_games WHERE id IN "
                "(SELECT id FROM crash_games WHERE status != 'active' AND finished_at IS NOT NULL "
                "AND finished_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "crash_actions" in present:
            deleted["crash_actions"] = batches(
                "DELETE FROM crash_actions WHERE rowid IN "
                "(SELECT rowid FROM crash_actions WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "chat_members" in present:
            deleted["chat_members"] = batches(
                "DELETE FROM chat_members WHERE rowid IN "
                "(SELECT rowid FROM chat_members WHERE last_seen < ? LIMIT ?)",
                (now - member_days * 86400, batch))
        if "deletion_tombstones" in present:
            deleted["deletion_tombstones"] = batches(
                "DELETE FROM deletion_tombstones WHERE rowid IN "
                "(SELECT rowid FROM deletion_tombstones WHERE deleted_at + ? <= ? LIMIT ?)",
                (COOLDOWN_SECONDS, now, batch))
    finally:
        conn.close()
    logger.info("Очистка старых данных: раунды=%d участники=%d надгробия=%d покупки=%d игры=%d кено=%d блэкджек=%d краш=%d",
                deleted["roulette_rounds"], deleted["chat_members"], deleted["deletion_tombstones"],
                deleted["farm_purchases"], deleted["mines_games"] + deleted["mines_actions"], deleted["keno_rounds"],
                deleted["blackjack_games"] + deleted["blackjack_actions"], deleted["crash_games"] + deleted["crash_actions"])
    return deleted

import asyncio
import logging
import os
import re
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("depnaya.backup")

# ---------- настройки (необязательные переменные окружения) ----------
DEFAULT_INTERVAL_HOURS = 24
MIN_INTERVAL_HOURS = 1
DEFAULT_KEEP = 7
DEFAULT_ROUNDS_RETENTION_DAYS = 30   # не меньше 2: на это время хранится защита от повторов request_id
MIN_ROUNDS_RETENTION_DAYS = 2
DEFAULT_MEMBER_RETENTION_DAYS = 90
MIN_MEMBER_RETENTION_DAYS = 7
FIRST_DELAY_SECONDS = 60             # первая копия не раньше, чем через минуту после старта
TICK_SECONDS = 3600                  # как часто фоновая задача проверяет, не пора ли

NAME_RE = re.compile(r"^players-(\d{8}T\d{6}Z)\.db$")   # имя итоговой копии
TABLES = ("players", "roulette_rounds", "chat_members", "deletion_tombstones")
# запросы подсчёта строк заданы целиком (без подстановки имён в SQL)
COUNT_SQL = {
    "players": "SELECT COUNT(*) FROM players",
    "roulette_rounds": "SELECT COUNT(*) FROM roulette_rounds",
    "chat_members": "SELECT COUNT(*) FROM chat_members",
    "deletion_tombstones": "SELECT COUNT(*) FROM deletion_tombstones",
}
# Копируем порциями (около 4 МБ) с паузой между ними, чтобы не блокировать запись надолго.
# Если пока идёт копирование кто-то пишет в основную базу, SQLite перезапускает копирование
# с начала; поэтому порция большая (база обычно умещается в одну), а на всю копию есть лимит
# времени: при постоянной записи копия сорвётся с ошибкой и будет сделана в следующий раз.
PAGES_PER_STEP = 1000
STEP_SLEEP = 0.01
MAX_COPY_SECONDS = 120
BUSY_TIMEOUT = 30


def _int_env(env, name, default, minimum, invalid):
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    if re.fullmatch(r"\d{1,6}", raw) and int(raw) >= minimum:
        return int(raw)
    invalid.append(name)  # неверное значение заменяется умолчанием
    return default


def load_config(env=None, db_path=None):
    """Читает настройки; неверные значения заменяются умолчаниями (имена попадают в invalid)."""
    env = os.environ if env is None else env
    invalid = []
    enabled_raw = (env.get("BACKUP_ENABLED") or "").strip()
    if enabled_raw in ("", "1"):
        enabled = True
    elif enabled_raw == "0":
        enabled = False
    else:
        enabled = True
        invalid.append("BACKUP_ENABLED")
    directory = (env.get("BACKUP_DIR") or "").strip()
    if "\x00" in directory:
        directory = ""
        invalid.append("BACKUP_DIR")
    if not directory:
        base = db_path if db_path else _default_db_path()
        directory = os.path.join(os.path.dirname(os.path.abspath(base)), "backups")
    return {
        "enabled": enabled,
        "dir": directory,
        "interval_hours": _int_env(env, "BACKUP_INTERVAL_HOURS", DEFAULT_INTERVAL_HOURS, MIN_INTERVAL_HOURS, invalid),
        "keep": _int_env(env, "BACKUP_KEEP", DEFAULT_KEEP, 1, invalid),
        "rounds_days": _int_env(env, "ROUNDS_RETENTION_DAYS", DEFAULT_ROUNDS_RETENTION_DAYS, MIN_ROUNDS_RETENTION_DAYS, invalid),
        "member_days": _int_env(env, "CHAT_MEMBER_RETENTION_DAYS", DEFAULT_MEMBER_RETENTION_DAYS, MIN_MEMBER_RETENTION_DAYS, invalid),
        "invalid": invalid,
    }


def _default_db_path():
    import db  # импорт здесь, чтобы избежать циклической зависимости
    return db._resolve_path(None)


def warn_config(config):
    """Одно предупреждение при старте: имена переменных с неверными значениями."""
    if config["invalid"]:
        logger.warning("Неверные значения переменных: %s. Использованы значения по умолчанию",
                       ", ".join(config["invalid"]))


# ---------- копия ----------

def _utc_name(now):
    stamp = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return "players-%s.db" % stamp


def _ro_uri(path):
    return Path(os.path.abspath(path)).as_uri() + "?mode=ro"


def inspect_database(path):
    """Проверяет файл базы только на чтение: integrity_check и число строк по таблицам.
    Возвращает словарь {таблица: число строк}; бросает исключение, если файл повреждён."""
    conn = sqlite3.connect(_ro_uri(path), uri=True, timeout=BUSY_TIMEOUT)
    try:
        result = conn.execute("PRAGMA integrity_check").fetchall()
        if result != [("ok",)]:
            raise sqlite3.DatabaseError("integrity_check failed")
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        return {t: conn.execute(COUNT_SQL[t]).fetchone()[0] for t in TABLES if t in present}
    finally:
        conn.close()


def _remove_quietly(path):
    try:
        os.remove(path)
    except OSError:
        pass


def create_snapshot(db_path, backup_dir, now=None, keep=DEFAULT_KEEP):
    """Согласованная копия базы через Connection.backup(). Возвращает путь к копии или None."""
    if now is None:
        now = int(time.time())
    tmp = latest_tmp = None
    try:
        os.makedirs(backup_dir, mode=0o700, exist_ok=True)
        size = os.path.getsize(db_path)
        free = shutil.disk_usage(backup_dir).free
        if free < 3 * size:
            logger.warning("Резервная копия пропущена: мало свободного места")
            return None
        final = os.path.join(backup_dir, _utc_name(now))
        tmp = final + ".tmp"
        _remove_quietly(tmp)
        src = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT)
        src.execute("PRAGMA query_only = ON")  # источник только читается
        try:
            dst = sqlite3.connect(tmp, timeout=BUSY_TIMEOUT)
            try:
                # порциями, с паузой между ними: запись в основную базу не блокируется надолго
                deadline = time.monotonic() + MAX_COPY_SECONDS

                def progress(status, remaining, total):
                    if time.monotonic() > deadline:
                        raise TimeoutError()

                src.backup(dst, pages=PAGES_PER_STEP, progress=progress, sleep=STEP_SLEEP)
            finally:
                dst.close()
        finally:
            src.close()
        counts = inspect_database(tmp)  # проверка копии до того, как она получит итоговое имя
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
        tmp = None

        # постоянное имя для скачивания
        latest = os.path.join(backup_dir, "latest.db")
        latest_tmp = latest + ".tmp"
        shutil.copyfile(final, latest_tmp)
        os.chmod(latest_tmp, 0o600)
        os.replace(latest_tmp, latest)
        latest_tmp = None

        rotate_backups(backup_dir, keep, db_path)
        logger.info("Резервная копия создана: время=%s файл=%s размер=%d строки=%s результат=ok",
                    _iso(now), os.path.basename(final), os.path.getsize(final),
                    ",".join("%s=%d" % kv for kv in counts.items()))
        return final
    except Exception as exc:
        for leftover in (tmp, latest_tmp):
            if leftover:
                _remove_quietly(leftover)
        logger.error("Резервная копия не создана: %s", type(exc).__name__)
        return None


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def list_backups(backup_dir):
    """Имена файлов копий (players-ГГГГММДДTЧЧММССZ.db) от старых к новым."""
    try:
        names = [n for n in os.listdir(backup_dir) if NAME_RE.match(n)]
    except OSError:
        return []
    return sorted(names)  # время в имени: порядок имён совпадает с хронологическим


def backup_time(name):
    stamp = NAME_RE.match(name).group(1)
    return int(datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp())


def rotate_backups(backup_dir, keep, db_path):
    """Оставляет keep новейших копий. Удаляет только файлы по шаблону внутри backup_dir,
    не трогает символические ссылки, пути вне каталога и саму основную базу."""
    root = os.path.realpath(backup_dir)
    main_db = os.path.realpath(db_path)
    for name in list_backups(backup_dir)[:-keep] if keep > 0 else list_backups(backup_dir):
        path = os.path.join(backup_dir, name)
        real = os.path.realpath(path)
        if os.path.islink(path) or real == main_db or os.path.dirname(real) != root or not os.path.isfile(real):
            logger.warning("Ротация пропустила файл: %s", name)
            continue
        _remove_quietly(real)


def snapshot_due(backup_dir, interval_hours, now):
    """Пора ли делать копию: самой свежей нет или она старше интервала."""
    names = list_backups(backup_dir)
    if not names:
        return True
    return now - backup_time(names[-1]) >= interval_hours * 3600


# ---------- фоновая задача ----------

def run_maintenance_once(config, db_path, now, last_purge, events=None):
    """Один проход: копия (если включена и пора) и очистка старых данных (раз в сутки).
    Возвращает время последней очистки. Сбой копии добавляет "snapshot_failed" в events."""
    import db
    first_pass = last_purge is None  # причины пропуска пишем в первом проходе, чтобы не засорять лог каждый час
    if not config["enabled"]:
        if first_pass:
            logger.info("Резервная копия пропущена: отключена (BACKUP_ENABLED=0)")
    elif snapshot_due(config["dir"], config["interval_hours"], now):
        if create_snapshot(db_path, config["dir"], now=now, keep=config["keep"]) is None and events is not None:
            events.append("snapshot_failed")
    elif first_pass:
        newest = list_backups(config["dir"])[-1]
        logger.info("Резервная копия пропущена: свежая уже есть, файл=%s возраст_часов=%d интервал_часов=%d",
                    newest, max(0, now - backup_time(newest)) // 3600, config["interval_hours"])
    try:
        db.close_expired_mines(now=now, db_path=db_path)  # просроченные игры в мины закрываются раз в проход (час)
    except Exception as exc:
        logger.error("Закрытие просроченных игр в мины не выполнено: %s", type(exc).__name__)
    if last_purge is None or now - last_purge >= 86400:
        db.purge_old_data(now=now, db_path=db_path, rounds_days=config["rounds_days"],
                          member_days=config["member_days"])
        last_purge = now
    return last_purge


async def maintenance_loop(config, db_path, first_delay=FIRST_DELAY_SECONDS, tick=TICK_SECONDS,
                           clock=time.time, once=False, notifier=None, sender=None):
    """Фоновая задача сервиса. Любая ошибка логируется (только тип) и не роняет сервис;
    блокирующая работа идёт в пуле потоков. Корректно отменяется при остановке."""
    last_purge = None
    logger.info("Фоновая задача запущена: копии=%s интервал_часов=%d хранить=%d первая проверка через %d с",
                "вкл" if config["enabled"] else "выкл", config["interval_hours"], config["keep"], first_delay)
    await asyncio.sleep(first_delay)
    while True:
        now = int(clock())
        events = []
        try:
            extra = (events,) if notifier is not None else ()  # события нужны только уведомлениям
            last_purge = await asyncio.to_thread(run_maintenance_once, config, db_path, now, last_purge, *extra)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Ошибка фоновой задачи: %s", type(exc).__name__)
        if sender is not None:
            try:
                events.extend(await sender.run_scheduled(now))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Ошибка отправки зашифрованной копии: %s", type(exc).__name__)
        if notifier is not None:
            try:
                await notifier.run(events, now)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Ошибка уведомлений владельцу: %s", type(exc).__name__)
        if once:
            return
        await asyncio.sleep(tick)

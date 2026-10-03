import asyncio
import logging
import os
import re
import shutil
from urllib.parse import urlsplit
from datetime import datetime, timezone

import backup
import db

logger = logging.getLogger("depnaya.notify")

DAY = 86400
DAILY_CAP = 3                       # не больше стольких сообщений владельцу в сутки
STALE_SECONDS = 48 * 3600           # «нет успешной копии» после этого срока
REMINDER_SECONDS = 7 * DAY
MIN_FREE_FRACTION = 0.15            # предупреждение о месте: меньше 15% или меньше 50 МБ
MIN_FREE_BYTES = 50 * 1024 * 1024
SEND_TIMEOUT = 30
HOST_PLACEHOLDER = "<домен сервиса>"
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")

# время последней отправки каждого вида хранится в service_meta (unix, целое)
PERIODS = {"snapshot_failed": DAY, "stale": DAY, "space": DAY, "reminder": REMINDER_SECONDS}
META_KEYS = {kind: "notify_last_" + kind for kind in PERIODS}
SENT_LOG_KEY = "notify_sent_log"    # времена отправок за последние сутки (для потолка)
SINCE_KEY = "notify_monitor_since"  # с какого момента следим за копиями (чтобы не тревожить на свежем томе)

ORDER = ("snapshot_failed", "stale", "space", "reminder")  # при нехватке потолка важнее тревоги


def load_owner_id(env=None):
    """Числовой идентификатор владельца или None (не задан или не целое положительное число)."""
    env = os.environ if env is None else env
    raw = (env.get("OWNER_CHAT_ID") or "").strip()
    if re.fullmatch(r"[0-9]{1,15}", raw) and int(raw) > 0:
        return int(raw)
    return None


def public_host(env=None):
    """Хост из PUBLIC_URL (без схемы, порта и пути) или заглушка, если определить нельзя."""
    env = os.environ if env is None else env
    raw = (env.get("PUBLIC_URL") or "").strip()
    try:
        host = urlsplit(raw if "//" in raw else "//" + raw).hostname or ""
    except ValueError:
        host = ""
    return host if HOST_RE.match(host) else HOST_PLACEHOLDER


def warn_owner(owner_id):
    """Одно предупреждение при старте; значение переменной в лог не попадает."""
    if owner_id is None:
        logger.warning("OWNER_CHAT_ID не задан или неверен: уведомления владельцу отключены")


def _int_or_none(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _stamp(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class Notifier:
    """Уведомления владельцу о резервных копиях. Только агрегированные данные: даты, размеры, числа."""

    def __init__(self, owner_id, get_bot, db_path, config):
        self.owner_id = owner_id
        self.get_bot = get_bot      # вызываемое: бот или None (режим только API / бот ещё не запущен)
        self.db_path = db_path
        self.config = config

    # ---------- блокирующая часть (в потоке) ----------

    def _due(self, kind, now):
        last = _int_or_none(db.get_meta(META_KEYS[kind], self.db_path))
        return last is None or last > now or now - last >= PERIODS[kind]

    def _last_backup(self):
        names = backup.list_backups(self.config["dir"])
        return (names[-1], backup.backup_time(names[-1])) if names else (None, None)

    def _low_space(self):
        try:
            usage = shutil.disk_usage(os.path.dirname(os.path.abspath(self.db_path)))
        except OSError:
            return None
        if usage.total > 0 and (usage.free < MIN_FREE_FRACTION * usage.total or usage.free < MIN_FREE_BYTES):
            return usage
        return None

    def _collect(self, events, now):
        """Список (вид, текст) сообщений, которые пора отправить."""
        found = {}
        enabled = self.config["enabled"]
        name, made = self._last_backup() if enabled else (None, None)
        last_text = _stamp(made) if made is not None else "нет"

        if "snapshot_failed" in events and self._due("snapshot_failed", now):
            found["snapshot_failed"] = ("Тревога: резервная копия базы не создана. Подробности в логах "
                                        "сервиса. Последняя успешная копия: %s." % last_text)
        if enabled:
            since = _int_or_none(db.get_meta(SINCE_KEY, self.db_path))
            if since is None or since > now:
                since = now
                db.set_meta(SINCE_KEY, since, self.db_path)
            reference = made if made is not None else since
            if now - reference >= STALE_SECONDS and self._due("stale", now):
                found["stale"] = ("Тревога: нет успешной резервной копии больше 48 часов. Последняя: %s. "
                                  "Проверьте логи сервиса и место на томе." % last_text)
        if self._due("space", now):
            usage = self._low_space()
            if usage is not None:
                found["space"] = ("Мало места на томе: свободно %d МБ из %d МБ (%d%%)."
                                  % (usage.free // 2**20, usage.total // 2**20, usage.free * 100 // usage.total))
        if enabled and name is not None and self._due("reminder", now):
            path = os.path.join(self.config["dir"], name)
            try:
                players = backup.inspect_database(path).get("players", 0)
                size_kb = (os.path.getsize(path) + 1023) // 1024
            except Exception as exc:
                logger.error("Напоминание не подготовлено: %s", type(exc).__name__)
            else:
                host = public_host()
                found["reminder"] = (
                    "Напоминание о резервной копии. Последняя копия на сервере: %s, %d КБ, игроков: %d. "
                    "Скачайте её командами:\n"
                    "scp %s@ssh.railway.com:%s/latest.db ~/roulette-backups/latest-$(date +%%F).db\n"
                    "python3 bot/verify_backup.py ~/roulette-backups/latest-$(date +%%F).db\n"
                    "и удалите на компьютере копии старше 30 дней."
                    % (_stamp(made), size_kb, players, host, self.config["dir"].rstrip("/")))
        return [(kind, found[kind]) for kind in ORDER if kind in found]

    def _reserve(self, kind, now):
        """Проверяет потолок и записывает отправку до самой отправки (повтора при сбое не будет)."""
        sent = [t for t in map(_int_or_none, (db.get_meta(SENT_LOG_KEY, self.db_path) or "").split(","))
                if t is not None and now - DAY < t <= now]
        if len(sent) >= DAILY_CAP:
            return False
        sent.append(now)
        db.set_meta(SENT_LOG_KEY, ",".join(str(t) for t in sent), self.db_path)
        db.set_meta(META_KEYS[kind], now, self.db_path)
        return True

    # ---------- асинхронная часть ----------

    async def run(self, events, now):
        if self.owner_id is None:
            return
        bot = self.get_bot()
        if bot is None:  # режим только API или бот ещё не запущен
            return
        for kind, text in await asyncio.to_thread(self._collect, events, now):
            if not await asyncio.to_thread(self._reserve, kind, now):
                logger.warning("Уведомление владельцу пропущено: исчерпан суточный потолок (%s)", kind)
                continue
            try:
                # простой текст, без parse_mode
                await asyncio.wait_for(bot.send_message(chat_id=self.owner_id, text=text), SEND_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # только тип ошибки: в тексте могут быть адрес и токен; повторов в цикле нет
                logger.error("Уведомление владельцу не отправлено: %s", type(exc).__name__)

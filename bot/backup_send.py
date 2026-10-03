"""Еженедельная отправка зашифрованной копии базы владельцу в личный чат Telegram.

Сервер знает только открытый ключ (BACKUP_PUBLIC_KEY). Копия шифруется до отправки; незашифрованной
отправки нет ни в каком режиме. Время последней успешной отправки лежит в service_meta (backup_sent_at).
"""
import asyncio
import logging
import os
from datetime import datetime, timezone

import backup
import db
import notify

logger = logging.getLogger("depnaya.backup.send")

DAY = 86400
FRESH_SECONDS = 48 * 3600        # отправляем самую свежую копию не старше 48 часов, иначе делаем новую
DEFAULT_INTERVAL_DAYS = 7
DEFAULT_MAX_MB = 20
SEND_TIMEOUT = 120
SENT_KEY = "backup_sent_at"
FAILED_KEY = "backup_send_failed_at"   # время последней неудачной попытки (повтор не чаще раза в 24 часа)
PLAIN_SQLITE_HEADER = b"SQLite format 3"


def load_config(env=None, owner_id=None):
    """Настройки отправки. Включена, только если задан верный BACKUP_PUBLIC_KEY и есть владелец."""
    env = os.environ if env is None else env
    invalid = []
    problems = []
    key = None
    raw = (env.get("BACKUP_PUBLIC_KEY") or "").strip()
    if not raw:
        problems.append("BACKUP_PUBLIC_KEY не задан")
    else:
        try:
            import backup_crypto
            backup_crypto.parse_public_key(raw)
            key = raw
        except ValueError:
            problems.append("BACKUP_PUBLIC_KEY неверен")
    if key is not None:
        try:
            import nacl.public  # noqa: F401
        except ImportError:
            key = None
            problems.append("не установлена библиотека PyNaCl")
    if owner_id is None:
        problems.append("OWNER_CHAT_ID не задан или неверен")
    return {
        "enabled": key is not None and owner_id is not None,
        "public_key": key,
        "interval_days": backup._int_env(env, "BACKUP_SEND_INTERVAL_DAYS", DEFAULT_INTERVAL_DAYS, 1, invalid),
        "max_mb": backup._int_env(env, "BACKUP_SEND_MAX_MB", DEFAULT_MAX_MB, 1, invalid),
        "problems": problems,
        "invalid": invalid,
    }


def warn_config(send_config):
    """Одно предупреждение, если отправка отключена (без значений), и одно про неверные числа."""
    if not send_config["enabled"]:
        logger.warning("Отправка зашифрованных копий отключена: %s", "; ".join(send_config["problems"]))
    if send_config["invalid"]:
        logger.warning("Неверные значения переменных: %s. Использованы значения по умолчанию",
                       ", ".join(send_config["invalid"]))


def _int_meta(key, db_path):
    try:
        return int(db.get_meta(key, db_path))
    except (TypeError, ValueError):
        return None


def _stamp(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class EncryptedSender:
    def __init__(self, owner_id, get_bot, db_path, backup_config, send_config):
        self.owner_id = owner_id
        self.get_bot = get_bot          # вызываемое: бот или None (режим только API / бот ещё не запущен)
        self.db_path = db_path
        self.backup_config = backup_config
        self.send_config = send_config
        self._busy = False

    @property
    def enabled(self):
        return bool(self.send_config["enabled"])

    # ---------- расписание ----------

    def _due(self, now):
        sent = _int_meta(SENT_KEY, self.db_path)
        failed = _int_meta(FAILED_KEY, self.db_path)
        if failed is not None and failed <= now and now - failed < DAY:
            return False
        return sent is None or sent > now or now - sent >= self.send_config["interval_days"] * DAY

    def _record(self, result, now):
        if result == "ok":
            db.set_meta(SENT_KEY, now, self.db_path)
        else:
            db.set_meta(FAILED_KEY, now, self.db_path)

    # ---------- подготовка файла (блокирующая часть, в потоке) ----------

    def _fresh_copy(self, now):
        directory = self.backup_config["dir"]
        names = backup.list_backups(directory)
        if names and 0 <= now - backup.backup_time(names[-1]) <= FRESH_SECONDS:
            path = os.path.join(directory, names[-1])
            try:
                backup.inspect_database(path)   # копия должна быть цела
                return path
            except Exception as exc:
                logger.warning("Свежая копия не прошла проверку (%s), делаю новую", type(exc).__name__)
        return backup.create_snapshot(self.db_path, directory, now=now, keep=self.backup_config["keep"])

    def _caption(self, made, size_kb, players):
        return (
            "Резервная копия базы (зашифрована). Дата: %s, размер: %d КБ, игроков: %d.\n"
            "Расшифровка: python3 bot/decrypt_backup.py ФАЙЛ ПРИВАТНЫЙ_КЛЮЧ ВЫХОД.db\n"
            "Запасной способ скачивания (файл на сервере не зашифрован): "
            "scp %s@ssh.railway.com:%s/latest.db ~/roulette-backups/latest-$(date +%%F).db\n"
            "Удалите это сообщение не позднее чем через 30 дней."
            % (_stamp(made), size_kb, players, notify.public_host(), self.backup_config["dir"].rstrip("/")))

    def _prepare(self, now):
        """("ok", байты, имя файла, подпись) | ("too_big",) | ("failed",). Только зашифрованные байты."""
        try:
            path = self._fresh_copy(now)
            if path is None:
                return ("failed",)
            size = os.path.getsize(path)
            if size > self.send_config["max_mb"] * 1024 * 1024:
                logger.warning("Копия не отправлена: размер %d КБ больше лимита BACKUP_SEND_MAX_MB", size // 1024)
                return ("too_big",)
            players = backup.inspect_database(path).get("players", 0)
            with open(path, "rb") as handle:
                data = handle.read()
            import backup_crypto
            payload = backup_crypto.encrypt_bytes(data, self.send_config["public_key"])
            if not payload.startswith(backup_crypto.MAGIC) or PLAIN_SQLITE_HEADER in payload[:64]:
                raise RuntimeError("encryption check failed")
            name = os.path.basename(path)
            made = backup.backup_time(name)
            return ("ok", payload, name + ".enc", self._caption(made, (size + 1023) // 1024, players))
        except Exception as exc:
            logger.error("Подготовка зашифрованной копии не удалась: %s", type(exc).__name__)
            return ("failed",)

    # ---------- отправка ----------

    async def _deliver(self, bot, now):
        """Готовит и отправляет. Результат: "ok", "failed", "too_big" или "busy"."""
        if self._busy:
            return "busy"
        self._busy = True
        try:
            result = await asyncio.to_thread(self._prepare, now)
            if result[0] != "ok":
                return result[0]
            _, payload, filename, caption = result
            try:
                # простой текст подписи, без parse_mode
                await asyncio.wait_for(
                    bot.send_document(chat_id=self.owner_id, document=payload, filename=filename, caption=caption),
                    SEND_TIMEOUT)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Зашифрованная копия не отправлена: %s", type(exc).__name__)
                return "failed"
            logger.info("Зашифрованная копия отправлена: файл=%s размер=%d КБ", filename, (len(payload) + 1023) // 1024)
            return "ok"
        finally:
            self._busy = False

    async def run_scheduled(self, now):
        """Один проход по расписанию. Возвращает события для Notifier (send_failed / send_too_big)."""
        if not self.enabled:
            return []
        bot = self.get_bot()
        if bot is None:  # режим только API или бот ещё не запущен
            return []
        if not await asyncio.to_thread(self._due, now):
            return []
        result = await self._deliver(bot, now)
        if result == "busy":
            return []
        await asyncio.to_thread(self._record, result, now)
        if result == "ok":
            return []
        return ["send_too_big" if result == "too_big" else "send_failed"]

    async def send_now(self, bot, now):
        """Отправка вне расписания (/backupnow): backup_sent_at и метку сбоя не меняет."""
        if not self.enabled:
            return "disabled"
        return await self._deliver(bot, now)

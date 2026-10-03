import hashlib
import hmac
import os

# Защита от повторной регистрации ради стартовых фишек. После /deletemydata на это число
# дней стартовые фишки при новой регистрации не выдаются (баланс 0, начисление по часам идёт
# как у всех). Это единственное место, где задан срок: на него опираются база, бот и текст
# политики конфиденциальности (там написано 30 дней, при изменении обновите и её).
REGISTRATION_COOLDOWN_DAYS = 30
COOLDOWN_SECONDS = REGISTRATION_COOLDOWN_DAYS * 86400


class TombstoneUnavailable(Exception):
    """Не задан TOMBSTONE_SECRET: удалять данные нельзя, иначе защиту потом не включить."""


def tombstone_secret():
    """Секретный ключ HMAC из окружения (читается при каждом обращении) или None."""
    value = (os.environ.get("TOMBSTONE_SECRET") or "").strip()
    return value.encode("utf-8") if value else None


def key_hash(telegram_id, secret):
    """HMAC-SHA256(ключ=секрет, сообщение=str(telegram_id)) в виде hex-строки.
    Сам telegram_id в базе не хранится, обратить хэш без секрета нельзя."""
    return hmac.new(secret, str(telegram_id).encode("utf-8"), hashlib.sha256).hexdigest()

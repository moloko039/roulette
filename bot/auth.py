import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

# Проверка данных Mini App (Telegram.WebApp.initData).
# Официальная документация, раздел "Validating data received via the Mini App":
# https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
#
# Алгоритм (проверка по токену бота):
# 1. initData — строка запроса вида key=value&key=value. Разбираем её и
#    URL-декодируем значения.
# 2. Достаём поле hash. Остальные поля, ВКЛЮЧАЯ signature, сортируем по имени
#    и склеиваем строкой key=value через перевод строки (\n) — это data-check-string.
#    (Поле signature исключается только в другом способе, для третьих лиц,
#    где проверяется подпись Ed25519; здесь он не используется.)
# 3. secret_key = HMAC_SHA256(ключ="WebAppData", сообщение=токен бота).
# 4. Ожидаемый хэш = hex(HMAC_SHA256(ключ=secret_key, сообщение=data-check-string)).
# 5. Сравниваем с присланным hash через hmac.compare_digest.
# 6. Проверяем auth_date: документация советует отвергать устаревшие данные.

# initData создаётся один раз при открытии мини-аппа и не обновляется, пока он
# открыт. Если поставить срок слишком маленьким, живой игрок, который просто
# не закрывал мини-апп, начнёт получать отказ. Поэтому не меньше суток.
MAX_AGE = 24 * 3600
# допуск на расхождение часов: данные «из будущего» дальше этого отвергаем
FUTURE_SKEW = 60


class InvalidInitData(Exception):
    """Любая причина отказа; подробности наружу не отдаём."""


def validate_init_data(init_data, bot_token, now=None, max_age=MAX_AGE):
    """Возвращает telegram id пользователя из проверенных данных или бросает InvalidInitData."""
    try:
        return _validate(init_data, bot_token, now, max_age)
    except InvalidInitData:
        raise
    except Exception:
        raise InvalidInitData()


def _validate(init_data, bot_token, now, max_age):
    if not init_data or not bot_token:
        raise InvalidInitData()
    if now is None:
        now = int(time.time())

    pairs = parse_qsl(init_data, keep_blank_values=True)
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise InvalidInitData()  # повторяющиеся ключи

    received_hash = fields.pop("hash", None)
    if not received_hash:
        raise InvalidInitData()

    data_check_string = "\n".join("{}={}".format(k, fields[k]) for k in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), received_hash.encode()):
        raise InvalidInitData()

    auth_date = int(fields["auth_date"])
    if now - auth_date > max_age or auth_date - now > FUTURE_SKEW:
        raise InvalidInitData()

    user = json.loads(fields["user"])
    user_id = user["id"]
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        raise InvalidInitData()
    return user_id

"""Переводы фишек между участниками одной беседы: правила и метки участников. Чистые функции, без базы.

Все значения можно менять здесь, в одном месте. Клиент лимитов не дублирует: они приходят в GET /api/me (transfer_limits).
Фишки виртуальные: перевод это подарок между участниками игры, не покупка и не обмен.
"""
import hashlib
import hmac
import os
import re
import secrets

TRANSFER_MIN = 100
TRANSFER_MAX = 50_000
SEND_DAILY_LIMIT = 500_000        # сумма, списанная у отправителя (вместе с комиссией) за скользящие 24 часа
RECEIVE_DAILY_LIMIT = 500_000     # сумма, полученная получателем «чистыми» (после комиссии) за скользящие 24 часа
COOLDOWN_SECONDS = 10             # между двумя переводами одного отправителя
SENDER_MIN_LEVEL = 3
MIN_ACCOUNT_AGE_HOURS = 1         # с момента регистрации игрока
MIN_STAKED_TO_SEND = 20_000       # накопленные ставки отправителя (players.total_staked): защита от пустых аккаунтов
# Владелец (OWNER_CHAT_ID): без суточных лимитов (отправки и получения) и без условия по ставкам; мин., макс. сумма, кулдаун,
# уровень и возраст аккаунта действуют и для него. Комиссия на счёт владельца идёт вне лимитов и в лимиты других не входит.
FEE_PERCENT = 5                   # 0 отключает комиссию
DAY_SECONDS = 86400
HISTORY_LIMIT = 20
MEMBERS_PAGE = 30                 # участников на страницу списка «Кому перевести»
MEMBERS_QUERY_MAX = 32            # длина поискового запроса
MEMBERS_OFFSET_MAX = 100_000

MEMBER_REF_RE = re.compile(r"[0-9a-f]{32}")


class TransferError(Exception):
    """Отказ в переводе: code идёт в ответ API (409), extra добавляется в тело (например seconds для cooldown)."""

    def __init__(self, code, **extra):
        super().__init__(code)
        self.code = code
        self.extra = extra


class RequestConflict(TransferError):
    def __init__(self):
        super().__init__("request_conflict")


def fee_for(amount, fee_percent=None):
    """Комиссия с суммы: max(1, amount * FEE_PERCENT // 100); 0, если комиссия отключена."""
    percent = FEE_PERCENT if fee_percent is None else fee_percent
    if percent <= 0:
        return 0
    return max(1, amount * percent // 100)


def valid_amount(amount):
    return type(amount) is int and TRANSFER_MIN <= amount <= TRANSFER_MAX


def valid_member_ref(ref):
    return type(ref) is str and MEMBER_REF_RE.fullmatch(ref) is not None


# Ключ меток: MEMBER_REF_SECRET; если его нет, ключ выводится из TOMBSTONE_SECRET (с отдельным назначением, метки не
# совпадают с хэшами надгробий); если нет и его, ключ случайный на время работы процесса: метки живут до перезапуска
# (после него клиент получает 400 invalid_request и обновляет рейтинг), сервис при этом не падает.
_EPHEMERAL_KEY = secrets.token_bytes(32)


def _ref_key():
    value = (os.environ.get("MEMBER_REF_SECRET") or "").strip()
    if value:
        return value.encode("utf-8")
    tomb = (os.environ.get("TOMBSTONE_SECRET") or "").strip()
    if tomb:
        return hmac.new(tomb.encode("utf-8"), b"member-ref-v1", hashlib.sha256).digest()
    return _EPHEMERAL_KEY


def member_ref(chat_instance, telegram_id):
    """Непрозрачная метка участника беседы: HMAC-SHA256 от (беседа, игрок) секретом сервера, 32 hex-символа.
    Стабильна для пары (беседа, игрок), не раскрывает идентификатор и бесполезна в другой беседе."""
    message = ("%s\0%d" % (chat_instance, telegram_id)).encode("utf-8")
    return hmac.new(_ref_key(), message, hashlib.sha256).hexdigest()[:32]

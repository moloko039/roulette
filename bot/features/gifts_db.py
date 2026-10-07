"""Подарки косметикой (ECONOMY_ADDITIONS.md, п. 2): предмет за кристаллы покупается на имя участника той же беседы.

Получатель задаётся непрозрачной меткой (HMAC от беседы и игрока, Telegram id клиент не видит). Одна транзакция BEGIN IMMEDIATE: повтор по (отправитель, request_id), проверки до списания
(беседа, получатель, предмет, «уже есть», суточный лимит), wallet.gems_debit (причина gift_purchase), запись gifts, выдача предмета получателю (источник gift). Фишки, кристаллы и пакеты
дарить нельзя, подаренное передать нельзя (нового пути передачи нет). Опыт, ставки и фишки не меняются."""
import hashlib
import hmac
import os
import secrets as _secrets
import time

import cosmetics
import economy_config
import wallet
from roulette import RequestConflict

from core.db_conn import _connect
from core.members import MAX_CHAT_MEMBERS
from features.cosmetics_db import _grant_in, _owns

DAY_SECONDS = 86400
_EPHEMERAL_KEY = _secrets.token_bytes(32)


class GiftError(Exception):
    code = "gift_error"


class NoChat(GiftError):
    code = "no_chat"


class NotInChat(GiftError):
    code = "not_in_chat"


class UnknownRecipient(GiftError):
    code = "unknown_recipient"


class SelfGift(GiftError):
    code = "self_gift"


class GiftDailyLimit(GiftError):
    code = "daily_limit"


def _ref_key():
    for name in ("TOMBSTONE_SECRET", "MEMBER_REF_SECRET"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return hmac.new(value.encode("utf-8"), b"gift-ref-v1", hashlib.sha256).digest()
    return _EPHEMERAL_KEY


def member_ref(chat_instance, telegram_id):
    """Непрозрачная метка участника беседы (32 hex): без Telegram id и названия беседы."""
    return hmac.new(_ref_key(), ("%s|%d" % (chat_instance, telegram_id)).encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _members(conn, chat_instance):
    return conn.execute("SELECT telegram_id, first_name FROM chat_members WHERE chat_instance = ? ORDER BY last_seen DESC, telegram_id LIMIT ?", (chat_instance, MAX_CHAT_MEMBERS)).fetchall()


def _require_member(conn, chat_instance, telegram_id):
    if chat_instance is None:
        raise NoChat()
    if conn.execute("SELECT 1 FROM chat_members WHERE chat_instance = ? AND telegram_id = ?", (chat_instance, telegram_id)).fetchone() is None:
        raise NotInChat()


def _daily_left(conn, telegram_id, now):
    used = conn.execute("SELECT COUNT(*) FROM gifts WHERE from_user = ? AND created_at > ?", (telegram_id, now - DAY_SECONDS)).fetchone()[0]
    return max(0, economy_config.GIFT_DAILY_LIMIT - used)


def gift_recipients(telegram_id, chat_instance, now=None, db_path=None):
    """Участники беседы, которым можно подарить (только чтение): [{name, ref}] без самого игрока, только те, у кого есть профиль; плюс предметы за кристаллы и сколько подарков осталось сегодня.
    Бросает NoChat, NotInChat."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        _require_member(conn, chat_instance, telegram_id)
        items = []
        for row in _members(conn, chat_instance):
            if row["telegram_id"] == telegram_id:
                continue
            if conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (row["telegram_id"],)).fetchone() is None:
                continue
            items.append({"name": row["first_name"], "ref": member_ref(chat_instance, row["telegram_id"])})
            if len(items) >= economy_config.GIFT_RECIPIENTS_PAGE:
                break
        return {"recipients": items, "daily_left": _daily_left(conn, telegram_id, now), "gems": wallet.gems_balance(conn, telegram_id)}
    finally:
        conn.close()


def send_gift(sender, chat_instance, request_id, ref, item_code, sender_name, now=None, db_path=None):
    """Подарок. Возвращает (ответ API, уведомление или None для повтора). Ошибки: NoChat, NotInChat, UnknownRecipient, SelfGift, cosmetics.UnknownItem, ItemUnavailable, NotForGems,
    AlreadyOwned, GiftDailyLimit, wallet.InsufficientGems, RequestConflict (тот же request_id с другим получателем или предметом)."""
    if type(ref) is not str or type(item_code) is not str:
        raise ValueError("invalid")
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _require_member(conn, chat_instance, sender)
            target = None
            for row in _members(conn, chat_instance):
                if hmac.compare_digest(member_ref(chat_instance, row["telegram_id"]), ref):
                    target = row
                    break
            if target is None or conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (target["telegram_id"],)).fetchone() is None:
                raise UnknownRecipient()
            old = conn.execute("SELECT to_user, item_code, gems FROM gifts WHERE from_user = ? AND request_id = ?", (sender, request_id)).fetchone()
            if old is not None:
                if old["to_user"] != target["telegram_id"] or old["item_code"] != item_code:
                    raise RequestConflict()
                result = {"item_code": item_code, "price": {"currency": "gems", "amount": old["gems"]}, "recipient": target["first_name"], "gems": wallet.gems_balance(conn, sender),
                          "daily_left": _daily_left(conn, sender, now), "replayed": True}
                conn.execute("COMMIT")
                return result, None
            if target["telegram_id"] == sender:
                raise SelfGift()
            it = cosmetics.item(item_code)
            if it is None:
                raise cosmetics.UnknownItem()
            if not it["available"] or it["starter"] or it["price"] is None:
                raise cosmetics.ItemUnavailable()
            if it["price"]["currency"] != cosmetics.GEMS:
                raise cosmetics.NotForGems()
            if _owns(conn, target["telegram_id"], item_code):
                raise cosmetics.AlreadyOwned()                           # до списания
            if _daily_left(conn, sender, now) <= 0:
                raise GiftDailyLimit()
            price = it["price"]["amount"]
            wallet.gems_debit(conn, sender, price, "gift_purchase", "gift:" + request_id, now)
            cur = conn.execute("INSERT INTO gifts (from_user, to_user, from_name, item_code, gems, request_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (sender, target["telegram_id"], (sender_name or "")[:64], item_code, price, request_id, now))
            _grant_in(conn, target["telegram_id"], item_code, "gift", "gift-%d" % cur.lastrowid, now)
            result = {"item_code": item_code, "price": {"currency": "gems", "amount": price}, "recipient": target["first_name"], "gems": wallet.gems_balance(conn, sender),
                      "daily_left": _daily_left(conn, sender, now), "replayed": False}
            conn.execute("COMMIT")
            return result, {"to_user": target["telegram_id"], "from_name": (sender_name or "")[:64], "item_name": it["name"]}
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def gift_sender_names(conn, telegram_id):
    """{код предмета: имя отправителя} для подаренных предметов игрока (для «подарок от …» в /api/cosmetics/mine)."""
    out = {}
    for r in conn.execute("SELECT item_code, from_name FROM gifts WHERE to_user = ? ORDER BY id", (telegram_id,)):
        out[r["item_code"]] = r["from_name"]
    return out

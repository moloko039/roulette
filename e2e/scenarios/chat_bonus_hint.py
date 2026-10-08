"""Подсказка бонусов беседы вне группового чата (настоящий сервер):
открыть рейтинг вне беседы — текст подсказки содержит фразу про личный чат;
на ферме строка «Бонусы беседы работают только в групповом чате»."""
import hashlib
import hmac
import json
import time
import urllib.parse

from harness import check

NAME = "chat_bonus_hint"
USERS = {"me": {"rate": 0}}
CLOCK_MOD = 5
SP = "(s) => s.replace(/\\s/g, ' ')"


def sign_private_init_data(token, user_id, name, auth_date):
    """Подписанный initData для личного чата (chat_type: sender, без chat_instance)."""
    fields = {
        "auth_date": str(auth_date),
        "query_id": "AAH-e2e",
        "signature": "e2e-signature",
        "chat_type": "sender",
        "user": json.dumps({"id": user_id, "first_name": name}, ensure_ascii=False, separators=(",", ":")),
    }
    check_str = "\n".join("%s=%s" % (k, fields[k]) for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_str.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


async def run(w):
    p = w.page
    u = w.users["me"]

    # Авторизуемся как в личном чате (без группы и chat_instance)
    priv_init = sign_private_init_data(w.server.token, u.id, u.name, int(time.time()))
    await p.ev("localStorage.setItem('__init', %s)" % json.dumps(priv_init))
    await w.reload()

    # Открыть вкладку Рейтинг
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.getElementById('rating-msg').hidden && document.getElementById('rating-msg').textContent.length > 0", 10, "сообщение рейтинга")
    rating_msg = await p.ev("document.getElementById('rating-msg').textContent")
    check("текст подсказки в рейтинге содержит фразу про личный чат",
          "Бусты беседы и бонус беседы к ферме работают только когда игра открыта из группового чата: из личного чата они не действуют." in rating_msg,
          True)

    # Открыть вкладку Ферма
    await p.tap(".tab[data-tab=farm]")
    await p.wait("!document.getElementById('farm-body').hidden", 10, "экран фермы")
    await p.wait("!document.getElementById('farm-chat-bonus').hidden", 10, "блок бонуса беседы на ферме")

    farm_chat_note = await p.ev("document.getElementById('farm-chat-note').textContent")
    check("на ферме строка «Бонусы беседы работают только в групповом чате»",
          farm_chat_note,
          "Бонусы беседы работают только в групповом чате")

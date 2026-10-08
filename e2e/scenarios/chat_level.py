"""Уровень беседы в рейтинге: строка «Беседа: уровень N», подпись «A из B очков», полоса прогресса внутри текущего уровня (role=progressbar), строка скрыта в личном чате."""
import hashlib
import hmac
import json
import time
import urllib.parse

from harness import check

NAME = "chat_level"
# staked: me 5000 + bob 4000 + carol 2000 = 11 000 очков беседы (уровень 2: с 10 000 до 25 000)
USERS = {"me": {"rate": 0, "balance": 1000, "staked": 5000}, "bob": {"balance": 5000, "rate": 0, "staked": 4000}, "carol": {"balance": 4000, "rate": 0, "staked": 2000}}

READ = """(() => { const f = document.getElementById('chat-level-bar-fill'), b = document.getElementById('chat-level-bar');
  return { hidden: document.getElementById('chat-level-wrap').hidden, title: document.getElementById('chat-level-title').textContent,
           score: document.getElementById('chat-level-score').textContent.replace(/\\s/g, ' '), now: b.getAttribute('aria-valuenow'), width: f.style.width }; })()"""


def sign_private_init_data(token, user_id, name, auth_date):
    """Подписанный initData личного чата (chat_type: sender, без chat_instance), как в сценарии chat_bonus_hint."""
    fields = {"auth_date": str(auth_date), "query_id": "AAH-e2e", "signature": "e2e-signature", "chat_type": "sender",
              "user": json.dumps({"id": user_id, "first_name": name}, ensure_ascii=False, separators=(",", ":"))}
    check_string = "\n".join("%s=%s" % (k, fields[k]) for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


async def open_rating(w):
    p = w.page
    await p.tap(".tab[data-tab=rating]")
    await p.wait("document.querySelectorAll('#rating-list li').length >= 3", 15, "рейтинг беседы загрузился")
    await p.wait("!document.getElementById('chat-level-wrap').hidden", 5, "строка уровня видна")
    return await p.ev(READ)


async def run(w):
    p = w.page

    # ---- уровень 2: 11 000 очков, уровень идёт с 10 000 до 25 000, прогресс внутри уровня (11 000 - 10 000) / 15 000 = 6,7 %
    got = await open_rating(w)
    check("уровень 2: строка видна, заголовок, подпись очков", (got["hidden"], got["title"], got["score"]), (False, "Беседа: уровень 2", "11 000 из 25 000 очков"))
    check("уровень 2: прогресс внутри уровня, а не доля от порога (aria-valuenow и ширина)", (got["now"], got["width"]), ("6", "6.7%"))
    check("у полосы есть роль и подпись", await p.ev("[document.getElementById('chat-level-bar').getAttribute('role'), document.getElementById('chat-level-bar').getAttribute('aria-label')]"),
          ["progressbar", "Прогресс уровня беседы"])

    # ---- уровень 1: 8 000 очков, уровень идёт с 0 до 10 000, прогресс 80 %
    w.sql("UPDATE players SET total_staked = 2000 WHERE telegram_id = ?", (w.users["bob"].id,))
    w.sql("UPDATE players SET total_staked = 1000 WHERE telegram_id = ?", (w.users["carol"].id,))
    await w.reload()
    got = await open_rating(w)
    check("уровень 1: заголовок и подпись очков", (got["title"], got["score"]), ("Беседа: уровень 1", "8 000 из 10 000 очков"))
    check("уровень 1: прогресс 80 %", (got["now"], got["width"]), ("80", "80%"))

    # ---- личный чат: строки уровня нет, остальное работает
    u = w.users["me"]
    priv = sign_private_init_data(w.server.token, u.id, u.name, int(time.time()))
    await p.ev("localStorage.setItem('__init', %s)" % json.dumps(priv))
    await w.reload()
    await p.tap(".tab[data-tab=rating]")
    await p.wait("!document.getElementById('rating-msg').hidden && document.getElementById('rating-msg').textContent.length > 0", 15, "подсказка рейтинга в личном чате")
    check("в личном чате строка уровня скрыта", await p.ev("document.getElementById('chat-level-wrap').hidden"), True)

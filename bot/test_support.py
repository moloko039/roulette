"""Поддержка (/support): обращение уходит владельцу с заголовком, ответ владельца доходит игроку, чужие ответы и лишнее молчат, лимиты работают."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import os
from types import SimpleNamespace

from stubs import FakeUpdate
import tg.support as sup

OWNER, PLAYER, OTHER = 424242421, 424242422, 424242423
os.environ["OWNER_CHAT_ID"] = str(OWNER)


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


class Bot:
    def __init__(self):
        self.sent = []
        self.fail = False

    async def send_message(self, **kw):
        if self.fail:
            raise RuntimeError("x")
        self.sent.append(kw)


def ctx(bot, args=(), data=None):
    return SimpleNamespace(bot=bot, args=list(args), user_data=data if data is not None else {})


def msg_update(user_id, text, reply_text=None, kind="private"):
    u = FakeUpdate(kind, user_id=user_id)
    u.effective_message.text = text
    u.effective_message.reply_to_message = SimpleNamespace(text=reply_text) if reply_text is not None else None
    return u


def run(coro):
    return asyncio.run(coro)


# /support без текста: просит написать, дальше ждёт сообщение
b, data = Bot(), {}
u = FakeUpdate("private", user_id=PLAYER)
run(sup.support(u, ctx(b, data=data)))
check("просит текст", data.get("support"), True)
u = msg_update(PLAYER, "не пришёл предмет")
run(sup.support_message(u, ctx(b, data=data)))
check("владельцу ушло", b.sent, [{"chat_id": OWNER, "text": "✉️ Поддержка #%d\nне пришёл предмет" % PLAYER}])
check("ответ игроку", u.replies[0]["text"], "Передано. Ответ придёт в этот чат")
check("флаг снят", "support" in data, False)

# обычный текст без /support молчит
b = Bot()
u = msg_update(OTHER, "привет")
run(sup.support_message(u, ctx(b)))
check("без /support молчание", (b.sent, u.replies), ([], []))

# /support с текстом сразу, лимит 30 секунд
sup.support_limiter.last.clear()
b = Bot()
u = FakeUpdate("private", user_id=OTHER)
run(sup.support(u, ctx(b, ["вопрос", "по", "оплате"])))
check("с текстом сразу", b.sent[0]["text"], "✉️ Поддержка #%d\nвопрос по оплате" % OTHER)
u = FakeUpdate("private", user_id=OTHER)
run(sup.support(u, ctx(b, ["ещё"])))
check("лимит", (len(b.sent), u.replies[0]["text"]), (1, "Слишком часто, повторите через полминуты"))

# слишком длинно
sup.support_limiter.last.clear()
b = Bot()
u = FakeUpdate("private", user_id=PLAYER)
run(sup.support(u, ctx(b, ["я" * 1001])))
check("длинно не уходит", b.sent, [])

# группа: только личка
b = Bot()
u = FakeUpdate("group", user_id=PLAYER, chat_id=-5)
run(sup.support(u, ctx(b, ["текст"])))
check("группа", (b.sent, u.replies[0]["text"]), ([], "Эта команда работает в личной переписке с ботом"))

# ответ владельца
b = Bot()
header = "✉️ Поддержка #%d\nне пришёл предмет" % PLAYER
u = msg_update(OWNER, "выдал, проверьте", reply_text=header)
run(sup.support_message(u, ctx(b)))
check("ответ дошёл", b.sent, [{"chat_id": PLAYER, "text": "Ответ поддержки:\nвыдал, проверьте"}])
check("владелец видит статус", u.replies[0]["text"], "Отправлено")
# ответ не на заголовок и ответ не от владельца
b = Bot()
run(sup.support_message(msg_update(OWNER, "текст", reply_text="что-то другое"), ctx(b)))
run(sup.support_message(msg_update(OTHER, "я владелец", reply_text=header), ctx(b)))
check("чужие ответы молчат", b.sent, [])
# игрок заблокировал бота
b = Bot()
b.fail = True
u = msg_update(OWNER, "ответ", reply_text=header)
run(sup.support_message(u, ctx(b)))
check("недоставлено", u.replies[0]["text"].startswith("Не доставлено"), True)

# владелец не задан
os.environ.pop("OWNER_CHAT_ID")
u = FakeUpdate("private", user_id=PLAYER)
run(sup.support(u, ctx(Bot(), ["текст"])))
check("без владельца", u.replies[0]["text"], "Эта функция пока недоступна")
print("test_support OK")

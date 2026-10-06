import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
from auth import MAX_AGE, InvalidInitData, validate_init_data, validate_init_data_full
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_700_000_100


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def rejected(init_data, token=TOKEN, now=NOW):
    try:
        validate_init_data(init_data, token, now=now)
    except InvalidInitData:
        return True
    return False


good = make_init_data(TOKEN, user_id=777)

# корректные данные принимаются, id достаётся верно
check("корректные данные", validate_init_data(good, TOKEN, now=NOW), 777)

# изменён любой символ (в данных или в хэше): отклонено
for i, ch in enumerate(good):
    if ch == "~":
        continue
    broken = good[:i] + "~" + good[i + 1:]
    assert rejected(broken), f"символ {i} ({ch!r}) заменён, но данные приняты"

# изменена одна шестнадцатеричная цифра хэша
i = good.index("hash=") + 5
digit = "0" if good[i] != "0" else "1"
assert rejected(good[:i] + digit + good[i + 1:]), "изменённый хэш принят"

# подпись от другого токена
assert rejected(make_init_data("999:OTHER-TOKEN")), "чужой токен принят"
assert rejected(good, token="999:OTHER-TOKEN"), "проверка с чужим токеном прошла"

# auth_date: ровно MAX_AGE допустимо, старше — нет, далеко в будущем — нет
check("ровно MAX_AGE", validate_init_data(make_init_data(TOKEN, auth_date=NOW - MAX_AGE), TOKEN, now=NOW), 12345)
assert rejected(make_init_data(TOKEN, auth_date=NOW - MAX_AGE - 1)), "старые данные приняты"
assert rejected(make_init_data(TOKEN, auth_date=NOW + 3600)), "данные из будущего приняты"

# нет данных, пусто, мусор
for junk in [None, "", "   ", "garbage", "hash=abc", "a=b&a=c", "%%%%", "user={}&hash=0"]:
    assert rejected(junk), f"мусор {junk!r} принят"

# данные без поля user (подпись корректна, но user нет)
assert rejected(make_init_data(TOKEN, with_user=False)), "данные без user приняты"

# user без id и с нечисловым id
assert rejected(make_init_data(TOKEN, with_user=False, extra={"user": '{"first_name":"x"}'}))
assert rejected(make_init_data(TOKEN, with_user=False, extra={"user": '{"id":"5"}'}))
assert rejected(make_init_data(TOKEN, with_user=False, extra={"user": "not json"}))


# ---------- беседа и имя (validate_init_data_full) ----------
def full(data, token=TOKEN):
    return validate_init_data_full(data, token, now=NOW)


d = full(make_init_data(TOKEN, user_id=5, chat_type="supergroup", chat_instance="-1001234567890_abc", first_name="Аня"))
check("поля из подписанных данных", d, {"user_id": 5, "chat_type": "supergroup",
                                        "chat_instance": "-1001234567890_abc", "first_name": "Аня"})
for t in ["sender", "private", "group", "supergroup", "channel"]:
    check("chat_type " + t, full(make_init_data(TOKEN, chat_type=t, chat_instance="abc"))["chat_type"], t)

# подмена chat_instance или chat_type после подписи: отказ
signed = make_init_data(TOKEN, chat_type="group", chat_instance="room-1")
for old, new in [("chat_instance=room-1", "chat_instance=room-2"), ("chat_type=group", "chat_type=private")]:
    assert old in signed
    try:
        full(signed.replace(old, new))
        raise AssertionError("подмена принята: " + new)
    except InvalidInitData:
        pass

# отсутствие полей даёт None
d = full(make_init_data(TOKEN, user_id=7))
check("без чата", (d["chat_type"], d["chat_instance"]), (None, None))
check("имя по умолчанию из testutil", d["first_name"], "Тест")
d = full(make_init_data(TOKEN, user_id=7, chat_type="group"))
check("тип без instance", (d["chat_type"], d["chat_instance"]), ("group", None))

# неверный chat_instance игнорируется (подпись при этом корректна)
for bad in ["", "a" * 65, "has space", "a b", "кириллица", "dot.dot", "slash/slash", "new\nline", "%", "a;b"]:
    d = full(make_init_data(TOKEN, chat_type="group", chat_instance=bad, extra={"chat_instance": bad} if bad == "" else None))
    check("chat_instance %r игнорируется" % bad, d["chat_instance"], None)
check("граница 64", full(make_init_data(TOKEN, chat_instance="a" * 64))["chat_instance"], "a" * 64)
check("граница 1", full(make_init_data(TOKEN, chat_instance="-"))["chat_instance"], "-")

# неизвестный chat_type и неверное имя
check("неизвестный chat_type", full(make_init_data(TOKEN, chat_type="bot", chat_instance="abc"))["chat_type"], None)
d = full(make_init_data(TOKEN, with_user=False, extra={"user": '{"id":5,"first_name":123}'}))
check("имя не строка", d["first_name"], None)
d = full(make_init_data(TOKEN, with_user=False, extra={"user": '{"id":5}'}))
check("нет имени", d["first_name"], None)

# неверная подпись по-прежнему отказ
try:
    full(make_init_data("999:OTHER", chat_type="group", chat_instance="abc"))
    raise AssertionError("чужая подпись принята")
except InvalidInitData:
    pass

print("Все проверки прошли")

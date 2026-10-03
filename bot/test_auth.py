from auth import MAX_AGE, InvalidInitData, validate_init_data
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

print("Все проверки прошли")

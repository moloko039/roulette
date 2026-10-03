import os
import tempfile

from economy import HOUR
from db import init_db, get_player

T = 1_700_000_000  # произвольный момент регистрации


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


# отдельный файл базы, удаляется после проверок
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    init_db(path)
    init_db(path)  # повторный вызов безопасен

    # новый игрок: 1000 фишек и скорость 100
    p = get_player(1, now=T, db_path=path)
    check("новый игрок", (p["balance"], p["rate"], p["last_accrual"]), (1000, 100, T))

    # через 30 минут ничего не меняется
    p = get_player(1, now=T + 30 * 60, db_path=path)
    check("30 минут", (p["balance"], p["last_accrual"]), (1000, T))

    # через 2 ч 40 мин: +200, 40 минут остаются в запасе
    t2 = T + 2 * HOUR + 40 * 60
    p = get_player(1, now=t2, db_path=path)
    check("2ч 40м", (p["balance"], p["last_accrual"]), (1200, T + 2 * HOUR))

    # повторный заход в ту же секунду не начисляет второй раз
    p = get_player(1, now=t2, db_path=path)
    check("та же секунда", (p["balance"], p["last_accrual"]), (1200, T + 2 * HOUR))

    # добор до 3 часов от регистрации: +100
    p = get_player(1, now=T + 3 * HOUR, db_path=path)
    check("до 3 часов", (p["balance"], p["last_accrual"]), (1300, T + 3 * HOUR))

    # второй игрок не зависит от первого
    q = get_player(2, now=T + 3 * HOUR, db_path=path)
    check("второй игрок", (q["balance"], q["last_accrual"]), (1000, T + 3 * HOUR))
    p = get_player(1, now=T + 3 * HOUR, db_path=path)
    check("первый не изменился", p["balance"], 1300)

    # через 50 часов после последнего начисления: потолок 30 часов
    t50 = T + 3 * HOUR + 50 * HOUR
    p = get_player(1, now=t50, db_path=path)
    check("потолок", (p["balance"], p["last_accrual"]), (1300 + 3000, t50))
finally:
    os.remove(path)

print("Все проверки прошли")

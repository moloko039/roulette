import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import tempfile

from economy import HOUR
from db import init_db, get_player

T = 1_700_000_000  # произвольный момент регистрации
A = T // 60 * 60   # метка начисления на границе минуты


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


# отдельный файл базы, удаляется после проверок
fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
try:
    init_db(path)
    init_db(path)  # повторный вызов безопасен

    # новый игрок: 1000 фишек и скорость 100, метка на границе минуты
    p = get_player(1, now=T, db_path=path)
    check("новый игрок", (p["balance"], p["rate"], p["last_accrual"], p["accrued"]), (1000, 100, A, 0))

    # через 30 минут: 30 тиков по 100/60 = ровно 50 фишек (доход идёт каждую минуту, не раз в час)
    p = get_player(1, now=T + 30 * 60, db_path=path)
    check("30 минут", (p["balance"], p["last_accrual"], p["accrued"]), (1050, A + 30 * 60, 50))

    # через 2 ч 40 мин от регистрации: 160 минут = ровно 266,67, то есть кумулятивно 267 (не больше чем на фишку выше точного)
    t2 = T + 2 * HOUR + 40 * 60
    p = get_player(1, now=t2, db_path=path)
    check("2ч 40м", (p["balance"], p["last_accrual"]), (1000 + 267, A + 160 * 60))

    # повторный заход в ту же секунду не начисляет второй раз
    p = get_player(1, now=t2, db_path=path)
    check("та же секунда", (p["balance"], p["last_accrual"], p["accrued"]), (1267, A + 160 * 60, 0))

    # добор до 3 часов от регистрации: за 180 минут ровно 300 (сумма за каждые 60 минут равна ровно 100)
    p = get_player(1, now=T + 3 * HOUR, db_path=path)
    check("до 3 часов: ровно 300", (p["balance"], p["last_accrual"]), (1300, A + 180 * 60))

    # второй игрок не зависит от первого
    q = get_player(2, now=T + 3 * HOUR, db_path=path)
    check("второй игрок", (q["balance"], q["last_accrual"]), (1000, (T + 3 * HOUR) // 60 * 60))
    p = get_player(1, now=T + 3 * HOUR, db_path=path)
    check("первый не изменился", p["balance"], 1300)

    # через 50 часов после последнего начисления: потолок 8 часов (уровень хранилища 0), лишнее сгорает, метка переносится на текущую минуту
    t50 = T + 3 * HOUR + 50 * HOUR
    p = get_player(1, now=t50, db_path=path)
    check("потолок", (p["balance"], p["last_accrual"]), (1300 + 800, t50 // 60 * 60))
finally:
    os.remove(path)

print("Все проверки прошли")

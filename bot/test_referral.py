"""Рефералка (docs/ECONOMY.md, разделы 6 и 14), шаг 1: код приглашения, привязка приглашённого при первом входе по подписанному start_param, стартовый бонус 500 фишек один раз,
защита (самореферал, существующий игрок, неверный код, блокировка повторной регистрации после удаления данных), гонки, откат привязки при ошибке."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import re
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

from fastapi.testclient import TestClient

import balance_guard
import db
import economy_config
import wallet
from api import create_app
from economy import START_BALANCE
from features.referral_db import get_or_create_code, link_for
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
A, B, C, D = 424242422, 424242423, 424242424, 424242425
BONUS = economy_config.REFERRAL_INVITEE_CHIPS

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID", "GAME_LINK")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


tmp = tempfile.mkdtemp()
counter = [0]


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "ref%d.db" % counter[0])
    db.init_db(path)
    os.environ["DB_PATH"] = path
    return path


def me(client, uid, start_param=None):
    extra = {"start_param": start_param} if start_param is not None else None
    headers = {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок", extra=extra)}
    return client.get("/api/me", headers=headers)


def balance(path, uid):
    rows = sql(path, "SELECT balance FROM players WHERE telegram_id = ?", (uid,))
    return rows[0][0] if rows else None


def refs(path):
    return sql(path, "SELECT invitee_id, referrer_id FROM referrals ORDER BY invitee_id")


try:
    # ---- код приглашения: непрозрачный, стабильный, уникальный
    path = new_db()
    code_a = get_or_create_code(A, db_path=path)
    check("код: 10 символов a-z0-9", bool(re.fullmatch(r"[a-z0-9]{10}", code_a)), True)
    check("код не содержит Telegram-id", str(A) in code_a, False)
    check("повторный вызов даёт тот же код", get_or_create_code(A, db_path=path), code_a)
    code_b = get_or_create_code(B, db_path=path)
    check("у разных игроков разные коды", code_a != code_b, True)
    check("в базе по одной строке на игрока", sql(path, "SELECT COUNT(*) FROM referral_codes")[0][0], 2)
    check("ссылка: приложение + startapp=ref_<код>", link_for(code_a, "https://t.me/test_bot/app"), "https://t.me/test_bot/app?startapp=ref_" + code_a)
    check("ссылки нет, если ссылка приложения не задана", (link_for(code_a, None), link_for(code_a, "")), (None, None))

    # гонка: 12 одновременных запросов кода одного игрока дают один код и одну строку
    path = new_db()
    gate = threading.Barrier(12)

    def race_code(_):
        gate.wait()
        return get_or_create_code(C, db_path=path)
    with ThreadPoolExecutor(12) as pool:
        codes = set(pool.map(race_code, range(12)))
    check("12 параллельных запросов кода: один код, одна строка", (len(codes), sql(path, "SELECT COUNT(*) FROM referral_codes WHERE telegram_id = ?", (C,))[0][0]), (1, 1))

    # ---- привязка при первом входе через API (подписанный start_param)
    path = new_db()
    client = TestClient(create_app(TOKEN, [], db_path=path))
    code = get_or_create_code(A, db_path=path)
    r = me(client, B, "ref_" + code)
    check("первый вход с кодом: 200", r.status_code, 200)
    check("привязка записана", refs(path), [(B, A)])
    got = balance(path, B)
    check("приглашённый получил стартовые фишки и бонус (плюс доход за секунды)", START_BALANCE + BONUS <= got <= START_BALANCE + BONUS + 10, True)
    me(client, B, "ref_" + code)
    me(client, B)
    after = balance(path, B)
    check("повторные входы не дают бонус второй раз", (refs(path), after < START_BALANCE + 2 * BONUS), ([(B, A)], True))

    # ---- существующий игрок со ссылкой: ничего
    me(client, C)
    base = balance(path, C)
    r = me(client, C, "ref_" + code)
    check("существующий игрок со ссылкой: нет привязки, нет бонуса", (refs(path), balance(path, C) < base + BONUS), ([(B, A)], True))

    # ---- самореферал
    path = new_db()
    client = TestClient(create_app(TOKEN, [], db_path=path))
    code = get_or_create_code(A, db_path=path)
    me(client, A, "ref_" + code)
    check("самореферал: нет привязки и бонуса", (refs(path), START_BALANCE <= balance(path, A) < START_BALANCE + 10), ([], True))

    # ---- неверные коды: игрок создаётся как обычно, ответ 200
    for bad in ("ref_zzzzzzzzzz", "foo", "ref_", "ref_" + "a" * 500, "REF_" + code):
        uid = 500 + counter[0] * 10 + len(bad) % 10
        r = me(client, uid, bad)
        check("неверный код «%s…»: вход работает, привязки нет" % bad[:12], (r.status_code, [x for x in refs(path) if x[0] == uid], START_BALANCE <= balance(path, uid) < START_BALANCE + 10), (200, [], True))

    # ---- блокировка повторной регистрации после удаления данных: ни привязки, ни бонуса
    path = new_db()
    client = TestClient(create_app(TOKEN, [], db_path=path))
    code = get_or_create_code(A, db_path=path)
    me(client, D)
    db.delete_player_data(D, db_path=path)
    r = me(client, D, "ref_" + code)
    check("после удаления данных в период блокировки: нет привязки, баланс 0", (r.status_code, refs(path), balance(path, D) < 10), (200, [], True))

    # ---- гонка: 10 параллельных первых входов одного игрока с кодом дают одну привязку и один бонус
    path = new_db()
    client = TestClient(create_app(TOKEN, [], db_path=path))
    code = get_or_create_code(A, db_path=path)
    gate = threading.Barrier(10)

    def race_me(_):
        gate.wait()
        return me(client, B, "ref_" + code).status_code
    with ThreadPoolExecutor(10) as pool:
        statuses = list(pool.map(race_me, range(10)))
    check("10 параллельных первых входов: все 200, одна привязка, один бонус", (set(statuses), refs(path), balance(path, B) < START_BALANCE + 2 * BONUS), ({200}, [(B, A)], True))

    # ---- ошибка при привязке не ломает вход и откатывает привязку целиком (запись и бонус)
    path = new_db()
    client = TestClient(create_app(TOKEN, [], db_path=path))
    code = get_or_create_code(A, db_path=path)
    with mock.patch.object(wallet, "credit", side_effect=RuntimeError("boom")):
        r = me(client, B, "ref_" + code)
    check("ошибка в бонусе: вход работает, игрок создан, записи привязки нет", (r.status_code, refs(path), START_BALANCE <= balance(path, B) < START_BALANCE + 10), (200, [], True))

    check("статически чисто: фишки меняет только wallet", balance_guard.violations(), [])
    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

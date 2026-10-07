"""Покупки косметики: за фишки (транзакция, идемпотентность, гонки), за Telegram Stars (инвойс, pre_checkout, successful_payment, возврат,
ручная выдача, тестовый предмет), журнал оплат (экспорт, удаление, очистка), миграция. Настоящего Telegram здесь нет: поддельный бот с теми же
вызовами (create_invoice_link, answer_pre_checkout_query, refund_star_payment, send_invoice, send_message)."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient
from telegram.error import TelegramError

import web.routes_cosmetics as api  # INVOICE_INTERVAL живёт в модуле маршрутов косметики
import bot
import tg.common
import tg.owner
import tg.payments
import tg.user
import cosmetics
import db
import ratelimit
from api import create_app
from features import cosmetics_db as cdb
from stubs import FakeUpdate
from tg_testutil import make_init_data

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
OWNER, A, B, STRANGER = 424242421, 424242422, 424242423, 424242499

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID", "PAY_SUPPORT_CONTACT", "TERMS_URL", "PRIVACY_URL", "GAME_LINK",
             "DEVELOPER_CONTACT", "PLAY_MODE")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as caught:
        return caught
    except Exception as other:  # noqa: BLE001
        raise AssertionError("ожидали %s, получили %r" % (exc.__name__, other))
    raise AssertionError("ожидали %s, исключения не было" % exc.__name__)


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


cap = Capture()
logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger().addHandler(cap)
tmp = tempfile.mkdtemp()
counter = [0]
rid_n = [0]


def rid():
    rid_n[0] += 1
    return "buy-req-%06d" % rid_n[0]


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "b%d.db" % counter[0])
    db.init_db(path)
    os.environ["DB_PATH"] = path
    return path


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def add_player(path, uid, balance=1_000_000, xp=3000, total=7777):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
              "VALUES (?, ?, 100, ?, ?, ?, ?, 0, 0)", (uid, balance, NOW + 10 * 86400, NOW - 86400, total, xp))


def player(path, uid):
    return sql(path, "SELECT balance, xp, total_staked, income_level, storage_level, rate FROM players WHERE telegram_id = ?", (uid,))[0]


def owned(path, uid):
    return sorted(r[0] for r in sql(path, "SELECT item_code FROM cosmetic_items WHERE telegram_id = ?", (uid,)))


# ---------- поддельный Telegram ----------
class FakeBot:
    def __init__(self):
        self.sent, self.refunds, self.invoices, self.links = [], [], [], []
        self.refund_error = None
        self.link_error = None

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    async def refund_star_payment(self, user_id, telegram_payment_charge_id):
        if self.refund_error:
            raise TelegramError(self.refund_error)
        self.refunds.append((user_id, telegram_payment_charge_id))
        return True

    async def send_invoice(self, **kw):
        self.invoices.append(kw)

    async def create_invoice_link(self, **kw):
        if self.link_error:
            raise TelegramError(self.link_error)
        self.links.append(kw)
        return "https://t.me/$fake-link-%d" % len(self.links)

    def texts(self, chat_id):
        return [t for c, t in self.sent if c == chat_id]


def ctx(fake, args=None):
    return SimpleNamespace(bot=fake, args=args or [], application=SimpleNamespace(bot=fake, bot_data=ctx.store))


ctx.store = {}


def run(coro):
    return asyncio.run(coro)


def command(handler, fake, args, uid=OWNER, chat="private"):
    update = FakeUpdate(chat, user_id=uid)
    run(handler(update, ctx(fake, args)))
    return [r["text"] for r in update.replies]


def pre_checkout(fake, uid, payload, amount=150, currency="XTR"):
    answers = []

    async def answer(ok, error_message=None):
        answers.append((ok, error_message))
    query = SimpleNamespace(id="q1", from_user=SimpleNamespace(id=uid), currency=currency, total_amount=amount, invoice_payload=payload, answer=answer)
    run(bot.pre_checkout(SimpleNamespace(pre_checkout_query=query), ctx(fake)))
    return answers


def paid(fake, uid, payload, charge, amount=150, currency="XTR"):
    pay = SimpleNamespace(currency=currency, total_amount=amount, invoice_payload=payload, telegram_payment_charge_id=charge, provider_payment_charge_id="p")
    update = SimpleNamespace(effective_message=SimpleNamespace(successful_payment=pay), effective_user=SimpleNamespace(id=uid))
    run(bot.successful_payment(update, ctx(fake)))


try:
    # ================= покупка за фишки =================
    path = new_db()
    add_player(path, A, 100_000)
    before = player(path, A)
    r = db.buy_with_chips(A, "buy-req-ok0001", "chip_ring", now=NOW, db_path=path)
    check("покупка: ответ", (r["item_code"], r["price"], r["balance"], r["replayed"]), ("chip_ring", {"currency": "chips", "amount": 40000}, 60_000, False))
    check("предмет выдан источником chips", sql(path, "SELECT item_code, source, payment_ref FROM cosmetic_items"), [("chip_ring", "chips", None)])
    after = player(path, A)
    check("деньги и только они: баланс -40000, опыт, ставки, уровни, доход прежние", (after[0], after[1:]), (60_000, before[1:]))
    check("повтор того же request_id: тот же результат, второго списания нет", (db.buy_with_chips(A, "buy-req-ok0001", "chip_ring", now=NOW + 1, db_path=path)["replayed"], player(path, A)[0]), (True, 60_000))
    raises(cosmetics.RequestConflict, db.buy_with_chips, A, "buy-req-ok0001", "badge_spade", now=NOW + 2, db_path=path)
    raises(cosmetics.AlreadyOwned, db.buy_with_chips, A, rid(), "chip_ring", now=NOW + 3, db_path=path)
    check("«уже есть»: ничего не списано", player(path, A)[0], 60_000)
    raises(cosmetics.NotForChips, db.buy_with_chips, A, rid(), "table_blue", now=NOW + 4, db_path=path)
    raises(cosmetics.ItemUnavailable, db.buy_with_chips, A, rid(), "back_ember", now=NOW + 5, db_path=path)
    raises(cosmetics.ItemUnavailable, db.buy_with_chips, A, rid(), "chip_plain", now=NOW + 5, db_path=path)      # стартовый не продаётся
    raises(cosmetics.UnknownItem, db.buy_with_chips, A, rid(), "nope", now=NOW + 5, db_path=path)
    raises(cosmetics.UnknownItem, db.buy_with_chips, A, rid(), "test_1star", now=NOW + 5, db_path=path)           # скрытый тестовый не покупается за фишки
    raises(ValueError, db.buy_with_chips, A, rid(), 5, now=NOW + 5, db_path=path)
    raises(cosmetics.InsufficientChips, db.buy_with_chips, A, rid(), "frame_thin", now=NOW + 6, db_path=path)     # 100000 при балансе 60000
    check("нехватка: баланс и предметы не изменились", (player(path, A)[0], owned(path, A)), (60_000, ["chip_ring"]))
    # порядок проверок: «уже есть» раньше баланса
    sql(path, "UPDATE players SET balance = 5 WHERE telegram_id = ?", (A,))
    raises(cosmetics.AlreadyOwned, db.buy_with_chips, A, rid(), "chip_ring", now=NOW + 7, db_path=path)
    check("при нехватке и «уже есть» отказ именно «уже есть»", player(path, A)[0], 5)
    # покупка сразу после действия не упирается в «раз в секунду»
    sql(path, "UPDATE players SET balance = 500000 WHERE telegram_id = ?", (A,))
    db.equip_item(A, rid(), "chip", "chip_ring", now=NOW + 100, db_path=path)
    check("покупка в ту же секунду после смены разрешена", db.buy_with_chips(A, rid(), "badge_spade", now=NOW + 100, db_path=path)["balance"], 480_000)
    db.equip_item(A, rid(), "badge", "badge_spade", now=NOW + 101, db_path=path)
    # начисление дохода подтягивается до списания (баланс как в /api/me), не меняя опыт и ставки
    path = new_db()
    add_player(path, A, 50_000)
    sql(path, "UPDATE players SET last_accrual = ? WHERE telegram_id = ?", (NOW - 3600, A))
    r = db.buy_with_chips(A, rid(), "badge_spade", now=NOW, db_path=path)
    check("после покупки баланс = 50000 + доход - 20000", r["balance"] == player(path, A)[0] and 30_000 < r["balance"] <= 30_000 + 100 * 2, True)

    # атомарность: сбой после списания откатывает всё
    path = new_db()
    add_player(path, A, 100_000)
    with mock.patch.object(cdb, "_grant_in", side_effect=RuntimeError("сбой выдачи")):
        raises(RuntimeError, db.buy_with_chips, A, "buy-req-atom01", "chip_ring", now=NOW, db_path=path)
    check("сбой после списания: баланс прежний, предмета нет, журнал действий пуст", (player(path, A)[0], owned(path, A), sql(path, "SELECT COUNT(*) FROM cosmetic_actions")[0][0]), (100_000, [], 0))
    check("после сбоя та же покупка проходит", db.buy_with_chips(A, "buy-req-atom01", "chip_ring", now=NOW + 1, db_path=path)["balance"], 60_000)

    # гонки: 20 потоков
    path = new_db()
    add_player(path, A, 1_000_000)
    gate = threading.Barrier(20)

    def work(i):
        gate.wait()
        try:
            return db.buy_with_chips(A, "race-req-%05d" % i, "mine_star", now=NOW, db_path=path)["balance"]
        except cosmetics.CosmeticsError as exc:
            return exc.code
    with ThreadPoolExecutor(20) as pool:
        res = list(pool.map(work, range(20)))
    check("20 параллельных покупок одного предмета: одна успешная, остальные «уже есть»", (sum(1 for x in res if isinstance(x, int)), sorted({x for x in res if isinstance(x, str)})), (1, ["already_owned"]))
    check("одно списание, баланс сходится", (player(path, A)[0], owned(path, A)), (1_000_000 - 60_000, ["mine_star"]))
    path = new_db()
    add_player(path, A, 1_000_000)
    gate2 = threading.Barrier(20)

    def same(i):
        gate2.wait()
        return db.buy_with_chips(A, "race-same-0001", "mine_star", now=NOW, db_path=path)
    with ThreadPoolExecutor(20) as pool:
        res = list(pool.map(same, range(20)))
    check("20 одинаковых request_id: одно действие и одно списание", (sum(1 for x in res if not x["replayed"]), player(path, A)[0]), (1, 940_000))
    # разные предметы параллельно: сумма списаний равна сумме цен
    path = new_db()
    add_player(path, A, 1_000_000)
    gate3 = threading.Barrier(4)
    items = ["badge_spade", "chip_ring", "mine_star", "frame_thin"]

    def many(i):
        gate3.wait()
        return db.buy_with_chips(A, "multi-req-%04d" % i, items[i], now=NOW, db_path=path)["item_code"]
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(many, range(4)))
    check("четыре разных предмета параллельно: списано 220000", (player(path, A)[0], owned(path, A)), (780_000, sorted(items)))

    # ================= API: покупка за фишки и инвойс =================
    path = new_db()
    for uid in (OWNER, A, B):
        add_player(path, uid, 1_000_000)
    sql(path, "UPDATE players SET last_accrual = ?", (int(time.time()) + 10 * 86400,))     # API идёт по настоящему времени: доход не начисляется
    clock = [1000.0]
    lim = ratelimit.RateLimiter(ratelimit.load_config({"WRITE_RATE_PER_SEC": "1", "WRITE_RATE_BURST": "100", "READ_RATE_PER_SEC": "1", "READ_RATE_BURST": "100"}), clock=lambda: clock[0])
    application = create_app(TOKEN, [], db_path=path, rate_limiter=lim)
    client = TestClient(application)
    examples = json.load(open(os.path.join(ROOT, "docs", "examples", "cosmetics.json"), encoding="utf-8"))

    def auth(uid):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок")}

    def post(uid, route, body):
        return client.post("/api/cosmetics/" + route, headers=auth(uid), json=body)

    cat = client.get("/api/cosmetics/catalog", headers=auth(A)).json()
    prices = {i["code"]: i["price"] for i in cat["items"] if i["price"]}
    check("каталог отдаёт цены восьми предметов", sorted(prices), sorted(cosmetics.PRICES))
    check("форма цены", prices["table_blue"], {"currency": "stars", "amount": 150})
    check("в каталоге нет скрытого тестового предмета", "test_1star" in json.dumps(cat), False)
    r = post(A, "buy", {"request_id": "api-buy-000001", "item_code": "badge_spade"})
    check("API покупка", (r.status_code, sorted(r.json()), r.json()["balance"]), (200, sorted(examples["buy"]), 980_000))
    check("повтор через API", post(A, "buy", {"request_id": "api-buy-000001", "item_code": "badge_spade"}).json()["replayed"], True)
    errs = {}
    for name, body, code in (("уже есть", {"request_id": rid(), "item_code": "badge_spade"}, "already_owned"), ("за Stars", {"request_id": rid(), "item_code": "table_blue"}, "not_for_chips"),
                             ("недоступен", {"request_id": rid(), "item_code": "back_ember"}, "item_unavailable"), ("конфликт", {"request_id": "api-buy-000001", "item_code": "chip_ring"}, "request_conflict")):
        r = post(A, "buy", body)
        check("API: " + name, (r.status_code, r.json()), (409, {"detail": code}))
    check("API: неизвестный предмет", post(A, "buy", {"request_id": rid(), "item_code": "nope"}).status_code, 404)
    check("API: скрытый тестовый предмет", post(A, "buy", {"request_id": rid(), "item_code": "test_1star"}).status_code, 404)
    for body in ({}, {"request_id": rid()}, {"item_code": "chip_ring"}, {"request_id": rid(), "item_code": 5}, {"request_id": rid(), "item_code": "chip_ring", "x": 1}, {"request_id": "x", "item_code": "chip_ring"}):
        check("API 400 %s" % json.dumps(body)[:40], post(A, "buy", body).status_code, 400)
    sql(path, "UPDATE players SET balance = 10 WHERE telegram_id = ?", (B,))
    check("API: нехватка фишек", (lambda x: (x.status_code, x.json()))(post(B, "buy", {"request_id": rid(), "item_code": "chip_ring"})), (409, {"detail": "insufficient_chips"}))
    check("без подписи", client.post("/api/cosmetics/buy", json={"request_id": rid(), "item_code": "chip_ring"}).status_code, 401)
    check("покупка видна в /mine и не меняет игровые поля /api/me", (client.get("/api/cosmetics/mine", headers=auth(A)).json()["owned"][0]["source"],
                                                                   client.get("/api/me", headers=auth(A)).json()["level"]), ("chips", 3))
    # ---- инвойс ----
    body = {"request_id": "api-inv-000001", "item_code": "table_blue"}
    check("без бота (нет приложения Telegram): 503", (lambda x: (x.status_code, x.json()))(post(A, "invoice", body)), (503, {"detail": "payments_unavailable"}))
    fake = FakeBot()
    application.state.application = SimpleNamespace(bot=fake)
    r = post(A, "invoice", body)
    check("инвойс: ссылка", (r.status_code, r.json()), (200, {"invoice_url": "https://t.me/$fake-link-1", "replayed": False}))
    call = fake.links[0]
    check("параметры Stars: XTR, пустой provider_token, одна цена 150", (call["currency"], call["provider_token"], [(p.label, p.amount) for p in call["prices"]]), ("XTR", "", [("Лагуна", 150)]))
    text = (call["title"] + " " + call["description"]).lower()
    assert not any(w in text for w in ("выигр", "удач", "шанс", "фортун")), text
    assert len(call["title"]) <= 32 and len(call["description"]) <= 255
    payload = call["payload"]
    assert len(payload.encode()) <= 128 and str(A) not in payload, payload
    check("метка: подпись связана с игроком, предмет читается", (cosmetics.parse_payload(payload, A, now=int(time.time())), cosmetics.parse_payload(payload, B, now=int(time.time()))), ("table_blue", None))
    check("метка не принимается позже суток и с испорченной подписью", (cosmetics.parse_payload(payload, A, now=int(time.time()) + 90_000), cosmetics.parse_payload(payload[:-1] + ("1" if payload[-1] == "0" else "0"), A, now=int(time.time()))), (None, None))
    check("повтор того же request_id: та же ссылка", post(A, "invoice", body).json(), {"invoice_url": "https://t.me/$fake-link-1", "replayed": True})
    r = post(A, "invoice", {"request_id": "api-inv-000002", "item_code": "table_blue"})
    check("не чаще одной ссылки на игрока и предмет за 10 секунд: 429", (r.status_code, r.json(), r.headers.get("Retry-After") is not None), (429, {"error": "too_many_requests"}, True))
    check("другой предмет за Stars сразу", post(A, "invoice", {"request_id": "api-inv-000003", "item_code": "crash_neon"}).status_code, 200)
    check("после интервала можно снова", (lambda: (setattr(api, "INVOICE_INTERVAL", 0), post(A, "invoice", {"request_id": "api-inv-000004", "item_code": "table_blue"}).status_code)[1])(), 200)
    api.INVOICE_INTERVAL = 10
    for name, item, status, code in (("за фишки", "chip_ring", 409, "not_for_stars"), ("недоступный", "frame_double", 409, "item_unavailable"), ("стартовый", "table_green", 409, "item_unavailable")):
        r = post(A, "invoice", {"request_id": rid(), "item_code": item})
        check("инвойс: " + name, (r.status_code, r.json()), (status, {"detail": code}))
    check("инвойс: неизвестный предмет", post(A, "invoice", {"request_id": rid(), "item_code": "nope"}).status_code, 404)
    check("инвойс: скрытый тестовый предмет через API недоступен", post(A, "invoice", {"request_id": rid(), "item_code": "test_1star"}).status_code, 404)
    db.grant_item(B, "keno_hex", "owner_gift", db_path=path)
    check("инвойс: предмет уже есть", (lambda x: (x.status_code, x.json()))(post(B, "invoice", {"request_id": rid(), "item_code": "keno_hex"})), (409, {"detail": "already_owned"}))
    fake.link_error = "boom"
    check("сбой Telegram при создании ссылки: 502", post(A, "invoice", {"request_id": rid(), "item_code": "back_midnight"}).status_code, 502)
    fake.link_error = None
    check("инвойс: 400 на неверное тело", post(A, "invoice", {"request_id": rid()}).status_code, 400)

    # ================= pre_checkout_query =================
    path = new_db()
    for uid in (OWNER, A, B):
        add_player(path, uid)
    fake = FakeBot()
    good = cosmetics.make_payload(A, "table_blue", int(time.time()))
    check("pre_checkout ок", pre_checkout(fake, A, good), [(True, None)])
    check("чужая метка: отказ с понятным текстом", pre_checkout(fake, B, good), [(False, "Предмет сейчас недоступен или цена изменилась. Откройте магазин и попробуйте снова")])
    check("неверная сумма", pre_checkout(fake, A, good, amount=149)[0][0], False)
    check("не та валюта", pre_checkout(fake, A, good, currency="USD")[0][0], False)
    check("испорченная метка", pre_checkout(fake, A, "ci1.table_blue.1.zzz")[0][0], False)
    check("метка вообще не наша", pre_checkout(fake, A, "hello")[0][0], False)
    check("предмет за фишки по Stars не оплачивается", pre_checkout(fake, A, cosmetics.make_payload(A, "chip_ring", int(time.time())))[0][0], False)
    check("недоступный предмет", pre_checkout(fake, A, cosmetics.make_payload(A, "frame_double", int(time.time())))[0][0], False)
    old = cosmetics.make_payload(A, "table_blue", int(time.time()) - 90_000)
    check("метка старше суток", pre_checkout(fake, A, old)[0][0], False)
    db.grant_item(A, "table_blue", "owner_gift", db_path=path)
    check("предмет уже есть", pre_checkout(fake, A, good), [(False, "Этот предмет у вас уже есть")])
    check("тестовый предмет владельца проходит", pre_checkout(fake, OWNER, cosmetics.make_payload(OWNER, "test_1star", int(time.time())), amount=1), [(True, None)])

    # ================= successful_payment =================
    path = new_db()
    for uid in (OWNER, A, B):
        add_player(path, uid)
    fake = FakeBot()
    ctx.store = {}
    os.environ["OWNER_CHAT_ID"] = str(OWNER)
    payload = cosmetics.make_payload(A, "table_blue", int(time.time()))
    cap.lines.clear()
    paid(fake, A, payload, "chg-0001")
    check("оплата: предмет в гардеробе и сообщение", (owned(path, A), fake.texts(A)), (["table_blue"], ["Предмет добавлен во вкладку «Стиль»"]))
    check("журнал: запись paid без лишнего", sql(path, "SELECT charge_id, telegram_id, item_code, amount_stars, status, refunded_at FROM cosmetic_purchases"), [("chg-0001", A, "table_blue", 150, "paid", None)])
    check("предмет связан с платежом", sql(path, "SELECT source, payment_ref FROM cosmetic_items"), [("stars", "chg-0001")])
    paid(fake, A, payload, "chg-0001")
    check("повторная доставка того же платежа: без дублей и без второго сообщения", (sql(path, "SELECT COUNT(*) FROM cosmetic_purchases")[0][0], owned(path, A), len(fake.texts(A))), (1, ["table_blue"], 1))
    # гонка двух инвойсов на один предмет: второй платёж возвращается автоматически
    paid(fake, A, payload, "chg-0002")
    check("второй платёж за тот же предмет: автоматический возврат", (fake.refunds, sql(path, "SELECT status FROM cosmetic_purchases WHERE charge_id = 'chg-0002'")), ([(A, "chg-0002")], [("refunded",)]))
    check("игрок предупреждён, предмет остался один", (fake.texts(A)[-1], owned(path, A), sql(path, "SELECT payment_ref FROM cosmetic_items")), ("Этот предмет у вас уже был, оплата возвращена.", ["table_blue"], [("chg-0001",)]))
    # возврат не удался: статус refund_pending, владельцу сообщение
    fake.refund_error = "network"
    paid(fake, A, payload, "chg-0003")
    check("возврат не удался: refund_pending и сообщение владельцу", (sql(path, "SELECT status FROM cosmetic_purchases WHERE charge_id = 'chg-0003'"), any("chg-0003" in t for t in fake.texts(OWNER))), ([("refund_pending",)], True))
    fake.refund_error = None
    # сбой записи: три попытки, затем владельцу и ручная выдача
    with mock.patch.object(tg.payments, "PAY_RETRY_DELAY", 0):
        calls = []
        real = db.record_stars_payment

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) < 3:
                raise sqlite3.OperationalError("база занята")
            return real(*a, **k)
        with mock.patch.object(db, "record_stars_payment", side_effect=flaky):
            pay_b = cosmetics.make_payload(B, "crash_neon", int(time.time()))
            paid(fake, B, pay_b, "chg-0004", amount=100)
        check("сбой дважды, на третьей попытке записано", (len(calls), owned(path, B), fake.texts(B)[-1]), (3, ["crash_neon"], "Предмет добавлен во вкладку «Стиль»"))
        cap.lines.clear()
        with mock.patch.object(db, "record_stars_payment", side_effect=sqlite3.OperationalError("диск")):
            paid(fake, B, cosmetics.make_payload(B, "keno_hex", int(time.time())), "chg-0005", amount=75)
    check("три сбоя: игрок предупреждён, владельцу пришли платёж и инструкция", (fake.texts(B)[-1].startswith("Оплата получена, но предмет не удалось выдать сразу"),
                                                                              any("chg-0005" in t and "/regrant" in t for t in fake.texts(OWNER))), (True, True))
    check("платёж в памяти для ручной выдачи; в логах нет идентификаторов", (ctx.store["failed_payments"]["chg-0005"], any("chg-0005" in l or str(B) in l for l in cap.lines)),
          ({"user": B, "code": "keno_hex", "amount": 75}, False))
    check("предмета нет и записи нет", (owned(path, B), sql(path, "SELECT COUNT(*) FROM cosmetic_purchases WHERE charge_id = 'chg-0005'")[0][0]), (["crash_neon"], 0))
    # метка не распознана: возврат
    paid(fake, A, "ci1.table_blue.1760000000.deadbeef", "chg-0006")
    check("нераспознанный заказ: возврат и сообщение", (fake.refunds[-1], fake.texts(A)[-1]), ((A, "chg-0006"), "Не удалось распознать заказ, оплата возвращена."))

    # ================= /regrant =================
    check("regrant: чужому молчание", command(bot.regrant, fake, ["chg-0005"], uid=STRANGER), [])
    os.environ["OWNER_CHAT_ID"] = str(OWNER)
    check("regrant: нет записи", command(bot.regrant, fake, ["chg-0005"])[0].startswith("Платёж не найден"), True)
    out = command(bot.regrant, fake, ["chg-0005", str(B), "keno_hex", "75"])
    check("regrant: полная форма выдаёт и пишет запись", (out, owned(path, B), sql(path, "SELECT status FROM cosmetic_purchases WHERE charge_id = 'chg-0005'")), (["Выдано"], ["crash_neon", "keno_hex"], [("paid",)]))
    check("regrant: повтор безопасен", command(bot.regrant, fake, ["chg-0005", str(B), "keno_hex", "75"]), ["Предмет у игрока уже есть"])
    sql(path, "DELETE FROM cosmetic_items WHERE telegram_id = ? AND item_code = 'keno_hex'", (B,))
    check("regrant: запись есть, предмета нет", (command(bot.regrant, fake, ["chg-0005"]), owned(path, B)), (["Выдано"], ["crash_neon", "keno_hex"]))
    check("regrant: возвращённый платёж не выдаётся", command(bot.regrant, fake, ["chg-0002"]), ["Платёж уже возвращён, выдавать нечего"])
    check("regrant: неверный формат", command(bot.regrant, fake, [])[0].startswith("Формат"), True)
    check("regrant: группа молчит", command(bot.regrant, fake, ["chg-0005"], chat="supergroup"), [])

    # ================= /refund =================
    fake.refunds.clear()
    check("refund: чужому молчание, возврата нет", (command(bot.refund, fake, ["chg-0001"], uid=STRANGER), fake.refunds), ([], []))
    check("refund: группа молчит", (command(bot.refund, fake, ["chg-0001"], chat="supergroup"), fake.refunds), ([], []))
    db.equip_item(A, rid(), "table", "table_blue", now=NOW + 500, db_path=path)
    check("refund: нет платежа", command(bot.refund, fake, ["no-such-charge"]), ["Платёж не найден в журнале"])
    check("refund: формат", command(bot.refund, fake, ["bad id!"])[0].startswith("Формат"), True)
    out = command(bot.refund, fake, ["chg-0001"])
    check("refund: возврат выполнен", (out, fake.refunds), (["Возврат выполнен, предмет убран у игрока"], [(A, "chg-0001")]))
    check("предмет убран и снят, статус refunded", (owned(path, A), sql(path, "SELECT COUNT(*) FROM cosmetic_equipped WHERE telegram_id = ?", (A,))[0][0],
                                                   sql(path, "SELECT status, refunded_at IS NOT NULL FROM cosmetic_purchases WHERE charge_id = 'chg-0001'")), ([], 0, [("refunded", 1)]))
    check("игроку сообщение", fake.texts(A)[-1], "Платёж возвращён, предмет убран из вкладки «Стиль».")
    check("refund: повтор безопасен", (command(bot.refund, fake, ["chg-0001"]), len(fake.refunds)), (["Этот платёж уже возвращён"], 1))
    fake.refund_error = "Bad Request: CHARGE_ALREADY_REFUNDED"
    sql(path, "INSERT INTO cosmetic_purchases (charge_id, telegram_id, item_code, amount_stars, status, created_at) VALUES ('chg-9', ?, 'crash_neon', 100, 'paid', ?)", (A, NOW))
    check("Telegram: уже возвращено ранее: журнал выравнивается", (command(bot.refund, fake, ["chg-9"]), sql(path, "SELECT status FROM cosmetic_purchases WHERE charge_id = 'chg-9'")), (["Возврат выполнен, предмет убран у игрока"], [("refunded",)]))
    fake.refund_error = "Bad Request: something else"
    sql(path, "INSERT INTO cosmetic_purchases (charge_id, telegram_id, item_code, amount_stars, status, created_at) VALUES ('chg-8', ?, 'crash_neon', 100, 'paid', ?)", (A, NOW))
    check("Telegram отказал: статус не меняется", (command(bot.refund, fake, ["chg-8"]), sql(path, "SELECT status FROM cosmetic_purchases WHERE charge_id = 'chg-8'")), (["Возврат не выполнен (подробности в логах сервиса)"], [("paid",)]))
    fake.refund_error = None

    # ================= /teststars =================
    fake = FakeBot()
    check("teststars: чужому молчание", (command(bot.teststars, fake, [], uid=STRANGER), fake.invoices), ([], []))
    check("teststars: группа молчит", (command(bot.teststars, fake, [], chat="supergroup"), fake.invoices), ([], []))
    command(bot.teststars, fake, [])
    inv = fake.invoices[0]
    check("тестовый инвойс: 1 Star, XTR, пустой provider_token, чат владельца", (inv["chat_id"], inv["currency"], inv["provider_token"], [(p.label, p.amount) for p in inv["prices"]]),
          (OWNER, "XTR", "", [("Тестовый предмет", 1)]))
    check("метка тестового инвойса читается", cosmetics.parse_payload(inv["payload"], OWNER, now=int(time.time())), "test_1star")
    check("pre_checkout тестового инвойса", pre_checkout(fake, OWNER, inv["payload"], amount=1), [(True, None)])
    paid(fake, OWNER, inv["payload"], "chg-test-1", amount=1)
    check("после оплаты запись обычная, предмет скрыт для клиента", (sql(path, "SELECT status, amount_stars FROM cosmetic_purchases WHERE charge_id = 'chg-test-1'"), owned(path, OWNER),
                                                                   [o["code"] for o in db.cosmetics_mine(OWNER, db_path=path)["owned"]]), ([("paid", 1)], ["test_1star"], []))
    raises(cosmetics.UnknownItem, db.equip_item, OWNER, rid(), "badge", "test_1star", now=NOW + 900, db_path=path)      # надеть нельзя
    command(bot.refund, fake, ["chg-test-1"])
    check("возврат тестового платежа через /refund", (owned(path, OWNER), fake.refunds[-1]), ([], (OWNER, "chg-test-1")))
    check("тестовый предмет не выдаётся подарком и не попадает в каталог", (raises(ValueError, db.grant_item, OWNER, "test_1star", "owner_gift", db_path=path) is not None,
                                                                         "test_1star" in [i["code"] for i in cosmetics.CATALOG]), (True, False))

    # ================= /paysupport и /terms =================
    os.environ.pop("PAY_SUPPORT_CONTACT", None)
    out = command(bot.paysupport, FakeBot(), [], uid=A)[0]
    assert "контакт для связи пока не указан" in out and "/terms" in out, out
    os.environ["PAY_SUPPORT_CONTACT"] = "support@example.test"
    check("paysupport с контактом из окружения", "support@example.test" in command(bot.paysupport, FakeBot(), [], uid=A)[0], True)
    os.environ["PRIVACY_URL"] = "https://example.test/roulette/privacy.html"
    out = command(bot.paysupport, FakeBot(), [], uid=A)[0]
    check("paysupport со ссылкой на условия", out.endswith("Условия: https://example.test/roulette/terms.html"), True)
    check("terms: ссылка выводится из PRIVACY_URL", command(bot.terms, FakeBot(), [], uid=A), ["Условия покупки предметов: https://example.test/roulette/terms.html"])
    os.environ["TERMS_URL"] = "https://example.test/t.html"
    check("terms: TERMS_URL главнее", command(bot.terms, FakeBot(), [], uid=A), ["Условия покупки предметов: https://example.test/t.html"])
    os.environ.pop("TERMS_URL"), os.environ.pop("PRIVACY_URL")
    check("terms без настроек: нейтральный ответ", command(bot.terms, FakeBot(), [], uid=A), [bot.UNAVAILABLE])
    check("в группе: только личная переписка", (command(bot.paysupport, FakeBot(), [], uid=A, chat="supergroup"), command(bot.terms, FakeBot(), [], uid=A, chat="group")), ([bot.PRIVATE_ONLY], [bot.PRIVATE_ONLY]))
    os.environ.pop("PAY_SUPPORT_CONTACT")
    for text in (bot.PRIVATE_HELP,):
        assert "/paysupport" in text and "/terms" in text
    assert "refund" not in bot.PRIVATE_HELP and "teststars" not in bot.PRIVATE_HELP and "regrant" not in bot.PRIVATE_HELP
    menu = [c.command for c in bot.PRIVATE_COMMANDS + bot.GROUP_COMMANDS]
    assert not {"refund", "regrant", "teststars"} & set(menu), menu

    # ================= экспорт, удаление, очистка журнала =================
    path = new_db()
    add_player(path, A)
    sql(path, "INSERT INTO cosmetic_purchases (charge_id, telegram_id, item_code, amount_stars, status, created_at) VALUES ('secret-charge', ?, 'table_blue', 150, 'paid', ?)", (A, NOW))
    db.grant_item(A, "table_blue", "stars", "secret-charge", now=NOW, db_path=path)
    ex = db.get_player_export(A, db_path=path)
    check("экспорт: покупки без идентификатора платежа", ex["cosmetics"]["purchases"], [{"item_code": "table_blue", "amount_stars": 150, "time": NOW, "status": "paid"}])
    assert "secret-charge" not in json.dumps(ex, ensure_ascii=False), "charge_id не должен попадать в выгрузку"
    assert "payment_ref" not in json.dumps(ex)
    only = new_db()
    sql(only, "INSERT INTO cosmetic_purchases (charge_id, telegram_id, item_code, amount_stars, status, created_at) VALUES ('c1', ?, 'table_blue', 150, 'paid', ?)", (B, NOW))
    check("только журнал оплат: выгрузка есть", db.get_player_export(B, db_path=only) is not None, True)
    path2 = path
    os.environ["DB_PATH"] = path
    db.delete_player_data(A, db_path=path, now=NOW + 100)
    check("после удаления данных: предметы и гардероб удалены, запись об оплате осталась", (owned(path, A), sql(path, "SELECT charge_id, telegram_id, status FROM cosmetic_purchases")), ([], [("secret-charge", A, "paid")]))
    assert str(cosmetics.PURCHASE_RETENTION_DAYS) in bot.DELETE_WARNING and "без возмещения" in bot.DELETE_WARNING and "Telegram Stars" in bot.DELETE_WARNING
    check("срок хранения журнала", cosmetics.PURCHASE_RETENTION_DAYS, 365)
    db.purge_old_data(now=NOW + 364 * 86400, db_path=path)
    check("до срока запись хранится", sql(path, "SELECT COUNT(*) FROM cosmetic_purchases")[0][0], 1)
    res = db.purge_old_data(now=NOW + 366 * 86400, db_path=path)
    check("через 365 дней очистка удаляет запись", (sql(path, "SELECT COUNT(*) FROM cosmetic_purchases")[0][0], res["cosmetic_purchases"]), (0, 1))

    # ================= UNIQUE и идемпотентность по charge_id =================
    path = new_db()
    add_player(path, A)
    db.record_stars_payment(A, "dup-charge", "table_blue", 150, now=NOW, db_path=path)
    check("повтор записи платежа: duplicate, дубля нет", (db.record_stars_payment(A, "dup-charge", "table_blue", 150, now=NOW, db_path=path)["result"], sql(path, "SELECT COUNT(*) FROM cosmetic_purchases")[0][0]), ("duplicate", 1))
    raises(sqlite3.IntegrityError, sql, path, "INSERT INTO cosmetic_purchases (charge_id, telegram_id, item_code, amount_stars, status, created_at) VALUES ('dup-charge', 1, 'x', 1, 'paid', 1)")
    for bad in (("", "table_blue", 150), ("c", "nope", 150), ("c", "table_blue", 0), ("c", "table_blue", "150"), (5, "table_blue", 150)):
        raises(ValueError, db.record_stars_payment, A, *bad, now=NOW, db_path=path)
    gate4 = threading.Barrier(20)

    def pay_race(i):
        gate4.wait()
        return db.record_stars_payment(A, "race-charge", "crash_neon", 100, now=NOW, db_path=path)["result"]
    with ThreadPoolExecutor(20) as pool:
        res = list(pool.map(pay_race, range(20)))
    check("20 одновременных доставок одного платежа: одна выдача", (res.count("granted"), res.count("duplicate"), sql(path, "SELECT COUNT(*) FROM cosmetic_purchases WHERE charge_id = 'race-charge'")[0][0]), (1, 19, 1))

    # Миграции со старых версий базы проверяет test_legacy_migration.py на фикстурах схемы (bot/testdata/legacy), без git.

    # ================= статически: «cosmetic» нигде в играх, wallet, economy, transfers =================
    forbidden = ([os.path.join(HERE, "games", n) for n in os.listdir(os.path.join(HERE, "games")) if n.endswith(".py")] +
                 [os.path.join(HERE, n) for n in ("wallet.py", "economy.py", "farm.py", "transfers.py", "levels.py", "xp.py", "roulette.py", "keno.py", "mines.py", "blackjack.py", "crash.py", "hilo.py")] +
                 [os.path.join(HERE, "features", n) for n in ("transfers_db.py", "farm_db.py", "give_db.py", "grants_db.py")])
    for f in forbidden:
        assert "cosmetic" not in open(f, encoding="utf-8").read().lower(), "в %s есть слово cosmetic" % os.path.relpath(f, HERE)
    check("логика покупки вызывает wallet как обычный потребитель", "wallet.debit" in open(os.path.join(HERE, "features", "cosmetics_db.py"), encoding="utf-8").read(), True)
    # в логах нет идентификаторов, имён и платёжных данных
    log_text = "\n".join(cap.lines)
    for needle in ("chg-0001", "chg-0002", "secret-charge", str(A), str(OWNER)):
        assert needle not in log_text, "в логах есть %r" % needle
    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

"""Кристаллы (план экономики, этап E2): кошелёк (журнал, идемпотентность, классы причин, потолок), оплата пакета Stars (pre_checkout, successful_payment,
повтор, потолок), возврат (только неиспользованные, принудительный владельцем), API (/api/me, пакеты, ссылка на оплату), /mydata, /deletemydata, очистка.
Настоящего Telegram здесь нет: боту подставлен поддельный."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import json
import os
import sqlite3
import tempfile
import time
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient
from telegram.error import TelegramError

import balance_guard
import bot
import cosmetics
import db
import economy_config
import wallet
from api import create_app
from stubs import FakeUpdate
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
NOW = 1_760_000_000
OWNER, A, B = 424242421, 424242422, 424242423

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID", "PAY_SUPPORT_CONTACT")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["OWNER_CHAT_ID"] = str(OWNER)


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


tmp = tempfile.mkdtemp()
counter = [0]


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "g%d.db" % counter[0])
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


def ledger_sum_ok(path):
    """Инвариант: баланс кристаллов каждого игрока равен сумме его журнала."""
    rows = sql(path, "SELECT telegram_id, SUM(delta) FROM gems_ledger GROUP BY telegram_id")
    bal = dict(sql(path, "SELECT telegram_id, gems FROM gem_balances"))
    return all(bal.get(u, 0) == s for u, s in rows) and all(s == 0 or u in dict(rows) for u, s in bal.items())


def in_tx(path, fn):
    conn = db._connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            out = fn(conn)
            conn.execute("COMMIT")
            return out
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


class FakeBot:
    def __init__(self):
        self.sent, self.refunds, self.links = [], [], []
        self.refund_error = None

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    async def refund_star_payment(self, user_id, telegram_payment_charge_id):
        if self.refund_error:
            raise TelegramError(self.refund_error)
        self.refunds.append((user_id, telegram_payment_charge_id))
        return True

    async def create_invoice_link(self, **kw):
        self.links.append(kw)
        return "https://t.me/$fake-gems-%d" % len(self.links)

    def texts(self, chat_id):
        return [t for c, t in self.sent if c == chat_id]


def auth_h(uid):
    return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок")}


def ctx(fake, args=None):
    return SimpleNamespace(bot=fake, args=args or [], application=SimpleNamespace(bot=fake, bot_data={}))


def run(coro):
    return asyncio.run(coro)


def command(handler, fake, args, uid=OWNER):
    update = FakeUpdate("private", user_id=uid)
    run(handler(update, ctx(fake, args)))
    return [r["text"] for r in update.replies]


def pre_checkout(fake, uid, payload, amount, currency="XTR"):
    answers = []

    async def answer(ok, error_message=None):
        answers.append((ok, error_message))
    query = SimpleNamespace(id="q1", from_user=SimpleNamespace(id=uid), currency=currency, total_amount=amount, invoice_payload=payload, answer=answer)
    run(bot.pre_checkout(SimpleNamespace(pre_checkout_query=query), ctx(fake)))
    return answers


def paid(fake, uid, payload, charge, amount, currency="XTR"):
    pay = SimpleNamespace(currency=currency, total_amount=amount, invoice_payload=payload, telegram_payment_charge_id=charge, provider_payment_charge_id="p")
    update = SimpleNamespace(effective_message=SimpleNamespace(successful_payment=pay), effective_user=SimpleNamespace(id=uid))
    run(bot.successful_payment(update, ctx(fake)))


try:
    # ================= конфигурация =================
    check("пакеты из плана", economy_config.GEM_PACKS, {"gems_50": (50, 50), "gems_250": (250, 275), "gems_1000": (1000, 1200)})
    check("все коды пакетов подходят метке счёта и начинаются с префикса", all(c.startswith(economy_config.GEM_PACK_PREFIX) and cosmetics.make_payload(1, c, NOW) for c in economy_config.GEM_PACKS), True)
    check("у каждой причины есть класс source или sink", set(economy_config.GEM_REASONS.values()) <= {"source", "sink"}, True)
    check("список пакетов для клиента по возрастанию цены", [p["stars"] for p in db.packs_view()], [50, 250, 1000])

    # ================= кошелёк кристаллов =================
    path = new_db()
    in_tx(path, lambda c: db._register_player(c, A, NOW))
    check("новый игрок: 0 кристаллов", db.gems_state(A, path), {"gems": 0})
    check("начисление", in_tx(path, lambda c: wallet.gems_credit(c, A, 100, "owner_grant", "g1", NOW)), 100)
    check("списание", in_tx(path, lambda c: wallet.gems_debit(c, A, 30, "cosmetic_purchase", "table_blue", NOW)), 70)
    raises(sqlite3.IntegrityError, in_tx, path, lambda c: wallet.gems_credit(c, A, 100, "owner_grant", "g1", NOW))       # тот же ref второй раз
    check("повтор с тем же ref ничего не изменил", (db.gems_state(A, path)["gems"], ledger_sum_ok(path)), (70, True))
    raises(wallet.InsufficientGems, in_tx, path, lambda c: wallet.gems_debit(c, A, 71, "cosmetic_purchase", "x", NOW))
    check("нехватка: баланс прежний, строк журнала нет", (db.gems_state(A, path)["gems"], sql(path, "SELECT COUNT(*) FROM gems_ledger")[0][0]), (70, 2))
    raises(ValueError, in_tx, path, lambda c: wallet.gems_credit(c, A, 5, "no_such_reason", None, NOW))
    raises(ValueError, in_tx, path, lambda c: wallet.gems_credit(c, A, 5, "refund", None, NOW))              # sink нельзя начислять
    raises(ValueError, in_tx, path, lambda c: wallet.gems_debit(c, A, 5, "purchase", None, NOW))             # source нельзя списывать
    raises(ValueError, in_tx, path, lambda c: wallet.gems_credit(c, A, 0, "owner_grant", "z", NOW))
    raises(ValueError, in_tx, path, lambda c: wallet.gems_credit(c, A, True, "owner_grant", "z", NOW))
    with mock.patch.object(economy_config, "GEMS_MAX_BALANCE", 100):
        raises(wallet.GemsLimitExceeded, in_tx, path, lambda c: wallet.gems_credit(c, A, 31, "owner_grant", "lim", NOW))
        check("ровно до потолка можно", in_tx(path, lambda c: wallet.gems_credit(c, A, 30, "owner_grant", "lim2", NOW)), 100)
    check("инвариант баланс = сумма журнала", ledger_sum_ok(path), True)
    check("баланс кристаллов не отрицательный (CHECK в таблице)", raises(sqlite3.IntegrityError, sql, path, "UPDATE gem_balances SET gems = -1") is not None, True)
    # охранник: запись кристаллов вне wallet.py замечена
    rogue = 'def f(c):\n    c.execute("UPDATE gem_balances SET gems = gems + 1000 WHERE telegram_id = 1")\n'
    check("охранник видит запись gem_balances вне wallet", len(balance_guard.scan_text("games/x.py", rogue)), 1)
    rogue2 = 'def f(c):\n    c.execute("INSERT INTO gems_ledger (telegram_id, delta, reason, ref, created_at) VALUES (1, 5, \'x\', NULL, 0)")\n'
    check("охранник видит запись gems_ledger вне wallet", len(balance_guard.scan_text("features/x.py", rogue2)), 1)
    check("чтение кристаллов охранник не трогает", balance_guard.scan_text("a.py", 'def f(c):\n    return c.execute("SELECT gems FROM gem_balances")\n'), [])
    check("статический разбор исходников чист", balance_guard.violations(), [])

    # ================= оплата пакета =================
    path = new_db()
    r = db.record_gem_payment(A, "charge-gem-0001", "gems_250", 250, now=NOW, db_path=path)
    check("пакет начислен", (r["result"], r["gems"], r["balance"]), ("granted", 275, 275))
    check("журнал оплат и журнал кристаллов", (sql(path, "SELECT telegram_id, pack_code, amount_stars, gems, status FROM gem_purchases"),
                                                sql(path, "SELECT delta, reason, ref FROM gems_ledger")),
          ([(A, "gems_250", 250, 275, "paid")], [(275, "purchase", "charge-gem-0001")]))
    check("повторная доставка: дубль, баланс прежний", (db.record_gem_payment(A, "charge-gem-0001", "gems_250", 250, now=NOW + 5, db_path=path)["result"], db.gems_state(A, path)["gems"]), ("duplicate", 275))
    raises(ValueError, db.record_gem_payment, A, "charge-gem-0002", "gems_250", 249, now=NOW, db_path=path)          # сумма не равна цене
    raises(db.UnknownPack, db.record_gem_payment, A, "charge-gem-0003", "gems_9999", 10, now=NOW, db_path=path)
    raises(ValueError, db.record_gem_payment, A, "", "gems_50", 50, now=NOW, db_path=path)
    check("отказы ничего не записали", (sql(path, "SELECT COUNT(*) FROM gem_purchases")[0][0], db.gems_state(A, path)["gems"]), (1, 275))
    with mock.patch.object(economy_config, "GEMS_MAX_BALANCE", 280):
        r = db.record_gem_payment(A, "charge-gem-0004", "gems_50", 50, now=NOW + 10, db_path=path)
        check("потолок: кристаллы не начислены, платёж ждёт возврата", (r["result"], r["gems"], sql(path, "SELECT status FROM gem_purchases WHERE charge_id = 'charge-gem-0004'")[0][0], db.gems_state(A, path)["gems"]),
              ("limit", 0, "refund_pending", 275))

    # ================= Telegram: pre_checkout и successful_payment =================
    fake = FakeBot()
    path = new_db()
    good = cosmetics.make_payload(A, "gems_250", int(time.time()))
    check("pre_checkout: верная метка и цена", pre_checkout(fake, A, good, 250), [(True, None)])
    check("pre_checkout: цена изменилась", pre_checkout(fake, A, good, 249)[0][0], False)
    check("pre_checkout: чужая метка", pre_checkout(fake, B, good, 250)[0][0], False)
    check("pre_checkout: не XTR", pre_checkout(fake, A, good, 250, currency="USD")[0][0], False)
    check("pre_checkout: старая метка", pre_checkout(fake, A, cosmetics.make_payload(A, "gems_250", int(time.time()) - 2 * 86400), 250)[0][0], False)
    check("pre_checkout: неизвестный пакет", pre_checkout(fake, A, cosmetics.make_payload(A, "gems_77", int(time.time())), 77)[0][0], False)
    paid(fake, A, good, "charge-tg-0001", 250)
    check("успешная оплата: начислено и сообщение", (db.gems_state(A, path)["gems"], fake.texts(A)), (275, ["Кристаллы начислены: +275. Баланс: 275"]))
    paid(fake, A, good, "charge-tg-0001", 250)
    check("повтор апдейта: второго начисления нет, второго сообщения нет", (db.gems_state(A, path)["gems"], len(fake.texts(A))), (275, 1))
    paid(fake, A, cosmetics.make_payload(A, "gems_77", int(time.time())), "charge-tg-0002", 77)
    check("неизвестный пакет в оплате: деньги возвращаются, кристаллов нет", (fake.refunds[-1], db.gems_state(A, path)["gems"]), ((A, "charge-tg-0002"), 275))
    with mock.patch.object(economy_config, "GEMS_MAX_BALANCE", 280):
        paid(fake, A, cosmetics.make_payload(A, "gems_50", int(time.time())), "charge-tg-0003", 50)
    check("потолок: автоматический возврат Stars, кристаллы не тронуты", ((A, "charge-tg-0003") in fake.refunds, db.gems_state(A, path)["gems"], db.gem_purchase_by_charge("charge-tg-0003")["status"]),
          (True, 275, "refunded"))

    # ================= возврат пакета =================
    path = new_db()
    fake = FakeBot()
    paid(fake, A, cosmetics.make_payload(A, "gems_250", int(time.time())), "charge-ref-0001", 250)
    paid(fake, B, cosmetics.make_payload(B, "gems_50", int(time.time())), "charge-ref-0002", 50)
    check("неиспользованный пакет можно вернуть", db.refund_check("charge-ref-0001", path), {"ok": True, "reason": None, "telegram_id": A, "gems": 275})
    check("нет платежа", db.refund_check("charge-nope", path)["reason"], "missing")
    out = command(bot.refund, fake, ["charge-ref-0001"])
    check("/refund: Stars возвращены, кристаллы списаны", (fake.refunds[-1], db.gems_state(A, path)["gems"], db.gem_purchase_by_charge("charge-ref-0001")["status"], out),
          ((A, "charge-ref-0001"), 0, "refunded", ["Возврат выполнен, кристаллы пакета списаны"]))
    check("/refund повторно: уже возвращён", command(bot.refund, fake, ["charge-ref-0001"]), ["Этот платёж уже возвращён"])
    # потраченные
    in_tx(path, lambda c: wallet.gems_debit(c, B, 10, "cosmetic_purchase", "table_blue", NOW))
    check("потраченные кристаллы: вернуть нельзя", db.refund_check("charge-ref-0002", path)["reason"], "spent")
    refunds_before = len(fake.refunds)
    out = command(bot.refund, fake, ["charge-ref-0002"])
    check("/refund без force отказывает, Stars не возвращены", (len(fake.refunds), db.gems_state(B, path)["gems"], "force" in out[0]), (refunds_before, 40, True))
    out = command(bot.refund, fake, ["charge-ref-0002", "force"])
    check("/refund force: Stars возвращены, списано сколько есть", (fake.refunds[-1], db.gems_state(B, path)["gems"], db.gem_purchase_by_charge("charge-ref-0002")["status"]),
          ((B, "charge-ref-0002"), 0, "refunded"))
    check("после возвратов баланс = сумма журнала", ledger_sum_ok(path), True)
    check("не владелец /refund не может", command(bot.refund, fake, ["charge-ref-0002"], uid=A), [])
    # Telegram отказал: ничего не меняется
    paid(fake, A, cosmetics.make_payload(A, "gems_50", int(time.time())), "charge-ref-0003", 50)
    fake.refund_error = "network"
    out = command(bot.refund, fake, ["charge-ref-0003"])
    check("сбой Telegram: кристаллы и статус прежние", (db.gems_state(A, path)["gems"], db.gem_purchase_by_charge("charge-ref-0003")["status"], out), (50, "paid", ["Возврат не выполнен (подробности в логах сервиса)"]))
    fake.refund_error = None

    # ================= API =================
    path = new_db()
    fake = FakeBot()
    client = TestClient(create_app(TOKEN, [], db_path=path))
    client.app.state.application = SimpleNamespace(bot=fake)

    def auth(uid=A):
        return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок")}
    check("пакеты без подписи: 401", client.get("/api/gems/packs").status_code, 401)
    r = client.get("/api/gems/packs", headers=auth())
    check("пакеты: список и баланс", (r.status_code, r.json()), (200, {"packs": [{"code": "gems_50", "stars": 50, "gems": 50}, {"code": "gems_250", "stars": 250, "gems": 275},
                                                                       {"code": "gems_1000", "stars": 1000, "gems": 1200}], "gems": 0}))
    check("/api/me: поле gems", client.get("/api/me", headers=auth()).json()["gems"], 0)
    db.owner_grant_gems(A, 7, "dev-1", now=NOW, db_path=path)
    check("/api/me: баланс кристаллов виден", client.get("/api/me", headers=auth()).json()["gems"], 7)
    check("выдача владельцем идемпотентна по ref", (db.owner_grant_gems(A, 7, "dev-1", now=NOW, db_path=path)["result"], db.gems_state(A, path)["gems"]), ("duplicate", 7))
    body = {"request_id": "gems-req-000001", "pack_code": "gems_250"}
    r = client.post("/api/gems/invoice", headers=auth(), json=body)
    check("ссылка на оплату", (r.status_code, r.json()), (200, {"invoice_url": "https://t.me/$fake-gems-1", "replayed": False}))
    link = fake.links[-1]
    check("в счёте: XTR, цена пакета, метка для игрока", (link["currency"], link["prices"][0].amount, cosmetics.parse_payload(link["payload"], A, now=int(time.time())), link["provider_token"]),
          ("XTR", 250, "gems_250", ""))
    check("в описании нет обещаний про фишки и деньги", ("фишк" in link["description"].lower(), "деньг" in link["description"].lower()), (False, False))
    r = client.post("/api/gems/invoice", headers=auth(), json=body)
    check("повтор того же request_id: та же ссылка", (r.status_code, r.json()), (200, {"invoice_url": "https://t.me/$fake-gems-1", "replayed": True}))
    r = client.post("/api/gems/invoice", headers=auth(), json=dict(body, request_id="gems-req-000002"))
    check("другой request_id сразу: 429", r.status_code, 429)
    check("неизвестный пакет", client.post("/api/gems/invoice", headers=auth(), json={"request_id": "gems-req-000003", "pack_code": "gems_1"}).json(), {"detail": "unknown_pack"})
    check("лишние поля: 400", client.post("/api/gems/invoice", headers=auth(), json=dict(body, extra=1)).status_code, 400)
    check("без подписи: 401", client.post("/api/gems/invoice", json=body).status_code, 401)
    client.app.state.application = None
    check("бот не запущен: 503", client.post("/api/gems/invoice", headers=auth(B), json=dict(body, request_id="gems-req-000004")).status_code, 503)

    # ================= покупка предметов за кристаллы =================
    path = new_db()
    in_tx(path, lambda c: db._register_player(c, A, NOW))
    sql(path, "UPDATE players SET balance = 500000 WHERE telegram_id = ?", (A,))
    check("цены предметов в кристаллах (4 старых и части наборов), остальные за фишки", {c: v for c, v in cosmetics.PRICES.items() if v[0] == "gems"}, {"table_blue": ("gems", 150), "crash_neon": ("gems", 100), "back_midnight": ("gems", 100), "keno_hex": ("gems", 75), "draft_crash": ("gems", 100), "draft_mines": ("gems", 100), "draft_table": ("gems", 100), "draft_badge": ("gems", 100), "draft_chip": ("gems", 100), "draft_keno": ("gems", 100), "draft_back": ("gems", 100), "draft_frame": ("gems", 100), "void_table": ("gems", 150), "void_chip": ("gems", 150), "void_badge": ("gems", 150), "chip_patina": ("gems", 400), "back_patina": ("gems", 400), "mine_patina": ("gems", 400), "frame_patina": ("gems", 400), "table_deep": ("gems", 150), "chip_pearl": ("gems", 150), "mine_urchin": ("gems", 150), "keno_bubble": ("gems", 150), "crash_deep": ("gems", 150)})
    raises(cosmetics.InsufficientGems, db.buy_with_gems, A, "gem-buy-000001", "table_blue", now=NOW, db_path=path)
    check("нехватка: ничего не списано, предмета нет", (db.gems_state(A, path)["gems"], sql(path, "SELECT COUNT(*) FROM cosmetic_items")[0][0]), (0, 0))
    db.owner_grant_gems(A, 400, "dev-buy", now=NOW, db_path=path)
    r = db.buy_with_gems(A, "gem-buy-000002", "table_blue", now=NOW + 1, db_path=path)
    check("покупка: кристаллы списаны, фишки целы, предмет выдан (источник gems)", (r["gems"], r["balance"], r["price"], sql(path, "SELECT item_code, source FROM cosmetic_items"), db.gems_state(A, path)["gems"]),
          (250, 500000, {"currency": "gems", "amount": 150}, [("table_blue", "gems")], 250))
    check("журнал: строка списания с причиной и кодом предмета", sql(path, "SELECT delta, reason, ref FROM gems_ledger ORDER BY id"), [(400, "owner_grant", "dev-buy"), (-150, "cosmetic_purchase", "table_blue")])
    check("повтор того же request_id: тот же ответ, второго списания нет", (db.buy_with_gems(A, "gem-buy-000002", "table_blue", now=NOW + 2, db_path=path)["replayed"], db.gems_state(A, path)["gems"]), (True, 250))
    raises(cosmetics.AlreadyOwned, db.buy_with_gems, A, "gem-buy-000003", "table_blue", now=NOW + 3, db_path=path)
    raises(cosmetics.NotForGems, db.buy_with_gems, A, "gem-buy-000004", "chip_ring", now=NOW + 3, db_path=path)           # этот предмет за фишки
    raises(cosmetics.NotForChips, db.buy_with_chips, A, "gem-buy-000005", "crash_neon", now=NOW + 3, db_path=path)       # а этот за кристаллы
    raises(cosmetics.ItemUnavailable, db.buy_with_gems, A, "gem-buy-000006", "back_ember", now=NOW + 3, db_path=path)
    raises(cosmetics.UnknownItem, db.buy_with_gems, A, "gem-buy-000007", "nope", now=NOW + 3, db_path=path)
    raises(cosmetics.UnknownItem, db.buy_with_gems, A, "gem-buy-000008", "test_1star", now=NOW + 3, db_path=path)
    check("отказы ничего не списали, инвариант цел", (db.gems_state(A, path)["gems"], ledger_sum_ok(path)), (250, True))
    r = db.buy_item(A, "gem-buy-000009", "chip_ring", now=NOW + 4, db_path=path)
    check("buy_item: предмет за фишки идёт через фишки, кристаллы не тронуты", (r["balance"], r["gems"], r["price"]["currency"]), (460000, 250, "chips"))
    r = db.buy_item(A, "gem-buy-000010", "crash_neon", now=NOW + 5, db_path=path)
    check("buy_item: предмет за кристаллы идёт через кристаллы", (r["balance"], r["gems"], r["price"]["currency"]), (460000, 150, "gems"))
    # охранник: кристаллы пишет только wallet (покупка предмета идёт через него)
    check("статически чисто и после покупок", balance_guard.violations(), [])
    # API
    client = TestClient(create_app(TOKEN, [], db_path=path))
    r = client.post("/api/cosmetics/buy", headers=auth_h(A), json={"request_id": "gem-api-000001", "item_code": "keno_hex"})
    check("API: покупка за кристаллы", (r.status_code, sorted(r.json()), r.json()["gems"], r.json()["price"]), (200, ["balance", "gems", "item_code", "price", "replayed"], 75, {"currency": "gems", "amount": 75}))
    r = client.post("/api/cosmetics/buy", headers=auth_h(B), json={"request_id": "gem-api-000002", "item_code": "back_midnight"})
    check("API: игрок без кристаллов", (r.status_code, r.json()), (409, {"detail": "insufficient_gems"}))
    r = client.post("/api/cosmetics/invoice", headers=auth_h(A), json={"request_id": "gem-api-000003", "item_code": "back_midnight"})
    check("API: счёт на предмет за Stars больше не создаётся", (r.status_code, r.json()), (410, {"detail": "gone"}))
    cat = client.get("/api/cosmetics/catalog", headers=auth_h(A)).json()
    check("каталог: валюты цен только gems и chips", sorted({i["price"]["currency"] for i in cat["items"] if i["price"]}), ["chips", "gems"])

    # ================= /givegems: кристаллы владельцу самому себе =================
    path = new_db()
    fake = FakeBot()
    in_tx(path, lambda c: db._register_player(c, OWNER, NOW))
    in_tx(path, lambda c: db._register_player(c, A, NOW))
    out = command(bot.givegems, fake, ["500"], uid=OWNER)
    check("владелец: начислено 500, баланс 500", (out, db.gems_state(OWNER, path)["gems"]), (["Начислено кристаллов: 500. Баланс кристаллов: 500"], 500))
    check("в журнале причина owner_grant", sql(path, "SELECT delta, reason FROM gems_ledger WHERE telegram_id = ?", (OWNER,)), [(500, "owner_grant")])
    out = command(bot.givegems, fake, ["500"], uid=OWNER)
    check("повторная команда начисляет ещё раз (каждая команда отдельное начисление)", db.gems_state(OWNER, path)["gems"], 1000)
    check("не владелец: молчание и ничего не начислено", (command(bot.givegems, fake, ["500"], uid=A), db.gems_state(A, path)["gems"]), ([], 0))
    check("в группе молчание", (lambda u: (run(bot.givegems(u, ctx(fake, ["500"]))), u.replies)[1])(FakeUpdate("group", user_id=OWNER)), [])
    for bad in ([], ["0"], ["-5"], ["abc"], ["500", "x"], [str(economy_config.GEMS_GIVE_MAX + 1)], ["1" * 10]):
        out = command(bot.givegems, fake, bad, uid=OWNER)
        check("формат %r: подсказка, ничего не начислено" % (bad,), (out[0].startswith("Формат: /givegems"), db.gems_state(OWNER, path)["gems"]), (True, 1000))
    path2 = new_db()
    out = command(bot.givegems, fake, ["5"], uid=OWNER)
    check("владельца нет в базе: просьба открыть игру, профиль не создан", (out, sql(path2, "SELECT COUNT(*) FROM players")[0][0]), (["Вас ещё нет в базе: откройте игру один раз и повторите команду"], 0))
    check("статически чисто и после команды", balance_guard.violations(), [])

    # ================= /mydata, /deletemydata, очистка =================
    path = new_db()
    fake = FakeBot()
    paid(fake, A, cosmetics.make_payload(A, "gems_250", int(time.time())), "charge-dat-0001", 250)
    export = db.get_player_export(A, db_path=path)
    check("выгрузка: кристаллы без идентификатора платежа", (export["gems"]["balance"], export["gems"]["ledger"][0]["delta"], export["gems"]["purchases"][0]["pack"], "charge" in json.dumps(export["gems"])),
          (275, 275, "gems_250", False))
    counts = db.delete_player_data(A, db_path=path)
    check("удаление: журнал и баланс кристаллов удалены, запись об оплате осталась", (sql(path, "SELECT COUNT(*) FROM gems_ledger")[0][0], sql(path, "SELECT COUNT(*) FROM gem_balances")[0][0],
                                                                                     sql(path, "SELECT COUNT(*) FROM gem_purchases")[0][0]), (0, 0, 1))
    sql(path, "UPDATE gem_purchases SET created_at = ?", (NOW,))
    db.purge_old_data(now=NOW + (cosmetics.PURCHASE_RETENTION_DAYS + 1) * 86400, db_path=path)
    check("очистка: оплата старше срока хранения удалена", sql(path, "SELECT COUNT(*) FROM gem_purchases")[0][0], 0)

    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

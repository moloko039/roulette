import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import glob
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from types import SimpleNamespace
from unittest import mock

import backup
import bot
import tg.common
import tg.owner
import tg.payments
import tg.user
import db
import wallet
from roulette import MAX_SAFE_INT
from stubs import FakeUpdate, StubBot

OWNER = 424242421
STRANGER = 777000111
NOW = 1_760_000_000

# тест не зависит от окружения и bot/.env: переменные очищаются, в конце возвращаются
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "OWNER_CHAT_ID", "BACKUP_DIR", "BACKUP_ENABLED", "BACKUP_KEEP",
             "BACKUP_INTERVAL_HOURS", "BACKUP_PUBLIC_KEY", "GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT", "PLAY_MODE")
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
root = logging.getLogger()
old_level = root.level
root.setLevel(logging.DEBUG)
root.addHandler(cap)

tmp = tempfile.mkdtemp()
counter = [0]


def new_db():
    counter[0] += 1
    folder = os.path.join(tmp, "g%d" % counter[0])
    os.makedirs(folder)
    path = os.path.join(folder, "players.db")
    db.init_db(path)
    os.environ["DB_PATH"] = path
    os.environ["BACKUP_DIR"] = os.path.join(folder, "backups")
    return path


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def add_player(path, uid, balance, total=0, xp=0):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
              "VALUES (?, ?, 100, ?, ?, ?, ?, 0, 0)", (uid, balance, NOW, NOW, total, xp))


def balances(path):
    return dict(sql(path, "SELECT telegram_id, balance FROM players"))


def say(args, uid=OWNER, chat="private", store=None):
    """Одна команда /grantall; возвращает тексты ответов бота."""
    update = FakeUpdate(chat, user_id=uid)
    data = store if store is not None else {}
    ctx = SimpleNamespace(args=args, bot=StubBot(), application=SimpleNamespace(bot_data=data))

    async def run():
        await bot.grantall(update, ctx)
        if data.get("grant_announce_task") is not None:   # фоновое объявление (объявления Telegram здесь заглушка)
            await data["grant_announce_task"]
    asyncio.run(run())
    return [r["text"] for r in update.replies]


try:
    os.environ["OWNER_CHAT_ID"] = str(OWNER)

    # ================= только владелец =================
    path = new_db()
    add_player(path, 1, 1000)
    store = {}
    check("чужой в личке: нет ответа", say(["10000", "oct4"], uid=STRANGER, store=store), [])
    check("чужой confirm: нет ответа", say(["confirm", "oct4"], uid=STRANGER, store=store), [])
    check("владелец в группе: нет ответа", say(["10000", "oct4"], chat="supergroup", store=store), [])
    check("канал: нет ответа", say(["10000", "oct4"], chat="channel", store=store), [])
    check("ничего не запомнено", store, {})
    os.environ.pop("OWNER_CHAT_ID")
    check("без OWNER_CHAT_ID: нет ответа", say(["10000", "oct4"], store=store), [])
    os.environ["OWNER_CHAT_ID"] = str(OWNER)
    check("в базе ничего не изменилось", (balances(path), sql(path, "SELECT COUNT(*) FROM admin_grants")[0][0]), ({1: 1000}, 0))
    # в меню и справке команды нет
    menu = [c.command for c in bot.PRIVATE_COMMANDS + bot.GROUP_COMMANDS]
    assert "grantall" not in menu, menu
    assert "grantall" not in bot.PRIVATE_HELP and "grantall" not in bot.GROUP_HELP
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = bot.build_application("123456:TEST-TOKEN-not-real", use_updater=False)
    names = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
    assert "grantall" in names and "backupnow" in names, names

    # ================= проверка суммы и id =================
    for args in ([], ["10000"], ["10000", "a", "b"], ["abc", "oct4"], ["0", "oct4"], ["1000001", "oct4"], ["-5", "oct4"],
                 ["10.5", "oct4"], ["10000", "bad id"], ["10000", "под"], ["10000", "x" * 33], ["10000", "a_b"], ["99999999", "oct4"]):
        out = say(args, store=store)
        assert len(out) == 1 and ("Формат" in out[0] or "Сумма:" in out[0]), (args, out)
    check("ничего не запомнено после ошибок", store, {})
    for amount, gid in ((0, "a"), (1_000_001, "a"), (True, "a"), (1.5, "a"), ("5", "a"), (5, ""), (5, "x" * 33), (5, "a b"), (5, None), (5, "я")):
        raises(ValueError, db.validate_grant, amount, gid)
    db.validate_grant(1, "a")
    db.validate_grant(1_000_000, "A-b-9" + "x" * 27)
    raises(ValueError, db.grant_all, 0, "oct4", now=NOW, db_path=path)
    raises(ValueError, db.grant_preview, 5, "bad id", db_path=path)

    # ================= шаг 1: сколько игроков и фишек =================
    path = new_db()
    add_player(path, 1, 1000)
    add_player(path, 2, 0)
    add_player(path, 3, MAX_SAFE_INT - 4000)       # поместится только 4000
    add_player(path, 4, MAX_SAFE_INT)              # на потолке: ничего
    store = {}
    out = say(["10000", "oct4"], store=store)
    check("шаг 1: игроков 3 (на потолке не считается), всего 10000 + 10000 + 4000", len(out), 1)
    assert "игроков 3" in out[0] and "всего будет выдано 24000" in out[0] and "/grantall confirm oct4" in out[0] and "5 минут" in out[0], out
    check("шаг 1 ничего не меняет", (balances(path)[1], balances(path)[3], sql(path, "SELECT COUNT(*) FROM admin_grants")[0][0]), (1000, MAX_SAFE_INT - 4000, 0))
    check("ожидает подтверждения", (store["grant_pending"]["id"], store["grant_pending"]["amount"]), ("oct4", 10000))
    check("grant_preview", db.grant_preview(10000, "oct4", db_path=path), (3, 24000))

    # ================= шаг 2: подтверждение =================
    xp_before = sql(path, "SELECT telegram_id, total_staked, xp, rate, last_accrual, income_level, storage_level FROM players ORDER BY telegram_id")
    out = say(["confirm", "oct4"], store=store)
    check("итог владельцу", out, ["Начисление выполнено: получили игроков 3, выдано всего 24000"])
    check("балансы", balances(path), {1: 11000, 2: 10000, 3: MAX_SAFE_INT, 4: MAX_SAFE_INT})
    check("total_staked, XP, ставка дохода и время начисления не тронуты",
          sql(path, "SELECT telegram_id, total_staked, xp, rate, last_accrual, income_level, storage_level FROM players ORDER BY telegram_id"), xp_before)
    check("одна итоговая строка", sql(path, "SELECT grant_id, amount, created_at > 0, players, total_given FROM admin_grants"), [("oct4", 10000, 1, 3, 24000)])
    check("в таблице нет списков игроков", [r[1] for r in sql(path, "PRAGMA table_info(admin_grants)")], ["grant_id", "amount", "created_at", "players", "total_given"])
    check("подтверждение одноразовое", store.get("grant_pending"), None)
    out = say(["confirm", "oct4"], store=store)
    assert "Нет начисления" in out[0], out
    check("повторный confirm ничего не начислил", balances(path)[1], 11000)
    # повтор с тем же id запрещён
    out = say(["5", "oct4"], store=store)
    check("повтор id", out, ["Начисление с таким id уже было, ничего не изменено"])
    check("повтор не оставил ожидания", store.get("grant_pending"), None)
    raises(db.GrantExists, db.grant_all, 5, "oct4", now=NOW, db_path=path)
    check("после попыток баланс прежний", balances(path)[1], 11000)
    # другой id работает
    check("другой id", say(["7", "oct5"], store=store)[0].startswith("Получат фишки: игроков 2"), True)    # на потолке уже двое
    say(["confirm", "oct5"], store=store)
    check("второе начисление", balances(path)[1], 11007)

    # ================= неверный id и срок =================
    path = new_db()
    add_player(path, 1, 1000)
    store = {}
    say(["100", "oct4"], store=store)
    out = say(["confirm", "other"], store=store)
    assert "Неверный id" in out[0], out
    check("неверный id сбрасывает ожидание и ничего не начисляет", (balances(path), store.get("grant_pending")), ({1: 1000}, None))
    say(["100", "oct4"], store=store)
    t0 = store["grant_pending"]["at"]
    with mock.patch.object(tg.common, "_wall", return_value=t0 + 301):
        out = say(["confirm", "oct4"], store=store)
    assert "время вышло" in out[0], out
    check("через 5 минут 1 секунду: нет начисления", (balances(path), sql(path, "SELECT COUNT(*) FROM admin_grants")[0][0]), ({1: 1000}, 0))
    say(["100", "oct4"], store=store)
    t0 = store["grant_pending"]["at"]
    with mock.patch.object(tg.common, "_wall", return_value=t0 + 300):
        out = say(["confirm", "oct4"], store=store)
    check("ровно через 5 минут ещё действует", (out[0].startswith("Начисление выполнено"), balances(path)), (True, {1: 1100}))
    # новый /grantall заменяет ожидающее
    path = new_db()
    add_player(path, 1, 1000)
    store = {}
    say(["100", "aaa"], store=store)
    say(["200", "bbb"], store=store)
    assert "Неверный id" in say(["confirm", "aaa"], store=store)[0]

    # ================= копия делается до начисления =================
    path = new_db()
    add_player(path, 1, 1000)
    add_player(path, 2, 2000)
    seen = {}
    real_snapshot = backup.create_snapshot

    def spy_snapshot(db_path, backup_dir, now=None, keep=None):
        seen["balances_at_snapshot"] = balances(path)
        seen["grants_at_snapshot"] = sql(path, "SELECT COUNT(*) FROM admin_grants")[0][0]
        return real_snapshot(db_path, backup_dir, now, keep) if keep else real_snapshot(db_path, backup_dir, now)

    store = {}
    say(["500", "snap1"], store=store)
    with mock.patch.object(backup, "create_snapshot", side_effect=spy_snapshot):
        out = say(["confirm", "snap1"], store=store)
    check("при копировании баланс прежний", (seen["balances_at_snapshot"], seen["grants_at_snapshot"]), ({1: 1000, 2: 2000}, 0))
    check("начисление выполнено после копии", (out[0].startswith("Начисление выполнено"), balances(path)), (True, {1: 1500, 2: 2500}))
    files = glob.glob(os.path.join(os.environ["BACKUP_DIR"], "players-*.db"))
    check("файл копии создан", len(files), 1)
    check("копия содержит балансы ДО начисления", dict(sql(files[0], "SELECT telegram_id, balance FROM players")), {1: 1000, 2: 2000})
    check("копия проверена (без таблицы начислений с этим id)", sql(files[0], "SELECT COUNT(*) FROM admin_grants")[0][0], 0)
    # копия не создалась: начисления нет
    path = new_db()
    add_player(path, 1, 1000)
    store = {}
    say(["500", "nosnap"], store=store)
    with mock.patch.object(backup, "create_snapshot", return_value=None):
        out = say(["confirm", "nosnap"], store=store)
    check("без копии не начисляется", (out, balances(path), sql(path, "SELECT COUNT(*) FROM admin_grants")[0][0]),
          (["Резервная копия не создана, начисление не выполнено"], {1: 1000}, 0))
    check("ожидание после отказа сброшено", store.get("grant_pending"), None)

    # ================= атомарность =================
    path = new_db()
    for uid in range(1, 6):
        add_player(path, uid, 1000 * uid)
    store = {}
    say(["100", "atom"], store=store)
    real_credit = wallet.credit
    n = [0]

    def flaky(conn, uid, amount):
        n[0] += 1
        if n[0] == 3:
            raise RuntimeError("boom")
        return real_credit(conn, uid, amount)

    with mock.patch.object(wallet, "credit", side_effect=flaky):
        out = say(["confirm", "atom"], store=store)
    check("ошибка: сообщение владельцу", out, ["Не удалось выполнить начисление, ничего не изменено (подробности в логах сервиса)"])
    check("ошибка: не получил никто и записи нет", (balances(path), sql(path, "SELECT COUNT(*) FROM admin_grants")[0][0]),
          ({1: 1000, 2: 2000, 3: 3000, 4: 4000, 5: 5000}, 0))
    n[0] = 0
    with mock.patch.object(wallet, "credit", side_effect=flaky):
        raises(RuntimeError, db.grant_all, 100, "atom2", now=NOW, db_path=path)
    check("прямой вызов: тоже откат", (balances(path)[1], sql(path, "SELECT COUNT(*) FROM admin_grants")[0][0]), (1000, 0))
    out = say(["100", "atom"], store={})
    assert out[0].startswith("Получат фишки"), out        # id не сгорел после ошибки

    # ================= новые и удалённые игроки =================
    path = new_db()
    add_player(path, 1, 1000)
    add_player(path, 2, 1000)
    store = {}
    say(["100", "newp"], store=store)
    db.get_player(99, now=NOW, db_path=path)               # новый игрок регистрируется (стартовые фишки), но до начисления
    sql(path, "DELETE FROM players WHERE telegram_id = 99")
    db.delete_player_data(2, db_path=path, now=NOW)        # удалил данные: в базе его нет
    out = say(["confirm", "newp"], store=store)
    check("получил один игрок", out, ["Начисление выполнено: получили игроков 1, выдано всего 100"])
    db.get_player(100, now=NOW, db_path=path)
    check("игрок после начисления его не получает", sql(path, "SELECT balance FROM players WHERE telegram_id = 100")[0][0], db.START_BALANCE)
    check("удалившего нет в базе", sql(path, "SELECT COUNT(*) FROM players WHERE telegram_id = 2")[0][0], 0)
    # пустая база
    path = new_db()
    check("нет игроков", say(["100", "empty"], store={}), ["Некому начислять: в базе нет игроков с местом под потолок баланса"])

    # ================= потолок баланса =================
    path = new_db()
    add_player(path, 1, MAX_SAFE_INT - 1)
    add_player(path, 2, MAX_SAFE_INT - 1_000_000)
    check("прямое начисление у потолка", db.grant_all(1_000_000, "cap", now=NOW, db_path=path), (2, 1 + 1_000_000))
    check("балансы у потолка", balances(path), {1: MAX_SAFE_INT, 2: MAX_SAFE_INT})
    path = new_db()
    add_player(path, 1, MAX_SAFE_INT)
    check("все на потолке: 0 получателей, строка записана", (db.grant_all(5, "full", now=NOW, db_path=path), balances(path)), ((0, 0), {1: MAX_SAFE_INT}))

    # ================= в логах нет id, балансов, имён и сумм =================
    for secret in (str(OWNER), str(STRANGER), "oct4", "1000000", str(MAX_SAFE_INT)):
        for line in cap.lines:
            assert secret not in line, "секрет в логе: " + line[:90]
    assert any("Начисление выполнено: игроков=" in line for line in cap.lines), "нет строки «начисление выполнено»"

    # ================= политика: в admin_grants нет персональных данных =================
    privacy = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "privacy.html"), encoding="utf-8").read()
    assert "admin_grants" not in privacy
finally:
    mock.patch.stopall()
    root.removeHandler(cap)
    root.setLevel(old_level)
    for _k, _v in _saved_env.items():
        os.environ.pop(_k, None)
        if _v is not None:
            os.environ[_k] = _v
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")

import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import logging
import os
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace
from unittest import mock

import bot
import db
import wallet
from roulette import MAX_SAFE_INT
from stubs import FakeUpdate

OWNER = 424242421
STRANGER = 777000111
NOW = 1_760_000_000

# тест не зависит от окружения и bot/.env: переменные очищаются, в конце возвращаются
_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "OWNER_CHAT_ID", "GAME_LINK", "PRIVACY_URL", "DEVELOPER_CONTACT", "PLAY_MODE")
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
path = os.path.join(tmp, "players.db")
db.init_db(path)
os.environ["DB_PATH"] = path


def sql(query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def add_player(uid, balance, total=0, xp=0):
    sql("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked, xp, income_level, storage_level) "
        "VALUES (?, ?, 100, ?, ?, ?, ?, 0, 0)", (uid, balance, NOW, NOW, total, xp))


def row(uid):
    return sql("SELECT balance, total_staked, xp, rate, last_accrual, income_level, storage_level FROM players WHERE telegram_id = ?", (uid,))[0]


def say(args, uid=OWNER, chat="private"):
    update = FakeUpdate(chat, user_id=uid)
    asyncio.run(bot.give(update, SimpleNamespace(args=args, application=SimpleNamespace(bot_data={}))))
    return [r["text"] for r in update.replies]


try:
    os.environ["OWNER_CHAT_ID"] = str(OWNER)

    # ================= только владелец, только в личке =================
    add_player(OWNER, 1000, total=500, xp=70)
    add_player(STRANGER, 1000)
    check("чужой: нет ответа", say(["100"], uid=STRANGER), [])
    check("чужой с суммой владельца: нет ответа", say(["100"], uid=STRANGER, chat="private"), [])
    check("владелец в группе: нет ответа", say(["100"], chat="supergroup"), [])
    check("владелец в группе (group): нет ответа", say(["100"], chat="group"), [])
    check("канал: нет ответа", say(["100"], chat="channel"), [])
    os.environ.pop("OWNER_CHAT_ID")
    check("без OWNER_CHAT_ID: нет ответа", say(["100"]), [])
    os.environ["OWNER_CHAT_ID"] = str(OWNER)
    check("ничего не начислено", (row(OWNER)[0], row(STRANGER)[0]), (1000, 1000))
    menu = [c.command for c in bot.PRIVATE_COMMANDS + bot.GROUP_COMMANDS]
    assert "give" not in menu, menu
    assert "/give" not in bot.PRIVATE_HELP and "/give" not in bot.GROUP_HELP
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = bot.build_application("123456:TEST-TOKEN-not-real", use_updater=False)
    names = {c for h in app.handlers[0] for c in getattr(h, "commands", ())}
    assert "give" in names, names

    # ================= проверка суммы =================
    for args in ([], ["10", "20"], ["abc"], ["0"], ["-5"], ["10.5"], ["100000001"], ["1" * 10], ["1e3"], ["+5"], [""]):
        out = say(args)
        assert len(out) == 1 and out[0].startswith("Формат"), (args, out)
    check("после ошибок баланс прежний", row(OWNER)[0], 1000)
    for bad in (0, -1, 100_000_001, 1.5, "5", True, None):
        raises(ValueError, db.give_owner, OWNER, bad, db_path=path)
    check("прямые ошибки ничего не изменили", row(OWNER)[0], 1000)

    # ================= начисление владельцу =================
    before = row(OWNER)
    out = say(["250"])
    check("ответ", out, ["Начислено 250. Баланс: 1250"])
    after = row(OWNER)
    check("баланс вырос ровно на сумму", after[0], before[0] + 250)
    check("total_staked, XP, ставка дохода, время начисления и уровни не тронуты", after[1:], before[1:])
    check("другой игрок не получил", row(STRANGER)[0], 1000)
    check("граница суммы: 1 и 100000000", (say(["1"]), say(["100000000"])), (["Начислено 1. Баланс: 1251"], ["Начислено 100000000. Баланс: 100001251"]))
    check("новых таблиц нет", sorted(r[0] for r in sql("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '%give%'")), [])
    # начисляется только отправителю: id в аргументах игнорируется
    check("лишний аргумент (id) не принимается", say(["100", str(STRANGER)])[0].startswith("Формат"), True)
    check("чужой всё ещё без изменений", row(STRANGER)[0], 1000)
    # кошелёк используется
    seen = []
    real_credit = wallet.credit
    with mock.patch.object(wallet, "credit", side_effect=lambda *a: (seen.append(a[1:]), real_credit(*a))[1]):
        say(["5"])
    check("через wallet.credit именно владельцу", seen, [(OWNER, 5)])

    # ================= потолок баланса =================
    sql("UPDATE players SET balance = ? WHERE telegram_id = ?", (MAX_SAFE_INT - 30, OWNER))
    out = say(["100"])
    check("у потолка: зачислено сколько помещается", out, ["Баланс у потолка: начислено 30 из 100. Баланс: %d" % MAX_SAFE_INT])
    check("баланс на потолке", row(OWNER)[0], MAX_SAFE_INT)
    out = say(["100"])
    check("уже на потолке: начислено 0", out, ["Баланс у потолка: начислено 0 из 100. Баланс: %d" % MAX_SAFE_INT])
    check("баланс не вышел за потолок", row(OWNER)[0], MAX_SAFE_INT)

    # ================= несуществующий игрок =================
    sql("DELETE FROM players WHERE telegram_id = ?", (OWNER,))
    out = say(["100"])
    check("нет игрока: подсказка", out, ["Вас ещё нет в базе: откройте игру один раз и повторите команду"])
    check("игрок из команды не создан", sql("SELECT COUNT(*) FROM players WHERE telegram_id = ?", (OWNER,))[0][0], 0)
    raises(db.PlayerMissing, db.give_owner, OWNER, 5, db_path=path)

    # ================= ошибка кошелька откатывает =================
    add_player(OWNER, 1000)
    with mock.patch.object(wallet, "credit", side_effect=RuntimeError("boom")):
        out = say(["100"])
    check("сбой: сообщение и откат", (out, row(OWNER)[0]), (["Не удалось выполнить начисление (подробности в логах сервиса)"], 1000))

    # ================= в логах нет суммы, баланса и идентификаторов =================
    assert any("Начисление владельцу выполнено" in line for line in cap.lines), cap.lines
    for secret in (str(OWNER), str(STRANGER), "250", "1250", "100000000", str(MAX_SAFE_INT)):
        for line in cap.lines:
            assert secret not in line, "секрет в логе: " + line[:90]
    # клиент и политика не менялись этой задачей
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert "/give" not in open(os.path.join(root_dir, "privacy.html"), encoding="utf-8").read()
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

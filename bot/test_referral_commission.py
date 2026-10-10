"""Рефералка (решение владельца 2026-10-09): процент пригласившему от выигрыша казино (30 % чистого проигрыша приглашённого, 90 дней после квалификации,
не больше 300 000 фишек на одного приглашённого) и награда основателю беседы (бот добавлен в группу, беседа живая)."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import shutil
import sqlite3
import tempfile
import time
from unittest import mock

import crash_testutil as ct
import db
import cosmetics
import economy_config
import levels
import wallet
from roulette import MAX_SAFE_INT

_saved_secret = os.environ.pop("TOMBSTONE_SECRET", None)
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
NOW = 1_760_000_000
DAY = 86400
I, V, V2, F = 910001, 910002, 910003, 910004     # пригласивший, приглашённые, основатель беседы
_n = [0]


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


tmp = tempfile.mkdtemp()


def new_db():
    _n[0] += 1
    path = os.path.join(tmp, "c%d.db" % _n[0])
    db.init_db(path)
    return path


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def add_player(path, uid, balance=1_000_000, xp=0):
    sql(path, "INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, xp) VALUES (?, ?, 100, ?, ?, ?)", (uid, balance, NOW + 10 * DAY, NOW - 100 * DAY, xp))


def balance(path, uid):
    return sql(path, "SELECT balance FROM players WHERE telegram_id = ?", (uid,))[0][0]


def link(path, invitee=V, referrer=I, qualified_at=NOW):
    sql(path, "INSERT INTO referrals (invitee_id, referrer_id, created_at, qualified_at) VALUES (?, ?, ?, ?)", (invitee, referrer, NOW - DAY, qualified_at))


def rid():
    _n[0] += 1
    return "rc-req-%08d" % _n[0]


def spin(path, uid, color, amount, number, now):
    """Ставка на цвет; выпадает number (17 чёрное)."""
    db.spin_roulette(uid, rid(), [{"type": color, "value": None, "amount": amount}], now=now, db_path=path, rng=lambda n: number)


def state(path, invitee=V):
    return sql(path, "SELECT house_net, house_peak, commission_paid FROM referrals WHERE invitee_id = ?", (invitee,))[0]


try:
    # ================= процент от чистого проигрыша, нарастающим итогом по максимуму =================
    path = new_db()
    add_player(path, I, balance=5000)
    add_player(path, V)
    link(path)
    spin(path, V, "red", 1000, 17, NOW + 10)                      # проигрыш 1000: пригласивший получает 30 %
    check("проигрыш 1000: 300 фишек", (balance(path, I), state(path)), (5300, (1000, 1000, 300)))
    spin(path, V, "black", 500, 17, NOW + 20)                     # выигрыш 500 (выплата 1000): чистый проигрыш падает, платить нечего, отобрать нельзя
    check("выигрыш приглашённого: ничего не меняется у пригласившего", (balance(path, I), state(path)), (5300, (500, 1000, 300)))
    spin(path, V, "red", 400, 17, NOW + 30)                       # проигрыш 400: итог 900, не выше прежнего максимума 1000: не платим второй раз
    check("отыгранный проигрыш повторно не оплачивается", (balance(path, I), state(path)), (5300, (900, 1000, 300)))
    spin(path, V, "red", 600, 17, NOW + 40)                       # итог 1500, максимум 1500: 30 % = 450, к выплате 150
    check("новый максимум: доплата", (balance(path, I), state(path)), (5450, (1500, 1500, 450)))
    check("фишки приглашённого списаны и выплачены обычно, процент не из его кармана", balance(path, V), 1_000_000 - 1000 + 500 - 400 - 600)

    # ================= потолок на приглашённого =================
    sql(path, "UPDATE referrals SET house_net = ?, house_peak = ?, commission_paid = ? WHERE invitee_id = ?", (2_000_000, 2_000_000, economy_config.REFERRAL_COMMISSION_CAP - 10, V))
    before = balance(path, I)
    spin(path, V, "red", 1000, 17, NOW + 50)
    check("потолок: доплата только до 300 000", (balance(path, I) - before, state(path)[2]), (10, economy_config.REFERRAL_COMMISSION_CAP))
    spin(path, V, "red", 1000, 17, NOW + 60)
    check("после потолка ничего", (balance(path, I) - before, state(path)[2]), (10, economy_config.REFERRAL_COMMISSION_CAP))

    # ================= срок 90 дней, квалификация, удалённый пригласивший =================
    path = new_db()
    add_player(path, I, balance=5000)
    add_player(path, V)
    link(path, qualified_at=NOW)
    spin(path, V, "red", 1000, 17, NOW + economy_config.REFERRAL_COMMISSION_DAYS * DAY - 1)
    check("последняя секунда срока: платим", balance(path, I), 5300)
    spin(path, V, "red", 1000, 17, NOW + economy_config.REFERRAL_COMMISSION_DAYS * DAY)
    check("после срока не платим и не копим", (balance(path, I), state(path)), (5300, (1000, 1000, 300)))

    path = new_db()
    add_player(path, I, balance=5000)
    add_player(path, V)
    link(path, qualified_at=None)
    spin(path, V, "red", 1000, 17, NOW + 10)
    check("без квалификации процента нет", (balance(path, I), state(path)), (5000, (0, 0, 0)))

    path = new_db()
    add_player(path, V)
    link(path, referrer=0)           # пригласивший удалил данные
    spin(path, V, "red", 1000, 17, NOW + 10)
    check("пригласивший удалён: раунд идёт, платить некому", balance(path, V), 1_000_000 - 1000)

    path = new_db()
    add_player(path, I, balance=MAX_SAFE_INT - 100)
    add_player(path, V)
    link(path)
    spin(path, V, "red", 1000, 17, NOW + 10)
    check("у пригласившего баланс у потолка: зачислено сколько влезает, раунд не ломается", (balance(path, I), state(path)[2]), (MAX_SAFE_INT, 300))

    # ================= исходы всех игр доходят до процента: мины, живой краш, кено =================
    path = new_db()
    add_player(path, I, balance=5000)
    add_player(path, V)
    link(path)

    class Cells:
        def sample(self, population, k):
            return [0, 1, 2]

    db.mines_start(V, rid(), 1000, 3, now=NOW + 10, db_path=path, rng=Cells())
    db.mines_reveal(V, rid(), 0, now=NOW + 11, db_path=path)           # мина: проигрыш 1000
    check("мины: проигрыш считается", (balance(path, I), state(path)[0]), (5300, 1000))
    T = (NOW + 100) * 1000
    rnd = ct.open_round(path, T, 150)
    ct.bet(path, V, T, 1000)                                            # живой краш, крах ×1.50, ручной вывод не нажат: проигрыш 1000
    ct.end_round(path, rnd)
    check("живой краш: проигрыш считается", (balance(path, I), state(path)[0]), (5600, 2000))
    db.play_keno(V, rid(), 100, [1, 2, 3], now=NOW + 300, db_path=path, rng=mock.Mock(sample=lambda population, k: [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]))
    check("кено: проигрыш считается", (balance(path, I), state(path)[0]), (5630, 2100))

    # ================= основатель беседы =================
    path = new_db()
    lvl3 = levels.threshold(economy_config.FOUNDER_PLAYER_LEVEL)
    add_player(path, F, balance=1000, xp=lvl3)
    others = [920001 + i for i in range(6)]
    for uid in others:
        add_player(path, uid, xp=lvl3)
    add_player(path, 920099, xp=0)                                       # уровень 1: не считается

    def member(chat, uid, seen):
        sql(path, "INSERT OR REPLACE INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES (?, ?, 'x', ?, ?)", (chat, uid, seen, seen))

    db.record_founder(-1001, F, NOW, db_path=path)
    db.record_founder(-1001, 920001, NOW + 5, db_path=path)               # повторное событие по той же группе основателя не меняет
    check("основатель записан один раз", sql(path, "SELECT chat_id, founder_id, chat_instance, rewarded_at FROM chat_founders"), [(-1001, F, None, None)])
    member("chatX", F, NOW)
    for uid in others[:3]:
        member("chatX", uid, NOW + 10)
    check("чужой вход из группы беседу не привязывает", db.founder_progress("chatX", others[0], now=NOW + 20, db_path=path), None)
    check("беседа не привязана", sql(path, "SELECT chat_instance FROM chat_founders")[0][0], None)
    check("основатель открыл игру из группы: привязка, беседа ещё не живая (4 игрока уровня 3)", db.founder_progress("chatX", F, now=NOW + 30, db_path=path), None)
    check("беседа привязана", sql(path, "SELECT chat_instance FROM chat_founders")[0][0], "chatX")
    member("chatX", 920099, NOW + 31)                                      # игрок уровня 1 живой беседы не делает
    check("игрок уровня 1 не считается", db.founder_progress("chatX", 920099, now=NOW + 32, db_path=path), None)
    member("chatX", others[3], NOW + 33)
    got = db.founder_progress("chatX", others[3], now=NOW + 34, db_path=path)
    check("пять игроков уровня 3: награда", got, {"chips": economy_config.FOUNDER_CHIPS, "gems": economy_config.FOUNDER_GEMS})
    check("основатель получил фишки и кристаллы (журнал founder_reward)", (balance(path, F), sql(path, "SELECT delta, reason, ref FROM gems_ledger WHERE telegram_id = ?", (F,))),
          (1000 + economy_config.FOUNDER_CHIPS, [(economy_config.FOUNDER_GEMS, "founder_reward", "chat--1001")]))
    check("повторно не выдаётся", (db.founder_progress("chatX", F, now=NOW + 40, db_path=path), balance(path, F)), (None, 1000 + economy_config.FOUNDER_CHIPS))

    # неактивные участники (давно не заходили) беседу живой не делают; привязка только в первые FOUNDER_LINK_DAYS дней
    db.record_founder(-1002, F, NOW, db_path=path)
    for uid in others[:5]:
        member("chatY", uid, NOW - 8 * DAY)
    member("chatY", F, NOW + 100)
    check("участники старше 7 дней не считаются", db.founder_progress("chatY", F, now=NOW + 100, db_path=path), None)
    db.record_founder(-1003, F, NOW - economy_config.FOUNDER_LINK_DAYS * DAY - 10, db_path=path)
    member("chatZ", F, NOW + 200)
    db.founder_progress("chatZ", F, now=NOW + 200, db_path=path)
    check("слишком старую группу не привязываем", sql(path, "SELECT chat_instance FROM chat_founders WHERE chat_id = -1003")[0][0], None)

    # потолок кристаллов рефералки: основатель, уже набравший 100 за месяц, фишки получает, кристаллов нет
    path = new_db()
    add_player(path, F, balance=0, xp=lvl3)
    for uid in others[:5]:
        add_player(path, uid, xp=lvl3)
        sql(path, "INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES ('chatQ', ?, 'x', ?, ?)", (uid, NOW, NOW))
    sql(path, "INSERT INTO gems_ledger (telegram_id, delta, reason, ref, created_at) VALUES (?, ?, 'referral_reward', 'pre', ?)", (F, economy_config.REFERRAL_GEMS_MONTHLY_CAP, NOW))
    db.record_founder(-1004, F, NOW, db_path=path)
    got = db.founder_progress("chatQ", F, now=NOW + 5, db_path=path)
    check("кристаллов рефералки в месяц не больше потолка", got, {"chips": economy_config.FOUNDER_CHIPS, "gems": 0})

    # ================= удаление данных, выгрузка, очистка =================
    path = new_db()
    add_player(path, F)
    db.record_founder(-1005, F, NOW, db_path=path)
    db.record_founder(-1006, 920001, NOW, db_path=path)
    check("выгрузка: основатель", db.get_player_export(F, db_path=path)["referral"]["founded_chats"], 1)
    db.delete_player_data(F, db_path=path)
    check("удаление данных убирает записи основателя, чужие остаются", sql(path, "SELECT founder_id FROM chat_founders"), [(920001,)])
    db.purge_old_data(now=NOW + 91 * DAY, db_path=path)
    check("очистка убирает записи старше 90 дней", sql(path, "SELECT COUNT(*) FROM chat_founders")[0][0], 0)

    # ================= миграция старой таблицы referrals (без полей процента) =================
    legacy = os.path.join(tmp, "legacy.db")
    conn = sqlite3.connect(legacy)
    conn.execute("CREATE TABLE referrals (invitee_id INTEGER PRIMARY KEY, referrer_id INTEGER NOT NULL, created_at INTEGER NOT NULL, qualified_at INTEGER)")
    conn.execute("INSERT INTO referrals VALUES (1, 2, 3, 4)")
    conn.commit()
    conn.close()
    db.init_db(legacy)
    db.init_db(legacy)
    check("миграция добавила поля, строки целы", sql(legacy, "SELECT invitee_id, qualified_at, house_net, house_peak, commission_paid FROM referrals"), [(1, 4, 0, 0, 0)])

    # ================= вехи 3 / 10 / 30 квалифицированных приглашённых: косметика один раз, без денег =================
    path = new_db()
    add_player(path, I, balance=0)
    lvl3 = levels.threshold(economy_config.REFERRAL_QUALIFY_LEVEL)

    def qualify(n):
        uid = 930000 + n
        add_player(path, uid, xp=lvl3)
        sql(path, "INSERT INTO referrals (invitee_id, referrer_id, created_at) VALUES (?, ?, ?)", (uid, I, NOW - 50 * DAY))
        for k in range(economy_config.REFERRAL_QUALIFY_ROUNDS):
            sql(path, "INSERT INTO roulette_rounds (telegram_id, request_id, number, stake_total, payout_total, bets_json, created_at) VALUES (?, ?, 0, 1, 0, '[]', ?)", (uid, "q%d-%d" % (n, k), NOW - DAY))
        db.check_qualification(uid, now=NOW, db_path=path)

    def owned():
        return sorted(r[0] for r in sql(path, "SELECT item_code FROM cosmetic_items WHERE telegram_id = ?", (I,)))

    for n in range(1, 3):
        qualify(n)
    check("2 приглашённых: наград нет", owned(), [])
    qualify(3)
    check("3-й: значок «Гонец»", owned(), ["ref_scout"])
    for n in range(4, 10):
        qualify(n)
    check("9: тот же набор", owned(), ["ref_scout"])
    qualify(10)
    check("10-й: рамка «Маяк»", owned(), ["ref_beacon", "ref_scout"])
    for n in range(11, 30):
        qualify(n)
    qualify(30)
    check("30-й: рамка «Арка»", owned(), ["ref_arch", "ref_beacon", "ref_scout"])
    # порядковый номер основателя: выдаётся с первой вехой (3 друга), по очереди получения; у второго пригласившего номер 2
    from features.cosmetics_db import cosmetics_state
    check("номер основателя первого пригласившего: 1", cosmetics_state(I, db_path=path)["founder_no"], 1)
    I2 = 910100
    add_player(path, I2, balance=0)
    for n in range(1, 4):
        uid = 940000 + n
        add_player(path, uid, xp=lvl3)
        sql(path, "INSERT INTO referrals (invitee_id, referrer_id, created_at) VALUES (?, ?, ?)", (uid, I2, NOW - 50 * DAY))
        for k in range(economy_config.REFERRAL_QUALIFY_ROUNDS):
            sql(path, "INSERT INTO roulette_rounds (telegram_id, request_id, number, stake_total, payout_total, bets_json, created_at) VALUES (?, ?, 0, 1, 0, '[]', ?)", (uid, "w%d-%d" % (n, k), NOW - DAY))
        db.check_qualification(uid, now=NOW + 5, db_path=path)
    check("номер основателя второго: 2; у не получившего веху нет номера", [cosmetics_state(I2, db_path=path)["founder_no"], cosmetics_state(V, db_path=path)["founder_no"]], [2, None])
    from features.chat_db import _public_cosmetics
    sql(path, "INSERT INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, 'badge', 'ref_scout')", (I,))
    check("другим участникам виден номер основателя у надевшего значок «Камень»", _public_cosmetics(path, [I, I2]), {I: {"badge": "ref_scout", "founder_no": 1}})
    check("предметы с источником referral и без цены", sorted(sql(path, "SELECT source, payment_ref FROM cosmetic_items WHERE telegram_id = ?", (I,))), [("referral", "milestone-10"), ("referral", "milestone-3"), ("referral", "milestone-30")])
    check("вехи не требуют цены в каталоге", [cosmetics.item(c)["price"] for c in ("ref_scout", "ref_beacon", "ref_arch")], [None, None, None])
    check("слоты вех: значок, две рамки", [cosmetics.item(c)["slot"] for c in ("ref_scout", "ref_beacon", "ref_arch")], ["badge", "avatar_frame", "avatar_frame"])

    # ================= сводка числами =================
    check("числа решения владельца", (economy_config.REFERRAL_INVITER_CHIPS, economy_config.REFERRAL_INVITER_GEMS, economy_config.REFERRAL_COMMISSION_PCT, economy_config.REFERRAL_COMMISSION_DAYS,
                                      economy_config.REFERRAL_COMMISSION_CAP, economy_config.FOUNDER_CHIPS, economy_config.FOUNDER_GEMS, economy_config.FREE_GEMS_MONTHLY_CAP,
                                      economy_config.REFERRAL_GEMS_MONTHLY_CAP), (2000, 10, 30, 90, 300_000, 3000, 30, 150, 100))
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    os.environ.pop("TOMBSTONE_SECRET", None)
    if _saved_secret is not None:
        os.environ["TOMBSTONE_SECRET"] = _saved_secret

print("Все проверки прошли")

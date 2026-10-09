"""Сводка экономики для владельца (/stats, этап E8): числа считаются верно на заготовленных данных, в тексте нет идентификаторов и имён, команда отвечает только владельцу в личном чате."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import asyncio
import os
import re
import sqlite3
import tempfile
from types import SimpleNamespace

import bot
import db
import economy_config
import wallet
from stubs import FakeUpdate

NOW = 1_760_000_000
OWNER, A, B, C = 424242421, 424242422, 424242423, 424242424

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["OWNER_CHAT_ID"] = str(OWNER)


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "s.db")
db.init_db(path)
os.environ["DB_PATH"] = path


def sql(query, params=()):
    conn = sqlite3.connect(path)
    try:
        conn.execute(query, params)
        conn.commit()
    finally:
        conn.close()


def in_tx(fn):
    conn = db._connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        out = fn(conn)
        conn.execute("COMMIT")
        return out
    finally:
        conn.close()


try:
    # ---- пустая база
    empty = db.economy_stats(now=NOW, db_path=path)
    check("пустая база: нули", (empty["players"]["total"], empty["chips"]["supply"], empty["gems"]["supply"], empty["gem_sales"]["всего"]["packs"]), (0, 0, 0, 0))
    check("текст пустой базы собирается", "Сводка экономики" in db.stats_text(empty), True)

    # ---- данные: три игрока, ферма, покупки кристаллов и фишек
    for uid, balance, created, last, il, sl in ((A, 1000, NOW - 10 * 86400, NOW - 100, 0, 0), (B, 3000, NOW - 3600, NOW - 50, 3, 1), (C, 500, NOW - 9 * 86400, NOW - 3 * 86400, 5, 2)):
        sql("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, income_level, storage_level) VALUES (?, ?, 100, ?, ?, ?, ?)", (uid, balance, last, created, il, sl))
    sql("INSERT INTO farm_purchases (telegram_id, request_id, kind, level_after, cost, created_at) VALUES (?, 'r1', 'income', 1, 1000, ?)", (A, NOW - 500))
    sql("INSERT INTO farm_purchases (telegram_id, request_id, kind, level_after, cost, created_at) VALUES (?, 'r2', 'income', 2, 1600, ?)", (B, NOW - 400))
    db.record_gem_payment(A, "ch-1", "gems_250", 250, now=NOW - 1000, db_path=path)                  # +275
    db.record_gem_payment(B, "ch-2", "gems_50", 50, now=NOW - 2 * 86400, db_path=path)               # +50, давно
    in_tx(lambda c: wallet.gems_debit(c, A, 75, "cosmetic_purchase", "table_blue", NOW - 900))
    db.owner_grant_gems(OWNER, 10, "g1", now=NOW - 800, db_path=path)
    db.buy_chip_pack(A, "cp-1", "chips_6h", now=NOW - 700, db_path=path)                             # -30 кристаллов, +600 фишек
    sql("INSERT INTO roulette_rounds (telegram_id, request_id, number, bets_json, stake_total, payout_total, created_at) VALUES (?, 'rr1', 7, '[]', 10, 0, ?)", (A, NOW - 60))
    sql("INSERT INTO crash_bets (round_id, telegram_id, bet, status, request_id, created_at_ms) VALUES (1, ?, 10, 'lost', 'cr1', ?)", (A, (NOW - 60) * 1000))      # живой краш: ставка за 24 ч
    sql("INSERT INTO crash_bets (round_id, telegram_id, bet, status, request_id, created_at_ms) VALUES (2, ?, 10, 'lost', 'cr2', ?)", (A, (NOW - 3 * 86400) * 1000))   # старше суток
    stats = db.economy_stats(now=NOW, db_path=path)
    pl = stats["players"]
    check("игроки: всего, активных за 24 ч (C не активен), новых за 24 ч и 7 дней (B и владелец)", (pl["total"], pl["active_24h"], pl["new_24h"], pl["new_7d"]), (4, 3, 2, 2))      # четвёртый игрок это владелец, созданный выдачей кристаллов (стартовые 1000 фишек)
    check("фишки на руках и среднее", (stats["chips"]["supply"], stats["chips"]["average"]), (1000 + 3000 + 500 + 600 + 1000, (1000 + 3000 + 500 + 600 + 1000) // 4))
    check("на ферму потрачено", stats["chips"]["farm_spent_total"], 2600)
    check("кристаллов на руках: 275 + 50 - 75 + 10 - 30", stats["gems"]["supply"], 230)
    by24 = stats["gems"]["by_reason"]["24 ч"]
    check("кристаллы за 24 ч по причинам", by24, {"purchase": 275, "cosmetic_purchase": -75, "owner_grant": 10, "chip_purchase": -30})
    check("кристаллы за всё время: покупка 325", stats["gems"]["by_reason"]["всего"]["purchase"], 325)
    check("продажи пакетов: 24 ч, 7 дней, всего", (stats["gem_sales"]["24 ч"], stats["gem_sales"]["7 дней"], stats["gem_sales"]["всего"]),
          ({"packs": 1, "stars": 250, "gems": 275}, {"packs": 2, "stars": 300, "gems": 325}, {"packs": 2, "stars": 300, "gems": 325}))
    check("продажи фишек за 24 ч", stats["chip_sales"]["24 ч"], {"packs": 1, "gems": 30, "chips": 600})
    check("уровни дохода", stats["farm"]["income_levels"], {0: 2, 3: 1, 5: 1})
    check("раунды рулетки за 24 ч", stats["games"]["рулетка"], 1)
    check("ставки живого краша за 24 ч", stats["games"]["краш"], 1)
    text = db.stats_text(stats)
    ids = [str(x) for x in (OWNER, A, B, C)]
    check("в тексте нет идентификаторов Telegram и платежей", (any(i in text for i in ids), "ch-1" in text, "charge" in text), (False, False, False))
    check("в тексте есть источники и стоки кристаллов", ("появилось 285, ушло 105" in text, "Продано пакетов кристаллов за 24 ч: 1 на 250 Stars" in text), (True, True))

    # ---- команда
    def run_cmd(uid, chat="private"):
        update = FakeUpdate(chat, user_id=uid)
        asyncio.run(bot.stats(update, SimpleNamespace(bot=None, args=[], application=SimpleNamespace(bot=None, bot_data={}))))
        return [r["text"] for r in update.replies]
    out = run_cmd(OWNER)
    check("владелец получает сводку", (len(out), out[0].startswith("Сводка экономики"), re.search(r"\b4242424\d\d\b", out[0])), (1, True, None))
    check("не владелец: молчание", run_cmd(A), [])
    check("в группе молчание", run_cmd(OWNER, "group"), [])
    check("конфигурация версии в сводке", str(economy_config.VERSION) in out[0], True)
    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

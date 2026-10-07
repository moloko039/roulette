"""Миграция со старых версий базы без git: фикстуры схемы (bot/testdata/legacy/schema_<коммит>.sql: схема и вымышленные строки, настоящих данных нет)
открываются текущим init_db. Проверяется: все строки старых таблиц остались как были (балансы, опыт, ставки, раунды, участники бесед, косметика),
добавились только ожидаемые таблицы, повторная миграция ничего не меняет, на мигрированной базе работают игры, рейтинг, выгрузка и косметика.
Тест не использует git и tar и работает при пробеле в пути (временная папка с пробелом в имени), поэтому не может молча пропуститься в CI с неглубоким checkout."""
import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import shutil
import sqlite3
import tempfile

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID", "PLAY_MODE", "SQLITE_JOURNAL_MODE")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["MEMBER_REF_SECRET"] = "test-ref-secret-not-real"

import db  # noqa: E402
import roulette  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "testdata", "legacy")
NOW = 1_760_000_000
A, B, C = 900000001, 900000002, 900000003

# фикстура -> таблицы, которых в той версии ещё не было (их должна добавить миграция)
NEW_TABLES = {
    "dbc9242": {"cosmetic_items", "cosmetic_equipped", "cosmetic_prefs", "cosmetic_actions", "cosmetic_purchases", "player_best_win", "slot_rounds", "gems_ledger", "gem_balances", "gem_purchases", "chip_purchases", "streak_claims", "gifts"},
    "1e602d6": {"cosmetic_purchases", "player_best_win", "slot_rounds", "gems_ledger", "gem_balances", "gem_purchases", "chip_purchases", "streak_claims", "gifts"},
    "981d2c0": {"player_best_win", "slot_rounds", "gems_ledger", "gem_balances", "gem_purchases", "chip_purchases", "streak_claims", "gifts"},
}
BALANCES = {A: 12345, B: 1_000_000, C: 0}


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def tables(conn):
    return sorted(r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"))


def snapshot(conn, names):
    return {t: [tuple(r) for r in conn.execute("SELECT * FROM " + t + " ORDER BY rowid")] for t in names}   # имена из sqlite_master, не из ввода


def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


tmp = tempfile.mkdtemp(prefix="legacy migration ")      # пробел в пути
try:
    for commit, new_tables in sorted(NEW_TABLES.items()):
        sql_text = open(os.path.join(FIXTURES, "schema_%s.sql" % commit), encoding="utf-8").read()
        assert "players" in sql_text and len(sql_text) > 2000, "фикстура %s пуста" % commit
        path = os.path.join(tmp, "old %s.db" % commit)
        conn = sqlite3.connect(path)
        conn.executescript(sql_text)
        conn.commit()
        old_tables = tables(conn)
        before = snapshot(conn, old_tables)
        conn.close()
        check("%s: в фикстуре балансы" % commit, dict(sql(path, "SELECT telegram_id, balance FROM players")), BALANCES)
        assert not (set(old_tables) & new_tables), "в фикстуре %s уже есть новые таблицы" % commit

        db.init_db(path)
        conn = sqlite3.connect(path)
        check("%s: добавлены только новые таблицы" % commit, set(tables(conn)) - set(old_tables), new_tables)
        after = snapshot(conn, old_tables)
        conn.close()
        for t in old_tables:
            check("%s: строки таблицы %s не изменились (балансы, опыт, ставки и остальное)" % (commit, t), after[t], before[t])
        check("%s: балансы на месте" % commit, dict(sql(path, "SELECT telegram_id, balance FROM players")), BALANCES)
        db.init_db(path)
        conn = sqlite3.connect(path)
        check("%s: повторная миграция ничего не меняет" % commit, snapshot(conn, old_tables), before)
        check("%s: индекс chat_members(telegram_id) создан миграцией старой базы" % commit,
              [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'chat_members' AND name = 'idx_chat_members_telegram'")], ["idx_chat_members_telegram"])
        check("%s: индекс по столбцам (telegram_id, last_seen)" % commit, [r[2] for r in conn.execute("PRAGMA index_info(idx_chat_members_telegram)")], ["telegram_id", "last_seen"])
        plan = " ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN SELECT first_name FROM chat_members WHERE telegram_id = ? ORDER BY last_seen DESC LIMIT 1", (A,)))
        assert "idx_chat_members_telegram" in plan and "SCAN" not in plan.replace("SEARCH", ""), "запрос имени идёт без индекса: " + plan
        conn.close()

        # игры и чтение на мигрированной базе (доход выключен: last_accrual в фикстуре далеко впереди)
        check("%s: профиль читается, баланс прежний" % commit, db.get_player(A, now=NOW, db_path=path)["balance"], 12345)
        res = db.spin_roulette(A, "legacy-new-0001", roulette.validate_bets([{"type": "number", "value": 17, "amount": 100}]), now=NOW, db_path=path, rng=lambda n: 17)
        check("%s: рулетка работает: 12345 - 100 + 3600" % commit, (res["balance"], sql(path, "SELECT balance FROM players WHERE telegram_id = ?", (A,))[0][0]), (15845, 15845))
        check("%s: рекорд выигрыша записан" % commit, sql(path, "SELECT game, net_amount FROM player_best_win WHERE telegram_id = ?", (A,)), [("roulette", 3500)])
        top = db.chat_top("legacy-chat", A, "Тест", now=NOW, db_path=path)
        check("%s: рейтинг беседы: оба участника, баланс прежнего игрока" % commit, ([e["balance"] for e in top["top"]], top["me"]["total"]), ([1_000_000, 15845], 2))
        export = db.get_player_export(B, db_path=path)
        check("%s: выгрузка данных старого игрока" % commit, (export["player"]["balance"], export["player"]["xp"], len(export["chats"])), (1_000_000, 90000, 1))
        # косметика: у старых версий с косметикой данные целы, у самой старой работает с нуля
        if commit == "dbc9242":
            check("%s: косметики не было: стартовые предметы" % commit, db.cosmetics_state(B, db_path=path)["equipped"]["chip"], "chip_plain")
            db.grant_item(B, "chip_ring", "owner_gift", now=NOW, db_path=path)
            check("%s: выдача предмета работает" % commit, [r[0] for r in sql(path, "SELECT item_code FROM cosmetic_items WHERE telegram_id = ?", (B,))], ["chip_ring"])
        else:
            st = db.cosmetics_state(B, db_path=path)
            check("%s: надетое и показ сохранились" % commit, (st["equipped"]["chip"], st["show_in_rating"]), ("chip_ring", False))
            before_balance = sql(path, "SELECT balance FROM players WHERE telegram_id = ?", (B,))[0][0]
            bought = db.buy_with_chips(B, "legacy-buy-0001", "badge_spade", now=NOW, db_path=path)
            check("%s: покупка за фишки на старой базе: списано 20000" % commit, (bought["balance"], before_balance - bought["balance"]), (before_balance - 20000, 20000))
        # переводы на мигрированной базе: удаление данных получателя обезличивает записи, лимит отправителя сохраняется
        sql(path, "INSERT INTO transfers (sender, recipient, amount, fee, created_at, request_id) VALUES (?, ?, 40000, 2000, ?, ?)", (B, A, NOW, "legacy-tr-0001"))
        sql(path, "INSERT INTO transfers (sender, recipient, amount, fee, created_at, request_id) VALUES (?, ?, 1000, 50, ?, ?)", (A, B, NOW, "legacy-tr-0002"))
        deleted = db.delete_player_data(A, db_path=path, now=NOW + 10)
        check("%s: удаление: отправленный перевод удалён, полученный обезличен" % commit, (deleted["transfers"], deleted["transfers_anonymized"]), (1, 1))
        check("%s: запись отправителя B осталась без идентификатора получателя" % commit, sql(path, "SELECT sender, recipient, amount, fee FROM transfers"), [(B, 0, 40000, 2000)])
        print("миграция со схемы коммита %s проверена" % commit)
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

print("Все проверки прошли")

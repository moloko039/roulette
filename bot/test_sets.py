"""Тесты наборов косметики: покупка набора за кристаллы (buy_set)."""
import testenv  # noqa: F401
import os
import tempfile
import time

from core.db_conn import _connect
from core.kernel import _register_player
import cosmetics
import cosmetic_sets
from features import cosmetics_db
import wallet

def main():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    from core.schema import init_db
    init_db(path)
    
    try:
        now = int(time.time())

        # test_buy_set_success
        conn = _connect(path)
        conn.execute("BEGIN IMMEDIATE")
        _register_player(conn, 1, now)
        wallet.gems_credit(conn, 1, 1000, "owner_grant", "test_sets", now)
        conn.commit()
        conn.close()

        resp = cosmetics_db.buy_set(1, "req-1", "draft", now=now, db_path=path)
        assert resp["set_code"] == "draft"
        assert resp["price_gems"] == 200
        assert resp["gems"] == 800
        assert set(resp["items"]) == set(cosmetic_sets.SETS["draft"]["parts"])
        assert not resp.get("replayed")

        # Проверка базы: все предметы выданы, списание в gems_ledger
        conn = _connect(path)
        owned = [r["item_code"] for r in conn.execute("SELECT item_code FROM cosmetic_items WHERE telegram_id = 1")]
        assert set(owned) == set(cosmetic_sets.SETS["draft"]["parts"])
        
        ledger = conn.execute("SELECT delta, reason, ref FROM gems_ledger WHERE telegram_id = 1").fetchall()
        assert len(ledger) == 2 # 1 grant + 1 debit
        debit = [l for l in ledger if l["delta"] < 0][0]
        assert debit["delta"] == -200
        assert debit["reason"] == "cosmetic_purchase"
        assert debit["ref"] == "draft"
        conn.close()

        # Идемпотентность (повтор)
        resp2 = cosmetics_db.buy_set(1, "req-1", "draft", now=now, db_path=path)
        assert resp2["replayed"] is True
        assert resp2["gems"] == 800
        
        conn = _connect(path)
        ledger2 = conn.execute("SELECT delta, reason, ref FROM gems_ledger WHERE telegram_id = 1").fetchall()
        assert len(ledger2) == 2
        conn.close()

        # test_buy_set_already_owned
        conn = _connect(path)
        conn.execute("BEGIN IMMEDIATE")
        _register_player(conn, 2, now)
        wallet.gems_credit(conn, 2, 1000, "owner_grant", "test_sets", now)
        conn.execute("INSERT INTO cosmetic_items (telegram_id, item_code, source, acquired_at) VALUES (2, 'draft_table', 'gems', ?)", (now,))
        conn.commit()
        conn.close()

        try:
            cosmetics_db.buy_set(2, "req-2", "draft", db_path=path)
            assert False, "Expected AlreadyOwned"
        except cosmetics.AlreadyOwned:
            pass

        # Проверяем, что кристаллы не списались
        conn = _connect(path)
        assert wallet.gems_balance(conn, 2) == 1000
        conn.close()

        # test_buy_set_insufficient_gems
        conn = _connect(path)
        conn.execute("BEGIN IMMEDIATE")
        _register_player(conn, 3, now)
        wallet.gems_credit(conn, 3, 100, "owner_grant", "test_sets", now)
        conn.commit()
        conn.close()

        try:
            cosmetics_db.buy_set(3, "req-3", "void", db_path=path)
            assert False, "Expected InsufficientGems"
        except cosmetics.InsufficientGems:
            pass

        conn = _connect(path)
        assert wallet.gems_balance(conn, 3) == 100
        assert conn.execute("SELECT count(*) FROM cosmetic_items WHERE telegram_id = 3").fetchone()[0] == 0
        conn.close()

        # test_buy_set_unknown
        try:
            cosmetics_db.buy_set(1, "req-unknown", "nonexistent", db_path=path)
            assert False, "Expected UnknownSet"
        except cosmetics.UnknownSet:
            pass

        # test_sets_prices_sum
        for set_code, s in cosmetic_sets.SETS.items():
            parts = s["parts"]
            sum_price = 0
            for part in parts:
                it = cosmetics.item(part)
                assert it is not None
                assert it["price"]["currency"] == "gems"
                sum_price += it["price"]["amount"]
            assert sum_price > s["price_gems"]
    finally:
        os.unlink(path)

if __name__ == "__main__":
    main()

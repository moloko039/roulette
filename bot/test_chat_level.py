import testenv  # noqa: F401
import os
import sqlite3
import tempfile
import time

from features.chat_db import chat_level, chat_top, chat_next_points
from core.db_conn import _connect
from db import init_db

def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"

def test_chat_level_bounds():
    check("уровень 0", chat_level(0), 1)
    check("уровень 9_999", chat_level(9_999), 1)
    check("уровень 10_000", chat_level(10_000), 2)
    check("уровень 24_999", chat_level(24_999), 2)
    check("уровень 25_000", chat_level(25_000), 3)
    check("уровень 59_999", chat_level(59_999), 3)
    check("уровень 60_000", chat_level(60_000), 4)
    check("уровень 149_999", chat_level(149_999), 4)
    check("уровень 150_000", chat_level(150_000), 5)
    check("уровень 399_999", chat_level(399_999), 5)
    check("уровень 400_000", chat_level(400_000), 6)
    check("уровень 999_999", chat_level(999_999), 6)
    check("уровень 1_000_000", chat_level(1_000_000), 7)
    check("уровень 2_499_999", chat_level(2_499_999), 7)
    check("уровень 2_500_000", chat_level(2_500_000), 8)
    check("уровень 5_999_999", chat_level(5_999_999), 8)
    check("уровень 6_000_000", chat_level(6_000_000), 9)
    check("уровень 14_999_999", chat_level(14_999_999), 9)
    check("уровень 15_000_000", chat_level(15_000_000), 10)
    check("уровень 100_000_000", chat_level(100_000_000), 10)

    # chat_next_points
    check("0 очков", chat_next_points(0), 10_000)
    check("9_999", chat_next_points(9_999), 10_000)
    check("10_000", chat_next_points(10_000), 25_000)
    check("24_999", chat_next_points(24_999), 25_000)
    check("25_000", chat_next_points(25_000), 60_000)
    check("59_999", chat_next_points(59_999), 60_000)
    check("60_000", chat_next_points(60_000), 150_000)
    check("149_999", chat_next_points(149_999), 150_000)
    check("150_000", chat_next_points(150_000), 400_000)
    check("399_999", chat_next_points(399_999), 400_000)
    check("400_000", chat_next_points(400_000), 1_000_000)
    check("999_999", chat_next_points(999_999), 1_000_000)
    check("1_000_000", chat_next_points(1_000_000), 2_500_000)
    check("2_499_999", chat_next_points(2_499_999), 2_500_000)
    check("2_500_000", chat_next_points(2_500_000), 6_000_000)
    check("5_999_999", chat_next_points(5_999_999), 6_000_000)
    check("6_000_000", chat_next_points(6_000_000), 15_000_000)
    check("14_999_999", chat_next_points(14_999_999), 15_000_000)
    check("15_000_000", chat_next_points(15_000_000), None)
    check("100_000_000", chat_next_points(100_000_000), None)

def test_chat_top_includes_level_and_points():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        init_db(path)
        conn = sqlite3.connect(path)
        now = int(time.time())
        
        # insert directly to avoid calling accrue
        conn.execute("INSERT INTO players (telegram_id, balance, total_staked, rate, xp, last_accrual, storage_level, accrual_acc, created_at) VALUES (1, 100, 25000, 0, 0, ?, 0, 0, ?)", (now, now))
        conn.execute("INSERT INTO chat_members (chat_instance, telegram_id, first_name, last_seen, first_seen) VALUES ('chat1', 1, 'Test', ?, ?)", (now, now))
        conn.commit()
        conn.close()
        
        res = chat_top('chat1', 1, 'Test', db_path=path)
        check("scope", res['scope'], 'chat')
        check("chat_staked", res['chat_staked'], 25000)
        check("chat_points", res['chat_points'], 25000)
        check("chat_next_points", res['chat_next_points'], 60000)
        check("chat_level (points)", res['chat_level'], 3)
        check("chat_level (func)", res['chat_level'], chat_level(res['chat_staked']))
    finally:
        os.remove(path)

test_chat_level_bounds()
test_chat_top_includes_level_and_points()
print("Все проверки прошли")

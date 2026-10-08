import os
import time
import tempfile
from unittest.mock import patch
from db import init_db, get_player_export, delete_player_data
from core.db_conn import _connect
from core.kernel import _register_player
from test_antiabuse import get_player
from test_cosmetics import check
import core.achievements

from games.crash_db import crash_start, settle_expired_crash
from games.mines_db import mines_start, mines_reveal, _active_game
from games.blackjack_db import blackjack_start, blackjack_action
from games.keno_db import play_keno

def count_items(path, uid, code):
    conn = _connect(path)
    count = conn.execute("SELECT COUNT(*) FROM cosmetic_items WHERE telegram_id = ? AND item_code = ?", (uid, code)).fetchone()[0]
    conn.close()
    return count

def get_progress(path, uid, code):
    conn = _connect(path)
    row = conn.execute("SELECT count, streak, done_at FROM achievement_progress WHERE telegram_id = ? AND code = ?", (uid, code)).fetchone()
    conn.close()
    return row

def test_nearly():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    uid = 100
    get_player(uid, db_path=path)
    now = int(time.time() * 1000)
    
    with patch("games.crash_db.crash.new_crash") as mock_crash, patch("games.crash_db.crash.m100") as mock_m100:
        for i in range(5):
            mock_crash.return_value = 101 if i < 4 else 102
            mock_m100.return_value = 105
            crash_start(uid, f"req_{i}", 100, target_x100=200, now_ms=now, db_path=path)
            settle_expired_crash(uid, now_ms=now+50000, db_path=path)
        
        # 4 crashes <= 1.01. Item should not be granted.
        check("4 crashes", count_items(path, uid, "achv_nearly"), 0)
        
        # 5th crash <= 1.01
        mock_crash.return_value = 101
        crash_start(uid, "req_5", 100, target_x100=200, now_ms=now+100000, db_path=path)
        settle_expired_crash(uid, now_ms=now+150000, db_path=path)
        
        check("5 crashes", count_items(path, uid, "achv_nearly"), 1)
        row = get_progress(path, uid, "achv_nearly")
        check("progress done_at", row[2] is not None, True)

def test_sapper():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    uid = 101
    get_player(uid, db_path=path)
    now = int(time.time())
    
    with patch("games.mines_db.mines.new_layout") as mock_new:
        mock_new.return_value = 1
        mines_start(uid, "req_1", 100, 1, now=now, db_path=path)
        mines_reveal(uid, "req_1_rev1", 1, now=now+1, db_path=path)
        mines_reveal(uid, "req_1_rev2", 0, now=now+2, db_path=path)
        
        check("2nd move loss", count_items(path, uid, "achv_sapper"), 0)
        
        mines_start(uid, "req_2", 100, 1, now=now+10, db_path=path)
        mines_reveal(uid, "req_2_rev1", 0, now=now+11, db_path=path)
        check("1st move loss", count_items(path, uid, "achv_sapper"), 1)

def test_bust():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    uid = 102
    get_player(uid, db_path=path)
    now = int(time.time())
    
    with patch("games.blackjack_db.blackjack.new_shoe") as mock_shoe, patch("games.blackjack_db.blackjack._draw") as mock_draw:
        def play_bust(req):
            mock_shoe.return_value = ["10S", "10H", "10D", "10C"] + ["10S"]*10
            mock_draw.return_value = "10S"
            blackjack_start(uid, req, 100, now=now, db_path=path)
            blackjack_action(uid, req + "_action", "hit", now=now, db_path=path)

        def play_win(req):
            mock_shoe.return_value = ["10S", "10H", "10D", "6C"] + ["10S"]*10
            mock_draw.return_value = "10S"
            blackjack_start(uid, req, 100, now=now, db_path=path)
            blackjack_action(uid, req + "_action", "stand", now=now, db_path=path)
            
        play_bust("b1")
        play_bust("b2")
        check("2 busts", count_items(path, uid, "achv_bust"), 0)
        play_win("w1")
        play_bust("b3")
        play_bust("b4")
        play_bust("b5")
        check("3 busts", count_items(path, uid, "achv_bust"), 1)
        play_bust("b6")
        check("4 busts doesn't give 2nd item", count_items(path, uid, "achv_bust"), 1)

def test_keno():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    uid = 103
    get_player(uid, db_path=path)
    now = int(time.time())
    
    with patch("games.keno_db.keno.draw_numbers") as mock_draw:
        mock_draw.return_value = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20]
        play_keno(uid, "req_1", 100, [1,21,22,23,24,25,26,27,28,29], now=now, db_path=path)
        check("1 match", count_items(path, uid, "achv_keno"), 0)
        
        play_keno(uid, "req_2", 100, [21,22,23,24,25,26,27,28,29,30], now=now, db_path=path)
        check("0 matches", count_items(path, uid, "achv_keno"), 1)
        
def test_mydata_delete():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    init_db(path)
    uid = 104
    get_player(uid, db_path=path)
    now = int(time.time())
    os.environ["TOMBSTONE_SECRET"] = "secret"
    
    conn = _connect(path)
    core.achievements.record(conn, uid, "mines_explode", now, is_first_move=True)
    conn.commit()
    conn.close()
    
    data = get_player_export(uid, db_path=path)
    check("mydata", data["achievements"], [{"code": "achv_sapper", "time": now}])
    
    delete_player_data(uid, db_path=path)
    conn = _connect(path)
    cnt = conn.execute("SELECT COUNT(*) FROM achievement_progress WHERE telegram_id = ?", (uid,)).fetchone()[0]
    conn.close()
    check("deleted", cnt, 0)

if __name__ == "__main__":
    test_nearly()
    test_sapper()
    test_bust()
    test_keno()
    test_mydata_delete()
    print("test_achievements OK")

import testenv  # noqa: F401
import os
import sqlite3
import tempfile
import time
import concurrent.futures
from unittest import mock

from fastapi.testclient import TestClient

import db
import wallet
import economy_config
from api import create_app
from core.db_conn import _connect
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
U_INVITER = 111
U_INVITEE = 222
U_INVITEE_2 = 333
NOW = int(time.time())

_ENV_KEYS = ("DB_PATH", "TOMBSTONE_SECRET", "GAME_LINK")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["GAME_LINK"] = "https://t.me/TestBot/game"


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


tmp = tempfile.mkdtemp()
counter = [0]


def new_db():
    counter[0] += 1
    path = os.path.join(tmp, f"refq{counter[0]}.db")
    db.init_db(path)
    os.environ["DB_PATH"] = path
    return path


def auth(uid, extra=None):
    return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=NOW, first_name="Игрок", extra=extra)}


def set_invitee_stats(path, uid, hours, level, rounds):
    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # hours
            created_at = NOW - int(hours * 3600)
            # level
            from levels import threshold
            xp = threshold(level) if level > 1 else 0
            conn.execute("UPDATE players SET created_at = ?, xp = ? WHERE telegram_id = ?", (created_at, xp, uid))
            
            # rounds
            conn.execute("DELETE FROM roulette_rounds WHERE telegram_id = ?", (uid,))
            for i in range(rounds):
                conn.execute("INSERT INTO roulette_rounds (telegram_id, request_id, number, stake_total, payout_total, bets_json, created_at) VALUES (?, ?, 0, 10, 0, '[]', ?)", (uid, f"req{i}", NOW))
            
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.close()


def get_rewards(path, uid):
    conn = _connect(path)
    try:
        chips = conn.execute("SELECT balance FROM players WHERE telegram_id = ?", (uid,)).fetchone()[0]
        gems = conn.execute("SELECT gems FROM gem_balances WHERE telegram_id = ?", (uid,)).fetchone()
        return chips, (gems[0] if gems else 0)
    finally:
        conn.close()


try:
    path = new_db()
    client = TestClient(create_app(TOKEN, [], db_path=path))
    
    # Регистрация пригласившего и получение кода
    client.get("/api/me", headers=auth(U_INVITER))
    code = client.get("/api/referral", headers=auth(U_INVITER)).json()["link"].split("ref_")[1]
    
    # Регистрация приглашённого
    client.get("/api/me", headers=auth(U_INVITEE, extra={"start_param": "ref_" + code}))
    chips_before, gems_before = get_rewards(path, U_INVITER)
    
    # Проверка 1: до порога времени нет награды (24 ч минус минута)
    set_invitee_stats(path, U_INVITEE, economy_config.REFERRAL_QUALIFY_HOURS - 1/60, economy_config.REFERRAL_QUALIFY_LEVEL, economy_config.REFERRAL_QUALIFY_ROUNDS)
    client.get("/api/me", headers=auth(U_INVITEE)) # lazy check
    check("до порога времени награды нет", get_rewards(path, U_INVITER), (chips_before, gems_before))
    
    # Проверка 2: уровень ниже порога нет
    set_invitee_stats(path, U_INVITEE, economy_config.REFERRAL_QUALIFY_HOURS, economy_config.REFERRAL_QUALIFY_LEVEL - 1, economy_config.REFERRAL_QUALIFY_ROUNDS)
    client.get("/api/me", headers=auth(U_INVITEE))
    check("ниже уровня награды нет", get_rewards(path, U_INVITER), (chips_before, gems_before))
    
    # Проверка 3: раундов 9 нет
    set_invitee_stats(path, U_INVITEE, economy_config.REFERRAL_QUALIFY_HOURS, economy_config.REFERRAL_QUALIFY_LEVEL, economy_config.REFERRAL_QUALIFY_ROUNDS - 1)
    client.get("/api/me", headers=auth(U_INVITEE))
    check("меньше раундов награды нет", get_rewards(path, U_INVITER), (chips_before, gems_before))
    
    # Проверка 3б: незавершённые партии (finished_at NULL) раундами не считаются: 9 завершённых + 1 открытая партия мин = всё ещё 9
    conn = sqlite3.connect(path)
    for i in range(1):    # открытая партия мин может быть только одна на игрока (уникальный индекс)
        conn.execute("INSERT INTO mines_games (telegram_id, bet, mines_count, mine_mask, status, created_at, updated_at, finished_at) VALUES (?, 10, 3, 7, 'active', ?, ?, NULL)", (U_INVITEE, NOW, NOW))
    conn.commit()
    conn.close()
    client.get("/api/me", headers=auth(U_INVITEE))
    check("открытые партии не считаются раундами", get_rewards(path, U_INVITER), (chips_before, gems_before))
    # завершённая партия засчитывается: закрываем одну (9 + 1 = 10)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE mines_games SET finished_at = ?, status = 'lost' WHERE id = (SELECT MIN(id) FROM mines_games WHERE telegram_id = ?)", (NOW, U_INVITEE))
    conn.commit()
    conn.close()
    client.get("/api/me", headers=auth(U_INVITEE))
    chips_fin, gems_fin = get_rewards(path, U_INVITER)
    check("завершённая партия засчитывается наравне с остальными", (chips_fin - chips_before, gems_fin - gems_before), (economy_config.REFERRAL_INVITER_CHIPS, economy_config.REFERRAL_INVITER_GEMS))
    # вернуть состояние до проверки 4: награда снята, квалификация сброшена, партии убраны
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM mines_games WHERE telegram_id = ?", (U_INVITEE,))
    conn.execute("UPDATE referrals SET qualified_at = NULL WHERE invitee_id = ?", (U_INVITEE,))
    conn.execute("UPDATE players SET balance = balance - ? WHERE telegram_id = ?", (economy_config.REFERRAL_INVITER_CHIPS, U_INVITER))
    conn.execute("DELETE FROM gems_ledger WHERE telegram_id = ? AND reason = 'referral_reward'", (U_INVITER,))
    conn.execute("UPDATE gem_balances SET gems = gems - ? WHERE telegram_id = ?", (economy_config.REFERRAL_INVITER_GEMS, U_INVITER))
    conn.commit()
    conn.close()
    chips_before, gems_before = get_rewards(path, U_INVITER)

    # Проверка 4: на пороге есть, награда: 1000 фишек, 5 кристаллов, запись с referral_reward и непрозрачным ref
    set_invitee_stats(path, U_INVITEE, economy_config.REFERRAL_QUALIFY_HOURS, economy_config.REFERRAL_QUALIFY_LEVEL, economy_config.REFERRAL_QUALIFY_ROUNDS)
    client.get("/api/me", headers=auth(U_INVITEE))
    chips_after, gems_after = get_rewards(path, U_INVITER)
    check("награда выдана фишки", chips_after - chips_before, economy_config.REFERRAL_INVITER_CHIPS)
    check("награда выдана кристаллы", gems_after - gems_before, economy_config.REFERRAL_INVITER_GEMS)
    
    conn = sqlite3.connect(path)
    ref_rowid = conn.execute("SELECT rowid FROM referrals WHERE invitee_id = ?", (U_INVITEE,)).fetchone()[0]
    ledger = conn.execute("SELECT delta, reason, ref FROM gems_ledger WHERE telegram_id = ? ORDER BY id DESC LIMIT 1", (U_INVITER,)).fetchone()
    conn.close()
    check("запись в журнале", ledger, (economy_config.REFERRAL_INVITER_GEMS, "referral_reward", f"invitee-{ref_rowid}"))
    
    # Проверка 5: повторная проверка даёт 0
    chips_before, gems_before = get_rewards(path, U_INVITER)
    client.get("/api/me", headers=auth(U_INVITEE))
    check("повторно не выдаётся", get_rewards(path, U_INVITER), (chips_before, gems_before))
    
    # Проверка 6: GET /api/referral у пригласившего показывает qualified 1
    r = client.get("/api/referral", headers=auth(U_INVITER)).json()
    check("qualified 1", r["qualified"], 1)
    
    # Проверка 7: параллельные (12 потоков) дают одну награду
    client.get("/api/me", headers=auth(U_INVITEE_2, extra={"start_param": "ref_" + code}))
    set_invitee_stats(path, U_INVITEE_2, economy_config.REFERRAL_QUALIFY_HOURS, economy_config.REFERRAL_QUALIFY_LEVEL, economy_config.REFERRAL_QUALIFY_ROUNDS)
    chips_before, gems_before = get_rewards(path, U_INVITER)
    
    def parallel_me():
        c = TestClient(create_app(TOKEN, [], db_path=path))
        c.get("/api/me", headers=auth(U_INVITEE_2))
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(parallel_me) for _ in range(12)]
        concurrent.futures.wait(futures)
        
    chips_after, gems_after = get_rewards(path, U_INVITER)
    check("гонка: выдана одна награда фишки", chips_after - chips_before, economy_config.REFERRAL_INVITER_CHIPS)
    check("гонка: выдана одна награда кристаллы", gems_after - gems_before, economy_config.REFERRAL_INVITER_GEMS)
    r = client.get("/api/referral", headers=auth(U_INVITER)).json()
    check("qualified 2", r["qualified"], 2)
    
    # Проверка 8: потолок - набрал 98 бесплатных, получает 2
    path2 = new_db()
    client2 = TestClient(create_app(TOKEN, [], db_path=path2))
    client2.get("/api/me", headers=auth(U_INVITER))
    code2 = client2.get("/api/referral", headers=auth(U_INVITER)).json()["link"].split("ref_")[1]
    
    conn2 = sqlite3.connect(path2)
    conn2.execute("INSERT INTO gems_ledger (telegram_id, delta, reason, ref, created_at) VALUES (?, ?, 'streak_gems', 'pre', ?)", (U_INVITER, 98, NOW))
    conn2.commit()
    conn2.close()
    
    client2.get("/api/me", headers=auth(U_INVITEE, extra={"start_param": "ref_" + code2}))
    set_invitee_stats(path2, U_INVITEE, economy_config.REFERRAL_QUALIFY_HOURS, economy_config.REFERRAL_QUALIFY_LEVEL, economy_config.REFERRAL_QUALIFY_ROUNDS)
    chips_before, gems_before = get_rewards(path2, U_INVITER)
    client2.get("/api/me", headers=auth(U_INVITEE))
    chips_after, gems_after = get_rewards(path2, U_INVITER)
    check("потолок: только 2 кристалла", gems_after - gems_before, 2)
    check("потолок: фишки полностью", chips_after - chips_before, economy_config.REFERRAL_INVITER_CHIPS)
    
    # Проверка 9: потолок исчерпан (100) -> 0
    client2.get("/api/me", headers=auth(U_INVITEE_2, extra={"start_param": "ref_" + code2}))
    set_invitee_stats(path2, U_INVITEE_2, economy_config.REFERRAL_QUALIFY_HOURS, economy_config.REFERRAL_QUALIFY_LEVEL, economy_config.REFERRAL_QUALIFY_ROUNDS)
    chips_before, gems_before = get_rewards(path2, U_INVITER)
    client2.get("/api/me", headers=auth(U_INVITEE_2))
    chips_after, gems_after = get_rewards(path2, U_INVITER)
    check("потолок: 0 кристаллов", gems_after - gems_before, 0)
    check("потолок: фишки полностью 2", chips_after - chips_before, economy_config.REFERRAL_INVITER_CHIPS)

    # Проверка 10: пригласивший удалил данные (referrer_id = 0) -> ничего не начисляется
    path3 = new_db()
    client3 = TestClient(create_app(TOKEN, [], db_path=path3))
    client3.get("/api/me", headers=auth(U_INVITER))
    code3 = client3.get("/api/referral", headers=auth(U_INVITER)).json()["link"].split("ref_")[1]
    client3.get("/api/me", headers=auth(U_INVITEE, extra={"start_param": "ref_" + code3}))
    db.delete_player_data(U_INVITER, db_path=path3)
    
    set_invitee_stats(path3, U_INVITEE, economy_config.REFERRAL_QUALIFY_HOURS, economy_config.REFERRAL_QUALIFY_LEVEL, economy_config.REFERRAL_QUALIFY_ROUNDS)
    client3.get("/api/me", headers=auth(U_INVITEE))
    conn3 = sqlite3.connect(path3)
    q = conn3.execute("SELECT qualified_at FROM referrals WHERE invitee_id = ?", (U_INVITEE,)).fetchone()[0]
    conn3.close()
    check("удалённый реферер не квалифицируется", q, None)

    # Проверка 11: приглашённый без реферала ничего не получает
    path4 = new_db()
    client4 = TestClient(create_app(TOKEN, [], db_path=path4))
    client4.get("/api/me", headers=auth(U_INVITEE))
    set_invitee_stats(path4, U_INVITEE, economy_config.REFERRAL_QUALIFY_HOURS, economy_config.REFERRAL_QUALIFY_LEVEL, economy_config.REFERRAL_QUALIFY_ROUNDS)
    # Shouldn't crash
    client4.get("/api/me", headers=auth(U_INVITEE))

    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

import testenv  # noqa: F401
import os
import sqlite3
import time

import economy_config
import db
import tg_testutil

from features.referral_db import get_or_create_code, link_for

NOW = 1700000000
INVITER = 101
INVITEE = 102
GAME_LINK = "https://t.me/test_bot/app"

def test_referral_binding():
    os.environ["GAME_LINK"] = GAME_LINK
    db.init_db()

    # Создаём пригласившего
    db.get_player(INVITER, now=NOW)
    code = get_or_create_code(INVITER, now=NOW)
    assert len(code) == 10
    assert code.isalnum()
    
    # Проверка ссылки
    link = link_for(code)
    assert link == f"{GAME_LINK}?startapp=ref_{code}"

    # Первый вход приглашённого с правильным start_param
    init_data = tg_testutil.make_init_data(INVITEE, now=NOW, extra={"start_param": f"ref_{code}"})
    
    # Эмулируем запрос к /api/me (вызывает _register_player с start_param)
    from web.context import Ctx
    from auth import validate_init_data_full
    info = validate_init_data_full(init_data, "test_token", now=NOW)
    assert info["start_param"] == f"ref_{code}"
    
    # Прямой вызов _register_player как в routes_account
    from core.db_conn import _connect
    conn = _connect()
    conn.execute("BEGIN IMMEDIATE")
    db._register_player(conn, INVITEE, NOW, start_param=info["start_param"])
    conn.commit()
    conn.close()
    
    # Проверяем бонус
    player = db.get_player(INVITEE, now=NOW)
    from economy import START_BALANCE
    expected = START_BALANCE + economy_config.REFERRAL_INVITEE_CHIPS
    assert player["balance"] == expected, f"Expected {expected}, got {player['balance']}"
    
    # Проверяем таблицу referrals
    conn = _connect()
    refs = conn.execute("SELECT * FROM referrals WHERE invitee_id = ?", (INVITEE,)).fetchall()
    assert len(refs) == 1
    assert refs[0]["referrer_id"] == INVITER
    conn.close()

def test_referral_already_exists():
    db.init_db()
    code = get_or_create_code(INVITER, now=NOW)
    
    # Создаём игрока БЕЗ рефералки
    db.get_player(INVITEE, now=NOW)
    
    # Повторный вход со ссылкой
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.execute("BEGIN IMMEDIATE")
    db._register_player(conn, INVITEE, NOW, start_param=f"ref_{code}")
    conn.commit()
    conn.close()
    
    # Бонус не должен начислиться
    player = db.get_player(INVITEE, now=NOW)
    from economy import START_BALANCE
    assert player["balance"] == START_BALANCE
    
    # Записи в referrals быть не должно
    conn = sqlite3.connect(os.environ["DB_PATH"])
    refs = conn.execute("SELECT * FROM referrals WHERE invitee_id = ?", (INVITEE,)).fetchall()
    assert len(refs) == 0
    conn.close()

def test_referral_invalid_code():
    db.init_db()
    # Вход с неверным кодом
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.execute("BEGIN IMMEDIATE")
    db._register_player(conn, INVITEE, NOW, start_param="ref_invalid123")
    conn.commit()
    conn.close()
    
    # Создан нормально, но без бонуса
    player = db.get_player(INVITEE, now=NOW)
    from economy import START_BALANCE
    assert player["balance"] == START_BALANCE

def test_referral_self_bind():
    db.init_db()
    db.get_player(INVITER, now=NOW)
    code = get_or_create_code(INVITER, now=NOW)
    
    # Самореферал не работает, потому что он уже создан, но даже если бы создавался
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.execute("BEGIN IMMEDIATE")
    db._register_player(conn, INVITER, NOW, start_param=f"ref_{code}")
    conn.commit()
    conn.close()
    
    player = db.get_player(INVITER, now=NOW)
    from economy import START_BALANCE
    assert player["balance"] == START_BALANCE

def test_referral_cooldown():
    db.init_db()
    code = get_or_create_code(INVITER, now=NOW)
    
    # Создаём, удаляем
    db.get_player(INVITEE, now=NOW)
    db.delete_player_data(INVITEE, now=NOW)
    
    # Заходим по ссылке во время кулдауна
    conn = sqlite3.connect(os.environ["DB_PATH"])
    conn.execute("BEGIN IMMEDIATE")
    db._register_player(conn, INVITEE, NOW + 10, start_param=f"ref_{code}")
    conn.commit()
    conn.close()
    
    # Баланс 0, привязки нет
    player = db.get_player(INVITEE, now=NOW + 10)
    assert player["balance"] == 0
    
    conn = sqlite3.connect(os.environ["DB_PATH"])
    refs = conn.execute("SELECT * FROM referrals WHERE invitee_id = ?", (INVITEE,)).fetchall()
    assert len(refs) == 0
    conn.close()

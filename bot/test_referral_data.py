import testenv  # noqa: F401
import os
import sqlite3
import tempfile
import time

from fastapi.testclient import TestClient

import db
from api import create_app
from core.db_conn import _connect
from features.referral_db import get_or_create_code
from tg_testutil import make_init_data

TOKEN = "123456:TEST-TOKEN-not-real"
U_INVITER = 777111
U_INVITEE = 777222

_ENV_KEYS = ("GAME_LINK", "DB_PATH", "TOMBSTONE_SECRET")
_saved_env = {k: os.environ.pop(k, None) for k in _ENV_KEYS}
os.environ["TOMBSTONE_SECRET"] = "test-secret-not-real"
os.environ["GAME_LINK"] = "https://t.me/TestBot/game"

def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"

tmp = tempfile.mkdtemp()
counter = [0]

def new_db():
    counter[0] += 1
    path = os.path.join(tmp, "ref%d.db" % counter[0])
    db.init_db(path)
    os.environ["DB_PATH"] = path
    return path

def sql(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()

def auth(uid, extra=None):
    return {"Authorization": "tma " + make_init_data(TOKEN, user_id=uid, auth_date=int(time.time()), first_name="Игрок", extra=extra)}

try:
    path = new_db()
    client = TestClient(create_app(TOKEN, [], db_path=path))
    
    # 401 without signature
    check("GET /api/referral 401", client.get("/api/referral").status_code, 401)
    
    client.get("/api/me", headers=auth(U_INVITER))
    # GET /api/referral keys & valid link
    r = client.get("/api/referral", headers=auth(U_INVITER))
    check("GET /api/referral status 200", r.status_code, 200)
    data = r.json()
    check("Keys in response", sorted(data.keys()), ["invited", "link", "qualified"])
    check("invited is 0", data["invited"], 0)
    check("qualified is 0", data["qualified"], 0)
    check("link has code", "startapp=ref_" in data["link"], True)
    
    # link should be null if GAME_LINK not valid
    os.environ.pop("GAME_LINK")
    client2 = TestClient(create_app(TOKEN, [], db_path=path))
    r = client2.get("/api/referral", headers=auth(U_INVITER))
    check("link is null without GAME_LINK", r.json()["link"], None)
    os.environ["GAME_LINK"] = "https://t.me/TestBot/game"
    
    # Now simulate binding invitee using GET /api/me with start_param
    code = data["link"].split("ref_")[1]
    r = client.get("/api/me", headers=auth(U_INVITEE, extra={"start_param": "ref_"+code}))
    # check that invited is now 1
    r = client.get("/api/referral", headers=auth(U_INVITER))
    check("invited increased", r.json()["invited"], 1)
    check("qualified is still 0", r.json()["qualified"], 0)
    
    # Check /mydata for inviter
    export_inviter = db.get_player_export(U_INVITER, db_path=path)
    check("inviter /mydata keys", sorted(export_inviter["referral"].keys()), ["invited_by_someone", "invited_count", "qualified_count"])
    check("inviter referral data", export_inviter["referral"], {"invited_by_someone": False, "invited_count": 1, "qualified_count": 0})
    
    # Check /mydata for invitee
    export_invitee = db.get_player_export(U_INVITEE, db_path=path)
    check("invitee referral data", export_invitee["referral"], {"invited_by_someone": True, "invited_count": 0, "qualified_count": 0})
    
    # Check privacy: no other IDs
    import json
    dump = json.dumps(export_inviter)
    check("no invitee id in inviter dump", str(U_INVITEE) in dump, False)
    check("no referral code in dump", code in dump, False)
    
    # Check delete invitee
    counts = db.delete_player_data(U_INVITEE, db_path=path)
    check("counts has referrals_as_invitee", "referrals_as_invitee" in counts, True)
    check("counts referrals_as_invitee", counts["referrals_as_invitee"], 1)
    check("counts referrals_as_referrer", counts["referrals_as_referrer"], 0)
    
    # Inviter's invited count should decrease
    r = client.get("/api/referral", headers=auth(U_INVITER))
    check("invited decreased after invitee delete", r.json()["invited"], 0)
    
    # Add invitee 2
    U_INVITEE_2 = 777333
    client.get("/api/me", headers=auth(U_INVITEE_2, extra={"start_param": "ref_"+code}))
    check("invited back to 1", client.get("/api/referral", headers=auth(U_INVITER)).json()["invited"], 1)
    
    # Delete inviter
    counts2 = db.delete_player_data(U_INVITER, db_path=path)
    check("counts2 referrals_as_referrer", counts2["referrals_as_referrer"], 1)
    check("counts2 referral_codes", counts2["referral_codes"], 1)
    
    # Invitee 2 should still be there but referrer_id=0
    export_invitee2 = db.get_player_export(U_INVITEE_2, db_path=path)
    check("invitee 2 invited_by_someone is False after inviter deleted", export_invitee2["referral"]["invited_by_someone"], False)

    print("Все проверки прошли")
finally:
    for k, v in _saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

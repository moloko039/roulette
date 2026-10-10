import time

import economy_config
from features.round_counts import ROUND_COUNT_SQL

def _big_crashes(conn, telegram_id):
    """Крахи не ниже порога патины: партии прежнего краша (закрыт, история остаётся) и закрытые ставки живого краша."""
    old = conn.execute(
        "SELECT COUNT(*) FROM crash_games WHERE telegram_id = ? AND finished_at IS NOT NULL AND crash_x100 >= ?",
        (telegram_id, economy_config.PATINA_BIG_CRASH_X100)).fetchone()[0]
    live = conn.execute(
        "SELECT COUNT(*) FROM crash_bets b JOIN crash_rounds r ON r.id = b.round_id "
        "WHERE b.telegram_id = ? AND b.status != 'open' AND r.crash_x100 >= ?",
        (telegram_id, economy_config.PATINA_BIG_CRASH_X100)).fetchone()[0]
    return old + live


def patina_counters(conn, telegram_id):
    """Счётчики для патины (используются в тестах)."""
    crashes = _big_crashes(conn, telegram_id)
    
    explosions = conn.execute(
        "SELECT COUNT(*) FROM mines_games WHERE telegram_id = ? AND status = 'lost' AND finished_at IS NOT NULL",
        (telegram_id,)
    ).fetchone()[0]
    
    rounds = 0
    for query in ROUND_COUNT_SQL:
        rounds += conn.execute(query, (telegram_id,)).fetchone()[0]
        
    return {"big_crashes": crashes, "explosions": explosions, "rounds": rounds}

def _calc_stage(val, thresholds):
    stage = 0
    for t in thresholds:
        if val >= t:
            stage += 1
        else:
            break
    return stage

def account_age_stage(conn, telegram_id, now=None):
    """Стадия рамки «Патина» по стажу аккаунта (дней с первого входа): 0..4. Игрока нет: 0."""
    row = conn.execute("SELECT created_at FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if row is None:
        return 0
    days = max(0, (int(time.time()) if now is None else now) - row["created_at"]) // 86400
    return _calc_stage(days, economy_config.PATINA_FRAME_DAYS)


def _keno_rounds(conn, telegram_id):
    return conn.execute("SELECT COUNT(*) FROM keno_rounds WHERE telegram_id = ?", (telegram_id,)).fetchone()[0]


def _crash_rounds(conn, telegram_id):
    """Сыгранные раунды краша: закрытые ставки живого краша и прежние партии."""
    old = conn.execute("SELECT COUNT(*) FROM crash_games WHERE telegram_id = ? AND finished_at IS NOT NULL", (telegram_id,)).fetchone()[0]
    live = conn.execute("SELECT COUNT(*) FROM crash_bets WHERE telegram_id = ? AND status != 'open'", (telegram_id,)).fetchone()[0]
    return old + live


def account_days(conn, telegram_id, now=None):
    """Дней с первого входа (для именного жетона «Патина»)."""
    row = conn.execute("SELECT created_at FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    if row is None:
        return 0
    return max(0, (int(time.time()) if now is None else now) - row["created_at"]) // 86400


def patina_info(conn, telegram_id, equipped, now=None):
    """Числа для рисунка надетой патины (не стадии): chip_notches (засечки на ребре жетона, не больше PATINA_MAX_NOTCHES) и days (именной жетон). Только для надетого, иначе пусто."""
    info = {}
    if equipped.get("chip") == "chip_patina":
        info["chip_notches"] = min(economy_config.PATINA_MAX_NOTCHES, _big_crashes(conn, telegram_id))
    if equipped.get("badge") == "badge_patina":
        info["days"] = account_days(conn, telegram_id, now)
    return info


def patina_stages(conn, telegram_id, equipped, now=None):
    """Словарь {слот: стадия} только для слотов с надетой патиной, либо пустой."""
    res = {}
    need_crashes = equipped.get("chip") == "chip_patina"
    need_rounds = equipped.get("card_back") == "back_patina"
    need_explosions = equipped.get("mine_icons") == "mine_patina"
    need_frame = equipped.get("avatar_frame") == "frame_patina"
    need_keno = equipped.get("keno_ball") == "keno_patina"
    need_crash_skin = equipped.get("crash") == "crash_patina"
    need_badge = equipped.get("badge") == "badge_patina"

    if not (need_crashes or need_rounds or need_explosions or need_frame or need_keno or need_crash_skin or need_badge):
        return res
    if need_badge:
        res["badge"] = account_age_stage(conn, telegram_id, now)
    if need_keno:
        res["keno_ball"] = _calc_stage(_keno_rounds(conn, telegram_id), economy_config.PATINA_STAGE_THRESHOLDS["keno_ball"])
    if need_crash_skin:
        res["crash"] = _calc_stage(_crash_rounds(conn, telegram_id), economy_config.PATINA_STAGE_THRESHOLDS["crash"])
    if need_frame:
        res["avatar_frame"] = account_age_stage(conn, telegram_id, now)
        
    if need_crashes:
        crashes = _big_crashes(conn, telegram_id)
        res["chip"] = _calc_stage(crashes, economy_config.PATINA_STAGE_THRESHOLDS["chip"])
        
    if need_rounds:
        rounds = 0
        for query in ROUND_COUNT_SQL:
            rounds += conn.execute(query, (telegram_id,)).fetchone()[0]
        res["card_back"] = _calc_stage(rounds, economy_config.PATINA_STAGE_THRESHOLDS["card_back"])
        
    if need_explosions:
        explosions = conn.execute(
            "SELECT COUNT(*) FROM mines_games WHERE telegram_id = ? AND status = 'lost' AND finished_at IS NOT NULL",
            (telegram_id,)
        ).fetchone()[0]
        res["mine_icons"] = _calc_stage(explosions, economy_config.PATINA_STAGE_THRESHOLDS["mine_icons"])
        
    return res

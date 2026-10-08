import economy_config
from features.round_counts import ROUND_COUNT_SQL

def patina_counters(conn, telegram_id):
    """Счётчики для патины (используются в тестах)."""
    crashes = conn.execute(
        "SELECT COUNT(*) FROM crash_games WHERE telegram_id = ? AND finished_at IS NOT NULL AND crash_x100 >= ?",
        (telegram_id, economy_config.PATINA_BIG_CRASH_X100)
    ).fetchone()[0]
    
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

def patina_stages(conn, telegram_id, equipped):
    """Словарь {слот: стадия} только для слотов с надетой патиной, либо пустой."""
    res = {}
    need_crashes = equipped.get("chip") == "chip_patina"
    need_rounds = equipped.get("card_back") == "back_patina"
    need_explosions = equipped.get("mine_icons") == "mine_patina"
    
    if not (need_crashes or need_rounds or need_explosions):
        return res
        
    if need_crashes:
        crashes = conn.execute(
            "SELECT COUNT(*) FROM crash_games WHERE telegram_id = ? AND finished_at IS NOT NULL AND crash_x100 >= ?",
            (telegram_id, economy_config.PATINA_BIG_CRASH_X100)
        ).fetchone()[0]
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

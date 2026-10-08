def record(conn, telegram_id, event, now, **params):
    code = None
    should_grant = False

    if event == "crash_crash":
        if params.get("mult_x100", 0) <= 101:
            code = "achv_nearly"
            row = conn.execute("SELECT count FROM achievement_progress WHERE telegram_id = ? AND code = ?", (telegram_id, code)).fetchone()
            count = row[0] if row else 0
            count += 1
            if count >= 5:
                should_grant = True
            conn.execute(
                "INSERT INTO achievement_progress (telegram_id, code, count, streak, done_at) VALUES (?, ?, ?, 0, ?) "
                "ON CONFLICT(telegram_id, code) DO UPDATE SET count = excluded.count, done_at = COALESCE(achievement_progress.done_at, excluded.done_at)",
                (telegram_id, code, count, now if should_grant else None)
            )

    elif event == "mines_explode":
        if params.get("is_first_move", False):
            code = "achv_sapper"
            should_grant = True
            conn.execute(
                "INSERT INTO achievement_progress (telegram_id, code, count, streak, done_at) VALUES (?, ?, 1, 0, ?) "
                "ON CONFLICT(telegram_id, code) DO NOTHING",
                (telegram_id, code, now)
            )

    elif event == "blackjack_end":
        code = "achv_bust"
        busted = params.get("busted", False)
        row = conn.execute("SELECT streak FROM achievement_progress WHERE telegram_id = ? AND code = ?", (telegram_id, code)).fetchone()
        streak = row[0] if row else 0
        
        if busted:
            streak += 1
            if streak >= 3:
                should_grant = True
        else:
            streak = 0
            
        conn.execute(
            "INSERT INTO achievement_progress (telegram_id, code, count, streak, done_at) VALUES (?, ?, 0, ?, ?) "
            "ON CONFLICT(telegram_id, code) DO UPDATE SET streak = excluded.streak, done_at = COALESCE(achievement_progress.done_at, excluded.done_at)",
            (telegram_id, code, streak, now if should_grant else None)
        )

    elif event == "keno_end":
        if params.get("chosen", 0) == 10 and params.get("matched", 0) == 0:
            code = "achv_keno"
            should_grant = True
            conn.execute(
                "INSERT INTO achievement_progress (telegram_id, code, count, streak, done_at) VALUES (?, ?, 1, 0, ?) "
                "ON CONFLICT(telegram_id, code) DO NOTHING",
                (telegram_id, code, now)
            )

    if should_grant and code:
        conn.execute(
            "INSERT OR IGNORE INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, ?, 'achievement', NULL, ?)",
            (telegram_id, code, now)
        )

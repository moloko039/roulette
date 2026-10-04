"""Права на данные: выгрузка (/mydata) и удаление (/deletemydata)."""

import json
import time

import antiabuse
from antiabuse import COOLDOWN_SECONDS
from antiabuse import TombstoneUnavailable
import hilo
import mines

from core.db_conn import _connect
from core.members import _member_name


def get_player_export(telegram_id, rounds_limit=100, db_path=None):
    """Данные игрока для /mydata (только чтение). None, если о нём вообще ничего нет.

    В выгрузку не входят идентификаторы чатов и данные других игроков.
    """
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN")  # один снимок для всех запросов
        try:
            player = conn.execute(
                "SELECT telegram_id, balance, rate, last_accrual, accrual_acc, created_at, total_staked, xp, income_level, storage_level "
                "FROM players WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            rounds = conn.execute(
                "SELECT created_at, bets_json, number, stake_total, payout_total FROM roulette_rounds "
                "WHERE telegram_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            purchases = conn.execute(
                "SELECT created_at, kind, level_after, cost FROM farm_purchases "
                "WHERE telegram_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            games = conn.execute(
                "SELECT created_at, bet, mines_count, revealed_mask, status, payout, finished_at FROM mines_games "
                "WHERE telegram_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            keno_rounds = conn.execute(
                "SELECT created_at, bet, picks_json, draw_json, hit_count, payout FROM keno_rounds "
                "WHERE telegram_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            bj_games = conn.execute(
                "SELECT created_at, bet, wager, player_json, dealer_json, result, payout, finished_at FROM blackjack_games "
                "WHERE telegram_id = ? AND status = 'finished' ORDER BY created_at DESC, id DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            bj_active = conn.execute(
                "SELECT 1 FROM blackjack_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,)
            ).fetchone() is not None
            crash_games = conn.execute(
                "SELECT created_at, bet, mode, target_x100, crash_x100, result, payout, finished_at FROM crash_games "
                "WHERE telegram_id = ? AND status = 'finished' ORDER BY created_at DESC, id DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            crash_active = conn.execute(
                "SELECT 1 FROM crash_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,)
            ).fetchone() is not None
            hilo_games = conn.execute(
                "SELECT created_at, bet, steps, mult_num, mult_den, status, payout, finished_at FROM hilo_games "
                "WHERE telegram_id = ? AND status != 'active' ORDER BY created_at DESC, id DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            hilo_active = conn.execute(
                "SELECT 1 FROM hilo_games WHERE telegram_id = ? AND status = 'active'", (telegram_id,)
            ).fetchone() is not None
            transfer_rows = conn.execute(
                "SELECT sender, recipient, amount, fee, created_at FROM transfers WHERE sender = ? OR recipient = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?", (telegram_id, telegram_id, rounds_limit)).fetchall()
            transfer_items = [
                {"time": r["created_at"], "direction": "out" if r["sender"] == telegram_id else "in",
                 "name": _member_name(conn, r["recipient"] if r["sender"] == telegram_id else r["sender"]),
                 "amount": r["amount"], "fee": r["fee"]} for r in transfer_rows]
            chats = conn.execute(
                "SELECT first_seen, last_seen, first_name FROM chat_members "
                "WHERE telegram_id = ? ORDER BY first_seen, last_seen",
                (telegram_id,),
            ).fetchall()
        finally:
            conn.execute("COMMIT")
    finally:
        conn.close()
    if player is None and not rounds and not chats and not purchases and not games and not keno_rounds and not bj_games and not bj_active and not crash_games and not crash_active and not hilo_games and not hilo_active and not transfer_items:
        return None
    return {
        "player": dict(player) if player is not None else None,
        "rounds": [
            {"time": r["created_at"], "bets": json.loads(r["bets_json"]), "number": r["number"],
             "stake_total": r["stake_total"], "payout_total": r["payout_total"]}
            for r in rounds
        ],
        # раскладка мин (mine_mask) в выгрузку не входит никогда: она раскрыла бы поле текущей игры
        "mines_games": [
            {"created_at": g["created_at"], "bet": g["bet"], "mines": g["mines_count"],
             "opened": mines.popcount(g["revealed_mask"]), "status": g["status"], "payout": g["payout"],
             "finished_at": g["finished_at"]}
            for g in games
        ],
        # колода и карты незавершённой раздачи в выгрузку не входят: они раскрыли бы скрытую карту дилера
        "blackjack_games": [
            {"created_at": g["created_at"], "bet": g["bet"], "wager": g["wager"],
             "player_cards": json.loads(g["player_json"]), "dealer_cards": json.loads(g["dealer_json"]),
             "result": g["result"], "payout": g["payout"], "finished_at": g["finished_at"]}
            for g in bj_games
        ],
        "blackjack_active": bj_active,
        # точка краха активного раунда в выгрузку не входит: она раскрыла бы исход
        "crash_games": [
            {"created_at": g["created_at"], "bet": g["bet"], "mode": g["mode"], "target_x100": g["target_x100"],
             "crash_x100": g["crash_x100"], "result": g["result"], "payout": g["payout"], "finished_at": g["finished_at"]}
            for g in crash_games
        ],
        "crash_active": crash_active,
        # хило: карты незавершённой партии в выгрузку не входят (текущая карта и история видны игроку в игре, но не нужны здесь)
        "hilo_games": [
            {"created_at": g["created_at"], "bet": g["bet"], "steps": g["steps"],
             "multiplier": hilo.multiplier_text(hilo.frac(g["mult_num"], g["mult_den"])), "status": g["status"],
             "payout": g["payout"], "finished_at": g["finished_at"]}
            for g in hilo_games
        ],
        "hilo_active": hilo_active,
        # идентификаторы Telegram других игроков не включаются: только имя второй стороны как в рейтинге
        "transfers": transfer_items,
        "keno_rounds": [
            {"time": k["created_at"], "bet": k["bet"], "picks": json.loads(k["picks_json"]),
             "draw": json.loads(k["draw_json"]), "hits": k["hit_count"], "payout": k["payout"]}
            for k in keno_rounds
        ],
        "farm_purchases": [
            {"time": p["created_at"], "kind": p["kind"], "level": p["level_after"], "cost": p["cost"]}
            for p in purchases
        ],
        "chats": [{"first_seen": c["first_seen"], "last_seen": c["last_seen"], "name": c["first_name"]}
                  for c in chats],
    }


def delete_player_data(telegram_id, db_path=None, now=None):
    """Удаляет все данные игрока в одной транзакции. Возвращает число удалённых строк по таблицам.

    В той же транзакции, если у игрока была строка в players, записывается «надгробие»:
    HMAC-хэш идентификатора и время удаления (идентификатор не хранится). Просроченные
    надгробия удаляются. Без TOMBSTONE_SECRET ничего не удаляется: бросается
    TombstoneUnavailable.
    """
    secret = antiabuse.tombstone_secret()
    if secret is None:
        raise TombstoneUnavailable()
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            counts = {
                "players": conn.execute("DELETE FROM players WHERE telegram_id = ?", (telegram_id,)).rowcount,
                "roulette_rounds": conn.execute(
                    "DELETE FROM roulette_rounds WHERE telegram_id = ?", (telegram_id,)).rowcount,
                "chat_members": conn.execute(
                    "DELETE FROM chat_members WHERE telegram_id = ?", (telegram_id,)).rowcount,
                "farm_purchases": conn.execute(
                    "DELETE FROM farm_purchases WHERE telegram_id = ?", (telegram_id,)).rowcount,
                # незавершённая игра удаляется вместе со ставкой
                "mines_games": conn.execute(
                    "DELETE FROM mines_games WHERE telegram_id = ?", (telegram_id,)).rowcount,
            }
            conn.execute("DELETE FROM mines_actions WHERE telegram_id = ?", (telegram_id,))
            counts["blackjack_games"] = conn.execute(
                "DELETE FROM blackjack_games WHERE telegram_id = ?", (telegram_id,)).rowcount   # и незавершённая вместе со ставкой
            conn.execute("DELETE FROM blackjack_actions WHERE telegram_id = ?", (telegram_id,))
            counts["crash_games"] = conn.execute(
                "DELETE FROM crash_games WHERE telegram_id = ?", (telegram_id,)).rowcount   # и незавершённый вместе со ставкой
            conn.execute("DELETE FROM crash_actions WHERE telegram_id = ?", (telegram_id,))
            counts["hilo_games"] = conn.execute(
                "DELETE FROM hilo_games WHERE telegram_id = ?", (telegram_id,)).rowcount   # и незавершённая вместе со ставкой
            conn.execute("DELETE FROM hilo_actions WHERE telegram_id = ?", (telegram_id,))
            counts["transfers"] = conn.execute(
                "DELETE FROM transfers WHERE sender = ? OR recipient = ?", (telegram_id, telegram_id)).rowcount
            counts["keno_rounds"] = conn.execute(
                "DELETE FROM keno_rounds WHERE telegram_id = ?", (telegram_id,)).rowcount
            if counts["players"] > 0:
                conn.execute(
                    "INSERT OR REPLACE INTO deletion_tombstones (key_hash, deleted_at) VALUES (?, ?)",
                    (antiabuse.key_hash(telegram_id, secret), now),
                )
            conn.execute(
                "DELETE FROM deletion_tombstones WHERE deleted_at + ? <= ?", (COOLDOWN_SECONDS, now)
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return counts

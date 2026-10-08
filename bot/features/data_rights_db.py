"""Права на данные: выгрузка (/mydata) и удаление (/deletemydata)."""

import json
import time

import antiabuse
from antiabuse import COOLDOWN_SECONDS
from antiabuse import TombstoneUnavailable
import hilo
import mines
import transfers

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
            slot_rounds = conn.execute(
                "SELECT created_at, coin, bought, cost, payout, round_json FROM slot_rounds "
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
            cosmetic_items = conn.execute(
                "SELECT item_code, source, acquired_at FROM cosmetic_items WHERE telegram_id = ? ORDER BY acquired_at, item_code",
                (telegram_id,)).fetchall()
            cosmetic_equipped = conn.execute(
                "SELECT slot, item_code FROM cosmetic_equipped WHERE telegram_id = ? ORDER BY slot", (telegram_id,)).fetchall()
            purchase_rows = conn.execute(
                "SELECT item_code, amount_stars, created_at, status FROM cosmetic_purchases WHERE telegram_id = ? ORDER BY created_at, id", (telegram_id,)).fetchall()
            cosmetic_pref = conn.execute(
                "SELECT show_in_rating FROM cosmetic_prefs WHERE telegram_id = ?", (telegram_id,)).fetchone()
            gem_balance = conn.execute("SELECT gems FROM gem_balances WHERE telegram_id = ?", (telegram_id,)).fetchone()
            gem_ledger = conn.execute("SELECT created_at, delta, reason FROM gems_ledger WHERE telegram_id = ? ORDER BY id", (telegram_id,)).fetchall()
            gifts_sent = conn.execute("SELECT item_code, gems, created_at FROM gifts WHERE from_user = ? ORDER BY id", (telegram_id,)).fetchall()
            gifts_received = conn.execute("SELECT item_code, from_name, created_at FROM gifts WHERE to_user = ? ORDER BY id", (telegram_id,)).fetchall()
            streak_rows = conn.execute("SELECT day, streak_day, cycle, chips, gems FROM streak_claims WHERE telegram_id = ? ORDER BY day", (telegram_id,)).fetchall()
            chip_purchase_rows = conn.execute(
                "SELECT pack_code, gems, chips, created_at FROM chip_purchases WHERE telegram_id = ? ORDER BY id", (telegram_id,)).fetchall()
            gem_purchase_rows = conn.execute(
                "SELECT pack_code, amount_stars, gems, status, created_at FROM gem_purchases WHERE telegram_id = ? ORDER BY created_at", (telegram_id,)).fetchall()
            best_win = conn.execute(
                "SELECT game, net_amount, achieved_at FROM player_best_win WHERE telegram_id = ?", (telegram_id,)).fetchone()
            chats = conn.execute(
                "SELECT first_seen, last_seen, first_name FROM chat_members "
                "WHERE telegram_id = ? ORDER BY first_seen, last_seen",
                (telegram_id,),
            ).fetchall()
            chat_boost_rows = conn.execute(
                "SELECT created_at FROM chat_boosts WHERE telegram_id = ? ORDER BY id",
                (telegram_id,),
            ).fetchall()
            achievement_rows = conn.execute(
                "SELECT code, done_at FROM achievement_progress WHERE telegram_id = ? AND done_at IS NOT NULL ORDER BY done_at, code", (telegram_id,)
            ).fetchall()
        finally:
            conn.execute("COMMIT")
    finally:
        conn.close()
    if player is None and not rounds and not chats and not purchases and not games and not keno_rounds and not slot_rounds and not bj_games and not bj_active and not crash_games and not crash_active and not hilo_games and not hilo_active and not transfer_items and not cosmetic_items and not cosmetic_equipped and cosmetic_pref is None and not purchase_rows and best_win is None:
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
        # без платёжных идентификаторов: payment_ref в выгрузку не входит
        "cosmetics": {
            "items": [{"code": c["item_code"], "source": c["source"], "acquired_at": c["acquired_at"]} for c in cosmetic_items],
            "equipped": {c["slot"]: c["item_code"] for c in cosmetic_equipped},
            "show_in_rating": True if cosmetic_pref is None else bool(cosmetic_pref["show_in_rating"]),
            # покупки за Stars: без идентификатора платежа (он остаётся у владельца для споров и возвратов)
            "purchases": [{"item_code": r["item_code"], "amount_stars": r["amount_stars"], "time": r["created_at"], "status": r["status"]} for r in purchase_rows],
        },
        # подарки: отправленные (предмет, кристаллы, время; получатель не указывается) и полученные (предмет, имя дарителя на момент подарка, время)
        "gifts": {"sent": [{"item": r["item_code"], "gems": r["gems"], "time": r["created_at"]} for r in gifts_sent],
                  "received": [{"item": r["item_code"], "from": r["from_name"], "time": r["created_at"]} for r in gifts_received]},
        # серия входов: день (московская дата как число), день цикла, цикл, фишки и кристаллы награды
        "streak": [{"day": r["day"], "streak_day": r["streak_day"], "cycle": r["cycle"], "chips": r["chips"], "gems": r["gems"]} for r in streak_rows],
        # кристаллы: баланс, журнал изменений (без идентификаторов платежей) и оплаты пакетов Stars
        "gems": {
            "balance": 0 if gem_balance is None else gem_balance["gems"],
            "ledger": [{"time": r["created_at"], "delta": r["delta"], "reason": r["reason"]} for r in gem_ledger],
            "purchases": [{"pack": r["pack_code"], "amount_stars": r["amount_stars"], "gems": r["gems"], "status": r["status"], "time": r["created_at"]} for r in gem_purchase_rows],
            "chip_purchases": [{"pack": r["pack_code"], "gems": r["gems"], "chips": r["chips"], "time": r["created_at"]} for r in chip_purchase_rows],
        },
        # личный рекорд (лучший чистый выигрыш за раунд); его видят участники бесед: имя, сумма и игра
        "best_win": None if best_win is None else {"game": best_win["game"], "net_amount": best_win["net_amount"], "achieved_at": best_win["achieved_at"]},
        "keno_rounds": [
            {"time": k["created_at"], "bet": k["bet"], "picks": json.loads(k["picks_json"]),
             "draw": json.loads(k["draw_json"]), "hits": k["hit_count"], "payout": k["payout"]}
            for k in keno_rounds
        ],
        # раунд слота целиком (поля, каскады, бесплатные вращения) в формате ответа API: это и хранится
        "slot_rounds": [
            {"time": s["created_at"], "coin": s["coin"], "bought": bool(s["bought"]), "cost": s["cost"], "payout": s["payout"],
             "round": json.loads(s["round_json"])}
            for s in slot_rounds
        ],
        "farm_purchases": [
            {"time": p["created_at"], "kind": p["kind"], "level": p["level_after"], "cost": p["cost"]}
            for p in purchases
        ],
        "chats": [{"first_seen": c["first_seen"], "last_seen": c["last_seen"], "name": c["first_name"]}
                  for c in chats],
        "chat_boosts": [{"time": r["created_at"], "gems": 50} for r in chat_boost_rows],
        "achievements": [{"code": r["code"], "time": r["done_at"]} for r in achievement_rows],
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
            # Переводы: собственные отправленные записи удаляются; записи, где удаляющий получатель, НЕ удаляются, а обезличиваются (его идентификатор
            # заменяется на transfers.ANONYMOUS_ID). Иначе отправитель вместе с сообщником, который получил перевод и удалил данные, обнулял бы себе
            # суточный лимит отправки (он считается по записям отправителя) и учёт комиссии. Обезличенные записи уходят по общему сроку хранения.
            counts["transfers"] = conn.execute("DELETE FROM transfers WHERE sender = ?", (telegram_id,)).rowcount
            counts["transfers_anonymized"] = conn.execute(
                "UPDATE transfers SET recipient = ? WHERE recipient = ?", (transfers.ANONYMOUS_ID, telegram_id)).rowcount
            counts["keno_rounds"] = conn.execute(
                "DELETE FROM keno_rounds WHERE telegram_id = ?", (telegram_id,)).rowcount
            counts["slot_rounds"] = conn.execute(
                "DELETE FROM slot_rounds WHERE telegram_id = ?", (telegram_id,)).rowcount
            counts["player_best_win"] = conn.execute("DELETE FROM player_best_win WHERE telegram_id = ?", (telegram_id,)).rowcount
            for table in ("cosmetic_items", "cosmetic_equipped", "cosmetic_prefs", "cosmetic_actions"):   # косметика удаляется вместе с игроком
                conn.execute("DELETE FROM " + table + " WHERE telegram_id = ?", (telegram_id,))
            # кристаллы удаляются вместе с игроком без возмещения (как купленные предметы); запись об оплате Stars (gem_purchases) остаётся на срок хранения
            conn.execute("DELETE FROM gems_ledger WHERE telegram_id = ?", (telegram_id,))
            conn.execute("DELETE FROM gem_balances WHERE telegram_id = ?", (telegram_id,))
            conn.execute("DELETE FROM chip_purchases WHERE telegram_id = ?", (telegram_id,))
            conn.execute("DELETE FROM streak_claims WHERE telegram_id = ?", (telegram_id,))
            # подарки: полученные удаляются вместе с предметом; отправленные остаются у получателя без имени дарителя (предмет подарен, имя и id отправителя стираются)
            conn.execute("DELETE FROM gifts WHERE to_user = ?", (telegram_id,))
            conn.execute("UPDATE gifts SET from_user = 0, from_name = '' WHERE from_user = ?", (telegram_id,))
            counts["achievement_progress"] = conn.execute("DELETE FROM achievement_progress WHERE telegram_id = ?", (telegram_id,)).rowcount
            conn.execute("DELETE FROM chat_boosts WHERE telegram_id = ?", (telegram_id,))
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

"""Рейтинг беседы и рекорды выигрыша."""

import time

import cosmetic_sets
import cosmetics
from levels import profile_level
from roulette import MAX_SAFE_INT

from core.db_conn import _connect
from core.kernel import _pending_accrual, _register_player
from core.members import MAX_CHAT_MEMBERS, _touch_member


TOP_SIZE = 10


def touch_chat_member(chat_instance, telegram_id, first_name, now=None, db_path=None):
    """Записывает или обновляет участника беседы (не чаще раза в 60 секунд). Возвращает True, если записал."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            changed = _touch_member(conn, chat_instance, telegram_id, first_name, now)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return changed


def _public_cosmetics(db_path, ids):
    """{telegram_id: {slot: код}} только для публичных слотов (cosmetics.PUBLIC_SLOTS), только надетых не стартовых предметов
    и только у игроков, не скрывших показ в рейтинге. Остальные слоты других игроков не отдаются никогда."""
    if not ids:
        return {}
    conn = _connect(db_path)
    try:
        marks = ",".join("?" * len(ids))
        slots = ",".join("?" * len(cosmetics.PUBLIC_SLOTS))
        rows = conn.execute(
            "SELECT e.telegram_id, e.slot, e.item_code FROM cosmetic_equipped e "
            "LEFT JOIN cosmetic_prefs p ON p.telegram_id = e.telegram_id "
            "WHERE e.telegram_id IN (" + marks + ") AND e.slot IN (" + slots + ") AND COALESCE(p.show_in_rating, 1) = 1",
            list(ids) + list(cosmetics.PUBLIC_SLOTS)).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        it = cosmetics.item(r["item_code"])
        if it is not None and it["slot"] == r["slot"] and not it["starter"]:
            out.setdefault(r["telegram_id"], {})[r["slot"]] = r["item_code"]
    return out


def _public_sets(db_path, ids):
    """{telegram_id: [код_коллекции]} полностью собранных коллекций игрока, если показ не скрыт.
    Для скрывших показ (show_in_rating = 0) и игроков без собранных коллекций список пуст."""
    if not ids:
        return {}
    conn = _connect(db_path)
    try:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            "SELECT i.telegram_id, i.item_code FROM cosmetic_items i "
            "LEFT JOIN cosmetic_prefs p ON p.telegram_id = i.telegram_id "
            "WHERE i.telegram_id IN (" + marks + ") AND COALESCE(p.show_in_rating, 1) = 1",
            list(ids)).fetchall()
    finally:
        conn.close()
    items_by_id = {}
    for r in rows:
        items_by_id.setdefault(r["telegram_id"], set()).add(r["item_code"])
    out = {}
    for uid, codes in items_by_id.items():
        done = [c["code"] for c in cosmetic_sets.progress(codes) if c["complete"]]
        if done:
            out[uid] = done
    return out


def chat_top(chat_instance, telegram_id, first_name, now=None, db_path=None):
    """Рейтинг беседы: до 10 лучших и позиция вызвавшего.

    Вызвавший записывается как участник (как в touch_chat_member). Баланс для показа =
    хранимый + то, что начислилось бы по accrue() на время now; в базе из-за этого
    расчёта ничего не меняется. Сортировка: баланс по убыванию, затем first_seen, затем
    telegram_id. Игроки без записи в players пропускаются.
    """
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # вызвавший должен быть в players, чтобы попасть в список без вызова /api/me;
            # начисление ему при этом не применяется (строка создаётся только для новых)
            _register_player(conn, telegram_id, now)
            _touch_member(conn, chat_instance, telegram_id, first_name, now)
            rows = conn.execute(
                "SELECT m.telegram_id, m.first_name, m.first_seen, p.balance, p.rate, p.last_accrual, p.accrual_acc, "
                "       p.total_staked, p.storage_level, p.xp "
                "FROM (SELECT telegram_id, first_name, first_seen FROM chat_members "
                "      WHERE chat_instance = ? ORDER BY last_seen DESC, telegram_id LIMIT ?) m "
                "JOIN players p ON p.telegram_id = m.telegram_id",
                (chat_instance, MAX_CHAT_MEMBERS),
            ).fetchall()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    entries = []
    for r in rows:
        earned = _pending_accrual(r, now)
        entries.append((-(r["balance"] + earned), r["first_seen"], r["telegram_id"], r["first_name"],
                        r["total_staked"], r["xp"]))
    entries.sort(key=lambda e: (e[0], e[1], e[2]))

    shown = _public_cosmetics(db_path, [e[2] for e in entries[:TOP_SIZE]])
    shown_sets = _public_sets(db_path, [e[2] for e in entries[:TOP_SIZE]])
    top = [
        {"rank": i + 1, "name": e[3], "balance": -e[0], "is_me": e[2] == telegram_id, "staked": e[4],
         "cosmetics": shown.get(e[2], {}),   # только публичные слоты (рамка, значок), если надеты и игрок их не скрыл
         "complete_sets": shown_sets.get(e[2], []),
         "level": profile_level(e[5]),   # уровень по опыту, поле staked остаётся информацией
         }
        for i, e in enumerate(entries[:TOP_SIZE])
    ]
    me = None
    for i, e in enumerate(entries):
        if e[2] == telegram_id:
            # total здесь число участников рейтинга (не сумма ставок); сумма ставок: staked и chat_staked
            me = {"rank": i + 1, "balance": -e[0], "total": len(entries), "staked": e[4],
                  "level": profile_level(e[5])}
    # сумма ставок всех участников того же набора, по которому строится рейтинг (без разбивки по людям)
    chat_staked = min(sum(e[4] for e in entries), MAX_SAFE_INT)
    return {"scope": "chat", "top": top, "me": me, "chat_staked": chat_staked}


def chat_best_wins(chat_instance, telegram_id, db_path=None):
    """Рекорды выигрыша беседы: до 10 лучших личных рекордов (чистый выигрыш за один раунд) и место вызвавшего.

    Участники определяются так же, как в chat_top: последние MAX_CHAT_MEMBERS участников этой беседы (chat_members), у которых есть
    строка в players; чужие беседы не видны. Только чтение: вызвавшего не регистрирует и не отмечает участником (это делает
    рейтинг баланса). Порядок: сумма по убыванию, при равенстве раньше достигший, затем telegram_id. В ответе нет telegram_id и времени;
    member_ref как в chat_top (у себя None); рамка и значок по тем же правилам видимости. «me»: место среди участников с рекордом
    (None, если своего рекорда нет), total: сколько в беседе участников с рекордом."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT m.telegram_id, m.first_name, b.game, b.net_amount "
            "FROM (SELECT telegram_id, first_name FROM chat_members WHERE chat_instance = ? ORDER BY last_seen DESC, telegram_id LIMIT ?) m "
            "JOIN players p ON p.telegram_id = m.telegram_id "
            "JOIN player_best_win b ON b.telegram_id = m.telegram_id "
            "ORDER BY b.net_amount DESC, b.achieved_at ASC, m.telegram_id ASC",
            (chat_instance, MAX_CHAT_MEMBERS)).fetchall()
    finally:
        conn.close()
    shown = _public_cosmetics(db_path, [r["telegram_id"] for r in rows[:TOP_SIZE]])
    top = [
        {"rank": i + 1, "name": r["first_name"], "net_amount": r["net_amount"], "game": r["game"], "is_me": r["telegram_id"] == telegram_id,
         "cosmetics": shown.get(r["telegram_id"], {})}
        for i, r in enumerate(rows[:TOP_SIZE])
    ]
    me = None
    for i, r in enumerate(rows):
        if r["telegram_id"] == telegram_id:
            me = {"rank": i + 1, "net_amount": r["net_amount"], "game": r["game"], "total": len(rows)}
    return {"scope": "chat", "top": top, "me": me, "total": len(rows)}

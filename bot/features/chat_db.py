"""Рейтинг беседы и список участников для выбора получателя перевода."""

import unicodedata
import time

import transfers
from levels import profile_level
from roulette import MAX_SAFE_INT

from core.db_conn import _connect
from core.kernel import _pending_accrual, _register_player
from core.members import MAX_CHAT_MEMBERS, _BIDI, _touch_member


def _clean_query(q):
    """Поисковый запрос списка участников: управляющие и двунаправленные символы отбрасываются, не больше 32 символов."""
    kept = "".join(ch for ch in q if unicodedata.category(ch) not in ("Cc", "Cs") and ch not in _BIDI)
    return kept.strip()[:transfers.MEMBERS_QUERY_MAX].strip()


def chat_members_page(telegram_id, chat_instance, q="", offset=0, db_path=None):
    """Участники беседы для выбора получателя перевода: (items, next_offset).

    Запрашивающий должен быть участником этой беседы (chat_instance None: no_chat, нет в chat_members: not_in_chat, как у
    перевода). Порядок: по последней активности (last_seen) по убыванию. q ищет по имени без учёта регистра по вхождению (в Python:
    SQLite lower() не знает кириллицу), пустой q = все. В ответе только name и member_ref (без балансов, уровней и
    идентификаторов Telegram); самого игрока в списке нет. Не больше MEMBERS_PAGE за страницу.
    Бросает transfers.TransferError или ValueError (плохой offset)."""
    if type(offset) is not int or not 0 <= offset <= transfers.MEMBERS_OFFSET_MAX:
        raise ValueError("offset")
    if chat_instance is None:
        raise transfers.TransferError("no_chat")
    needle = _clean_query(q).casefold()
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM chat_members WHERE chat_instance = ? AND telegram_id = ?", (chat_instance, telegram_id)).fetchone() is None:
            raise transfers.TransferError("not_in_chat")
        rows = conn.execute(
            "SELECT m.telegram_id, m.first_name FROM chat_members m JOIN players p ON p.telegram_id = m.telegram_id "
            "WHERE m.chat_instance = ? AND m.telegram_id != ? ORDER BY m.last_seen DESC, m.telegram_id LIMIT ?",
            (chat_instance, telegram_id, MAX_CHAT_MEMBERS)).fetchall()
    finally:
        conn.close()
    found = [r for r in rows if not needle or needle in r["first_name"].casefold()]
    page = found[offset:offset + transfers.MEMBERS_PAGE]
    items = [{"name": r["first_name"], "member_ref": transfers.member_ref(chat_instance, r["telegram_id"])} for r in page]
    next_offset = offset + transfers.MEMBERS_PAGE if len(found) > offset + transfers.MEMBERS_PAGE else None
    return items, next_offset


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

    top = [
        {"rank": i + 1, "name": e[3], "balance": -e[0], "is_me": e[2] == telegram_id, "staked": e[4],
         "level": profile_level(e[5]),   # уровень по опыту, поле staked остаётся информацией
         # непрозрачная метка для перевода (HMAC беседы и игрока); у самого себя нет
         "member_ref": None if e[2] == telegram_id else transfers.member_ref(chat_instance, e[2])}
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

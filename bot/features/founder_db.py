"""Основатель беседы (рефералка, E5): игрок, добавивший бота в группу, получает награду, когда беседа становится живой.

Бот узнаёт об основателе из события добавления в группу (`record_founder`: chat_id группы и id добавившего). В игре беседа известна только по chat_instance, который
из chat_id не выводится, поэтому связь ставится так: когда основатель сам открывает игру из группы (chat_instance в подписанных данных), его самая ранняя непривязанная группа
(добавлена не позже FOUNDER_LINK_DAYS дней назад) привязывается к этому chat_instance. Беседа живая, когда не меньше FOUNDER_ACTIVE_PLAYERS её участников уровня
FOUNDER_PLAYER_LEVEL и выше заходили в игру за последние FOUNDER_WINDOW_DAYS дней. Награда (фишки и кристаллы) один раз на группу, кристаллы в месячных потолках рефералки.
В базе хранятся только числовые chat_id, id основателя и времена; название группы и список участников бот не запрашивает."""
import time

import economy_config
import levels
import wallet
from roulette import MAX_SAFE_INT

from core.db_conn import _connect
from features.referral_db import grant_referral_gems


def record_founder(chat_id, founder_id, now=None, db_path=None):
    """Бота добавили в группу chat_id, добавил игрок founder_id. Запись одна на группу (повторное добавление основателя не меняет)."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("INSERT OR IGNORE INTO chat_founders (chat_id, founder_id, added_at) VALUES (?, ?, ?)", (chat_id, founder_id, now))
    finally:
        conn.close()


def _lively(conn, chat_instance, now):
    since = now - economy_config.FOUNDER_WINDOW_DAYS * 86400
    return conn.execute(
        "SELECT COUNT(*) FROM chat_members m JOIN players p ON p.telegram_id = m.telegram_id WHERE m.chat_instance = ? AND m.last_seen >= ? AND p.xp >= ?",
        (chat_instance, since, levels.threshold(economy_config.FOUNDER_PLAYER_LEVEL))).fetchone()[0] >= economy_config.FOUNDER_ACTIVE_PLAYERS


def founder_progress(chat_instance, telegram_id, now=None, db_path=None):
    """Вызывается, когда игрок открывает игру из группы: привязывает беседу к основателю и выдаёт награду, если беседа стала живой. Возвращает
    {"chips": n, "gems": n} при выдаче, иначе None. Без подходящих записей только читает (запись и блокировка не нужны)."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        if conn.execute("SELECT 1 FROM chat_founders WHERE (founder_id = ? AND chat_instance IS NULL) OR (chat_instance = ? AND rewarded_at IS NULL) LIMIT 1",
                        (telegram_id, chat_instance)).fetchone() is None:
            return None
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT 1 FROM chat_founders WHERE chat_instance = ?", (chat_instance,)).fetchone() is None:
                conn.execute(
                    "UPDATE chat_founders SET chat_instance = ? WHERE chat_id = (SELECT chat_id FROM chat_founders WHERE founder_id = ? AND chat_instance IS NULL AND added_at >= ? "
                    "ORDER BY added_at LIMIT 1)", (chat_instance, telegram_id, now - economy_config.FOUNDER_LINK_DAYS * 86400))
            row = conn.execute("SELECT chat_id, founder_id FROM chat_founders WHERE chat_instance = ? AND rewarded_at IS NULL", (chat_instance,)).fetchone()
            result = None
            if row is not None and _lively(conn, chat_instance, now) and conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (row["founder_id"],)).fetchone() is not None:
                changed = conn.execute("UPDATE chat_founders SET rewarded_at = ? WHERE chat_id = ? AND rewarded_at IS NULL", (now, row["chat_id"])).rowcount
                if changed:
                    chips = max(0, min(economy_config.FOUNDER_CHIPS, MAX_SAFE_INT - wallet.get_balance(conn, row["founder_id"])))
                    if chips:
                        wallet.credit(conn, row["founder_id"], chips)
                    gems = grant_referral_gems(conn, row["founder_id"], economy_config.FOUNDER_GEMS, "founder_reward", "chat-%d" % row["chat_id"], now)
                    result = {"chips": chips, "gems": gems}
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

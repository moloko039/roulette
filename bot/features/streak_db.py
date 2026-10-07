"""Серия входов (ECONOMY_ADDITIONS.md, п. 3): ежедневная награда, растущая по дням недели и циклам.

«День» по московскому времени (economy_config.STREAK_UTC_OFFSET_HOURS). Состояние серии это последняя строка streak_claims игрока. Одна транзакция BEGIN IMMEDIATE: награда фишками через
wallet.credit (с подтянутым доходом) и кристаллами через wallet.gems_credit (причина streak_gems, месячный потолок бесплатных кристаллов). Опыт и ставки не меняются."""
import datetime
import time

import cosmetic_sets
import cosmetics
import economy_config
import wallet
from roulette import MAX_SAFE_INT

from core.db_conn import _connect
from core.kernel import _accrue_write, _register_player
from features.cosmetics_db import _grant_in


class StreakError(Exception):
    code = "streak_error"


def day_index(now):
    """Номер «дня» (московская дата как число дней с 1970-01-01)."""
    return (int(now) + economy_config.STREAK_UTC_OFFSET_HOURS * 3600) // 86400


def seconds_to_next_day(now):
    return 86400 - (int(now) + economy_config.STREAK_UTC_OFFSET_HOURS * 3600) % 86400


def month_start(now):
    """Момент (секунды) начала календарного месяца «дня» игрока."""
    d = datetime.date.fromordinal(day_index(now) + datetime.date(1970, 1, 1).toordinal())
    first = datetime.date(d.year, d.month, 1)
    return (first - datetime.date(1970, 1, 1)).days * 86400 - economy_config.STREAK_UTC_OFFSET_HOURS * 3600


def cycle_percent(cycle):
    return min(100 + economy_config.STREAK_CYCLE_GROWTH_PERCENT * (cycle - 1), economy_config.STREAK_CYCLE_MAX_PERCENT)


def reward_for(streak_day, cycle):
    """(фишки, кристаллы) за день цикла streak_day (1..7)."""
    chips_table = economy_config.STREAK_CHIPS
    base = chips_table[min(streak_day, len(chips_table)) - 1]
    chips = base * cycle_percent(cycle) // 100
    gems = 0
    if streak_day == 7:
        gems = economy_config.STREAK_GEMS_BASE + min(cycle - 1, economy_config.STREAK_GEMS_CYCLE_BONUS_MAX)
    return chips, gems


def next_position(last, today):
    """(день цикла 1..7, цикл), который даст ближайший сбор; last: строка последнего сбора или None."""
    if last is None:
        return 1, 1
    gap = today - last["day"]
    if gap <= 1:
        if last["streak_day"] >= 7:
            return 1, last["cycle"] + 1
        return last["streak_day"] + 1, last["cycle"]
    # пропуск: откат на начало текущей недели (законченная неделя уже закрыта: следующий цикл)
    return 1, last["cycle"] + 1 if last["streak_day"] >= 7 else last["cycle"]


def _last(conn, telegram_id):
    return conn.execute("SELECT day, streak_day, cycle, chips, gems FROM streak_claims WHERE telegram_id = ? ORDER BY day DESC LIMIT 1", (telegram_id,)).fetchone()


def _free_gems_used(conn, telegram_id, now):
    marks = ",".join("?" for _ in economy_config.FREE_GEM_REASONS)
    return conn.execute("SELECT COALESCE(SUM(delta), 0) FROM gems_ledger WHERE telegram_id = ? AND reason IN (" + marks + ") AND created_at >= ?",
                        (telegram_id, *economy_config.FREE_GEM_REASONS, month_start(now))).fetchone()[0]


def _preview(streak_day, cycle):
    """Недельная лента наград для экрана: для каждого дня цикла (фишки, кристаллы)."""
    return [{"day": d, "chips": reward_for(d, cycle)[0], "gems": reward_for(d, cycle)[1]} for d in range(1, 8)]


def streak_status(telegram_id, now=None, db_path=None):
    """Данные карточки «Награда дня» (только чтение): можно ли забрать сегодня, какой день цикла, что дадут, лента недели."""
    if now is None:
        now = int(time.time())
    today = day_index(now)
    conn = _connect(db_path)
    try:
        last = _last(conn, telegram_id)
        claimed = last is not None and last["day"] == today
        if claimed:
            # уже собрано сегодня: показываем, что будет завтра
            day, cycle = next_position(last, today + 1)
        else:
            day, cycle = next_position(last, today)
        chips, gems = reward_for(day, cycle)
        return {"claimed_today": claimed, "streak_day": day, "cycle": cycle, "reward": {"chips": chips, "gems": gems},
                "week": _preview(day, cycle), "seconds_to_next_day": seconds_to_next_day(now)}
    finally:
        conn.close()


def claim_streak(telegram_id, now=None, db_path=None):
    """Забрать награду дня. Один раз в «день»: повторный вызов в тот же день отдаёт тот же результат с replayed true и ничего не начисляет.
    Кристаллы урезаются до остатка месячного потолка бесплатных (gems_capped true). Возвращает словарь ответа API."""
    if now is None:
        now = int(time.time())
    today = day_index(now)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _register_player(conn, telegram_id, now)
            row = conn.execute("SELECT streak_day, cycle, chips, gems FROM streak_claims WHERE telegram_id = ? AND day = ?", (telegram_id, today)).fetchone()
            if row is not None:
                result = {"streak_day": row["streak_day"], "cycle": row["cycle"], "chips": row["chips"], "gems": row["gems"], "gems_capped": False,
                          "balance": wallet.get_balance(conn, telegram_id), "gems_balance": wallet.gems_balance(conn, telegram_id), "collection_part": None, "replayed": True}
                conn.execute("COMMIT")
                return result
            last = _last(conn, telegram_id)
            day, cycle = next_position(last, today)
            chips, gems = reward_for(day, cycle)
            capped = False
            if gems:
                left = max(0, economy_config.FREE_GEMS_MONTHLY_CAP - _free_gems_used(conn, telegram_id, now))
                if gems > left:
                    gems, capped = left, True
            _accrue_write(conn, telegram_id, now)
            balance = wallet.get_balance(conn, telegram_id)
            chips = max(0, min(chips, MAX_SAFE_INT - balance))
            if chips:
                balance = wallet.credit(conn, telegram_id, chips)
            gems_balance = wallet.gems_balance(conn, telegram_id)
            if gems:
                gems_balance = wallet.gems_credit(conn, telegram_id, gems, "streak_gems", "day-%d" % today, now)
            conn.execute("INSERT INTO streak_claims (telegram_id, day, streak_day, cycle, chips, gems, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (telegram_id, today, day, cycle, chips, gems, now))
            part = None
            owned = [r[0] for r in conn.execute("SELECT item_code FROM cosmetic_items WHERE telegram_id = ?", (telegram_id,))]
            pick = cosmetic_sets.streak_part_to_grant(owned, day, today)
            if pick is not None:
                collection, part_code = pick
                if _grant_in(conn, telegram_id, part_code, "collection", "collection:" + collection, now):
                    part = {"collection": collection, "collection_name": cosmetic_sets.COLLECTIONS[collection]["name"], "part": part_code, "name": cosmetics.item(part_code)["name"]}
            result = {"streak_day": day, "cycle": cycle, "chips": chips, "gems": gems, "gems_capped": capped, "balance": balance, "gems_balance": gems_balance,
                      "collection_part": part, "replayed": False}
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

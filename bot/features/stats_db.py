"""Сводка экономики для владельца (команда /stats, план экономики E8): только агрегаты, без идентификаторов, имён и отдельных балансов.

Только чтение. Источники и стоки кристаллов берутся из журнала gems_ledger по причинам; фишки пока считаются снимком (сколько всего на руках, сколько потрачено на ферму
и пакеты), потому что журнала событий фишек нет (игры считаются отдельно по числу раундов)."""
import time

import economy_config

from core.db_conn import _connect

DAY = 86400
GAME_TABLES = (("рулетка", "roulette_rounds"), ("кено", "keno_rounds"), ("слот", "slot_rounds"), ("мины", "mines_games"), ("блэкджек", "blackjack_games"),
               ("краш", "crash_games"), ("хило", "hilo_games"))
WINDOWS = (("24 ч", DAY), ("7 дней", 7 * DAY))


def _tables(conn):
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def economy_stats(now=None, db_path=None):
    """Словарь с агрегатами (все числа целые). Ключи: players, chips, gems, gem_sales, chip_sales, farm, games, config_version."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        present = _tables(conn)
        one = lambda q, p=(): conn.execute(q, p).fetchone()[0] or 0   # noqa: E731
        out = {"config_version": economy_config.VERSION}
        out["players"] = {
            "total": one("SELECT COUNT(*) FROM players"),
            "active_24h": one("SELECT COUNT(*) FROM players WHERE last_accrual >= ?", (now - DAY,)),
            "new_24h": one("SELECT COUNT(*) FROM players WHERE created_at >= ?", (now - DAY,)),
            "new_7d": one("SELECT COUNT(*) FROM players WHERE created_at >= ?", (now - 7 * DAY,)),
        }
        out["chips"] = {"supply": one("SELECT SUM(balance) FROM players"), "average": one("SELECT CAST(AVG(balance) AS INTEGER) FROM players"),
                        "farm_spent_total": one("SELECT SUM(cost) FROM farm_purchases") if "farm_purchases" in present else 0}
        gems = {"supply": one("SELECT SUM(gems) FROM gem_balances") if "gem_balances" in present else 0, "by_reason": {}}
        if "gems_ledger" in present:
            for label, span in WINDOWS + (("всего", None),):
                where, params = ("WHERE created_at >= ?", (now - span,)) if span else ("", ())
                rows = conn.execute("SELECT reason, SUM(delta) AS total FROM gems_ledger " + where + " GROUP BY reason", params).fetchall()
                gems["by_reason"][label] = {r["reason"]: r["total"] for r in rows}
        out["gems"] = gems
        sales = {}
        if "gem_purchases" in present:
            for label, span in WINDOWS + (("всего", None),):
                where, params = ("AND created_at >= ?", (now - span,)) if span else ("", ())
                row = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(amount_stars), 0) AS stars, COALESCE(SUM(gems), 0) AS gems FROM gem_purchases WHERE status = 'paid' " + where, params).fetchone()
                sales[label] = {"packs": row["n"], "stars": row["stars"], "gems": row["gems"]}
            sales["refunded"] = one("SELECT COUNT(*) FROM gem_purchases WHERE status = 'refunded'")
        out["gem_sales"] = sales
        chip_sales = {}
        if "chip_purchases" in present:
            for label, span in WINDOWS + (("всего", None),):
                where, params = ("WHERE created_at >= ?", (now - span,)) if span else ("", ())
                row = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(gems), 0) AS gems, COALESCE(SUM(chips), 0) AS chips FROM chip_purchases " + where, params).fetchone()
                chip_sales[label] = {"packs": row["n"], "gems": row["gems"], "chips": row["chips"]}
        out["chip_sales"] = chip_sales
        out["farm"] = {"income_levels": {r["income_level"]: r["n"] for r in conn.execute("SELECT income_level, COUNT(*) AS n FROM players GROUP BY income_level ORDER BY 1")},
                       "storage_levels": {r["storage_level"]: r["n"] for r in conn.execute("SELECT storage_level, COUNT(*) AS n FROM players GROUP BY storage_level ORDER BY 1")}}
        games = {}
        for name, table in GAME_TABLES:
            if table in present:
                games[name] = one("SELECT COUNT(*) FROM " + table + " WHERE created_at >= ?", (now - DAY,))
        out["games"] = games
        return out
    finally:
        conn.close()


def _fmt(n):
    return "{:,}".format(int(n)).replace(",", " ")


def stats_text(stats):
    """Текст сводки для чата (без идентификаторов)."""
    p, c, g = stats["players"], stats["chips"], stats["gems"]
    lines = ["Сводка экономики (числа версии %d)" % stats["config_version"], "",
             "Игроки: всего %s, активных за 24 ч %s, новых за 24 ч %s, за 7 дней %s" % (_fmt(p["total"]), _fmt(p["active_24h"]), _fmt(p["new_24h"]), _fmt(p["new_7d"])),
             "Фишки на руках: %s (в среднем %s), потрачено на ферму за всё время: %s" % (_fmt(c["supply"]), _fmt(c["average"]), _fmt(c["farm_spent_total"])),
             "Кристаллы на руках: %s" % _fmt(g["supply"])]
    classes = economy_config.GEM_REASONS
    for label in ("24 ч", "7 дней", "всего"):
        by = g["by_reason"].get(label, {})
        src = sum(v for k, v in by.items() if classes.get(k) == "source")
        snk = -sum(v for k, v in by.items() if classes.get(k) == "sink")
        detail = ", ".join("%s %+d" % (k, v) for k, v in sorted(by.items())) or "нет"
        lines.append("Кристаллы за %s: появилось %s, ушло %s (%s)" % (label, _fmt(src), _fmt(snk), detail))
    for label in ("24 ч", "7 дней", "всего"):
        s = stats["gem_sales"].get(label)
        if s is not None:
            lines.append("Продано пакетов кристаллов за %s: %s на %s Stars (%s кристаллов)" % (label, _fmt(s["packs"]), _fmt(s["stars"]), _fmt(s["gems"])))
    if "refunded" in stats["gem_sales"]:
        lines.append("Возвращено пакетов: %s" % _fmt(stats["gem_sales"]["refunded"]))
    for label in ("24 ч", "7 дней", "всего"):
        s = stats["chip_sales"].get(label)
        if s is not None:
            lines.append("Куплено пакетов фишек за %s: %s (потрачено %s кристаллов, выдано %s фишек)" % (label, _fmt(s["packs"]), _fmt(s["gems"]), _fmt(s["chips"])))
    lines.append("Уровни дохода фермы (уровень: игроков): " + (", ".join("%d: %d" % kv for kv in stats["farm"]["income_levels"].items()) or "нет"))
    lines.append("Раунды за 24 ч: " + (", ".join("%s %d" % kv for kv in stats["games"].items()) or "нет"))
    return "\n".join(lines)

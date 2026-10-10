"""Коллекции косметики (docs/COLLECTIONS.md, ECONOMY_ADDITIONS.md п. 4): наборы частей, которые выдаются только бесплатно и никогда не продаются и не дарятся.

Часть коллекции это обычный предмет каталога (cosmetics.py) без цены. Коллекция собрана, когда у игрока есть все её части. Выдача: «Листопад» (сезонная) за серию входов: на днях цикла
из economy_config.COLLECTION_STREAK_DAYS игрок получает следующую недостающую часть, пока идёт сезон (по московской дате), потом выдача прекращается навсегда, у получивших части остаются.
Без модуля collections в имени: такое имя уже занято стандартной библиотекой."""
import datetime

import economy_config

COLLECTIONS = {
    "leaves": {
        "name": "Листопад",
        "parts": ("back_leaves", "table_autumn", "mine_acorn", "chip_leaf", "keno_apple", "crash_maple", "frame_wreath", "badge_pumpkin"),
        "source": "streak",
        "season": ("2026-10-01", "2026-10-31"),          # московские даты включительно
        "how": "Награда дня на 3-й, 5-й и 7-й день серии входов, только в октябре",
    },
    "dacha": {
        "name": "Дачный сезон",
        "parts": ("back_rug", "chip_cork", "table_oilcloth", "mine_beetle", "keno_lotto", "crash_barrel", "frame_dacha", "badge_dacha"),
        "source": "farm",
        "season": None,
        "how": "За улучшения дохода фермы",
    },
}

PART_TO_COLLECTION = {part: code for code, c in COLLECTIONS.items() for part in c["parts"]}

SETS = {
    "draft": {"name": "Черновик", "parts": ("draft_crash", "draft_mines", "draft_table", "draft_badge", "draft_chip", "draft_keno", "draft_back", "draft_frame"), "price_gems": 200},
    "void": {"name": "Пустота", "parts": ("void_table", "void_chip", "void_badge"), "price_gems": 400},
    "patina": {"name": "Патина", "parts": ("chip_patina", "back_patina", "mine_patina", "frame_patina"), "price_gems": 1000},
    "deep": {"name": "Глубина", "parts": ("table_deep", "chip_pearl", "mine_urchin", "keno_bubble", "crash_deep", "back_deep", "frame_deep", "badge_deep"), "price_gems": 500},
}


def set_of(code):
    """Код набора, к которому относится часть, или None."""
    for set_code, s in SETS.items():
        if code in s["parts"]:
            return set_code
    return None


def parts(set_code):
    """Части набора по коду или пустой кортеж."""
    return SETS[set_code]["parts"] if set_code in SETS else ()


def _day(text):
    d = datetime.date.fromisoformat(text)
    return (d - datetime.date(1970, 1, 1)).days


def season_active(code, day_index):
    """Идёт ли сезон коллекции в «день» day_index (московская дата как число дней с 1970-01-01)."""
    if COLLECTIONS[code]["season"] is None:
        return True
    start, end = COLLECTIONS[code]["season"]
    return _day(start) <= day_index <= _day(end)


def collection_of(part_code):
    """Код коллекции, к которой относится часть, или None."""
    return PART_TO_COLLECTION.get(part_code)


def progress(owned_codes):
    """Список коллекций с прогрессом по множеству кодов принадлежащих предметов: [{code, name, how, season: [с, по], parts, owned, total, complete}]."""
    owned = set(owned_codes)
    out = []
    for code, c in COLLECTIONS.items():
        have = sum(1 for p in c["parts"] if p in owned)
        out.append({"code": code, "name": c["name"], "how": c["how"], "season": list(c["season"]) if c["season"] else None, "parts": list(c["parts"]),
                    "owned": have, "total": len(c["parts"]), "complete": have == len(c["parts"])})
    return out


def streak_part_to_grant(owned_codes, streak_day, day_index):
    """Какую часть выдать за сбор награды дня: (код коллекции, код части) или None. Только коллекции с источником streak, идущим сезоном, на днях цикла
    из economy_config.COLLECTION_STREAK_DAYS, следующая по порядку недостающая часть."""
    if streak_day not in economy_config.COLLECTION_STREAK_DAYS:
        return None
    owned = set(owned_codes)
    for code, c in COLLECTIONS.items():
        if c["source"] != "streak" or not season_active(code, day_index):
            continue
        for part in c["parts"]:
            if part not in owned:
                return code, part
    return None

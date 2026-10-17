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
    "founder": {
        "name": "Основатель",
        "parts": ("ref_scout", "ref_beacon", "ref_arch"),
        "source": "referral",
        "season": None,
        "how": "За друзей, дошедших до квалификации: 3, 10 и 30",
    },
}

PART_TO_COLLECTION = {part: code for code, c in COLLECTIONS.items() for part in c["parts"]}

SETS = {
    "draft": {"name": "Черновик", "parts": ("draft_crash", "draft_mines", "draft_table", "draft_badge", "draft_chip", "draft_keno", "draft_back", "draft_frame"), "price_gems": 200},
    "void": {"name": "Пустота", "parts": ("void_table", "void_chip", "void_badge", "void_mines", "void_keno", "void_crash", "void_back", "void_frame"), "price_gems": 2000},
    "patina": {"name": "Патина", "parts": ("chip_patina", "back_patina", "mine_patina", "frame_patina", "keno_patina", "crash_patina", "badge_patina"), "price_gems": 1000},
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
    if day_index is None:
        return False
    start, end = COLLECTIONS[code]["season"]
    return _day(start) <= day_index <= _day(end)


def collection_of(part_code):
    """Код коллекции, к которой относится часть, или None."""
    return PART_TO_COLLECTION.get(part_code)


_STATUS_ORDER = {"active": 0, "complete": 1, "ended": 2}


def progress(owned_codes, day_index=None):
    """Список коллекций с прогрессом по множеству кодов принадлежащих предметов:
    [{code, name, how, season: [с, по] | None, parts, owned, total, complete, source, status, days_left}].
    Порядок: сначала status 'active', затем 'complete', затем 'ended'; внутри группы прежний порядок COLLECTIONS.
    day_index не задан: days_left null, status без 'ended'."""
    owned = set(owned_codes)
    out = []
    for code, c in COLLECTIONS.items():
        have = sum(1 for p in c["parts"] if p in owned)
        is_complete = have == len(c["parts"])
        season = list(c["season"]) if c["season"] else None

        if is_complete:
            status = "complete"
            days_left = None
        elif day_index is None:
            status = "active"
            days_left = None
        elif c["season"] is not None and day_index > _day(c["season"][1]):
            status = "ended"
            days_left = None
        else:
            status = "active"
            if c["season"] is not None and season_active(code, day_index):
                days_left = _day(c["season"][1]) - day_index + 1
            else:
                days_left = None

        out.append({
            "code": code,
            "name": c["name"],
            "how": c["how"],
            "season": season,
            "parts": list(c["parts"]),
            "owned": have,
            "total": len(c["parts"]),
            "complete": is_complete,
            "source": c["source"],
            "status": status,
            "days_left": days_left,
        })

    out.sort(key=lambda item: _STATUS_ORDER[item["status"]])
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


def streak_part_days(owned_codes, from_day, day_index):
    """Дни цикла (из economy_config.COLLECTION_STREAK_DAYS, не раньше from_day), за которые ещё выдастся часть коллекции: по числу недостающих
    частей коллекций с источником streak, идущих сезоном. Для меток на ленте недели."""
    owned = set(owned_codes)
    missing = sum(1 for code, c in COLLECTIONS.items() if c["source"] == "streak" and season_active(code, day_index)
                  for part in c["parts"] if part not in owned)
    return [d for d in economy_config.COLLECTION_STREAK_DAYS if d >= from_day][:missing]

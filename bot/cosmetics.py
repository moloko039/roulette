"""Косметика: каталог предметов (в коде), слоты, источники получения, ошибки. Без базы.

Косметика только меняет внешний вид клиента и НЕ влияет на шансы, выплаты, множители, опыт, лимиты, ферму и экономику:
игровые модули, wallet, economy, transfers этот модуль не импортируют (проверяет test_cosmetics.py). Предметы не передаются между
игроками и не продаются за фишки. Стартовые предметы (по одному в каждом слоте) в базе не хранятся: если для слота нет записи
в cosmetic_equipped, действует стартовый. Публичные слоты (их видят другие в рейтинге беседы): avatar_frame и badge."""

SLOTS = ("card_back", "chip", "table", "mine_icons", "keno_ball", "crash", "avatar_frame", "badge")
SLOT_NAMES = {
    "card_back": "Рубашка карт", "chip": "Фишки", "table": "Стол и колесо", "mine_icons": "Значки мин",
    "keno_ball": "Шарики кено", "crash": "График краша", "avatar_frame": "Рамка аватара", "badge": "Значок в рейтинге",
}
PUBLIC_SLOTS = ("avatar_frame", "badge")      # видны другим участникам беседы, только если игрок их надел и не скрыл
RARITIES = ("starter", "common", "rare", "premium")    # редкость только визуальная
SOURCES = ("free", "owner_gift", "stars")


class CosmeticsError(Exception):
    code = "invalid_request"


class UnknownItem(CosmeticsError):
    code = "unknown_item"


class NotOwned(CosmeticsError):
    code = "not_owned"


class SlotMismatch(CosmeticsError):
    code = "slot_mismatch"


class ItemUnavailable(CosmeticsError):
    code = "item_unavailable"


class RequestConflict(CosmeticsError):
    code = "request_conflict"


class TooFast(CosmeticsError):
    """Смена чаще раза в секунду на игрока: API отвечает 429."""
    code = "too_many_requests"


class NoSuchPlayer(CosmeticsError):
    code = "no_such_player"


# (код, слот, название, описание, редкость, цена в Stars, доступен ли). Стартовые: редкость starter, цена 0.
_ROWS = (
    ("back_classic", "card_back", "Классика", "Диагональные тёмно-серые полосы", "starter", 0, True),
    ("back_midnight", "card_back", "Полночь", "Глубокий синий с мелкой сеткой ромбов", "common", 50, True),
    ("back_ember", "card_back", "Уголь", "Чёрный фон с тёплыми оранжевыми штрихами", "rare", 100, False),
    ("chip_plain", "chip", "Простые", "Тёмные круглые фишки", "starter", 0, True),
    ("chip_ring", "chip", "Кольцо", "Светлое кольцо с насечками по краю", "common", 40, True),
    ("chip_gold", "chip", "Золото", "Золотистый градиент с тонкой тёмной кромкой", "premium", 200, False),
    ("table_green", "table", "Сукно", "Привычная зелёно-красная палитра стола", "starter", 0, True),
    ("table_blue", "table", "Лагуна", "Бирюзовое сукно и мягкие синие сектора", "rare", 100, True),
    ("table_violet", "table", "Сумерки", "Сливовый фон и розовые сектора", "premium", 250, False),
    ("mine_classic", "mine_icons", "Обычные", "Привычные мина и кристалл", "starter", 0, True),
    ("mine_star", "mine_icons", "Звёзды", "Мина-звезда и кристалл-ромб", "common", 50, True),
    ("mine_gem", "mine_icons", "Самоцвет", "Огранённый камень и мина-капля", "rare", 120, False),
    ("keno_round", "keno_ball", "Круги", "Привычные круглые шарики", "starter", 0, True),
    ("keno_hex", "keno_ball", "Соты", "Шестиугольные шарики со светлой рамкой", "common", 40, True),
    ("crash_line", "crash", "Линия", "Привычная кривая и цвета краша", "starter", 0, True),
    ("crash_neon", "crash", "Неон", "Мягкое свечение линии, фиолетовый и голубой", "rare", 90, True),
    ("frame_plain", "avatar_frame", "Без рамки", "Аватар без рамки", "starter", 0, True),
    ("frame_thin", "avatar_frame", "Тонкая", "Светлое кольцо в 2 пикселя", "common", 40, True),
    ("frame_double", "avatar_frame", "Двойная", "Два кольца с зазором", "rare", 100, False),
    ("frame_crown", "avatar_frame", "Корона", "Кольцо с маленькой короной сверху", "premium", 300, False),
    ("badge_none", "badge", "Без значка", "Ничего рядом с именем", "starter", 0, True),
    ("badge_spade", "badge", "Пика", "Маленький значок масти рядом с именем", "common", 30, True),
    ("badge_flame", "badge", "Пламя", "Язычок пламени рядом с именем", "rare", 75, False),
)

CATALOG = tuple(
    {"code": c, "slot": s, "name": n, "description": d, "rarity": r, "price_stars": p, "starter": r == "starter", "available": a}
    for c, s, n, d, r, p, a in _ROWS)
_BY_CODE = {i["code"]: i for i in CATALOG}
STARTERS = {i["slot"]: i["code"] for i in CATALOG if i["starter"]}


def item(code):
    """Предмет каталога или None (код не строка и неизвестный код дают None)."""
    return _BY_CODE.get(code) if type(code) is str else None


def valid_slot(slot):
    return type(slot) is str and slot in SLOTS


def effective_equipped(rows):
    """Надетое по всем слотам: rows {слот: код} из базы; для слота без записи (или с неизвестным в каталоге кодом) стартовый."""
    out = {}
    for slot in SLOTS:
        code = rows.get(slot)
        it = item(code)
        out[slot] = code if it is not None and it["slot"] == slot else STARTERS[slot]
    return out


def catalog_view():
    """Ответ GET /api/cosmetics/catalog: одинаков для всех игроков (принадлежность не раскрывается)."""
    return {"slots": [{"slot": s, "name": SLOT_NAMES[s], "starter": STARTERS[s], "public": s in PUBLIC_SLOTS} for s in SLOTS],
            "items": [dict(i) for i in CATALOG]}

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
PURCHASE_RETENTION_DAYS = 365   # журнал оплат Stars хранится столько дней после покупки (споры и возвраты), даже после удаления данных игрока
SOURCES = ("free", "owner_gift", "stars", "chips", "gems", "gift", "collection", "achievement", "referral")


class CosmeticsError(Exception):
    code = "invalid_request"


class UnknownItem(CosmeticsError):
    code = "unknown_item"


class UnknownSet(CosmeticsError):
    code = "unknown_set"


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


class AlreadyOwned(CosmeticsError):
    code = "already_owned"


class InsufficientChips(CosmeticsError):
    code = "insufficient_chips"


class NotForChips(CosmeticsError):
    """Предмет продаётся за Stars, за фишки его купить нельзя."""
    code = "not_for_chips"


class InsufficientGems(CosmeticsError):
    code = "insufficient_gems"


class NotForGems(CosmeticsError):
    """Предмет продаётся не за кристаллы (за фишки или за Stars)."""
    code = "not_for_gems"


class NotForStars(CosmeticsError):
    """Предмет продаётся за фишки, за Stars его купить нельзя."""
    code = "not_for_stars"


# Цены (единственное место): код -> (валюта "stars"|"chips", сумма). Только у восьми доступных нестартовых предметов; у стартовых и недоступных цены нет.
# Фишки не продаются за Stars и не обмениваются: за Stars продаётся только косметика.
# С 2026-10-07 (план экономики, E2) за Stars продаются только кристаллы; предметы продаются за кристаллы или фишки (курс прежних цен в Stars 1 к 1).
PRICES = {
    "table_blue": ("gems", 150), "crash_neon": ("gems", 100), "back_midnight": ("gems", 100), "keno_hex": ("gems", 75),
    "badge_spade": ("chips", 20000), "chip_ring": ("chips", 40000), "mine_star": ("chips", 60000), "frame_thin": ("chips", 100000),
    # части наборов «Черновик» (200 💎 за набор) и «Пустота» (400 💎): по отдельности дороже набора, см. cosmetic_sets.SETS
    "draft_crash": ("gems", 100), "draft_mines": ("gems", 100), "draft_table": ("gems", 100), "draft_badge": ("gems", 100),
    "draft_chip": ("gems", 100), "draft_keno": ("gems", 100), "draft_back": ("gems", 100), "draft_frame": ("gems", 100),      # новые части «Черновика» по DESIGN.md (набор из восьми частей 200 💎)
    "void_table": ("gems", 150), "void_chip": ("gems", 150), "void_badge": ("gems", 150),
    # «Патина» (набор 1000 💎, решение владельца 2026-10-09; части по 400 💎, чтобы купившие бесплатные части раньше могли добрать рамку) и «Глубина» (набор 500 💎, части по 150 💎)
    "chip_patina": ("gems", 400), "back_patina": ("gems", 400), "mine_patina": ("gems", 400), "frame_patina": ("gems", 400),
    "table_deep": ("gems", 150), "chip_pearl": ("gems", 150), "mine_urchin": ("gems", 150), "keno_bubble": ("gems", 150), "crash_deep": ("gems", 150),
}
STARS, CHIPS, GEMS = "stars", "chips", "gems"
# Прежние цены в Stars: нужны только чтобы принять оплату по счетам, выставленным до перехода на кристаллы, и скрытому тестовому предмету (/teststars).
# Новые счета на предметы не создаются (POST /api/cosmetics/invoice отвечает 410).
LEGACY_STARS_PRICES = {"table_blue": 150, "crash_neon": 100, "back_midnight": 100, "keno_hex": 75}

# (код, слот, название, описание, редкость, доступен ли). Стартовые: редкость starter.
_ROWS = (
    ("back_classic", "card_back", "Классика", "Диагональные тёмно-серые полосы", "starter", True),
    ("back_midnight", "card_back", "Полночь", "Глубокий синий с мелкой сеткой ромбов", "common", True),
    ("back_ember", "card_back", "Уголь", "Чёрный фон с тёплыми оранжевыми штрихами", "rare", False),
    ("back_leaves", "card_back", "Листопад", "Осенние листья на тёплом коричневом фоне", "rare", True),
    ("chip_plain", "chip", "Простые", "Тёмные круглые фишки", "starter", True),
    ("chip_ring", "chip", "Кольцо", "Светлое кольцо с насечками по краю", "common", True),
    ("chip_gold", "chip", "Золото", "Золотистый градиент с тонкой тёмной кромкой", "premium", False),
    ("table_green", "table", "Сукно", "Привычная зелёно-красная палитра стола", "starter", True),
    ("table_blue", "table", "Лагуна", "Бирюзовое сукно и мягкие синие сектора", "rare", True),
    ("table_violet", "table", "Сумерки", "Сливовый фон и розовые сектора", "premium", False),
    ("table_autumn", "table", "Октябрь", "Горчичное сукно и осенние сектора", "rare", True),
    ("mine_classic", "mine_icons", "Обычные", "Привычные мина и кристалл", "starter", True),
    ("mine_star", "mine_icons", "Звёзды", "Мина-звезда и кристалл-ромб", "common", True),
    ("mine_gem", "mine_icons", "Самоцвет", "Огранённый камень и мина-капля", "rare", False),
    ("mine_acorn", "mine_icons", "Жёлуди", "Мина-жёлудь и листок вместо кристалла", "rare", True),
    ("keno_round", "keno_ball", "Круги", "Привычные круглые шарики", "starter", True),
    ("keno_hex", "keno_ball", "Соты", "Шестиугольные шарики со светлой рамкой", "common", True),
    ("crash_line", "crash", "Линия", "Привычная кривая и цвета краша", "starter", True),
    ("crash_neon", "crash", "Неон", "Мягкое свечение линии, фиолетовый и голубой", "rare", True),
    ("frame_plain", "avatar_frame", "Без рамки", "Аватар без рамки", "starter", True),
    ("frame_thin", "avatar_frame", "Тонкая", "Светлое кольцо в 2 пикселя", "common", True),
    ("frame_double", "avatar_frame", "Двойная", "Два кольца с зазором", "rare", False),
    ("frame_crown", "avatar_frame", "Корона", "Кольцо с маленькой короной сверху", "premium", False),
    ("badge_none", "badge", "Без значка", "Ничего рядом с именем", "starter", True),
    ("badge_spade", "badge", "Пика", "Маленький значок масти рядом с именем", "common", True),
    ("badge_flame", "badge", "Пламя", "Язычок пламени рядом с именем", "rare", False),
    ("achv_nearly", "badge", "Почти", "Клуб ×1.01", "common", True),
    ("achv_sapper", "mine_icons", "Сапёр-оптимист", "Клуб ×1.01", "common", True),
    ("achv_bust", "card_back", "Перебор", "Клуб ×1.01", "common", True),
    ("achv_keno", "keno_ball", "Ноль из десяти", "Клуб ×1.01", "common", True),
    ("draft_crash", "crash", "Черновик", "Карандашный график на миллиметровке", "common", True),
    ("draft_mines", "mine_icons", "Черновик", "Нарисованные бомбочки", "common", True),
    ("draft_table", "table", "Черновик", "Клетчатый лист со скрепкой", "common", True),
    ("draft_badge", "badge", "Черновик", "Штамп «УТВЕРЖДЕНО»", "common", True),
    ("void_table", "table", "Пустота", "Чёрный стол, белые цифры", "common", True),
    ("void_chip", "chip", "Пустота", "Без украшений", "common", True),
    ("void_badge", "badge", "Пустота", "Значок «—»", "common", True),
    ("back_rug", "card_back", "Ковёр", "Узор как на ковре на стене", "common", True),
    ("chip_cork", "chip", "Пробки", "Пробки от лимонада", "common", True),
    ("table_oilcloth", "table", "Клеёнка", "Клеёнка в клетку с вишнями", "common", True),
    ("mine_beetle", "mine_icons", "Жуки", "Колорадские жуки на грядке", "common", True),
    ("keno_lotto", "keno_ball", "Бочонки", "Бочонки деревенского лото", "common", True),
    ("crash_barrel", "crash", "Бочка", "Самодельная ракета из бочки", "common", True),
    ("frame_dacha", "avatar_frame", "Наличник", "Резной оконный наличник, белая краска с облупившейся голубой", "common", True),
    ("badge_dacha", "badge", "Банка огурцов", "Банка солёных огурцов с марлей под крышкой", "common", True),
    ("ref_scout", "badge", "Гонец", "Награда за 3 друзей, дошедших до квалификации", "rare", True),
    ("ref_beacon", "avatar_frame", "Маяк", "Награда за 10 друзей, дошедших до квалификации", "rare", True),
    ("ref_comet", "crash", "Комета", "Награда за 30 друзей, дошедших до квалификации", "rare", True),
    ("chip_patina", "chip", "Патина", "Засечки на ребре за каждый краш выше ×50", "common", True),
    ("back_patina", "card_back", "Патина", "Выцветает и протирается по числу сыгранных раундов", "common", True),
    ("mine_patina", "mine_icons", "Патина", "Трещины на поле от прошлых взрывов", "common", True),
    ("frame_patina", "avatar_frame", "Патина", "Тускнеет, как старое серебро, по стажу аккаунта", "rare", True),
    ("table_deep", "table", "Дно", "Морское дно: тёмная бирюза и коралловые сектора", "rare", True),
    ("chip_pearl", "chip", "Жемчуг", "Перламутровые фишки", "rare", True),
    ("mine_urchin", "mine_icons", "Морские ежи", "Морские ежи вместо мин", "rare", True),
    ("keno_bubble", "keno_ball", "Пузыри", "Воздушные пузыри вместо шариков", "rare", True),
    ("crash_deep", "crash", "Глубина", "График идёт вниз: множитель это глубина погружения", "rare", True),
    ("chip_leaf", "chip", "Лист в смоле", "Засушенный лист, залитый смолой", "rare", True),
    ("keno_apple", "keno_ball", "Яблоки", "Красные, жёлтые и зелёные яблоки", "rare", True),
    ("crash_maple", "crash", "Клён", "Кленовый лист несёт порыв ветра", "rare", True),
    ("frame_wreath", "avatar_frame", "Венок", "Венок из кленовых листьев", "rare", True),
    ("badge_pumpkin", "badge", "Тыква", "Тыква с вырезанной улыбкой", "rare", True),
    ("draft_chip", "chip", "Черновик", "Монета, нарисованная синей ручкой", "common", True),
    ("draft_keno", "keno_ball", "Черновик", "Лотерейный бланк с обведёнными числами", "common", True),
    ("draft_back", "card_back", "Черновик", "Скучающие каракули на обороте", "common", True),
    ("draft_frame", "avatar_frame", "Черновик", "Аватар на скотче рядом с жёлтым стикером", "common", True),
)

CATALOG = tuple(
    {"code": c, "slot": s, "name": n, "description": d, "rarity": r,
     "price": ({"currency": PRICES[c][0], "amount": PRICES[c][1]} if c in PRICES else None),
     "starter": r == "starter", "available": a}
    for c, s, n, d, r, a in _ROWS)
_BY_CODE = {i["code"]: i for i in CATALOG}

# Скрытый тестовый предмет для проверки оплаты и возврата на реальной звезде (/teststars): в каталог для клиента не входит, надеть его нельзя
# (item() его не знает), но покупается, хранится и возвращается как обычный платный предмет.
TEST_ITEM = {"code": "test_1star", "slot": "badge", "name": "Тестовый предмет", "description": "Проверка оплаты: на игру не влияет",
             "rarity": "common", "price": {"currency": STARS, "amount": 1}, "starter": False, "available": True}


def stars_price(code):
    """Цена в Stars для приёма оплаты по старому счёту или тестовому предмету; None, если за Stars предмет не продаётся."""
    if type(code) is not str:
        return None
    if code == TEST_ITEM["code"]:
        return TEST_ITEM["price"]["amount"]
    return LEGACY_STARS_PRICES.get(code)


def sellable(code):
    """Предмет, который можно продать (каталог и скрытый тестовый), или None."""
    return _BY_CODE.get(code) if type(code) is str and code in _BY_CODE else (TEST_ITEM if code == TEST_ITEM["code"] else None)
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


# ---------- подписанная метка инвойса (Telegram Stars) ----------
# В payload инвойса нет личных данных: только код предмета, время создания и HMAC от (игрок, предмет, время). Игрока Telegram сообщает сам
# (pre_checkout_query.from_user), подпись связывает метку с ним. Ключ: из TOMBSTONE_SECRET (с отдельным назначением), иначе из MEMBER_REF_SECRET,
# иначе случайный на время процесса (тогда после перезапуска ссылки перестают подходить: оплата отклоняется до списания).
import hashlib
import hmac
import os
import re
import secrets as _secrets

INVOICE_TTL_SECONDS = 24 * 3600     # pre_checkout принимает метки не старше суток
_PAYLOAD_RE = re.compile(r"^ci1\.([a-z0-9_]{1,40})\.([0-9]{9,12})\.([0-9a-f]{32})$")
_EPHEMERAL_PAY_KEY = _secrets.token_bytes(32)


def _pay_key():
    tomb = (os.environ.get("TOMBSTONE_SECRET") or "").strip()
    if tomb:
        return hmac.new(tomb.encode("utf-8"), b"invoice-v1", hashlib.sha256).digest()
    ref = (os.environ.get("MEMBER_REF_SECRET") or "").strip()
    if ref:
        return hmac.new(ref.encode("utf-8"), b"invoice-v1", hashlib.sha256).digest()
    return _EPHEMERAL_PAY_KEY


def _sign(user_id, code, ts):
    return hmac.new(_pay_key(), ("%d|%s|%d" % (user_id, code, ts)).encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def make_payload(user_id, code, now):
    """Метка инвойса (до 128 байт): ci1.<код>.<время>.<подпись>."""
    ts = int(now)
    payload = "ci1.%s.%d.%s" % (code, ts, _sign(user_id, code, ts))
    assert len(payload.encode("utf-8")) <= 128
    return payload


def parse_payload(payload, user_id, now=None, ttl=INVOICE_TTL_SECONDS):
    """Код предмета из метки или None (формат, подпись или срок не подходят). ttl=None: срок не проверяется (оплата уже прошла)."""
    m = _PAYLOAD_RE.fullmatch(payload) if type(payload) is str else None
    if m is None:
        return None
    code, ts = m.group(1), int(m.group(2))
    if not hmac.compare_digest(_sign(user_id, code, ts), m.group(3)):
        return None
    if ttl is not None and now is not None and not 0 <= now - ts <= ttl:
        return None
    return code

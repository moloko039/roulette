"""Косметика: принадлежность, надетое, показ в рейтинге, единственная точка выдачи (grant_item).

Только внешний вид: ни wallet, ни игровые модули, ни экономика сюда не заходят и косметику не читают. Стартовые предметы строками
не хранятся (действует стартовый слота, если записи нет). Изменения идут одной транзакцией BEGIN IMMEDIATE с идемпотентностью
по (игрок, request_id) и ограничением «не чаще одной смены в секунду на игрока»."""

import json
import time

import cosmetic_sets
import cosmetics
import wallet
from roulette import InsufficientFunds

from core.db_conn import _connect
from core.kernel import _accrue_write, _register_player

CHANGE_INTERVAL_SECONDS = 1


def _equipped_rows(conn, telegram_id):
    return {r["slot"]: r["item_code"] for r in conn.execute(
        "SELECT slot, item_code FROM cosmetic_equipped WHERE telegram_id = ?", (telegram_id,))}


def _show_in_rating(conn, telegram_id):
    row = conn.execute("SELECT show_in_rating FROM cosmetic_prefs WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return True if row is None else bool(row["show_in_rating"])


def cosmetics_state(telegram_id, db_path=None):
    """Для /api/me: надетое по всем слотам (стартовые, если записи нет) и показ в рейтинге. Только чтение, игрока не создаёт."""
    conn = _connect(db_path)
    try:
        return {"equipped": cosmetics.effective_equipped(_equipped_rows(conn, telegram_id)),
                "show_in_rating": _show_in_rating(conn, telegram_id)}
    finally:
        conn.close()


def grant_dacha_parts_in(conn, telegram_id, income_level, now):
    import economy_config
    for level, part in economy_config.DACHA_PARTS_BY_INCOME_LEVEL:
        if income_level >= level:
            conn.execute(
                "INSERT OR IGNORE INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, ?, ?, ?, ?)",
                (telegram_id, part, "collection", None, now)
            )


def cosmetics_mine(telegram_id, db_path=None):
    """GET /api/cosmetics/mine: свои предметы (без стартовых и без платёжных данных), надетое, показ в рейтинге."""
    import economy_config
    conn = _connect(db_path)
    try:
        player = conn.execute("SELECT income_level FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
        if player is not None:
            income_level = player["income_level"]
            first_threshold = economy_config.DACHA_PARTS_BY_INCOME_LEVEL[0][0]
            if income_level >= first_threshold:
                expected_parts = [part for level, part in economy_config.DACHA_PARTS_BY_INCOME_LEVEL if income_level >= level]
                owned_dacha = set(r["item_code"] for r in conn.execute(
                    "SELECT item_code FROM cosmetic_items WHERE telegram_id = ? AND item_code IN ({})".format(
                        ",".join("?" for _ in expected_parts)
                    ), (telegram_id, *expected_parts)).fetchall())
                if len(owned_dacha) < len(expected_parts):
                    now = int(time.time())
                    conn.execute("BEGIN IMMEDIATE")
                    grant_dacha_parts_in(conn, telegram_id, income_level, now)
                    conn.execute("COMMIT")

        gift_names = {r["item_code"]: r["from_name"] for r in conn.execute("SELECT item_code, from_name FROM gifts WHERE to_user = ? ORDER BY id", (telegram_id,))} if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'gifts'").fetchone() else {}
        owned = []
        for r in conn.execute("SELECT item_code, source, acquired_at FROM cosmetic_items WHERE telegram_id = ? ORDER BY acquired_at, item_code", (telegram_id,)):
            if cosmetics.item(r["item_code"]) is None:
                continue
            entry = {"code": r["item_code"], "source": r["source"], "acquired_at": r["acquired_at"]}
            if r["source"] == "gift":
                entry["gift_from"] = gift_names.get(r["item_code"], "")     # имя дарителя на момент подарка (пусто, если даритель удалил данные)
            owned.append(entry)
        state = {"equipped": cosmetics.effective_equipped(_equipped_rows(conn, telegram_id)),
                 "show_in_rating": _show_in_rating(conn, telegram_id)}
        state["collections"] = cosmetic_sets.progress([o["code"] for o in owned])
        return dict({"owned": owned}, **state)
    finally:
        conn.close()


def _grant_in(conn, telegram_id, code, source, payment_ref, now):
    """Выдача внутри открытой транзакции (повтор не создаёт дубль). True, если выдан сейчас. Скрытый тестовый предмет выдаётся только за Stars."""
    it = cosmetics.item(code) or (cosmetics.sellable(code) if source == "stars" else None)
    if it is None or source not in cosmetics.SOURCES or it["starter"]:
        raise ValueError("grant")
    if payment_ref is not None and type(payment_ref) is not str:
        raise ValueError("payment_ref")
    added = conn.execute(
        "INSERT OR IGNORE INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, ?, ?, ?, ?)",
        (telegram_id, code, source, payment_ref, now)).rowcount
    return added == 1


def _owns(conn, telegram_id, code):
    return conn.execute("SELECT 1 FROM cosmetic_items WHERE telegram_id = ? AND item_code = ?", (telegram_id, code)).fetchone() is not None


def grant_item(telegram_id, code, source, payment_ref=None, now=None, db_path=None):
    """Единственная точка выдачи предмета (подарок, оплата, покупка за фишки). Идемпотентна: повтор не создаёт дубль и не падает (False).
    True, если выдан сейчас. ValueError: неизвестный код, неверный источник или стартовый предмет (стартовые не выдаются);
    NoSuchPlayer: игрока нет в базе."""
    it = cosmetics.item(code) or (cosmetics.sellable(code) if source == "stars" else None)
    if it is None or source not in cosmetics.SOURCES or it["starter"]:
        raise ValueError("grant")
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone() is None:
                raise cosmetics.NoSuchPlayer()
            added = _grant_in(conn, telegram_id, code, source, payment_ref, now)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return added


def _run_action(telegram_id, request_id, action, params, body, now, db_path, throttle=True):
    """Общий порядок: повтор по request_id (другое действие или параметры: RequestConflict), ограничение частоты смен
    (TooFast), регистрация игрока, тело действия, запись ответа."""
    if now is None:
        now = int(time.time())
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute("SELECT action, params, response_json FROM cosmetic_actions WHERE telegram_id = ? AND request_id = ?",
                               (telegram_id, request_id)).fetchone()
            if old is not None:
                if old["action"] != action or old["params"] != params_json:
                    raise cosmetics.RequestConflict()
                response = json.loads(old["response_json"])
                response["replayed"] = True
                conn.execute("COMMIT")
                return response
            _register_player(conn, telegram_id, now)
            response = body(conn)     # ошибки проверок (не свой предмет, не тот слот...) приходят раньше ограничения частоты
            last = conn.execute("SELECT MAX(created_at) FROM cosmetic_actions WHERE telegram_id = ? AND action != 'buy'", (telegram_id,)).fetchone()[0]
            if throttle and last is not None and now - last < CHANGE_INTERVAL_SECONDS:
                raise cosmetics.TooFast()     # откат: смена не применяется
            response["replayed"] = False
            conn.execute(
                "INSERT OR IGNORE INTO cosmetic_actions (telegram_id, request_id, action, params, response_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, action, params_json, json.dumps(response, separators=(",", ":")), now))
            conn.execute("COMMIT")
            return response
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def equip_item(telegram_id, request_id, slot, code, now=None, db_path=None):
    """Надеть предмет в слот: только свой (или стартовый) и только в свой слот. Ошибки: UnknownItem, SlotMismatch,
    ItemUnavailable (в каталоге есть, но надеть нельзя), NotOwned. Стартовый предмет = снять (запись слота удаляется)."""
    if not cosmetics.valid_slot(slot) or type(code) is not str:
        raise ValueError("invalid")

    def body(conn):
        it = cosmetics.item(code)
        if it is None:
            raise cosmetics.UnknownItem()
        if it["slot"] != slot:
            raise cosmetics.SlotMismatch()
        if not it["available"]:
            raise cosmetics.ItemUnavailable()
        if it["starter"]:
            conn.execute("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = ?", (telegram_id, slot))
        else:
            if conn.execute("SELECT 1 FROM cosmetic_items WHERE telegram_id = ? AND item_code = ?", (telegram_id, code)).fetchone() is None:
                raise cosmetics.NotOwned()
            conn.execute("INSERT OR REPLACE INTO cosmetic_equipped (telegram_id, slot, item_code) VALUES (?, ?, ?)", (telegram_id, slot, code))
        return {"slot": slot, "code": code, "equipped": cosmetics.effective_equipped(_equipped_rows(conn, telegram_id))}

    return _run_action(telegram_id, request_id, "equip", {"slot": slot, "code": code}, body, now, db_path)


def unequip_item(telegram_id, request_id, slot, now=None, db_path=None):
    """Снять предмет слота (вернуться к стартовому). Слот без надетого предмета не ошибка."""
    if not cosmetics.valid_slot(slot):
        raise ValueError("invalid")

    def body(conn):
        conn.execute("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND slot = ?", (telegram_id, slot))
        return {"slot": slot, "code": cosmetics.STARTERS[slot], "equipped": cosmetics.effective_equipped(_equipped_rows(conn, telegram_id))}

    return _run_action(telegram_id, request_id, "unequip", {"slot": slot}, body, now, db_path)


def set_visibility(telegram_id, request_id, show_in_rating, now=None, db_path=None):
    """Показывать ли свои публичные слоты (рамка, значок) другим в рейтинге беседы."""
    if type(show_in_rating) is not bool:
        raise ValueError("invalid")

    def body(conn):
        conn.execute("INSERT OR IGNORE INTO cosmetic_prefs (telegram_id, show_in_rating) VALUES (?, ?) "
                     "ON CONFLICT(telegram_id) DO UPDATE SET show_in_rating = excluded.show_in_rating",
                     (telegram_id, 1 if show_in_rating else 0))
        return {"show_in_rating": show_in_rating}

    return _run_action(telegram_id, request_id, "visibility", {"show_in_rating": show_in_rating}, body, now, db_path)


# ---------- покупка за фишки ----------
def buy_with_chips(telegram_id, request_id, item_code, now=None, db_path=None):
    """Покупка предмета за фишки одной транзакцией BEGIN IMMEDIATE: предмет есть в каталоге, доступен, не стартовый и продаётся за фишки;
    «уже есть» проверяется ДО списания; затем баланс (с начислением), wallet.debit и выдача (источник chips). Опыт, total_staked, уровень и
    статистика ставок не меняются. Идемпотентно по (игрок, request_id). Ошибки: UnknownItem, ItemUnavailable, NotForChips, AlreadyOwned,
    InsufficientChips, RequestConflict."""
    if type(item_code) is not str:
        raise ValueError("invalid")
    if now is None:
        now = int(time.time())

    def body(conn):
        it = cosmetics.item(item_code)
        if it is None:
            raise cosmetics.UnknownItem()
        if not it["available"] or it["starter"]:
            raise cosmetics.ItemUnavailable()
        price = it["price"]
        if price is None:
            raise cosmetics.ItemUnavailable()
        if price["currency"] != cosmetics.CHIPS:
            raise cosmetics.NotForChips()
        if _owns(conn, telegram_id, item_code):
            raise cosmetics.AlreadyOwned()           # до списания
        _accrue_write(conn, telegram_id, now)
        try:
            wallet.debit(conn, telegram_id, price["amount"])
        except InsufficientFunds:
            raise cosmetics.InsufficientChips()
        _grant_in(conn, telegram_id, item_code, "chips", None, now)
        return {"item_code": item_code, "price": dict(price), "balance": wallet.get_balance(conn, telegram_id), "gems": wallet.gems_balance(conn, telegram_id)}

    return _run_action(telegram_id, request_id, "buy", {"item_code": item_code}, body, now, db_path, throttle=False)


def buy_with_gems(telegram_id, request_id, item_code, now=None, db_path=None):
    """Покупка предмета за кристаллы одной транзакцией BEGIN IMMEDIATE (так же, как за фишки): предмет в каталоге, доступен, не стартовый и продаётся за кристаллы;
    «уже есть» проверяется ДО списания; затем wallet.gems_debit (reason cosmetic_purchase, ref код предмета) и выдача (источник gems). Баланс фишек, опыт и
    ставки не меняются. Идемпотентно по (игрок, request_id). Ошибки: UnknownItem, ItemUnavailable, NotForGems, AlreadyOwned, InsufficientGems, RequestConflict."""
    if type(item_code) is not str:
        raise ValueError("invalid")
    if now is None:
        now = int(time.time())

    def body(conn):
        it = cosmetics.item(item_code)
        if it is None:
            raise cosmetics.UnknownItem()
        if not it["available"] or it["starter"]:
            raise cosmetics.ItemUnavailable()
        price = it["price"]
        if price is None:
            raise cosmetics.ItemUnavailable()
        if price["currency"] != cosmetics.GEMS:
            raise cosmetics.NotForGems()
        if _owns(conn, telegram_id, item_code):
            raise cosmetics.AlreadyOwned()           # до списания
        try:
            gems = wallet.gems_debit(conn, telegram_id, price["amount"], "cosmetic_purchase", item_code, now)
        except wallet.InsufficientGems:
            raise cosmetics.InsufficientGems()
        _grant_in(conn, telegram_id, item_code, "gems", None, now)
        return {"item_code": item_code, "price": dict(price), "balance": wallet.get_balance(conn, telegram_id), "gems": gems}

    return _run_action(telegram_id, request_id, "buy", {"item_code": item_code}, body, now, db_path, throttle=False)


def buy_set(telegram_id, request_id, set_code, now=None, db_path=None):
    """Покупка набора косметики за кристаллы. Одной транзакцией BEGIN IMMEDIATE: набор в каталоге,
    «уже есть хотя бы одна часть» проверяется ДО списания; затем wallet.gems_debit (reason cosmetic_purchase, ref код набора) 
    и выдача всех частей (источник gems). Баланс фишек, опыт и ставки не меняются.
    Идемпотентно по (игрок, request_id). Ошибки: UnknownSet, AlreadyOwned, InsufficientGems, RequestConflict."""
    if type(set_code) is not str:
        raise ValueError("invalid")
    if now is None:
        now = int(time.time())

    def body(conn):
        if set_code not in cosmetic_sets.SETS:
            raise cosmetics.UnknownSet()
        
        s = cosmetic_sets.SETS[set_code]
        parts = s["parts"]
        price_gems = s["price_gems"]
        
        for part_code in parts:
            if _owns(conn, telegram_id, part_code):
                raise cosmetics.AlreadyOwned()

        try:
            gems = wallet.gems_debit(conn, telegram_id, price_gems, "cosmetic_purchase", set_code, now)
        except wallet.InsufficientGems:
            raise cosmetics.InsufficientGems()
            
        for part_code in parts:
            _grant_in(conn, telegram_id, part_code, "gems", None, now)
            
        return {"set_code": set_code, "items": list(parts), "price_gems": price_gems, 
                "balance": wallet.get_balance(conn, telegram_id), "gems": gems}

    return _run_action(telegram_id, request_id, "buy_set", {"set_code": set_code}, body, now, db_path, throttle=False)


def buy_item(telegram_id, request_id, item_code, now=None, db_path=None):
    """Покупка предмета: за кристаллы или за фишки, как указано в каталоге."""
    it = cosmetics.item(item_code) if type(item_code) is str else None
    if it is not None and it["price"] is not None and it["price"]["currency"] == cosmetics.GEMS:
        return buy_with_gems(telegram_id, request_id, item_code, now=now, db_path=db_path)
    return buy_with_chips(telegram_id, request_id, item_code, now=now, db_path=db_path)


# ---------- оплата Telegram Stars ----------
def stars_offer(telegram_id, item_code, db_path=None):
    """Проверка в pre_checkout_query по старому счёту (только чтение): за Stars предмет принимается только по прежней цене (cosmetics.stars_price), он доступен и у игрока
    его ещё нет. Возвращает предмет с ценой в Stars. Ошибки: UnknownItem, ItemUnavailable, NotForStars, AlreadyOwned."""
    it = cosmetics.sellable(item_code)
    if it is None:
        raise cosmetics.UnknownItem()
    if not it["available"] or it["starter"]:
        raise cosmetics.ItemUnavailable()
    stars = cosmetics.stars_price(item_code)
    if stars is None:
        raise cosmetics.NotForStars()
    it = dict(it, price={"currency": cosmetics.STARS, "amount": stars})
    conn = _connect(db_path)
    try:
        if _owns(conn, telegram_id, item_code):
            raise cosmetics.AlreadyOwned()
    finally:
        conn.close()
    return it


def record_stars_payment(telegram_id, charge_id, item_code, amount, now=None, db_path=None):
    """Успешная оплата: ОДНА транзакция, запись в журнал (charge_id уникален) и выдача предмета (source stars, payment_ref = charge_id).
    Повторная доставка того же платежа ничего не создаёт: {"result": "duplicate"}. Если предмет у игрока уже есть (гонка двух инвойсов),
    запись получает статус refund_pending и возвращается {"result": "already_owned"}: вызывающий делает возврат и потом mark_refunded.
    {"result": "granted"} при успехе. ValueError: неверные данные платежа или предмета."""
    if type(charge_id) is not str or not 1 <= len(charge_id) <= 256 or type(amount) is not int or amount < 1 or cosmetics.sellable(item_code) is None:
        raise ValueError("payment")
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute("SELECT status FROM cosmetic_purchases WHERE charge_id = ?", (charge_id,)).fetchone()
            if old is not None:
                conn.execute("COMMIT")
                return {"result": "duplicate", "status": old["status"]}
            _register_player(conn, telegram_id, now)
            conn.execute("INSERT OR IGNORE INTO cosmetic_purchases (charge_id, telegram_id, item_code, amount_stars, status, created_at) VALUES (?, ?, ?, ?, 'paid', ?)",
                         (charge_id, telegram_id, item_code, amount, now))
            if _grant_in(conn, telegram_id, item_code, "stars", charge_id, now):
                result = "granted"
            else:
                conn.execute("UPDATE cosmetic_purchases SET status = 'refund_pending' WHERE charge_id = ?", (charge_id,))
                result = "already_owned"
            conn.execute("COMMIT")
            return {"result": result}
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def purchase_by_charge(charge_id, db_path=None):
    """Строка журнала оплат или None (для /refund и /regrant)."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT charge_id, telegram_id, item_code, amount_stars, status, created_at, refunded_at FROM cosmetic_purchases WHERE charge_id = ?",
                           (charge_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def mark_refunded(charge_id, now=None, db_path=None):
    """После возврата Stars: статус refunded, предмет игрока (выданный этим платежом) удаляется и снимается, если надет. Повторный вызов безопасен.
    Возвращает {"telegram_id", "item_code", "changed"} или None, если платежа нет."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT telegram_id, item_code, status FROM cosmetic_purchases WHERE charge_id = ?", (charge_id,)).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            changed = row["status"] != "refunded"
            if changed:
                conn.execute("UPDATE cosmetic_purchases SET status = 'refunded', refunded_at = ? WHERE charge_id = ?", (now, charge_id))
                removed = conn.execute("DELETE FROM cosmetic_items WHERE telegram_id = ? AND item_code = ? AND payment_ref = ?",
                                       (row["telegram_id"], row["item_code"], charge_id)).rowcount
                if removed:
                    conn.execute("DELETE FROM cosmetic_equipped WHERE telegram_id = ? AND item_code = ?", (row["telegram_id"], row["item_code"]))
            conn.execute("COMMIT")
            return {"telegram_id": row["telegram_id"], "item_code": row["item_code"], "changed": changed}
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def regrant_purchase(charge_id, now=None, db_path=None):
    """Ручная выдача оплаченного, если запись в журнале есть, а предмета нет: "granted", "already_has", "refunded" или "missing" (записи нет)."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT telegram_id, item_code, status FROM cosmetic_purchases WHERE charge_id = ?", (charge_id,)).fetchone()
            if row is None:
                result = "missing"
            elif row["status"] == "refunded":
                result = "refunded"
            else:
                result = "granted" if _grant_in(conn, row["telegram_id"], row["item_code"], "stars", charge_id, now) else "already_has"
            conn.execute("COMMIT")
            return result
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

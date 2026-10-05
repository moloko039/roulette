"""Косметика: принадлежность, надетое, показ в рейтинге, единственная точка выдачи (grant_item).

Только внешний вид: ни wallet, ни игровые модули, ни экономика сюда не заходят и косметику не читают. Стартовые предметы строками
не хранятся (действует стартовый слота, если записи нет). Изменения идут одной транзакцией BEGIN IMMEDIATE с идемпотентностью
по (игрок, request_id) и ограничением «не чаще одной смены в секунду на игрока»."""

import json
import time

import cosmetics

from core.db_conn import _connect
from core.kernel import _register_player

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


def cosmetics_mine(telegram_id, db_path=None):
    """GET /api/cosmetics/mine: свои предметы (без стартовых и без платёжных данных), надетое, показ в рейтинге."""
    conn = _connect(db_path)
    try:
        owned = [{"code": r["item_code"], "source": r["source"], "acquired_at": r["acquired_at"]} for r in conn.execute(
            "SELECT item_code, source, acquired_at FROM cosmetic_items WHERE telegram_id = ? ORDER BY acquired_at, item_code",
            (telegram_id,)) if cosmetics.item(r["item_code"]) is not None]
        state = {"equipped": cosmetics.effective_equipped(_equipped_rows(conn, telegram_id)),
                 "show_in_rating": _show_in_rating(conn, telegram_id)}
        return dict({"owned": owned}, **state)
    finally:
        conn.close()


def grant_item(telegram_id, code, source, payment_ref=None, now=None, db_path=None):
    """Единственная точка выдачи предмета. Идемпотентна: повтор не создаёт дубль и не падает (False). True, если выдан сейчас.
    ValueError: неизвестный код, неверный источник или стартовый предмет (стартовые не выдаются); NoSuchPlayer: игрока нет в базе.
    Платежей здесь нет: payment_ref пока всегда None (место для этапа с оплатой)."""
    it = cosmetics.item(code)
    if it is None or source not in cosmetics.SOURCES or it["starter"]:
        raise ValueError("grant")
    if payment_ref is not None and type(payment_ref) is not str:
        raise ValueError("payment_ref")
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone() is None:
                raise cosmetics.NoSuchPlayer()
            added = conn.execute(
                "INSERT OR IGNORE INTO cosmetic_items (telegram_id, item_code, source, payment_ref, acquired_at) VALUES (?, ?, ?, ?, ?)",
                (telegram_id, code, source, payment_ref, now)).rowcount
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return added == 1


def _run_action(telegram_id, request_id, action, params, body, now, db_path):
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
            last = conn.execute("SELECT MAX(created_at) FROM cosmetic_actions WHERE telegram_id = ?", (telegram_id,)).fetchone()[0]
            if last is not None and now - last < CHANGE_INTERVAL_SECONDS:
                raise cosmetics.TooFast()     # откат: смена не применяется
            response["replayed"] = False
            conn.execute(
                "INSERT INTO cosmetic_actions (telegram_id, request_id, action, params, response_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
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
        conn.execute("INSERT INTO cosmetic_prefs (telegram_id, show_in_rating) VALUES (?, ?) "
                     "ON CONFLICT(telegram_id) DO UPDATE SET show_in_rating = excluded.show_in_rating",
                     (telegram_id, 1 if show_in_rating else 0))
        return {"show_in_rating": show_in_rating}

    return _run_action(telegram_id, request_id, "visibility", {"show_in_rating": show_in_rating}, body, now, db_path)

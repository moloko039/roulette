"""Кристаллы: покупка пакетов за Telegram Stars, возврат пакета, выдача владельцем (план экономики, этап E2).

Все изменения баланса идут через wallet.gems_credit/gems_debit внутри транзакции BEGIN IMMEDIATE. Игровые модули и косметика кристаллов не читают
(траты на предметы оформления добавляются отдельным шагом). Оплата идемпотентна по charge_id Telegram."""
import sqlite3
import time

import economy_config
import wallet

from core.db_conn import _connect
from core.kernel import _register_player
from features.give_db import PlayerMissing


class GemsError(Exception):
    code = "gems_error"


class UnknownPack(GemsError):
    code = "unknown_pack"


def pack(pack_code):
    """(звёзды, кристаллы) пакета или UnknownPack."""
    entry = economy_config.GEM_PACKS.get(pack_code) if type(pack_code) is str else None
    if entry is None:
        raise UnknownPack()
    return entry


def packs_view():
    """Список пакетов для клиента: [{code, stars, gems}] (порядок по цене)."""
    return [{"code": c, "stars": s, "gems": g} for c, (s, g) in sorted(economy_config.GEM_PACKS.items(), key=lambda kv: kv[1][0])]


def gems_state(telegram_id, db_path=None):
    """{"gems": int}: баланс кристаллов (0, если записи нет). Только чтение."""
    conn = _connect(db_path)
    try:
        return {"gems": wallet.gems_balance(conn, telegram_id)}
    finally:
        conn.close()


def record_gem_payment(telegram_id, charge_id, pack_code, amount_stars, now=None, db_path=None):
    """Успешная оплата пакета: ОДНА транзакция, запись в журнал оплат (charge_id уникален) и начисление кристаллов (reason purchase, ref charge_id).
    Повторная доставка платежа ничего не создаёт: {"result": "duplicate"}. Баланс упёрся бы в потолок: запись получает статус refund_pending,
    кристаллы не начисляются, {"result": "limit"} (вызывающий делает возврат Stars). Успех: {"result": "granted", "gems": начислено, "balance": баланс}.
    ValueError: сумма платежа не равна цене пакета, плохой charge_id, неизвестный пакет."""
    if type(charge_id) is not str or not 1 <= len(charge_id) <= 256 or type(amount_stars) is not int or amount_stars < 1:
        raise ValueError("payment")
    stars, gems = pack(pack_code)
    if amount_stars != stars:
        raise ValueError("payment")
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            old = conn.execute("SELECT status FROM gem_purchases WHERE charge_id = ?", (charge_id,)).fetchone()
            if old is not None:
                conn.execute("COMMIT")
                return {"result": "duplicate", "status": old["status"]}
            _register_player(conn, telegram_id, now)
            try:
                balance = wallet.gems_credit(conn, telegram_id, gems, "purchase", charge_id, now)
                status, result = "paid", "granted"
            except wallet.GemsLimitExceeded:
                balance, status, result = wallet.gems_balance(conn, telegram_id), "refund_pending", "limit"
            conn.execute("INSERT INTO gem_purchases (charge_id, telegram_id, pack_code, amount_stars, gems, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (charge_id, telegram_id, pack_code, amount_stars, gems, status, now))
            conn.execute("COMMIT")
            return {"result": result, "gems": gems if result == "granted" else 0, "balance": balance}
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def gem_purchase_by_charge(charge_id, db_path=None):
    """Строка журнала оплат пакетов или None (для /refund)."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT charge_id, telegram_id, pack_code, amount_stars, gems, status, created_at, refunded_at FROM gem_purchases WHERE charge_id = ?",
                           (charge_id,)).fetchone()
        return dict(row) if row is not None else None
    finally:
        conn.close()


def refund_check(charge_id, db_path=None):
    """Можно ли вернуть пакет по правилам economy_config.REFUND_RULE (кристаллы этого платежа не потрачены): только чтение.
    Возвращает {"ok": bool, "reason": None | "missing" | "refunded" | "spent", "telegram_id", "gems"}.
    «Не потрачены»: после начисления пакета у игрока не было ни одного списания и баланс не меньше пакета."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT telegram_id, gems, status FROM gem_purchases WHERE charge_id = ?", (charge_id,)).fetchone()
        if row is None:
            return {"ok": False, "reason": "missing", "telegram_id": None, "gems": 0}
        info = {"telegram_id": row["telegram_id"], "gems": row["gems"]}
        if row["status"] == "refunded":
            return dict(info, ok=False, reason="refunded")
        credit = conn.execute("SELECT id FROM gems_ledger WHERE telegram_id = ? AND reason = 'purchase' AND ref = ?", (row["telegram_id"], charge_id)).fetchone()
        spent = False
        if credit is not None:
            spent = conn.execute("SELECT 1 FROM gems_ledger WHERE telegram_id = ? AND delta < 0 AND id > ? LIMIT 1", (row["telegram_id"], credit["id"])).fetchone() is not None
        if spent or wallet.gems_balance(conn, row["telegram_id"]) < row["gems"]:
            return dict(info, ok=False, reason="spent")
        return dict(info, ok=True, reason=None)
    finally:
        conn.close()


def finish_gem_refund(charge_id, now=None, db_path=None):
    """После возврата Stars: статус refunded и списание кристаллов пакета (reason refund). Если кристаллов на балансе меньше пакета (принудительный возврат владельцем),
    списывается всё, что есть. Повторный вызов безопасен. Возвращает {"telegram_id", "taken", "changed"} или None, если платежа нет."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT telegram_id, gems, status FROM gem_purchases WHERE charge_id = ?", (charge_id,)).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            taken, changed = 0, row["status"] != "refunded"
            if changed:
                conn.execute("UPDATE gem_purchases SET status = 'refunded', refunded_at = ? WHERE charge_id = ?", (now, charge_id))
                taken = min(row["gems"], wallet.gems_balance(conn, row["telegram_id"]))
                paid = conn.execute("SELECT 1 FROM gems_ledger WHERE telegram_id = ? AND reason = 'purchase' AND ref = ?", (row["telegram_id"], charge_id)).fetchone()
                if taken > 0 and paid is not None:
                    wallet.gems_debit(conn, row["telegram_id"], taken, "refund", charge_id, now)
                else:
                    taken = 0
            conn.execute("COMMIT")
            return {"telegram_id": row["telegram_id"], "taken": taken, "changed": changed}
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def owner_grant_gems(telegram_id, amount, ref, now=None, db_path=None, must_exist=False):
    """Выдача кристаллов владельцем (reason owner_grant); ref уникален на игрока (повтор с тем же ref не начисляет второй раз: {"result": "duplicate"}).
    Игрока без записи в players нет смысла создавать: PlayerMissing не нужен, кристаллы привязаны к Telegram id."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if must_exist:
                if conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone() is None:
                    raise PlayerMissing()
            else:
                _register_player(conn, telegram_id, now)
            try:
                balance = wallet.gems_credit(conn, telegram_id, amount, "owner_grant", ref, now)
            except sqlite3.IntegrityError:
                conn.execute("ROLLBACK")
                return {"result": "duplicate", "balance": gems_state(telegram_id, db_path)["gems"]}
            conn.execute("COMMIT")
            return {"result": "granted", "balance": balance}
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        conn.close()

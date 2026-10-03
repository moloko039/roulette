import json
import os
import secrets
import sqlite3
import time

from economy import START_BALANCE, BASE_RATE, accrue
from roulette import BalanceLimit, InsufficientFunds, MAX_SAFE_INT, max_payout, settle

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "players.db")

# SQLite рассчитана на ОДИН экземпляр сервиса: файл базы лежит на одном диске
# (на Railway это том), и два экземпляра не смогут безопасно писать в него.
# Число реплик должно быть 1.


def _resolve_path(db_path):
    """Путь к файлу: аргумент, затем переменная DB_PATH, затем файл рядом с кодом."""
    path = db_path or os.environ.get("DB_PATH") or DB_PATH
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)  # папки для файла может не быть (например, свежий том)
    return path


def _connect(db_path):
    conn = sqlite3.connect(_resolve_path(db_path))
    conn.row_factory = sqlite3.Row
    # транзакциями управляем вручную (BEGIN IMMEDIATE), а не автоматически
    conn.isolation_level = None
    return conn


def init_db(db_path=None):
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                telegram_id  INTEGER PRIMARY KEY,
                balance      INTEGER NOT NULL,
                rate         INTEGER NOT NULL,
                last_accrual INTEGER NOT NULL,
                created_at   INTEGER NOT NULL
            )
            """
        )
        # раунды рулетки: ключ (игрок, request_id) защищает от повторного списания при повторе запроса
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS roulette_rounds (
                telegram_id  INTEGER NOT NULL,
                request_id   TEXT    NOT NULL,
                number       INTEGER NOT NULL,
                stake_total  INTEGER NOT NULL,
                payout_total INTEGER NOT NULL,
                bets_json    TEXT    NOT NULL,
                created_at   INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
    finally:
        conn.close()


def get_player(telegram_id, now=None, db_path=None):
    """Находит игрока (или регистрирует), начисляет фишки, возвращает словарь."""
    if now is None:
        now = int(time.time())

    conn = _connect(db_path)
    try:
        # IMMEDIATE сразу берёт блокировку на запись: два одновременных запроса
        # одного игрока выполняются по очереди, а не читают одно и то же старое значение
        conn.execute("BEGIN IMMEDIATE")
        try:
            # новый игрок получает стартовый баланс; существующего не трогаем
            conn.execute(
                "INSERT OR IGNORE INTO players "
                "(telegram_id, balance, rate, last_accrual, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (telegram_id, START_BALANCE, BASE_RATE, now, now),
            )
            row = conn.execute(
                "SELECT balance, rate, last_accrual FROM players WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()

            earned, new_last = accrue(row["last_accrual"], now, row["rate"])
            balance = row["balance"] + earned

            if earned or new_last != row["last_accrual"]:
                conn.execute(
                    "UPDATE players SET balance = ?, last_accrual = ? WHERE telegram_id = ?",
                    (balance, new_last, telegram_id),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    return {
        "telegram_id": telegram_id,
        "balance": balance,
        "rate": row["rate"],
        "last_accrual": new_last,
    }


def _round_result(number, stake_total, payout_total, balance, replayed):
    return {
        "number": number,
        "stake_total": stake_total,
        "payout_total": payout_total,
        "net": payout_total - stake_total,
        "balance": balance,
        "replayed": replayed,
    }


def spin_roulette(telegram_id, request_id, bets, now=None, db_path=None, rng=None):
    """Один спин рулетки в одной транзакции.

    bets уже проверены validate_bets(). rng(n) возвращает число 0..n-1 (по умолчанию
    secrets.randbelow); параметр нужен, чтобы тесты подставляли числа.
    Бросает InsufficientFunds или BalanceLimit; тогда в базе ничего не меняется.
    """
    if now is None:
        now = int(time.time())
    if rng is None:
        rng = secrets.randbelow

    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # (а) повторный запрос: отдаём сохранённый результат, ничего не меняем.
            # balance в ответе — текущий баланс игрока, а не баланс на момент раунда
            old = conn.execute(
                "SELECT number, stake_total, payout_total FROM roulette_rounds "
                "WHERE telegram_id = ? AND request_id = ?",
                (telegram_id, request_id),
            ).fetchone()
            if old is not None:
                cur = conn.execute(
                    "SELECT balance FROM players WHERE telegram_id = ?", (telegram_id,)
                ).fetchone()
                result = _round_result(old["number"], old["stake_total"], old["payout_total"],
                                       cur["balance"] if cur else 0, True)
                conn.execute("COMMIT")
                return result

            # (б) игрок и начисление: потратить можно и только что начисленное
            conn.execute(
                "INSERT OR IGNORE INTO players "
                "(telegram_id, balance, rate, last_accrual, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (telegram_id, START_BALANCE, BASE_RATE, now, now),
            )
            row = conn.execute(
                "SELECT balance, rate, last_accrual FROM players WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            earned, new_last = accrue(row["last_accrual"], now, row["rate"])
            balance = row["balance"] + earned

            # (в) хватает ли фишек и не упрётся ли баланс в предел точных чисел JavaScript
            stake_total = sum(b["amount"] for b in bets)
            if stake_total > balance:
                raise InsufficientFunds()
            if balance - stake_total + max_payout(bets) > MAX_SAFE_INT:
                raise BalanceLimit()

            # (г) число, (д) выигрыш
            number = rng(37)
            if type(number) is not int or not 0 <= number <= 36:
                raise RuntimeError("bad random number")
            stake_total, payout_total = settle(bets, number)
            new_balance = balance - stake_total + payout_total

            # (е) новый баланс и запись раунда
            conn.execute(
                "UPDATE players SET balance = ?, last_accrual = ? WHERE telegram_id = ?",
                (new_balance, new_last, telegram_id),
            )
            conn.execute(
                "INSERT INTO roulette_rounds "
                "(telegram_id, request_id, number, stake_total, payout_total, bets_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (telegram_id, request_id, number, stake_total, payout_total,
                 json.dumps(bets, separators=(",", ":")), now),
            )
            # (ж)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    return _round_result(number, stake_total, payout_total, new_balance, False)

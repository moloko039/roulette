"""Общий скелет партийных игр (мины, блэкджек, краш, хило): повтор по request_id, автозакрытие брошенных, чтение состояния.

Игра описывается объектом Game (имя, ошибка конфликта request_id) и даёт модулю только своё: тело действия, правила окончания
партии, формы ответов. Здесь нет денег и правил игр: списания и выплаты остаются в играх и идут через wallet, этот модуль
только открывает и закрывает транзакции и пишет таблицу действий. Порядок SQL-запросов совпадает с тем, что делали игры до выноса
(проверяется эталоном bot/test_game_golden.py). Имена таблиц берутся из закрытого списка GAMES: значения в SQL только параметрами.

Слой games: зависит от core и wallet, друг от друга и от db.py не зависит."""

import json
import time

from levels import profile_level
from roulette import MAX_SAFE_INT

from core.db_conn import _connect, logger
from core.kernel import _accrue_write, _add_xp, _credit_capped, _record_best_win, _register_player

GAMES = ("mines", "blackjack", "crash", "hilo")
CLOSE_BATCH = 200   # сколько брошенных партий закрывает один проход фоновой задачи


class Game:
    """Описание игры для шаблона: таблицы партий и действий, ошибка «тот же request_id с другими параметрами»."""

    def __init__(self, name, conflict):
        if name not in GAMES:      # имя таблицы подставляется в текст запроса: допускаются только известные игры
            raise ValueError("unknown game")
        self.name = name
        self.games = name + "_games"
        self.conflict = conflict
        self.sql_active = "SELECT * FROM " + self.games + " WHERE telegram_id = ? AND status = 'active'"
        self.sql_latest = ("SELECT * FROM " + self.games +
                           " WHERE telegram_id = ? ORDER BY (status = 'active') DESC, id DESC LIMIT 1")
        self.sql_table_exists = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = '" + self.games + "'"
        self.sql_action_get = ("SELECT action, params, response_json FROM " + name +
                               "_actions WHERE telegram_id = ? AND request_id = ?")
        self.sql_action_put = ("INSERT INTO " + name + "_actions (telegram_id, request_id, action, params, response_json, created_at) "
                               "VALUES (?, ?, ?, ?, ?, ?)")


def now_or_clock(now):
    return int(time.time()) if now is None else now


def active_row(conn, game, telegram_id):
    """Незавершённая партия игрока или None."""
    return conn.execute(game.sql_active, (telegram_id,)).fetchone()


def latest_row(conn, game, telegram_id):
    """Активная партия, иначе последняя завершённая, иначе None."""
    return conn.execute(game.sql_latest, (telegram_id,)).fetchone()


def player_view(conn, telegram_id):
    """(баланс, уровень профиля, опыт) для ответа игры."""
    pl = conn.execute("SELECT balance, xp FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone()
    return pl["balance"], profile_level(pl["xp"]), pl["xp"]


def add_staked(conn, telegram_id, amount):
    """Ставка в total_staked (не выше MAX_SAFE_INT): момент учёта решает игра."""
    conn.execute("UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                 (amount, MAX_SAFE_INT, telegram_id))


def pay_and_xp(conn, telegram_id, paid, xp_amount, game, stake, now):
    """Окончание партии, шаг выплаты: выплата через wallet (не выше потолка), личный рекорд (_record_best_win по зачисленному и
    полной ставке раунда) и опыт. Вызывать после того, как партия уже помечена закрытой (повторно её никто не закроет,
    значит выплата, рекорд и опыт один раз)."""
    if paid > 0:
        _record_best_win(conn, telegram_id, game, stake, _credit_capped(conn, telegram_id, paid), now)
    if xp_amount is not None:
        _add_xp(conn, telegram_id, xp_amount)


def run_tx(conn, work):
    """work(conn) внутри BEGIN IMMEDIATE на уже открытом соединении: COMMIT при успехе, ROLLBACK при любой ошибке."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        result = work(conn)
        conn.execute("COMMIT")
        return result
    except Exception:
        conn.execute("ROLLBACK")
        raise


def in_transaction(db_path, work):
    """Своё соединение: work(conn) внутри BEGIN IMMEDIATE, соединение всегда закрывается."""
    conn = _connect(db_path)
    try:
        return run_tx(conn, work)
    finally:
        conn.close()


def run_action(game, telegram_id, request_id, action, params, body, now, db_path, presettle=None, finalize=None):
    """Порядок любого действия: закрытие просроченной партии (presettle, вне транзакции), повтор по request_id (другое
    действие или параметры: game.conflict), регистрация и минутное начисление, тело действия body(conn) -> ответ, запись
    ответа в таблицу действий. finalize(conn, ответ) дополняет ответ до поля replayed."""
    if presettle is not None:
        presettle()
    params_json = json.dumps(params, sort_keys=True, separators=(",", ":"))

    def work(conn):
        old = conn.execute(game.sql_action_get, (telegram_id, request_id)).fetchone()
        if old is not None:
            if old["action"] != action or old["params"] != params_json:
                raise game.conflict()
            response = json.loads(old["response_json"])
            response["replayed"] = True
            return response
        _register_player(conn, telegram_id, now)
        _accrue_write(conn, telegram_id, now)
        response = body(conn)
        if finalize is not None:
            finalize(conn, response)
        response["replayed"] = False
        conn.execute(game.sql_action_put,
                     (telegram_id, request_id, action, params_json, json.dumps(response, separators=(",", ":")), now))
        return response

    return in_transaction(db_path, work)


def settle_expired(settle_in, db_path, precheck=None):
    """Закрывает просроченную партию отдельной транзакцией (идемпотентно). precheck(conn) -> False: срок не вышел, транзакцию
    не открываем (игры с чтением до записи не берут блокировку зря). settle_in(conn) -> True, если закрыла."""
    conn = _connect(db_path)
    try:
        if precheck is not None and not precheck(conn):
            return False
        return run_tx(conn, settle_in)
    finally:
        conn.close()


def close_expired(game, owners_sql, owners_params, settle_one, db_path, label):
    """Фоновое закрытие: владельцы просроченных партий выбираются одним запросом (owners_sql), каждая закрывается своей
    транзакцией через settle_one(owner). Возвращает число закрытых; в журнал только число."""
    conn = _connect(db_path)
    try:
        if conn.execute(game.sql_table_exists).fetchone() is None:
            return 0
        owners = [r["telegram_id"] for r in conn.execute(owners_sql, owners_params)]
    finally:
        conn.close()
    closed = sum(1 for owner in owners if settle_one(owner))
    if closed:
        logger.info("%s: %d", label, closed)   # только количество
    return closed


def read_state(telegram_id, now, db_path, read):
    """Чтение состояния с регистрацией и минутным начислением (баланс как в /api/me): read(conn) внутри транзакции."""
    def work(conn):
        _register_player(conn, telegram_id, now)
        _accrue_write(conn, telegram_id, now)
        return read(conn)

    return in_transaction(db_path, work)

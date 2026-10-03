import json
import logging
import os
import unicodedata
import secrets
import sqlite3
import time

import antiabuse
from antiabuse import COOLDOWN_SECONDS, TombstoneUnavailable
from economy import START_BALANCE, BASE_RATE, accrue
import wallet
from levels import profile_level
from roulette import BalanceLimit, InsufficientFunds, MAX_SAFE_INT, max_payout, settle

logger = logging.getLogger("depnaya.db")

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
                created_at   INTEGER NOT NULL,
                total_staked INTEGER NOT NULL DEFAULT 0
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
        # участники бесед для рейтинга: chat_instance — глобальный id чата из подписанных данных
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_members (
                chat_instance TEXT    NOT NULL,
                telegram_id   INTEGER NOT NULL,
                first_name    TEXT    NOT NULL,
                first_seen    INTEGER NOT NULL,
                last_seen     INTEGER NOT NULL,
                PRIMARY KEY (chat_instance, telegram_id)
            )
            """
        )
        # «надгробия» после удаления данных: только хэш идентификатора и дата удаления
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deletion_tombstones (
                key_hash   TEXT    PRIMARY KEY,
                deleted_at INTEGER NOT NULL
            )
            """
        )
        # служебные отметки (время последних уведомлений владельцу); личных данных здесь нет
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        _migrate_total_staked(conn)
        # записи старше срока защиты не нужны
        conn.execute(
            "DELETE FROM deletion_tombstones WHERE deleted_at + ? <= ?",
            (COOLDOWN_SECONDS, int(time.time())),
        )
    finally:
        conn.close()


def _migrate_total_staked(conn):
    """Добавляет players.total_staked в старую базу (идемпотентно, одной транзакцией).

    Начальное значение игрока = сумма stake_total из roulette_rounds. Учитывается только то, что ещё
    хранится: раунды старше срока хранения уже удалены очисткой, поэтому у старых игроков значение
    может быть меньше фактической суммы ставок. Дальше счётчик растёт в spin_roulette и очисткой
    раундов не уменьшается.
    """
    def has_column():
        return any(r["name"] == "total_staked" for r in conn.execute("PRAGMA table_info(players)"))

    if has_column():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not has_column():  # повторная проверка внутри транзакции
            conn.execute("ALTER TABLE players ADD COLUMN total_staked INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                "UPDATE players SET total_staked = MIN(COALESCE("
                "(SELECT SUM(r.stake_total) FROM roulette_rounds r WHERE r.telegram_id = players.telegram_id), 0), ?)",
                (MAX_SAFE_INT,),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_meta(key, db_path=None):
    """Значение служебной отметки или None."""
    conn = _connect(db_path)
    try:
        row = conn.execute("SELECT value FROM service_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        conn.close()


def set_meta(key, value, db_path=None):
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO service_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
    finally:
        conn.close()


def _register_player(conn, telegram_id, now):
    """Единственное место, где создаётся строка в players (get_player, spin_roulette, chat_top).

    Новый игрок получает START_BALANCE, кроме случая, когда его данные удалили меньше
    REGISTRATION_COOLDOWN_DAYS назад: тогда баланс 0, скорость и время начисления обычные.
    Если игрок уже есть, ничего не меняется. Без TOMBSTONE_SECRET защита не работает,
    поведение прежнее.
    """
    if conn.execute("SELECT 1 FROM players WHERE telegram_id = ?", (telegram_id,)).fetchone() is not None:
        return
    balance = START_BALANCE
    secret = antiabuse.tombstone_secret()
    if secret is not None:
        row = conn.execute(
            "SELECT deleted_at FROM deletion_tombstones WHERE key_hash = ?",
            (antiabuse.key_hash(telegram_id, secret),),
        ).fetchone()
        if row is not None and row["deleted_at"] + COOLDOWN_SECONDS > now:
            balance = 0
    conn.execute(
        "INSERT OR IGNORE INTO players "
        "(telegram_id, balance, rate, last_accrual, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (telegram_id, balance, BASE_RATE, now, now),
    )


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
            # новый игрок получает стартовый баланс (или 0 в период защиты); существующего не трогаем
            _register_player(conn, telegram_id, now)
            row = conn.execute(
                "SELECT balance, rate, last_accrual, total_staked FROM players WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()

            # начисление по часам: единственная правка баланса вне wallet (это не игровое списание или выплата)
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
        "total_staked": row["total_staked"],
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

            # (б) игрок и начисление: потратить можно и только что начисленное.
            # Начисление по часам записывается сразу (при ошибке ниже транзакция откатится целиком)
            _register_player(conn, telegram_id, now)
            row = conn.execute(
                "SELECT balance, rate, last_accrual FROM players WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            earned, new_last = accrue(row["last_accrual"], now, row["rate"])
            conn.execute(
                "UPDATE players SET balance = ?, last_accrual = ? WHERE telegram_id = ?",
                (row["balance"] + earned, new_last, telegram_id),
            )

            # (в) списание ставки через кошелёк (InsufficientFunds, если фишек не хватает) и проверка,
            # не упрётся ли баланс в предел точных чисел JavaScript при самой большой выплате
            stake_total = sum(b["amount"] for b in bets)
            wallet.debit(conn, telegram_id, stake_total)
            if wallet.get_balance(conn, telegram_id) + max_payout(bets) > MAX_SAFE_INT:
                raise BalanceLimit()

            # (г) число, (д) выигрыш
            number = rng(37)
            if type(number) is not int or not 0 <= number <= 36:
                raise RuntimeError("bad random number")
            stake_total, payout_total = settle(bets, number)

            # (е) выплата через кошелёк, запись раунда и счётчик ставок (в той же транзакции)
            if payout_total > 0:
                wallet.credit(conn, telegram_id, payout_total)
            new_balance = wallet.get_balance(conn, telegram_id)
            conn.execute(
                "UPDATE players SET total_staked = MIN(total_staked + ?, ?) WHERE telegram_id = ?",
                (stake_total, MAX_SAFE_INT, telegram_id),
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


# ---------- рейтинг беседы ----------
NAME_MAX = 32
DEFAULT_NAME = "Игрок"
TOUCH_INTERVAL = 60       # запись участника обновляется не чаще раза в 60 секунд
TOP_SIZE = 10
# Ограничение для больших чатов: в рейтинге учитываются не больше 1000 участников одной
# беседы, самые недавно активные (по last_seen). Остальные в рейтинг не попадают, и
# me.total считается по этим же участникам, поэтому ранг и total всегда согласованы.
MAX_CHAT_MEMBERS = 1000

# управляющие направления текста (могут перевернуть соседний текст на экране)
_BIDI = set("\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")


def clean_name(name):
    """Убирает управляющие символы, обрезает пробелы по краям и длину до 32 символов."""
    if not isinstance(name, str):
        return DEFAULT_NAME
    kept = "".join(ch for ch in name if unicodedata.category(ch) not in ("Cc", "Cs") and ch not in _BIDI)
    kept = kept.strip()[:NAME_MAX].strip()
    return kept or DEFAULT_NAME


def _touch_member(conn, chat_instance, telegram_id, first_name, now):
    """Запись или обновление участника внутри открытой транзакции. True, если база изменена."""
    name = clean_name(first_name)
    row = conn.execute(
        "SELECT last_seen FROM chat_members WHERE chat_instance = ? AND telegram_id = ?",
        (chat_instance, telegram_id),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_instance, telegram_id, name, now, now),
        )
        return True
    if now - row["last_seen"] < TOUCH_INTERVAL:
        return False
    conn.execute(
        "UPDATE chat_members SET first_name = ?, last_seen = ? WHERE chat_instance = ? AND telegram_id = ?",
        (name, now, chat_instance, telegram_id),
    )
    return True


def touch_chat_member(chat_instance, telegram_id, first_name, now=None, db_path=None):
    """Записывает или обновляет участника беседы (не чаще раза в 60 секунд). Возвращает True, если записал."""
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            changed = _touch_member(conn, chat_instance, telegram_id, first_name, now)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return changed


def chat_top(chat_instance, telegram_id, first_name, now=None, db_path=None):
    """Рейтинг беседы: до 10 лучших и позиция вызвавшего.

    Вызвавший записывается как участник (как в touch_chat_member). Баланс для показа =
    хранимый + то, что начислилось бы по accrue() на время now; в базе из-за этого
    расчёта ничего не меняется. Сортировка: баланс по убыванию, затем first_seen, затем
    telegram_id. Игроки без записи в players пропускаются.
    """
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            # вызвавший должен быть в players, чтобы попасть в список без вызова /api/me;
            # начисление ему при этом не применяется (строка создаётся только для новых)
            _register_player(conn, telegram_id, now)
            _touch_member(conn, chat_instance, telegram_id, first_name, now)
            rows = conn.execute(
                "SELECT m.telegram_id, m.first_name, m.first_seen, p.balance, p.rate, p.last_accrual, "
                "       p.total_staked "
                "FROM (SELECT telegram_id, first_name, first_seen FROM chat_members "
                "      WHERE chat_instance = ? ORDER BY last_seen DESC, telegram_id LIMIT ?) m "
                "JOIN players p ON p.telegram_id = m.telegram_id",
                (chat_instance, MAX_CHAT_MEMBERS),
            ).fetchall()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()

    entries = []
    for r in rows:
        earned, _ = accrue(r["last_accrual"], now, r["rate"])
        entries.append((-(r["balance"] + earned), r["first_seen"], r["telegram_id"], r["first_name"],
                        r["total_staked"]))
    entries.sort(key=lambda e: (e[0], e[1], e[2]))

    top = [
        {"rank": i + 1, "name": e[3], "balance": -e[0], "is_me": e[2] == telegram_id, "staked": e[4],
         "level": profile_level(e[4])}
        for i, e in enumerate(entries[:TOP_SIZE])
    ]
    me = None
    for i, e in enumerate(entries):
        if e[2] == telegram_id:
            # total здесь число участников рейтинга (не сумма ставок); сумма ставок: staked и chat_staked
            me = {"rank": i + 1, "balance": -e[0], "total": len(entries), "staked": e[4],
                  "level": profile_level(e[4])}
    # сумма ставок всех участников того же набора, по которому строится рейтинг (без разбивки по людям)
    chat_staked = min(sum(e[4] for e in entries), MAX_SAFE_INT)
    return {"scope": "chat", "top": top, "me": me, "chat_staked": chat_staked}


# ---------- права на данные: выгрузка и удаление ----------

def get_player_export(telegram_id, rounds_limit=100, db_path=None):
    """Данные игрока для /mydata (только чтение). None, если о нём вообще ничего нет.

    В выгрузку не входят идентификаторы чатов и данные других игроков.
    """
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN")  # один снимок для всех запросов
        try:
            player = conn.execute(
                "SELECT telegram_id, balance, rate, last_accrual, created_at, total_staked FROM players "
                "WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()
            rounds = conn.execute(
                "SELECT created_at, bets_json, number, stake_total, payout_total FROM roulette_rounds "
                "WHERE telegram_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (telegram_id, rounds_limit),
            ).fetchall()
            chats = conn.execute(
                "SELECT first_seen, last_seen, first_name FROM chat_members "
                "WHERE telegram_id = ? ORDER BY first_seen, last_seen",
                (telegram_id,),
            ).fetchall()
        finally:
            conn.execute("COMMIT")
    finally:
        conn.close()
    if player is None and not rounds and not chats:
        return None
    return {
        "player": dict(player) if player is not None else None,
        "rounds": [
            {"time": r["created_at"], "bets": json.loads(r["bets_json"]), "number": r["number"],
             "stake_total": r["stake_total"], "payout_total": r["payout_total"]}
            for r in rounds
        ],
        "chats": [{"first_seen": c["first_seen"], "last_seen": c["last_seen"], "name": c["first_name"]}
                  for c in chats],
    }


def delete_player_data(telegram_id, db_path=None, now=None):
    """Удаляет все данные игрока в одной транзакции. Возвращает число удалённых строк по таблицам.

    В той же транзакции, если у игрока была строка в players, записывается «надгробие»:
    HMAC-хэш идентификатора и время удаления (идентификатор не хранится). Просроченные
    надгробия удаляются. Без TOMBSTONE_SECRET ничего не удаляется: бросается
    TombstoneUnavailable.
    """
    secret = antiabuse.tombstone_secret()
    if secret is None:
        raise TombstoneUnavailable()
    if now is None:
        now = int(time.time())
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            counts = {
                "players": conn.execute("DELETE FROM players WHERE telegram_id = ?", (telegram_id,)).rowcount,
                "roulette_rounds": conn.execute(
                    "DELETE FROM roulette_rounds WHERE telegram_id = ?", (telegram_id,)).rowcount,
                "chat_members": conn.execute(
                    "DELETE FROM chat_members WHERE telegram_id = ?", (telegram_id,)).rowcount,
            }
            if counts["players"] > 0:
                conn.execute(
                    "INSERT OR REPLACE INTO deletion_tombstones (key_hash, deleted_at) VALUES (?, ?)",
                    (antiabuse.key_hash(telegram_id, secret), now),
                )
            conn.execute(
                "DELETE FROM deletion_tombstones WHERE deleted_at + ? <= ?", (COOLDOWN_SECONDS, now)
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    return counts


# ---------- очистка старых данных ----------
PURGE_BATCH = 1000


def purge_old_data(now=None, db_path=None, rounds_days=30, member_days=90, batch=PURGE_BATCH):
    """Удаляет старые данные пачками (каждая пачка отдельной короткой транзакцией).

    roulette_rounds старше rounds_days (не меньше 2 суток: на это время нужна защита от
    повторов request_id), chat_members с last_seen старше member_days (не меньше 7) и
    просроченные deletion_tombstones. Таблицу players не трогает никогда.
    Возвращает число удалённых строк по таблицам; в лог идут только количества.
    """
    if now is None:
        now = int(time.time())
    rounds_days = max(int(rounds_days), 2)
    member_days = max(int(member_days), 7)
    conn = _connect(db_path)
    deleted = {"roulette_rounds": 0, "chat_members": 0, "deletion_tombstones": 0}
    try:
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

        def batches(sql, params):
            total = 0
            while True:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    n = conn.execute(sql, params).rowcount
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
                total += n
                if n < batch:
                    return total
                time.sleep(0.01)  # между пачками даём пройти другим записям

        if "roulette_rounds" in present:
            deleted["roulette_rounds"] = batches(
                "DELETE FROM roulette_rounds WHERE rowid IN "
                "(SELECT rowid FROM roulette_rounds WHERE created_at < ? LIMIT ?)",
                (now - rounds_days * 86400, batch))
        if "chat_members" in present:
            deleted["chat_members"] = batches(
                "DELETE FROM chat_members WHERE rowid IN "
                "(SELECT rowid FROM chat_members WHERE last_seen < ? LIMIT ?)",
                (now - member_days * 86400, batch))
        if "deletion_tombstones" in present:
            deleted["deletion_tombstones"] = batches(
                "DELETE FROM deletion_tombstones WHERE rowid IN "
                "(SELECT rowid FROM deletion_tombstones WHERE deleted_at + ? <= ? LIMIT ?)",
                (COOLDOWN_SECONDS, now, batch))
    finally:
        conn.close()
    logger.info("Очистка старых данных: раунды=%d участники=%d надгробия=%d",
                deleted["roulette_rounds"], deleted["chat_members"], deleted["deletion_tombstones"])
    return deleted

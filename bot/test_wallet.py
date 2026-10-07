import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import balance_guard
import glob
import os
import re
import shutil
import sqlite3
import tempfile
import threading

import db
import wallet
from core.db_conn import _connect
from roulette import MAX_SAFE_INT, BalanceLimit, InsufficientFunds

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = 1_760_000_000


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


def raises(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return True
    except Exception as other:  # noqa: BLE001
        raise AssertionError("ожидали %s, получили %r" % (exc.__name__, other))
    raise AssertionError("ожидали %s, исключения не было" % exc.__name__)


def bet(kind, value=None, amount=10):
    return {"type": kind, "value": value, "amount": amount}


tmp = tempfile.mkdtemp()
counter = [0]


def new_db(balance=1000, player=1, last_accrual=NOW):
    counter[0] += 1
    path = os.path.join(tmp, "w%d.db" % counter[0])
    db.init_db(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO players (telegram_id, balance, rate, last_accrual, created_at, total_staked) "
                 "VALUES (?, ?, 100, ?, ?, 0)", (player, balance, last_accrual, last_accrual))
    conn.commit()
    conn.close()
    return path


def conn_to(path):
    conn = sqlite3.connect(path)  # без row_factory: wallet не должен на него полагаться
    conn.isolation_level = None
    return conn


def one(path, query, params=()):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


try:
    # ================= сопоставление исключений =================
    assert issubclass(wallet.InsufficientFunds, InsufficientFunds)
    assert issubclass(wallet.BalanceLimitExceeded, BalanceLimit)
    assert issubclass(wallet.PlayerNotFound, LookupError)

    # ================= debit и credit =================
    path = new_db(1000)
    c = conn_to(path)
    check("get_balance", wallet.get_balance(c, 1), 1000)
    check("debit возвращает баланс", wallet.debit(c, 1, 300), 700)
    check("credit возвращает баланс", wallet.credit(c, 1, 50), 750)
    check("в базе", one(path, "SELECT balance FROM players")[0][0], 750)
    check("debit ровно весь баланс", wallet.debit(c, 1, 750), 0)
    raises(wallet.InsufficientFunds, wallet.debit, c, 1, 1)
    check("баланс не ушёл ниже 0", wallet.get_balance(c, 1), 0)
    raises(wallet.InsufficientFunds, wallet.debit, c, 1, MAX_SAFE_INT + 1)
    check("credit с нуля", wallet.credit(c, 1, 10), 10)
    raises(wallet.InsufficientFunds, wallet.debit, c, 1, 11)
    check("после отказа баланс прежний", wallet.get_balance(c, 1), 10)

    # неверная сумма
    for bad in (0, -1, 1.5, "5", None, True, False, 2.0):
        raises(ValueError, wallet.debit, c, 1, bad)
        raises(ValueError, wallet.credit, c, 1, bad)
    check("после ошибок суммы баланс прежний", wallet.get_balance(c, 1), 10)

    # предел MAX_SAFE_INT
    sql_c = sqlite3.connect(path)
    sql_c.execute("UPDATE players SET balance = ?", (MAX_SAFE_INT - 5,))
    sql_c.commit()
    sql_c.close()
    check("credit до предела", wallet.credit(c, 1, 5), MAX_SAFE_INT)
    raises(wallet.BalanceLimitExceeded, wallet.credit, c, 1, 1)
    check("баланс на пределе", wallet.get_balance(c, 1), MAX_SAFE_INT)
    raises(wallet.BalanceLimitExceeded, wallet.credit, c, 1, MAX_SAFE_INT + 1)
    raises(wallet.BalanceLimitExceeded, wallet.credit, c, 1, 2 ** 70)
    raises(wallet.InsufficientFunds, wallet.debit, c, 1, 2 ** 70)
    check("debit с предела", wallet.debit(c, 1, MAX_SAFE_INT), 0)

    # нет игрока
    for fn in (lambda: wallet.get_balance(c, 999), lambda: wallet.debit(c, 999, 1), lambda: wallet.credit(c, 999, 1),
               lambda: wallet.debit(c, 999, MAX_SAFE_INT + 1), lambda: wallet.credit(c, 999, MAX_SAFE_INT + 1)):
        raises(wallet.PlayerNotFound, fn)
    c.close()

    # ================= транзакцию кошелёк не трогает; откат внешней транзакции =================
    path = new_db(500)
    c = conn_to(path)
    check("до: транзакции нет", c.in_transaction, False)
    wallet.debit(c, 1, 100)
    check("автокоммит без внешней транзакции, wallet её не открыл", c.in_transaction, False)
    c.execute("BEGIN IMMEDIATE")
    wallet.debit(c, 1, 100)
    wallet.credit(c, 1, 30)
    check("внутри транзакции", c.in_transaction, True)
    check("видно внутри", wallet.get_balance(c, 1), 330)
    c.execute("ROLLBACK")
    check("откат вернул баланс", wallet.get_balance(c, 1), 400)
    c.execute("BEGIN IMMEDIATE")
    wallet.credit(c, 1, 5)
    raises(wallet.InsufficientFunds, wallet.debit, c, 1, 10_000)
    check("отказ не закрыл транзакцию", c.in_transaction, True)
    c.execute("COMMIT")
    check("после COMMIT", one(path, "SELECT balance FROM players")[0][0], 405)
    c.close()

    # ================= spin через wallet =================
    path = new_db(1000)
    r = db.spin_roulette(1, "wallet-req-01", [bet("number", 17, 10), bet("black", None, 20)], now=NOW, db_path=path,
                         rng=lambda n: 17)
    check("выплата зачислена: 1000 - 30 + 360 + 40", r["balance"], 1370)
    check("в базе", one(path, "SELECT balance, total_staked FROM players")[0], (1370, 30))
    # ошибки откатывают всё, включая начисление по часам
    path = new_db(100, last_accrual=NOW - 3 * 3600)
    try:
        db.spin_roulette(1, "wallet-req-02", [bet("red", None, 100_000)], now=NOW, db_path=path, rng=lambda n: 1)
        raise AssertionError("не хватило фишек")
    except InsufficientFunds:
        pass
    check("после InsufficientFunds ничего не записано",
          one(path, "SELECT balance, last_accrual, total_staked FROM players")[0], (100, NOW - 3 * 3600, 0))
    sql_c = sqlite3.connect(path)
    sql_c.execute("UPDATE players SET balance = ?", (MAX_SAFE_INT - 10,))
    sql_c.commit()
    sql_c.close()
    try:
        db.spin_roulette(1, "wallet-req-03", [bet("number", 1, 100)], now=NOW, db_path=path, rng=lambda n: 1)
        raise AssertionError("лимит баланса не сработал")
    except BalanceLimit:
        pass
    check("после BalanceLimit ничего не записано",
          one(path, "SELECT balance, last_accrual, total_staked FROM players")[0], (MAX_SAFE_INT - 10, NOW - 3 * 3600, 0))
    check("раундов нет", one(path, "SELECT COUNT(*) FROM roulette_rounds")[0][0], 0)
    # начисление по часам по-прежнему работает при спине
    path = new_db(0, last_accrual=NOW - 2 * 3600)
    r = db.spin_roulette(1, "wallet-req-04", [bet("red", None, 100)], now=NOW, db_path=path, rng=lambda n: 1)
    check("потрачено начисленное: 200 - 100 + 200", r["balance"], 300)

    # ================= параллельные вращения не теряют фишки =================
    path = new_db(10_000_000)
    errors = []

    def worker(tag):
        try:
            for i in range(30):
                db.spin_roulette(1, "par-%s-%04d" % (tag, i), [bet("red", None, 7), bet("number", 5, 3)], now=NOW,
                                 db_path=path, rng=lambda n: 5)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B", "C")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check("потоки без ошибок", errors, [])
    stakes, payouts = one(path, "SELECT SUM(stake_total), SUM(payout_total) FROM roulette_rounds")[0]
    check("90 раундов", one(path, "SELECT COUNT(*) FROM roulette_rounds")[0][0], 90)
    check("баланс = старт - ставки + выплаты", one(path, "SELECT balance FROM players")[0][0],
          10_000_000 - stakes + payouts)
    check("total_staked = сумма ставок", one(path, "SELECT total_staked FROM players")[0][0], stakes)

    # ================= баланс меняется только через wallet =================
    # Слой 1 (balance_guard.violations): разбор исходников через ast, не поиск по строкам; разрешены ровно четыре записи с точным текстом:
    # списание и начисление в wallet.py, регистрация игрока с стартовым балансом в core/kernel.py, разовая миграция начисления в core/migrations.py.
    check("запись баланса только в разрешённых местах", balance_guard.violations(), [])
    # сам сканер ловит обходы прежней проверки (по строкам): многострочный SQL, другой порядок столбцов, склейка, f-строка, REPLACE, ON CONFLICT,
    # триггер, executescript, executemany, прямое подключение, импорт connect
    bypasses = {
        "многострочный UPDATE": 'def f(c):\n    c.execute("UPDATE players\\n SET\\n  balance = 5 WHERE 1")\n',
        "balance не первым в SET": 'def f(c):\n    c.execute("UPDATE players SET xp = xp + 1, balance = ? WHERE telegram_id = ?")\n',
        "склейка строк": 'def f(c):\n    c.execute("UPDATE players " + "SET bal" "ance = 1")\n',
        "split через круглые скобки": 'def f(c):\n    c.execute(("UPDATE players "\n               "SET balance = 0"))\n',
        "f-строка": 'def f(c, col):\n    c.execute(f"UPDATE players SET {col} = 1")\n',
        "format": 'def f(c, col):\n    c.execute("UPDATE players SET {} = 1".format(col))\n',
        "REPLACE INTO": 'def f(c):\n    c.execute("REPLACE INTO players (telegram_id, balance) VALUES (1, 1)")\n',
        "INSERT OR REPLACE": 'def f(c):\n    c.execute("INSERT OR REPLACE INTO players (telegram_id, balance) VALUES (1, 1)")\n',
        "ON CONFLICT": 'def f(c):\n    c.execute("INSERT INTO t VALUES (1) ON CONFLICT(id) DO UPDATE SET balance = 1")\n',
        "триггер": 'def f(c):\n    c.execute("CREATE TRIGGER x AFTER INSERT ON t BEGIN UPDATE players SET balance = 0; END")\n',
        "executescript": 'def f(c):\n    c.executescript("SELECT 1")\n',
        "executemany": 'def f(c):\n    c.executemany("SELECT ?", [(1,)])\n',
        "прямое подключение": 'import sqlite3\n\ndef f(p):\n    sqlite3.connect(p)\n',
        "импорт connect": 'from sqlite3 import connect\n',
    }
    for name, code in bypasses.items():
        check("сканер ловит обход: " + name, bool(balance_guard.scan_text("rogue.py", code)), True)
    harmless = {
        "условие по balance в WHERE": 'def f(c):\n    c.execute("UPDATE players SET xp = xp + 1 WHERE balance > 0")\n',
        "чтение баланса": 'def f(c):\n    return c.execute("SELECT balance FROM players")\n',
        "документация": 'def f():\n    """UPDATE players SET balance = 1"""\n',
        "другая таблица": 'def f(c):\n    c.execute("UPDATE farm_purchases SET cost = 1")\n',
        "создание таблицы": 'def f(c):\n    c.execute("CREATE TABLE players (balance INTEGER)")\n',
    }
    for name, code in harmless.items():
        check("сканер не ругается: " + name, balance_guard.scan_text("ok.py", code), [])
    # подмена: лишняя запись баланса в копии модуля и потеря разрешённой записи тоже видны
    real = open(os.path.join(HERE, "wallet.py"), encoding="utf-8").read()
    rogue = real + "\n\ndef _rogue(conn):\n    conn.execute(\"UPDATE players SET rate = 1,\\n balance = 1\")\n"
    check("лишняя запись в wallet.py заметна", len([f for f in balance_guard.scan_text("wallet.py", rogue) if f[3] not in (balance_guard.DEBIT, balance_guard.CREDIT, balance_guard.GEM_LEDGER_INSERT, balance_guard.GEM_BALANCE_UPSERT, balance_guard.GEM_BALANCE_DEBIT)]), 1)
    # Слой 2 (время выполнения): testenv подключил защиту к соединениям проекта во всех тестах; запись баланса вне разрешённых запросов роняет тест
    conn = _connect(path)
    try:
        for bad_sql in ("UPDATE players SET balance = 1", "UPDATE players SET xp = 1, balance = 2 WHERE telegram_id = 1", "REPLACE INTO players (telegram_id, balance) VALUES (1, 1)"):
            try:
                conn.execute(bad_sql)
            except balance_guard.BalanceGuardError:
                pass
            else:
                raise AssertionError("защита времени выполнения не сработала: " + bad_sql)
        try:
            conn.executescript("SELECT 1")
        except balance_guard.BalanceGuardError:
            raise AssertionError("безобидный скрипт не должен падать")
        except Exception:
            pass
        check("чтение и обычные запросы проходят", conn.execute("SELECT COUNT(*) FROM players").fetchone()[0] >= 0, True)
    finally:
        conn.close()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")

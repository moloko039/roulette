"""Защита «баланс меняется только через wallet»: два слоя, используются тестами (test_wallet.py, testenv.py). В боевом коде не импортируется.

1. Статический: разбор исходников bot/ через ast (а не поиск по строкам). Все строки SQL собираются целиком, в том числе многострочные, склеенные
   через «+» и неявной склейкой, f-строки и .format/% (они считаются динамическими). Ищутся: UPDATE с balance в части SET (в любом порядке столбцов),
   INSERT/REPLACE INTO players, ON CONFLICT ... DO UPDATE SET balance, CREATE TRIGGER и VIEW, executescript, динамически собранный SQL про players/balance,
   прямые sqlite3.connect вне разрешённых модулей. Разрешено ровно четыре места (ALLOWED), с точным текстом и числом.
2. Время выполнения: guard() оборачивает execute и executemany соединения проекта (core.db_conn.TimedConnection): любая запись баланса, которой нет в
   RUNTIME_ALLOWED, роняет тест. Подключается в testenv.py, поэтому проверяет все тесты сразу."""
import ast
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))

# (файл, нормализованный SQL): сколько раз. Нормализация: пробелы схлопнуты, регистр сохранён.
DEBIT = "UPDATE players SET balance = balance - ? WHERE telegram_id = ? AND balance >= ?"
CREDIT = "UPDATE players SET balance = balance + ? WHERE telegram_id = ? AND balance <= ?"
REGISTER = "INSERT OR IGNORE INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (?, ?, ?, ?, ?)"
MIGRATION = "UPDATE players SET balance = balance + ?, last_accrual = ?, accrual_acc = ? WHERE telegram_id = ?"
GEM_LEDGER_INSERT = "INSERT INTO gems_ledger (telegram_id, delta, reason, ref, created_at) VALUES (?, ?, ?, ?, ?)"
GEM_BALANCE_UPSERT = ("INSERT INTO gem_balances (telegram_id, gems) VALUES (?, ?) ON CONFLICT(telegram_id) DO UPDATE SET gems = gems + excluded.gems")
GEM_BALANCE_DEBIT = "UPDATE gem_balances SET gems = gems - ? WHERE telegram_id = ? AND gems >= ?"
ALLOWED = {
    ("wallet.py", GEM_LEDGER_INSERT): 2,           # кристаллы: журнал (начисление и списание)
    ("wallet.py", GEM_BALANCE_UPSERT): 1,          # кристаллы: начисление
    ("wallet.py", GEM_BALANCE_DEBIT): 1,           # кристаллы: списание
    ("wallet.py", DEBIT): 1,                       # списание
    ("wallet.py", CREDIT): 1,                      # начисление (в том числе поминутный доход)
    (os.path.join("core", "kernel.py"), REGISTER): 1,       # регистрация игрока: стартовый баланс
    (os.path.join("core", "migrations.py"), MIGRATION): 1,  # разовая миграция поминутного начисления
}
RUNTIME_ALLOWED = {DEBIT, CREDIT, REGISTER, MIGRATION, GEM_LEDGER_INSERT, GEM_BALANCE_UPSERT, GEM_BALANCE_DEBIT}
# модули, которым разрешено открывать базу напрямую (копии и проверка копий только читают, db_conn создаёт соединения проекта)
CONNECT_ALLOWED = {"backup.py", "verify_backup.py", os.path.join("core", "db_conn.py")}

UPDATE_SET_RE = re.compile(r"\bUPDATE\b.*?\bSET\b(.*?)(?:\bWHERE\b|\bRETURNING\b|;|$)", re.I | re.S)
BALANCE_RE = re.compile(r"\bbalance\b", re.I)
INSERT_RE = re.compile(r"\b(?:INSERT(?:\s+OR\s+\w+)?|REPLACE)\s+INTO\s+(?:\w+\.)?players\b", re.I)
CONFLICT_RE = re.compile(r"\bON\s+CONFLICT\b.*?\bDO\s+UPDATE\s+SET\b.*?\bbalance\b", re.I | re.S)
OBJECT_RE = re.compile(r"\bCREATE\s+(?:TEMP\w*\s+)?(?:TRIGGER|VIEW)\b", re.I)
DYNAMIC_RE = re.compile(r"\b(?:UPDATE|INSERT|REPLACE|ALTER|DELETE)\b", re.I)


GEMS_WRITE_RE = re.compile(r"\b(?:INSERT(?:\s+OR\s+\w+)?|REPLACE)\s+INTO\s+(?:gems_ledger|gem_balances)\b|\bUPDATE\s+(?:OR\s+\w+\s+)?(?:gems_ledger|gem_balances)\b", re.I)


def normalize(sql):
    return " ".join(sql.split())


def balance_write_kinds(sql):
    """Виды записи баланса в тексте SQL (список строк; пусто: баланс не меняется)."""
    text = normalize(sql)
    kinds = []
    for m in UPDATE_SET_RE.finditer(text):
        if BALANCE_RE.search(m.group(1)):
            kinds.append("UPDATE ... SET ... balance")
    if INSERT_RE.search(text):
        kinds.append("INSERT/REPLACE INTO players")
    if CONFLICT_RE.search(text):
        kinds.append("ON CONFLICT DO UPDATE SET balance")
    if OBJECT_RE.search(text):
        kinds.append("CREATE TRIGGER/VIEW")
    if GEMS_WRITE_RE.search(text):
        kinds.append("запись кристаллов (gems_ledger, gem_balances)")
    return kinds


# ---------- слой 1: статический разбор ----------
def _fold(node):
    """(текст, динамический) для выражения-строки или None, если это не строка."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value, False
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            parts.append(v.value if isinstance(v, ast.Constant) else "\0")
        return "".join(parts), any(not isinstance(v, ast.Constant) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        left = _fold(node.left)
        if left is None:
            return None
        right = _fold(node.right) if isinstance(node.op, ast.Add) else None
        if isinstance(node.op, ast.Add):
            return (left[0] + (right[0] if right else "\0"), left[1] or right is None or right[1])
        return left[0], True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("format", "join"):
        base = _fold(node.func.value)
        if base is not None:
            return base[0], True
    return None


def _strings(tree):
    """Все строковые выражения дерева (свёрнутые): (текст, динамический, строка файла). Строки-документация пропускаются."""
    out = []

    def visit(node):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            return          # docstring или отдельная строка-комментарий
        folded = _fold(node)
        if folded is not None:
            out.append((folded[0], folded[1], node.lineno))
            return
        for child in ast.iter_child_nodes(node):
            visit(child)
    visit(tree)
    return out


def source_files(root=HERE):
    files = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".venv", "__pycache__", "testdata")]
        for n in names:
            if n.endswith(".py") and not n.startswith("test_") and n not in ("stubs.py", "tg_testutil.py", "balance_guard.py", "testenv.py"):
                files.append(os.path.join(folder, n))
    return sorted(files)


def scan_text(relname, source):
    """Находки одного файла: (файл, строка, вид, нормализованный SQL или ''). Для динамического SQL текст пуст (его содержимое неизвестно)."""
    tree = ast.parse(source)
    found = []
    for text, dynamic, line in _strings(tree):
        kinds = balance_write_kinds(text)
        for kind in kinds:
            found.append((relname, line, kind, normalize(text)))
        if dynamic and not kinds and DYNAMIC_RE.search(text) and re.search(r"\bplayers\b|\bbalance\b", text, re.I):
            found.append((relname, line, "динамический SQL про players/balance", ""))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("executescript", "executemany"):
                found.append((relname, node.lineno, node.func.attr, ""))
            if node.func.attr == "connect" and isinstance(node.func.value, ast.Name) and node.func.value.id == "sqlite3" and relname not in CONNECT_ALLOWED:
                found.append((relname, node.lineno, "прямой sqlite3.connect", ""))
        if isinstance(node, ast.ImportFrom) and node.module == "sqlite3" and any(a.name == "connect" for a in node.names) and relname not in CONNECT_ALLOWED:
            found.append((relname, node.lineno, "from sqlite3 import connect", ""))
    return found


def violations(root=HERE):
    """Нарушения: находки, которых нет в ALLOWED (с точным числом), и разрешённые, которых не нашлось (список устарел)."""
    seen = {}
    bad = []
    for path in source_files(root):
        rel = os.path.relpath(path, root)
        with open(path, encoding="utf-8") as handle:
            for f in scan_text(rel, handle.read()):
                key = (f[0], f[3])
                if f[3] and key in ALLOWED:
                    seen[key] = seen.get(key, 0) + 1
                else:
                    bad.append("%s:%d: %s" % (f[0], f[1], f[2]))
    for key, count in ALLOWED.items():
        if seen.get(key, 0) != count:
            bad.append("разрешённая запись изменилась: %s ожидалось %d, найдено %d" % (key[0], count, seen.get(key, 0)))
    return bad


# ---------- слой 2: время выполнения ----------
class BalanceGuardError(AssertionError):
    pass


def _check_runtime(sql):
    if isinstance(sql, str) and balance_write_kinds(sql) and normalize(sql) not in RUNTIME_ALLOWED:
        raise BalanceGuardError("запись баланса вне wallet: " + normalize(sql)[:80])


_installed = [False]


def install():
    """Оборачивает execute и executemany соединения проекта. Повторный вызов ничего не делает."""
    if _installed[0]:
        return
    from core import db_conn
    cls = db_conn.TimedConnection
    orig_execute, orig_many, orig_script = cls.execute, cls.executemany, cls.executescript

    def execute(self, sql, *args):
        _check_runtime(sql)
        return orig_execute(self, sql, *args)

    def executemany(self, sql, *args):
        _check_runtime(sql)
        return orig_many(self, sql, *args)

    def executescript(self, script):
        _check_runtime(script)
        return orig_script(self, script)

    cls.execute, cls.executemany, cls.executescript = execute, executemany, executescript
    _installed[0] = True

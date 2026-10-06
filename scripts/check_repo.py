"""Защита от случайной публикации секретов и файлов базы.

Проверяет файлы в индексе git (`git ls-files`): запрещённые имена и папки, а также строки, похожие на токен Telegram-бота и другие ключи
(закрытые ключи PEM, токены GitHub, ключи AWS, закрытый ключ копий BACKUP_PRIVATE_KEY). Только стандартная библиотека. Код выхода 0 (всё чисто)
или 1. Найденные значения не печатаются: только путь и номер строки.

Запуск:
  python scripts/check_repo.py [корень]            весь индекс git (так работает CI)
  python scripts/check_repo.py --staged [корень]   только добавленные и изменённые файлы в индексе, содержимое из индекса (хук .githooks/pre-commit)
  python scripts/check_repo.py --history [корень]  вся история git: добавленные строки всех коммитов всех веток и имена файлов (разовый скан)

Правило токена Telegram: число (6 и больше цифр), двоеточие (или %3A в URL) и 30 и больше символов [A-Za-z0-9_-]; перед числом не цифра, а буквы
допустимы (поэтому находится `api.telegram.org/bot<id>:<токен>`). Токен ищется в любом контексте: ссылка, заголовок, переменная, пример, комментарий.
Явные заглушки не считаются секретом: (1) точные строки из ALLOWED_FAKE_TOKENS (в том числе пример из документации Telegram);
(2) токен, тело которого (после двоеточия) содержит слово-заглушку из PLACEHOLDER_WORDS (например «example», «placeholder», «not-real», «fake»);
короткие тестовые значения вроде `123456:TEST-TOKEN-not-real` под шаблон не попадают. Шаблоны без цифр (`<TOKEN>`, `bot<ТОКЕН>`) токеном не являются.
Случайный токен (35 знаков) не содержит этих слов (шанс порядка 10^-6), настоящий токен заглушкой не станет.
"""
import fnmatch
import os
import re
import subprocess
import sys

# Явные заглушки: точные строки (пример из документации Telegram и т. п.).
ALLOWED_FAKE_TOKENS = ("123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",)
# Слова в теле токена (регистр не важен), по которым значение считается заглушкой.
PLACEHOLDER_WORDS = ("example", "placeholder", "not-real", "notreal", "fake", "dummy", "your_token", "yourtoken", "abc-def")

# Явные примеры настроек без значений разрешены.
ENV_EXAMPLES = (".env.example", ".env.sample", ".env.template", ".env.dist")
FORBIDDEN_NAME_PATTERNS = (
    ".env", ".env.*", "*.db", "*.db-wal", "*.db-shm", "*.db-journal", "*.sqlite*", "*.key", "*.enc", "*.pem",
    "id_rsa*", "id_ed25519*", "*private_key*",
)
FORBIDDEN_DIRS = (".venv", "backups")

# число, двоеточие (или %3A), затем 30 и больше символов; перед числом не цифра (буквы допустимы: bot123456:...)
TOKEN_RE = re.compile(r"(?<![0-9])(\d{6,})(?::|%3[Aa])([A-Za-z0-9_-]{30,})")
# другие ключи: (причина, шаблон)
OTHER_SECRET_RES = (
    ("закрытый ключ (PEM)", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----")),
    ("токен GitHub", re.compile(r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{36,}")),
    ("ключ доступа AWS", re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])")),
    ("закрытый ключ копий (значение BACKUP_PRIVATE_KEY)", re.compile(r"BACKUP_PRIVATE_KEY\s*[=:]\s*[\"']?[A-Za-z0-9+/]{43}=")),
)
MAX_SCAN_BYTES = 2 * 1024 * 1024


def forbidden_path_reason(path):
    """Причина, по которой путь нельзя хранить в репозитории, или None."""
    parts = path.replace("\\", "/").split("/")
    for folder in parts[:-1]:
        if folder in FORBIDDEN_DIRS:
            return "запрещённая папка %s" % folder
    name = parts[-1]
    # e2e: скриншоты, логи и вывод харнесса в репозиторий не попадают (e2e/out/ и картинки, логи, дампы рядом с кодом)
    if parts[0] == "e2e" and len(parts) > 1 and (parts[1] == "out" or name.lower().endswith((".png", ".jpg", ".log", ".json.tmp"))):
        return "вывод e2e (скриншоты, логи) в репозиторий не коммитится"
    if name.lower() in ENV_EXAMPLES:
        return None
    lowered = name.lower()
    for pattern in FORBIDDEN_NAME_PATTERNS:
        if fnmatch.fnmatchcase(lowered, pattern):
            return "запрещённое имя файла (шаблон %s)" % pattern
    return None


def is_placeholder(token_id, body, whole, allowed):
    """Явная заглушка: точная строка из списка разрешённых или слово-заглушка в теле токена."""
    if whole in allowed:
        return True
    low = body.lower()
    return any(word in low for word in PLACEHOLDER_WORDS)


def secret_reasons(line, allowed=None):
    """Причины, по которым строка похожа на секрет (список строк, без значений)."""
    allowed = ALLOWED_FAKE_TOKENS if allowed is None else allowed
    reasons = []
    for m in TOKEN_RE.finditer(line):
        whole = m.group(1) + ":" + m.group(2)
        if not is_placeholder(m.group(1), m.group(2), whole, allowed):
            reasons.append("строка похожа на токен Telegram-бота")
            break
    for reason, regex in OTHER_SECRET_RES:
        if regex.search(line):
            reasons.append(reason)
    return reasons


def token_lines(text, allowed=None):
    """Номера строк, где есть секрет (токен Telegram-бота и другие ключи; кроме разрешённых заглушек)."""
    return [number for number, line in enumerate(text.splitlines(), 1) if secret_reasons(line, allowed)]


def worktree_reader(root):
    def read(path):
        full = os.path.join(root, path)
        if not os.path.isfile(full) or os.path.islink(full):
            return None
        try:
            with open(full, "rb") as handle:
                return handle.read(MAX_SCAN_BYTES)
        except OSError:
            return None
    return read


def index_reader(root):
    """Содержимое файла из индекса git (то, что попадёт в коммит), а не из рабочей папки."""
    def read(path):
        r = subprocess.run(["git", "show", ":" + path], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return r.stdout[:MAX_SCAN_BYTES] if r.returncode == 0 else None
    return read


def find_problems(root, files, allowed=None, reader=None):
    """Список (путь, номер строки или None, причина). Значения найденных секретов не возвращаются."""
    problems = []
    reader = reader or worktree_reader(root)
    for path in files:
        reason = forbidden_path_reason(path)
        if reason:
            problems.append((path, None, reason))
            continue  # содержимое запрещённого файла не читаем
        data = reader(path)
        if data is None or b"\x00" in data:
            continue  # нет файла, ссылка или двоичный файл
        for number, line in enumerate(data.decode("utf-8", errors="ignore").splitlines(), 1):
            for why in secret_reasons(line, allowed):
                problems.append((path, number, why))
    return problems


def git_lines(root, *args):
    out = subprocess.run(("git",) + args, cwd=root, check=True, stdout=subprocess.PIPE).stdout
    return out.decode("utf-8", errors="surrogateescape")


def history_problems(root, allowed=None):
    """Скан всей истории: имена всех когда-либо добавленных файлов и добавленные строки всех коммитов всех веток.
    Список (коммит[:10], путь, номер строки в файле или None, причина). Значения секретов не возвращаются."""
    problems = []
    names = git_lines(root, "log", "--all", "--no-renames", "--diff-filter=A", "--name-only", "--pretty=format:@@%H", "-z")
    commit = ""
    for chunk in names.split("\0"):
        for part in chunk.split("\n"):
            if part.startswith("@@"):
                commit = part[2:12]
            elif part:
                reason = forbidden_path_reason(part)
                if reason:
                    problems.append((commit, part, None, reason))
    patch = git_lines(root, "log", "--all", "--no-renames", "-p", "-U0", "--no-color", "--pretty=format:@@@%H")
    commit, path, number = "", "", 0
    for line in patch.splitlines():
        if line.startswith("@@@"):
            commit = line[3:13]
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("@@ "):
            m = re.search(r"\+(\d+)", line)
            number = int(m.group(1)) - 1 if m else 0
        elif line.startswith("+") and not line.startswith("+++"):
            number += 1
            for why in secret_reasons(line[1:], allowed):
                problems.append((commit, path, number, why))
    return problems


def tracked_files(root):
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, stdout=subprocess.PIPE).stdout
    return [p for p in out.decode("utf-8", errors="surrogateescape").split("\0") if p]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    staged = "--staged" in argv
    history = "--history" in argv
    argv = [a for a in argv if not a.startswith("--")]
    root = argv[0] if argv else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        if history:
            found = history_problems(root)
            for commit, path, number, reason in found:
                print("%s %s%s: %s" % (commit, path, ":%d" % number if number else "", reason))
            print("Скан истории: найдено %d" % len(found) if found else "Скан истории: проблем нет")
            return 1 if found else 0
        if staged:
            out = git_lines(root, "diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
            files = [p for p in out.split("\0") if p]
            problems = find_problems(root, files, reader=index_reader(root))
        else:
            files = tracked_files(root)
            problems = find_problems(root, files)
    except (OSError, subprocess.CalledProcessError):
        print("Не удалось получить список файлов git (нужен git и репозиторий)")
        return 1
    for path, number, reason in problems:
        print("%s%s: %s" % (path, ":%d" % number if number else "", reason))
    if problems:
        print("Найдено проблем: %d" % len(problems))
        return 1
    print("Проверка репозитория: проблем нет (файлов проверено: %d)" % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Защита от случайной публикации секретов и файлов базы.

Проверяет файлы в индексе git (`git ls-files`): запрещённые имена и папки, а также строки,
похожие на токен Telegram-бота. Только стандартная библиотека. Код выхода 0 (всё чисто) или 1.
Найденные значения не печатаются: только путь и номер строки.

Запуск: python scripts/check_repo.py [корень репозитория]
"""
import fnmatch
import os
import re
import subprocess
import sys

# Заведомо поддельные токены в тестах: точные строки. Пусто, пока таких нет (тесты репозитория
# используют токены короче 30 символов после двоеточия и под шаблон не попадают).
ALLOWED_FAKE_TOKENS = ()

# Явные примеры настроек без значений разрешены.
ENV_EXAMPLES = (".env.example", ".env.sample", ".env.template", ".env.dist")
FORBIDDEN_NAME_PATTERNS = (
    ".env", ".env.*", "*.db", "*.sqlite", "*.sqlite3", "*.key", "*.enc", "*.pem",
    "id_rsa*", "id_ed25519*", "*private_key*",
)
FORBIDDEN_DIRS = (".venv", "backups")

# число, двоеточие, затем 30 и больше символов
TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])\d{6,}:[A-Za-z0-9_-]{30,}")
MAX_SCAN_BYTES = 2 * 1024 * 1024


def forbidden_path_reason(path):
    """Причина, по которой путь нельзя хранить в репозитории, или None."""
    parts = path.replace("\\", "/").split("/")
    for folder in parts[:-1]:
        if folder in FORBIDDEN_DIRS:
            return "запрещённая папка %s" % folder
    name = parts[-1]
    if name.lower() in ENV_EXAMPLES:
        return None
    lowered = name.lower()
    for pattern in FORBIDDEN_NAME_PATTERNS:
        if fnmatch.fnmatchcase(lowered, pattern):
            return "запрещённое имя файла (шаблон %s)" % pattern
    return None


def token_lines(text, allowed=None):
    """Номера строк, где есть похожее на токен Telegram-бота значение (кроме разрешённых)."""
    allowed = ALLOWED_FAKE_TOKENS if allowed is None else allowed
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        if any(m.group(0) not in allowed for m in TOKEN_RE.finditer(line)):
            found.append(number)
    return found


def find_problems(root, files, allowed=None):
    """Список (путь, номер строки или None, причина). Значения найденных секретов не возвращаются."""
    problems = []
    for path in files:
        reason = forbidden_path_reason(path)
        if reason:
            problems.append((path, None, reason))
            continue  # содержимое запрещённого файла не читаем
        full = os.path.join(root, path)
        if not os.path.isfile(full) or os.path.islink(full):
            continue
        try:
            with open(full, "rb") as handle:
                data = handle.read(MAX_SCAN_BYTES)
        except OSError:
            continue
        if b"\x00" in data:
            continue  # двоичный файл
        for number in token_lines(data.decode("utf-8", errors="ignore"), allowed):
            problems.append((path, number, "строка похожа на токен Telegram-бота"))
    return problems


def tracked_files(root):
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, stdout=subprocess.PIPE).stdout
    return [p for p in out.decode("utf-8", errors="surrogateescape").split("\0") if p]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    root = argv[0] if argv else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        files = tracked_files(root)
    except (OSError, subprocess.CalledProcessError):
        print("Не удалось получить список файлов git (нужен git и репозиторий)")
        return 1
    problems = find_problems(root, files)
    for path, number, reason in problems:
        print("%s%s: %s" % (path, ":%d" % number if number else "", reason))
    if problems:
        print("Найдено проблем: %d" % len(problems))
        return 1
    print("Проверка репозитория: проблем нет (файлов проверено: %d)" % len(files))
    return 0


if __name__ == "__main__":
    sys.exit(main())

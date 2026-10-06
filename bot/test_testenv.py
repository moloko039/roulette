import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
"""Проверка изоляции тестов: testenv.py удаляет переменные проекта и отключает чтение .env, даже если файл .env лежит рядом."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


tmp = tempfile.mkdtemp(prefix="testenv check ")     # пробел в пути
try:
    with open(os.path.join(tmp, ".env"), "w") as f:
        f.write("BOT_TOKEN=from-dotenv-file\nOWNER_CHAT_ID=999\nDB_PATH=/nonexistent/from-dotenv.db\n")
    code = ("import os, sys\n"
            "sys.path.insert(0, %r)\n"
            "import testenv\n"
            "from dotenv import load_dotenv\n"
            "load_dotenv()\n"
            "print(repr([os.environ.get(n) for n in testenv.PROJECT_ENV]))\n") % HERE
    hostile = dict(os.environ, BOT_TOKEN="from-shell", OWNER_CHAT_ID="1", DB_PATH="/x/shell.db", PORT="9", BACKUP_KEEP="1", PLAY_MODE="link")
    r = subprocess.run([sys.executable, "-c", code], cwd=tmp, env=hostile, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    check("после testenv ни одна переменная проекта не задана (ни из оболочки, ни из .env рядом)", (r.returncode, r.stdout.strip()), (0, repr([None] * len(testenv.PROJECT_ENV))))
    # список охватывает все переменные, которые читает код проекта (кроме служебных)
    import re
    names = set()
    for n in os.listdir(HERE):
        if n.endswith(".py") and not n.startswith("test_") and n not in ("testenv.py", "run_tests.py"):
            names |= set(re.findall(r"(?:environ\.get|getenv|_env|env\.get)\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']", open(os.path.join(HERE, n), encoding="utf-8").read()))
    for sub in ("core", "features", "games"):
        for n in os.listdir(os.path.join(HERE, sub)):
            if n.endswith(".py"):
                names |= set(re.findall(r"(?:environ\.get|getenv|_env|env\.get)\(\s*[\"']([A-Z][A-Z0-9_]+)[\"']", open(os.path.join(HERE, sub, n), encoding="utf-8").read()))
    missing = sorted(n for n in names if n not in testenv.PROJECT_ENV)
    check("все переменные, которые читает код, есть в testenv.PROJECT_ENV", missing, [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print("Все проверки прошли")

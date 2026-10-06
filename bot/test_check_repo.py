import testenv  # noqa: F401  (первым: очищает окружение проекта и отключает .env)
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import check_repo  # noqa: E402


def check(name, got, expected):
    assert got == expected, f"{name}: получили {got}, ожидали {expected}"


# поддельные токены собираются на лету, чтобы их не было в исходнике буквально
FAKE_TOKEN = "1234567" + ":" + "A" * 35
OTHER_TOKEN = "7654321" + ":" + "b-_" * 12
SHORT = "123456:TEST-TOKEN-not-real"

# ---------- имена и папки ----------
bad = [".env", "bot/.env", ".env.local", ".env.production", "players.db", "bot/data/x.sqlite", "a.sqlite3",
       "k.key", "x.enc", "cert.pem", "id_rsa", "id_rsa.pub", "id_ed25519", "ssh/id_ed25519.pub",
       "my_private_key.json", "PRIVATE_KEY.txt", "bot/.venv/lib/x.py", ".venv/pyvenv.cfg",
       "backups/latest.db", "bot/backups/readme.txt", "PLAYERS.DB",
       "e2e/out/shot.png", "e2e/shot.png", "e2e/server.log", "players.db-wal", "bot/players.db-shm", "players.db-journal", "x.sqlite-wal", "x.sqlite3-shm", "a.SQLITE", ".env.staging", "bot/k.pem", "dump.enc"]
for path in bad:
    assert check_repo.forbidden_path_reason(path), "не запрещён: " + path
good = ["e2e/harness.py", "e2e/scenarios/hilo.py", "e2e/requirements.txt", "bot/stubs.py", "docs/db.md", "wal.md", ".env.example", "bot/.env.example", ".env.sample", "bot/api.py", "index.html", "docs/JOURNAL.md",
        "bot/test_db.py", "environment.md", "bot/backup.py", "bot/test_backup.py", "keyboard.py",
        "scripts/check_repo.py", ".github/workflows/tests.yml"]
for path in good:
    check("разрешён " + path, check_repo.forbidden_path_reason(path), None)

# .gitignore закрывает те же имена (случайный git add не подхватит файлы базы и ключи)
ignore = open(os.path.join(ROOT, ".gitignore"), encoding="utf-8").read().split()
for pattern in ("*.db-wal", "*.db-shm", "*.db-journal", "*.sqlite*", ".env.*", "*.pem", "*.key", "*.enc", "*.db"):
    assert pattern in ignore, ".gitignore: нет " + pattern

# ---------- токены ----------
check("токен найден", check_repo.token_lines("a\nTOKEN = '%s'\nb" % FAKE_TOKEN, ()), [2])
check("несколько строк", check_repo.token_lines("%s\nok\n%s" % (FAKE_TOKEN, OTHER_TOKEN), ()), [1, 3])
check("короткий не токен", check_repo.token_lines("t = '%s'" % SHORT, ()), [])
check("29 символов не токен", check_repo.token_lines("1234567:" + "a" * 29, ()), [])
check("30 символов токен", check_repo.token_lines("1234567:" + "a" * 30, ()), [1])
check("без числа не токен", check_repo.token_lines("abc:" + "a" * 40, ()), [])
check("разрешённая строка", check_repo.token_lines(FAKE_TOKEN, (FAKE_TOKEN,)), [])
check("разрешена только точная", check_repo.token_lines(OTHER_TOKEN, (FAKE_TOKEN,)), [1])
check("список по умолчанию пуст или точный", all(isinstance(t, str) for t in check_repo.ALLOWED_FAKE_TOKENS), True)

# ---------- токен в любом контексте (в том числе в ссылке api.telegram.org/bot<id>:<токен>) ----------
BODY = "Zq9" * 11 + "Zq"          # 35 знаков, случайно-подобное тело токена без слов-заглушек
TID = "1234567890"
positives = {
    "ссылка бота (буквы bot перед числом)": "https://api.telegram.org/bot%s:%s/getMe" % (TID, BODY),
    "ссылка с файлом": "curl https://api.telegram.org/file/bot%s:%s/photos/x.jpg" % (TID, BODY),
    "ссылка с закодированным двоеточием": "https://api.telegram.org/bot%s%%3A%s/sendMessage" % (TID, BODY),
    "заголовок": "X-Telegram-Bot-Token: %s:%s" % (TID, BODY),
    "переменная окружения": "export BOT_TOKEN=%s:%s" % (TID, BODY),
    "JSON": '{"token": "%s:%s"}' % (TID, BODY),
    "комментарий": "# рабочий токен %s:%s не коммитить" % (TID, BODY),
    "после слова без разделителя": "bot%s:%s" % (TID, BODY),
    "в скобках": "(%s:%s)" % (TID, BODY),
    "шестизначный номер": "123456:%s" % BODY,
}
for name, line in positives.items():
    check("находится: " + name, check_repo.token_lines(line), [1])
negatives = {
    "документация Telegram (точный пример)": "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
    "то же в ссылке": "https://api.telegram.org/bot123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11/getMe",
    "шаблон без цифр": "https://api.telegram.org/bot<TOKEN>/getMe",
    "шаблон ТОКЕН": "bot<ТОКЕН>:",
    "многоточие": "123456:ABC-DEF...",
    "слово example": "1234567890:EXAMPLE_TOKEN_DO_NOT_USE_0000000",
    "слово placeholder": "1234567890:placeholder_placeholder_placeholder",
    "слово not-real": "1234567890:token-not-real-token-not-real-12345",
    "слово fake": "1234567890:fake_fake_fake_fake_fake_fake_fake_1",
    "короткое тестовое": "123456:TEST-TOKEN-not-real",
    "время": "2026-10-06T12:30:45+03:00 и 12:30:45.123456",
    "строка без токена": "api.telegram.org/bot",
    "UUID": "123e4567-e89b-12d3-a456-426614174000",
}
for name, line in negatives.items():
    check("не находится: " + name, check_repo.token_lines(line), [])
check("цифры перед числом не считаются началом токена", check_repo.token_lines("a" + "12345:" + BODY), [])
# явные заглушки: слово-заглушка только в теле, не в идентификаторе; настоящее тело без таких слов находится всегда
check("заглушка не маскирует настоящий токен в соседней позиции", check_repo.token_lines("example %s:%s" % (TID, BODY)), [1])
# другие ключи
for name, line in (("закрытый ключ PEM", "-----BEGIN " + "RSA PRIVATE KEY-----"), ("закрытый ключ OpenSSH", "-----BEGIN " + "OPENSSH PRIVATE KEY-----"),
                   ("токен GitHub", "t = 'gh" + "p_" + "a1B2" * 9 + "'"), ("ключ AWS", "AKIA" + "ABCDEFGHIJKLMNOP"),
                   ("закрытый ключ копий", "BACKUP_PRIVATE_KEY=" + "A1b2" * 10 + "abc" + "=")):
    check("находится: " + name, check_repo.token_lines(line), [1])
check("имя переменной без значения не находится", check_repo.token_lines("BACKUP_PRIVATE_KEY=<значение>\nBACKUP_PUBLIC_KEY=пусто"), [])

# ---------- find_problems на файлах: значение не попадает в результат ----------
tmp = tempfile.mkdtemp()
try:
    def put(rel, data, mode="w"):
        full = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, mode) as f:
            f.write(data)

    put("src/a.py", "x = 1\nTOKEN = '%s'\n" % FAKE_TOKEN)
    put("src/clean.py", "x = '%s'\n" % SHORT)
    put("src/bin.dat", b"\x00" + FAKE_TOKEN.encode(), "wb")
    put(".env", "BOT_TOKEN=%s\n" % FAKE_TOKEN)
    problems = check_repo.find_problems(tmp, ["src/a.py", "src/clean.py", "src/bin.dat", ".env", "gone.txt"], ())
    check("найденные проблемы", sorted((p, n) for p, n, _ in problems), [(".env", None), ("src/a.py", 2)])
    assert FAKE_TOKEN not in repr(problems), "значение токена попало в результат"
    check("разрешённый токен", check_repo.find_problems(tmp, ["src/a.py"], (FAKE_TOKEN,)), [])

    # ---------- запуск как программа в настоящем git-репозитории ----------
    def git(*args):
        subprocess.run(("git",) + args, cwd=tmp, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def run_script():
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "check_repo.py"), tmp],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        return r.returncode, r.stdout

    git("init", "-q")
    git("add", "src/clean.py", "src/bin.dat")
    code, out = run_script()
    check("чистый индекс: код 0", code, 0)
    git("add", "src/a.py")
    code, out = run_script()
    check("токен в индексе: код 1", code, 1)
    assert "src/a.py:2" in out, out
    assert FAKE_TOKEN not in out and "AAAAAAAAAA" not in out, "значение напечатано"
    git("rm", "-q", "--cached", "src/a.py")
    git("add", "-f", ".env")
    code, out = run_script()
    check(".env в индексе: код 1", code, 1)
    assert ".env" in out and FAKE_TOKEN not in out, out
    git("rm", "-q", "--cached", ".env")
    code, _ = run_script()
    check("после удаления из индекса: код 0", code, 0)
    # ---------- режим --staged: проверяется содержимое ИНДЕКСА, а не рабочей папки ----------
    def run_mode(mode):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "check_repo.py"), mode, tmp],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        return r.returncode, r.stdout

    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    git("commit", "-q", "-m", "base")
    code, out = run_mode("--staged")
    check("--staged: ничего не добавлено: код 0", code, 0)
    with open(os.path.join(tmp, "src", "new.py"), "w") as f:
        f.write("url = 'https://api.telegram.org/bot%s:%s/getMe'\n" % (TID, BODY))
    git("add", "src/new.py")
    code, out = run_mode("--staged")
    check("--staged: токен в индексе: код 1", code, 1)
    assert "src/new.py:1" in out and BODY not in out, out
    with open(os.path.join(tmp, "src", "new.py"), "w") as f:
        f.write("url = 'clean'\n")          # в рабочей папке чисто, а в индексе токен: всё равно ошибка
    code, _ = run_mode("--staged")
    check("--staged: смотрит индекс, а не рабочую папку", code, 1)
    git("add", "src/new.py")
    code, _ = run_mode("--staged")
    check("--staged: после исправления и добавления: код 0", code, 0)
    with open(os.path.join(tmp, "src", "new.py"), "w") as f:
        f.write("url = 'https://api.telegram.org/bot%s:%s/getMe'\n" % (TID, BODY))   # токен только в рабочей папке, не в индексе
    code, _ = run_mode("--staged")
    check("--staged: токен вне индекса не мешает коммиту", code, 0)
    git("checkout", "--", "src/new.py")
    # ---------- хук .githooks/pre-commit: настоящий git commit блокируется ----------
    hook_dir = os.path.join(ROOT, ".githooks")
    assert os.access(os.path.join(hook_dir, "pre-commit"), os.X_OK), "хук не исполняемый"
    git("config", "core.hooksPath", hook_dir)
    with open(os.path.join(tmp, "src", "leak.py"), "w") as f:
        f.write("T = '%s:%s'\n" % (TID, BODY))
    git("add", "src/leak.py")
    r = subprocess.run(["git", "commit", "-m", "x"], cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    check("хук блокирует коммит с токеном", r.returncode != 0, True)
    assert "src/leak.py:1" in r.stdout and BODY not in r.stdout, r.stdout
    git("rm", "-q", "--cached", "src/leak.py")
    r = subprocess.run(["git", "commit", "-q", "-m", "y", "--allow-empty"], cwd=tmp, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    check("хук пропускает чистый коммит", r.returncode, 0)
    # ---------- режим --history: токен, удалённый из рабочей папки, остаётся в истории ----------
    code, out = run_mode("--history")
    check("--history: чистая история: код 0", code, 0)
    git("add", "-f", "src/leak.py")
    git("commit", "-q", "--no-verify", "-m", "leak")
    git("rm", "-q", "src/leak.py")
    git("commit", "-q", "--no-verify", "-m", "fix")
    code, out = run_mode("--history")
    check("--history: токен в старом коммите найден: код 1", code, 1)
    assert "src/leak.py:1" in out and BODY not in out, out
    code, _ = run_script()
    check("а индекс при этом чист", code, 0)
    # не репозиторий: код 1, без падения
    notrepo = tempfile.mkdtemp()
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "check_repo.py"), notrepo],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
        check("не репозиторий: код 1", r.returncode, 1)
    finally:
        shutil.rmtree(notrepo, ignore_errors=True)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

# настоящий репозиторий проекта должен быть чистым
check("репозиторий проекта чист", check_repo.main([ROOT]), 0)

print("Все проверки прошли")

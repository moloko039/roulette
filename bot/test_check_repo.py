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
       "backups/latest.db", "bot/backups/readme.txt", "PLAYERS.DB"]
for path in bad:
    assert check_repo.forbidden_path_reason(path), "не запрещён: " + path
good = [".env.example", "bot/.env.example", ".env.sample", "bot/api.py", "index.html", "docs/JOURNAL.md",
        "bot/test_db.py", "environment.md", "bot/backup.py", "bot/test_backup.py", "keyboard.py",
        "scripts/check_repo.py", ".github/workflows/tests.yml"]
for path in good:
    check("разрешён " + path, check_repo.forbidden_path_reason(path), None)

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

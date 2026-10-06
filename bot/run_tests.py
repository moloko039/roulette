"""Запускает все bot/test_*.py по очереди и печатает итог. Код выхода 1, если что-то упало.

  python run_tests.py                 обычный запуск
  python run_tests.py --hostile-env   то же с «враждебным» окружением: в окружение каждого теста подставлены реалистичные значения всех переменных
                                      проекта (токен, секреты, владелец, база, режимы, лимиты); результаты не должны меняться (тесты сами задают и
                                      очищают переменные: testenv.py)"""
import glob
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

# Значения выглядят настоящими, но придуманы; токен собран из частей, чтобы scripts/check_repo.py не принял его за секрет.
HOSTILE_ENV = {
    "BOT_TOKEN": "".join(["7", "301", "234", "567", ":", "AAH", "q9Zk3vXw2LmN8pRtYb5cD1fGhJ4sUe6oQ0a"]),
    "WEBAPP_URL": "https://hostile.example.invalid/app/",
    "PUBLIC_URL": "https://hostile.example.invalid",
    "WEBHOOK_SECRET": "hostile-webhook-secret-0123456789",
    "TOMBSTONE_SECRET": "hostile-tombstone-secret-not-the-test-one",
    "MEMBER_REF_SECRET": "hostile-member-ref-secret-not-the-test-one",
    "OWNER_CHAT_ID": "424242421",
    "DB_PATH": os.path.join(tempfile.gettempdir(), "hostile-env-players.db"),
    "SQLITE_JOURNAL_MODE": "wal",
    "PLAY_MODE": "button",
    "GAME_LINK": "https://t.me/hostile_bot/app",
    "PRIVACY_URL": "https://hostile.example.invalid/privacy.html",
    "TERMS_URL": "https://hostile.example.invalid/terms.html",
    "DEVELOPER_CONTACT": "hostile@example.invalid",
    "PAY_SUPPORT_CONTACT": "@hostile_support",
    "BACKUP_ENABLED": "1",
    "BACKUP_DIR": os.path.join(tempfile.gettempdir(), "hostile-env-backups"),
    "BACKUP_KEEP": "2",
    "BACKUP_INTERVAL_HOURS": "1",
    "BACKUP_PUBLIC_KEY": "A" * 43 + "=",
    "BACKUP_SEND_INTERVAL_DAYS": "1",
    "BACKUP_SEND_MAX_MB": "1",
    "ROUNDS_RETENTION_DAYS": "2",
    "CHAT_MEMBER_RETENTION_DAYS": "7",
    "REGISTRATION_COOLDOWN_DAYS": "0",
    "READ_RATE_PER_SEC": "1",
    "READ_RATE_BURST": "1",
    "WRITE_RATE_PER_SEC": "1",
    "WRITE_RATE_BURST": "1",
    "PORT": "1",
    "ALLOWED_ORIGINS": "https://hostile.example.invalid",
    "LOCAL_POLLING": "1",
    "PYTHON_DOTENV_DISABLED": "",
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    env = None
    if "--hostile-env" in argv:
        env = dict(os.environ)
        env.update({k: v for k, v in HOSTILE_ENV.items() if v != ""})
        print("Враждебное окружение: подставлено %d переменных проекта" % len([1 for v in HOSTILE_ENV.values() if v != ""]))
    passed, failed = [], []
    for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
        name = os.path.basename(path)
        result = subprocess.run([sys.executable, path], cwd=HERE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, universal_newlines=True, env=env)
        if result.returncode == 0:
            passed.append(name)
            print("ПРОШЁЛ  " + name)
        else:
            failed.append(name)
            print("УПАЛ    " + name)
            print("\n".join(result.stdout.splitlines()[-15:]))
    print("\nИтого: прошло %d, упало %d" % (len(passed), len(failed)))
    if failed:
        print("Упали: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

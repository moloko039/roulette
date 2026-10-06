"""Изоляция тестов от окружения оболочки и bot/.env. Импортируется ПЕРВЫМ в каждом test_*.py (до импорта кода проекта: bot.py и api.py
вызывают load_dotenv() при импорте).

Что делает при импорте: (1) отключает чтение .env (PYTHON_DOTENV_DISABLED=1, поддерживается python-dotenv 1.x); (2) удаляет из окружения ВСЕ переменные
проекта (список ниже: любое значение из оболочки или .env тест не увидит). Нужные значения тест затем задаёт сам (os.environ[...] = ...).
Проверка: python run_tests.py --hostile-env (реалистичные значения всех переменных) и test_testenv.py."""
import os

PROJECT_ENV = (
    "BOT_TOKEN", "WEBAPP_URL", "PUBLIC_URL", "WEBHOOK_SECRET", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID", "DB_PATH",
    "SQLITE_JOURNAL_MODE", "PLAY_MODE", "GAME_LINK", "PRIVACY_URL", "TERMS_URL", "DEVELOPER_CONTACT", "PAY_SUPPORT_CONTACT",
    "BACKUP_ENABLED", "BACKUP_DIR", "BACKUP_KEEP", "BACKUP_INTERVAL_HOURS", "BACKUP_PUBLIC_KEY", "BACKUP_SEND_INTERVAL_DAYS", "BACKUP_SEND_MAX_MB",
    "ROUNDS_RETENTION_DAYS", "CHAT_MEMBER_RETENTION_DAYS", "REGISTRATION_COOLDOWN_DAYS",
    "READ_RATE_PER_SEC", "READ_RATE_BURST", "WRITE_RATE_PER_SEC", "WRITE_RATE_BURST", "PORT", "ALLOWED_ORIGINS", "LOCAL_POLLING",
)

os.environ["PYTHON_DOTENV_DISABLED"] = "1"
for _name in PROJECT_ENV:
    os.environ.pop(_name, None)

# Защита времени выполнения: запись players.balance вне разрешённых запросов wallet роняет любой тест (см. balance_guard.py)
import balance_guard  # noqa: E402

balance_guard.install()

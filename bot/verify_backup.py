"""Проверка файла резервной копии: python verify_backup.py <путь к файлу>.

Открывает файл только для чтения, выполняет PRAGMA integrity_check, проверяет наличие
таблиц players, roulette_rounds, chat_members, печатает число строк и дату самой свежей
записи. Личные данные (id, имена, балансы) не печатаются.
Код выхода: 0 — копия исправна, 1 — проблема.
"""
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REQUIRED = ("players", "roulette_rounds", "chat_members")
OPTIONAL = ("deletion_tombstones",)
# запросы заданы целиком, без подстановки значений в SQL
COUNT_SQL = {
    "players": "SELECT COUNT(*) FROM players",
    "roulette_rounds": "SELECT COUNT(*) FROM roulette_rounds",
    "chat_members": "SELECT COUNT(*) FROM chat_members",
    "deletion_tombstones": "SELECT COUNT(*) FROM deletion_tombstones",
}
NEWEST_SQL = (
    ("players", "SELECT MAX(MAX(created_at), MAX(last_accrual)) FROM players"),
    ("roulette_rounds", "SELECT MAX(created_at) FROM roulette_rounds"),
    ("chat_members", "SELECT MAX(last_seen) FROM chat_members"),
    ("deletion_tombstones", "SELECT MAX(deleted_at) FROM deletion_tombstones"),
)


def verify(path):
    """Возвращает (ok, строки_отчёта)."""
    lines = []
    if not os.path.isfile(path):
        return False, ["Файл не найден"]
    try:
        conn = sqlite3.connect(Path(os.path.abspath(path)).as_uri() + "?mode=ro", uri=True, timeout=30)
    except sqlite3.Error:
        return False, ["Не удалось открыть файл как базу SQLite"]
    try:
        try:
            result = conn.execute("PRAGMA integrity_check").fetchall()
        except sqlite3.DatabaseError:
            return False, ["Файл не является базой SQLite или повреждён"]
        if result != [("ok",)]:
            return False, ["integrity_check: найдены повреждения"]
        lines.append("integrity_check: ok")
        present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = [t for t in REQUIRED if t not in present]
        if missing:
            return False, lines + ["Нет таблиц: " + ", ".join(missing)]
        newest = []
        for table in REQUIRED + OPTIONAL:
            if table in present:
                lines.append("%s: %d строк" % (table, conn.execute(COUNT_SQL[table]).fetchone()[0]))
        for table, sql in NEWEST_SQL:
            if table in present:
                value = conn.execute(sql).fetchone()[0]
                if value is not None:
                    newest.append(value)
        if newest:
            lines.append("Самая свежая запись: " + datetime.fromtimestamp(max(newest), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        else:
            lines.append("Самая свежая запись: записей нет")
        return True, lines
    except sqlite3.Error:
        return False, lines + ["Ошибка чтения базы"]
    finally:
        conn.close()


def main(argv):
    if len(argv) != 2:
        print("Использование: python verify_backup.py <путь к файлу>")
        return 1
    ok, lines = verify(argv[1])
    for line in lines:
        print(line)
    print("Результат: исправна" if ok else "Результат: ПРОБЛЕМА")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

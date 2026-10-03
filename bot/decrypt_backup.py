"""Расшифровка зашифрованной копии (запускается локально у владельца, не на сервере).

  python3 bot/decrypt_backup.py ФАЙЛ.enc ФАЙЛ_ПРИВАТНОГО_КЛЮЧА ВЫХОД.db

Проверяет метку формата, расшифровывает, пишет файл с правами 0600 (существующий не перезаписывает),
затем выполняет ту же проверку, что verify_backup.py (integrity_check), и печатает количества строк.
Код выхода: 0 — всё хорошо, 1 — проблема.
"""
import os
import sys

import verify_backup

MAX_INPUT_BYTES = 200 * 1024 * 1024


def main(argv):
    if len(argv) != 4:
        print("Использование: python3 bot/decrypt_backup.py ФАЙЛ.enc ФАЙЛ_ПРИВАТНОГО_КЛЮЧА ВЫХОД.db")
        return 1
    src, key_path, out = argv[1], argv[2], argv[3]
    try:
        import backup_crypto
        import nacl.public  # noqa: F401
    except ImportError:
        print("Не установлена библиотека PyNaCl. Установите: bot/.venv/bin/pip install -r bot/requirements.txt "
              "(в окружении bot/.venv)")
        return 1
    if os.path.lexists(out):
        print("Отказ: выходной файл уже существует, он не перезаписывается")
        return 1
    try:
        if os.path.getsize(src) > MAX_INPUT_BYTES:
            print("Файл слишком большой для расшифровки")
            return 1
        with open(src, "rb") as handle:
            data = handle.read()
    except OSError:
        print("Не удалось прочитать зашифрованный файл")
        return 1
    try:
        with open(os.path.expanduser(key_path), "r") as handle:
            key = handle.read().strip()
    except OSError:
        print("Не удалось прочитать файл закрытого ключа")
        return 1
    try:
        plain = backup_crypto.decrypt_bytes(data, key)
    except ValueError as exc:
        print(str(exc))
        return 1
    try:
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError:
        print("Не удалось создать выходной файл")
        return 1
    with os.fdopen(fd, "wb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(plain)
    print("Расшифровано: " + out)
    ok, lines = verify_backup.verify(out)
    for line in lines:
        print(line)
    print("Результат: исправна" if ok else "Результат: ПРОБЛЕМА")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

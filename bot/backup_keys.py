"""Ключи для зашифрованных копий (запускается локально у владельца, не на сервере).

  python3 bot/backup_keys.py generate [--out ПУТЬ]   создать пару ключей
  python3 bot/backup_keys.py public --key ПУТЬ       напечатать открытый ключ из файла закрытого

Закрытый ключ пишется только в файл (права 0600) и в терминал не выводится. Печатается открытый
ключ (его значение задаётся на сервере в BACKUP_PUBLIC_KEY) и путь к файлу.
"""
import argparse
import base64
import os
import subprocess
import sys

DEFAULT_NAME = "roulette-backup-private.key"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    return path == root or path.startswith(root + os.sep)


def inside_git_repo(path):
    """Лежит ли путь внутри репозитория git (по папке .git выше по дереву, по git и по этому проекту)."""
    path = os.path.abspath(path)
    if _inside(path, REPO_ROOT):
        return True
    folder = os.path.dirname(path)
    while True:
        if os.path.exists(os.path.join(folder, ".git")):
            return True
        parent = os.path.dirname(folder)
        if parent == folder:
            break
        folder = parent
    existing = os.path.dirname(path)
    while existing and not os.path.isdir(existing):
        existing = os.path.dirname(existing)
    try:
        result = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"], cwd=existing or ".",
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
        return result.returncode == 0 and result.stdout.strip() == "true"
    except OSError:
        return False


def generate(out):
    from nacl.public import PrivateKey
    path = os.path.abspath(os.path.expanduser(out or os.path.join("~", DEFAULT_NAME)))
    if inside_git_repo(path):
        print("Отказ: путь внутри репозитория git, закрытый ключ нельзя хранить рядом с кодом. Укажите --out вне репозитория.")
        return 1
    if os.path.lexists(path):
        print("Отказ: файл уже существует, существующий ключ не перезаписывается")
        return 1
    key = PrivateKey.generate()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except OSError as exc:
        print("Не удалось создать файл ключа: %s" % type(exc).__name__)
        return 1
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(base64.b64encode(bytes(key)).decode("ascii") + "\n")
    print("Открытый ключ (значение BACKUP_PUBLIC_KEY на сервере): " + base64.b64encode(bytes(key.public_key)).decode("ascii"))
    print("Закрытый ключ сохранён в файле: " + path)
    print("Сохраните файл в менеджере паролей и во втором надёжном месте: без него копии не расшифровать.")
    return 0


def public(key_path):
    from nacl.public import PrivateKey
    import backup_crypto
    try:
        with open(os.path.expanduser(key_path), "r") as handle:
            raw = backup_crypto.parse_private_key(handle.read())
    except (OSError, ValueError):
        print("Не удалось прочитать закрытый ключ из файла")
        return 1
    print(base64.b64encode(bytes(PrivateKey(raw).public_key)).decode("ascii"))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Ключи для зашифрованных резервных копий")
    sub = parser.add_subparsers(dest="command")
    gen = sub.add_parser("generate", help="создать пару ключей")
    gen.add_argument("--out", help="куда записать закрытый ключ (по умолчанию ~/%s)" % DEFAULT_NAME)
    pub = sub.add_parser("public", help="напечатать открытый ключ")
    pub.add_argument("--key", required=True, help="файл закрытого ключа")
    args = parser.parse_args(argv)
    if args.command not in ("generate", "public"):
        parser.print_usage()
        return 1
    try:
        import nacl.public  # noqa: F401
    except ImportError:
        print("Не установлена библиотека PyNaCl. Установите: bot/.venv/bin/pip install -r bot/requirements.txt "
              "(в окружении bot/.venv)")
        return 1
    return generate(args.out) if args.command == "generate" else public(args.key)


if __name__ == "__main__":
    sys.exit(main())

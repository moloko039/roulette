"""Метка версии клиента: ?v=<хэш содержимого> у js/*.js и css/*.css в index.html.

GitHub Pages кэширует файлы отдельно, а клиент теперь из нескольких файлов: без метки браузер мог бы собрать страницу из старых и новых частей.
Запускай после любой правки js/, css/: `python scripts/stamp_client.py` (код 0, если менять нечего; `--check` только проверяет, код 1 при расхождении)."""
import hashlib
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAG = re.compile(r'(<(?:script src|link rel="stylesheet" href)="((?:js|css)/[^"?]+))(?:\?v=[0-9a-f]+)?(")')


def stamp(html):
    def one(m):
        with open(os.path.join(ROOT, m.group(2)), "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:8]
        return "%s?v=%s%s" % (m.group(1), digest, m.group(3))
    return TAG.sub(one, html)


def main():
    path = os.path.join(ROOT, "index.html")
    with open(path, encoding="utf-8") as f:
        old = f.read()
    new = stamp(old)
    if new == old:
        return 0
    if "--check" in sys.argv:
        print("index.html: метки версий устарели, запусти python scripts/stamp_client.py")
        return 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    print("index.html: метки версий обновлены")
    return 0


if __name__ == "__main__":
    sys.exit(main())

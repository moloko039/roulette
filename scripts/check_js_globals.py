"""Дубли имён верхнего уровня в js/*.js (общая область видимости: function повторно молча перекрывает прежнюю, const и let дают ошибку и ломают весь файл).

Запуск: `python scripts/check_js_globals.py` (код 0, если дублей нет). Разбор простой: строки верхнего уровня (без отступа) вида `function name`, `async function name`,
`const name`, `let name`, `var name`, `class name`."""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECL = re.compile(r"^(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)")
DESTRUCT = re.compile(r"^(?:const|let|var)\s+[\[{]\s*([^\]}=]+)[\]}]")


def declared(path):
    names = []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            m = DECL.match(line)
            if m:
                names.append((m.group(1), lineno))
                continue
            m = DESTRUCT.match(line)
            if m:
                for part in m.group(1).split(","):
                    name = part.split(":")[-1].strip()
                    if re.fullmatch(r"[A-Za-z_$][\w$]*", name):
                        names.append((name, lineno))
    return names


def duplicates():
    seen = {}
    bad = []
    for path in sorted(glob.glob(os.path.join(ROOT, "js", "*.js"))):
        rel = os.path.relpath(path, ROOT)
        for name, lineno in declared(path):
            if name in seen:
                bad.append("%s:%d: «%s» уже объявлено в %s" % (rel, lineno, name, seen[name]))
            else:
                seen[name] = "%s:%d" % (rel, lineno)
    return bad


if __name__ == "__main__":
    problems = duplicates()
    for line in problems:
        print(line)
    if problems:
        print("Дубли имён в js/: %d" % len(problems))
        sys.exit(1)
    print("Дублей имён верхнего уровня в js/ нет")

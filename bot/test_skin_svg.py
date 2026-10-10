"""Встроенные картинки скинов: каждый SVG в url("data:image/svg+xml,...") в css/*.css и skins/*.css должен быть корректным XML (браузер молча не рисует битую картинку,
и ни один e2e-сценарий этого не замечает: они смотрят на имена анимаций и классы, а не на картинку)."""
import testenv  # noqa: F401
import glob
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = re.compile(r'url\(\s*(["\'])data:image/svg\+xml[;,]((?:(?!\1).)*)\1\s*\)', re.S)

bad = []
total = 0
for path in sorted(glob.glob(os.path.join(ROOT, "css", "*.css")) + glob.glob(os.path.join(ROOT, "skins", "*.css"))):
    text = open(path, encoding="utf-8").read()
    for m in URL.finditer(text):
        total += 1
        body = m.group(2)
        if body.startswith("charset=utf8,"):
            body = body[len("charset=utf8,"):]
        svg = urllib.parse.unquote(body)
        try:
            ET.fromstring(svg)
        except ET.ParseError as exc:
            line = text.count("\n", 0, m.start()) + 1
            bad.append("%s:%d: %s" % (os.path.relpath(path, ROOT), line, exc))
assert total > 50, "SVG в css не найдены (%d): шаблон поиска сломан" % total
assert not bad, "битые встроенные SVG (%d из %d):\n%s" % (len(bad), total, "\n".join(bad))
print("SVG в css проверено: %d" % total)
print("Все проверки прошли")

"""Руны под полем Bloody slot (одноразовый скрипт разработчика, нужен Pillow): из плиток младших символов L1..L5 вырезаются только тёмные линии самого
знака, без плитки, рамки и пунктира. Результат: маски WebP (белый знак на прозрачном), клиент красит их через CSS mask-image (серый камень или горящий
оранжевый). Запуск: uv run --with pillow python tools/prepare_bloody_runes.py <папка bloody-slot-simple> <папка games/bloody-slot/img>"""
import os
import sys
from collections import deque

from PIL import Image, ImageFilter

RUNES = ["L1", "L2", "L3", "L4", "L5"]
SIZE = 160
MARGIN = 0.235       # доля края плитки, где рамка, пунктир и уголки: знак лежит внутри
RATIO = 0.6          # линия знака: темнее окружающего тона плитки (размытие) в столько раз и меньше DARK
DARK = 75
SOFT = 18            # ширина мягкого края маски (по яркости)
MIN_PART = 0.004     # куски меньше этой доли площади (пунктир, капли, трещины) отбрасываются


def rune_mask(im):
    base = Image.new("RGBA", im.size, (128, 128, 128, 255))
    base.alpha_composite(im.convert("RGBA"))
    g = base.convert("L")
    w, h = g.size
    m = int(w * MARGIN)
    g = g.crop((m, m, w - m, h - m))
    w, h = g.size
    tone = g.filter(ImageFilter.GaussianBlur(w * 0.08)).load()      # окружающий тон плитки
    px = g.load()
    limit = [[0] * w for _ in range(h)]
    solid = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            lim = min(DARK, tone[x, y] * RATIO)
            limit[y][x] = lim
            if px[x, y] < lim:
                solid[y * w + x] = 1
    keep = bytearray(w * h)
    seen = bytearray(w * h)
    for start in range(w * h):
        if not solid[start] or seen[start]:
            continue
        part, queue = [], deque([start])
        seen[start] = 1
        while queue:
            i = queue.popleft()
            part.append(i)
            x, y = i % w, i // w
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    j = ny * w + nx
                    if solid[j] and not seen[j]:
                        seen[j] = 1
                        queue.append(j)
        if len(part) >= MIN_PART * w * h:
            for i in part:
                keep[i] = 1
    out = Image.new("LA", (w, h), (255, 0))
    op = out.load()
    for y in range(h):
        for x in range(w):
            if keep[y * w + x]:
                a = max(0, min(255, int((limit[y][x] + SOFT - px[x, y]) * 255 / (2 * SOFT))))
                op[x, y] = (255, a)
    return out.resize((SIZE, SIZE), Image.LANCZOS)


def main(src, dst):
    for name in RUNES:
        im = Image.open(os.path.join(src, "02_Symbols", name + ".png"))
        out = rune_mask(im)
        path = os.path.join(dst, "rune-" + name + ".webp")
        out.convert("RGBA").save(path, "WEBP", quality=90, method=6)
        print(path, os.path.getsize(path))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

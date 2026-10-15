"""Подготовка графики Bloody slot (одноразовый скрипт разработчика, нужен Pillow): исходники 1024×1024 PNG из папки дизайнера → лёгкие WebP для мини-приложения.
У части символов квадратная рамка на белом фоне без прозрачности: белый фон, связанный с краем, делается прозрачным (заливка от краёв). Символы 192×192, фон 720×720.
Запуск: python tools/prepare_bloody_assets.py <папка bloody-slot-simple> <папка games/bloody-slot/img>"""
import os
import sys
from collections import deque

from PIL import Image

SYMBOLS = ["H1", "H2", "H3", "H4", "H5", "M1", "M2", "M3", "M4", "M5", "L1", "L2", "L3", "L4", "L5", "Wild", "Scatter", "Dagger", "Guillotine"]
SIZE = 192
WHITE = 226          # канал не меньше: «почти белый»


def clear_white_edges(im):
    """Белый фон, связанный с краями картинки, становится прозрачным (рамка символа остаётся, у неё тёмная кромка)."""
    im = im.convert("RGBA")
    w, h = im.size
    px = im.load()
    seen = bytearray(w * h)
    queue = deque()
    for x in range(w):
        queue.append((x, 0)); queue.append((x, h - 1))
    for y in range(h):
        queue.append((0, y)); queue.append((w - 1, y))
    while queue:
        x, y = queue.popleft()
        if x < 0 or y < 0 or x >= w or y >= h or seen[y * w + x]:
            continue
        r, g, b, a = px[x, y]
        if a < 8:
            seen[y * w + x] = 1
            queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
            continue
        if min(r, g, b) < WHITE:
            continue
        seen[y * w + x] = 1
        px[x, y] = (r, g, b, 0)
        queue.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    return im


def main(src, dst):
    os.makedirs(dst, exist_ok=True)
    for name in SYMBOLS:
        im = Image.open(os.path.join(src, "02_Symbols", name + ".png"))
        if im.mode == "RGB":
            im = clear_white_edges(im.resize((512, 512), Image.LANCZOS))     # заливка на уменьшенной копии быстрее и сглаживает кромку
        else:
            im = im.convert("RGBA")
        im = im.resize((SIZE, SIZE), Image.LANCZOS)
        im.save(os.path.join(dst, name + ".webp"), "WEBP", quality=82, method=6)
    bg = Image.open(os.path.join(src, "01_Backgrounds", "Main Game - Background.png")).convert("RGB").resize((720, 720), Image.LANCZOS)
    bg.save(os.path.join(dst, "bg.webp"), "WEBP", quality=70, method=6)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

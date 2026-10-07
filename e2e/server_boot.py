"""Запуск настоящего сервера (FastAPI из bot/) для e2e. Запускается харнессом как отдельный процесс: python server_boot.py CONFIG.json.

ВСЕ подмены для детерминизма живут только здесь (боевой код хуков не содержит): серверные часы = реальные + смещение из файла,
случайные числа игр берутся из файла сценария (script.json), пока он не пуст, иначе настоящие. Подмены ставятся до импорта приложения.
"""
import json
import os
import sqlite3
import sys
import time

cfg = json.load(open(sys.argv[1], encoding="utf-8"))
sys.path.insert(0, cfg["bot_dir"])
for name in ("DB_PATH", "TOMBSTONE_SECRET", "MEMBER_REF_SECRET", "OWNER_CHAT_ID", "SQLITE_JOURNAL_MODE", "WRITE_RATE_PER_SEC",
             "WRITE_RATE_BURST", "READ_RATE_PER_SEC", "READ_RATE_BURST", "PLAY_MODE"):
    os.environ.pop(name, None)
os.environ["TOMBSTONE_SECRET"] = cfg["secret"]
os.environ["MEMBER_REF_SECRET"] = cfg["secret"] + "-ref"
os.environ["OWNER_CHAT_ID"] = str(cfg["owner_id"])

# ---- серверные часы: реальные + смещение (сценарий двигает его, чтобы «прошли минуты»)
_real_time = time.time


def _clock():
    try:
        offset = float(open(cfg["offset_file"], encoding="utf-8").read().strip() or 0)
    except (OSError, ValueError):
        offset = 0.0
    return _real_time() + offset


time.time = _clock

# ---- генераторы случайных чисел игр: очередь значений из script.json (ключи spin, keno, mines, shoe, crash, hilo, invoice)
import blackjack  # noqa: E402
import crash  # noqa: E402
import hilo  # noqa: E402
import keno  # noqa: E402
import mines  # noqa: E402
import secrets  # noqa: E402


def take(key):
    """Следующее значение из очереди сценария или None (тогда работает настоящий генератор)."""
    try:
        data = json.load(open(cfg["script_file"], encoding="utf-8"))
    except (OSError, ValueError):
        return None
    queue = data.get(key) or []
    if not queue:
        return None
    value = queue.pop(0)
    data[key] = queue
    json.dump(data, open(cfg["script_file"], "w", encoding="utf-8"))
    return value


_real_randbelow, _real_draw, _real_layout, _real_shoe, _real_crash, _real_card = (
    secrets.randbelow, keno.draw_numbers, mines.new_layout, blackjack.new_shoe, crash.new_crash, hilo.draw_card)


def _randbelow(n):
    value = take("spin")
    return _real_randbelow(n) if value is None else value


def _draw(rng=None):
    value = take("keno")
    return _real_draw(rng) if value is None else value


def _layout(count, rng=None):
    cells = take("mines")
    if cells is None:
        return _real_layout(count, rng)
    mask = 0
    for cell in cells:
        mask |= 1 << cell
    return mask


class _Stack:
    """Колода: карты сценария кладутся сверху (в указанном порядке), остальные как были."""

    def __init__(self, cards):
        self.cards = cards

    def shuffle(self, shoe):
        for card in reversed(self.cards):
            shoe.remove(card)
            shoe.insert(0, card)


def _shoe(rng=None):
    cards = take("shoe")
    return _real_shoe(rng) if cards is None else _real_shoe(_Stack(cards))


def _crash(rng=None):
    value = take("crash")
    return _real_crash(rng) if value is None else value


def _card(rng=None):
    value = take("hilo")
    return _real_card(rng) if value is None else (value[0], value[1])


secrets.randbelow = _randbelow
keno.draw_numbers = _draw
mines.new_layout = _layout
blackjack.new_shoe = _shoe
crash.new_crash = _crash
hilo.draw_card = _card

import cosmetic_sets  # noqa: E402

# сезон коллекции «Листопад» в e2e идёт всегда (иначе сценарии зависели бы от календаря); в боевом коде сезон только октябрь 2026
for _c in cosmetic_sets.COLLECTIONS.values():
    _c["season"] = ("2020-01-01", "2099-12-31")
import db  # noqa: E402
import ratelimit  # noqa: E402
import uvicorn  # noqa: E402
from api import create_app  # noqa: E402

db.init_db(cfg["db"])
now = int(time.time())
conn = sqlite3.connect(cfg["db"])
for u in cfg["users"]:
    conn.execute(
        "INSERT INTO players (telegram_id, balance, rate, last_accrual, accrual_acc, created_at, total_staked, xp, income_level, storage_level) "
        "VALUES (?, ?, ?, ?, 0, ?, ?, ?, 0, 0)",
        (u["id"], u["balance"], u["rate"], now // 60 * 60, now - u["age_days"] * 86400, u["staked"], u["xp"]))
    conn.execute("INSERT INTO chat_members (chat_instance, telegram_id, first_name, first_seen, last_seen) VALUES (?, ?, ?, ?, ?)",
                 (cfg["chat"], u["id"], u["name"], now - 86400, now))
conn.commit()
conn.close()
app = create_app(cfg["token"], [cfg["origin"]], db_path=cfg["db"], rate_limiter=ratelimit.RateLimiter(ratelimit.load_config({})))


class _FakeBot:
    """Подмена Telegram для оплаты (только e2e): ссылку на инвойс берёт из очереди сценария (ключ invoice; "ERR" = сбой Telegram, 502)."""

    async def create_invoice_link(self, **kwargs):
        value = take("invoice")
        if value == "ERR":
            raise RuntimeError("e2e: сбой Telegram")
        return value or "https://t.me/$e2e-invoice"


import contextlib  # noqa: E402
import types  # noqa: E402

_lifespan = app.router.lifespan_context


@contextlib.asynccontextmanager
async def _lifespan_with_fake_bot(a):
    async with _lifespan(a):                                   # штатный запуск (в режиме API приложение Telegram = None) ...
        a.state.application = types.SimpleNamespace(bot=_FakeBot())   # ... и подмена бота только для e2e
        yield


app.router.lifespan_context = _lifespan_with_fake_bot
uvicorn.run(app, host="127.0.0.1", port=cfg["port"], log_level="warning")

"""Ограничение частоты запросов: токен-бакет в памяти на пару (telegram_id, группа).

Общее для всех игр: группа "write" (POST: вращение рулетки и будущие игровые и фермерские запросы) и
"read" (GET /api/me, /api/chat/top). Лимиты щедрые: цель остановить клиенты, шлющие запросы в цикле, а
не мешать человеку и быстрым играм. Проверка идёт после проверки подписи initData, поэтому
неподписанные запросы бакетов не создают. Ключ содержит id игрока только в памяти, в лог он не пишется.
"""
import logging
import math
import re
import threading
import time
from collections import OrderedDict

logger = logging.getLogger("depnaya.ratelimit")

DEFAULTS = {
    "WRITE_RATE_PER_SEC": 5,
    "WRITE_RATE_BURST": 20,
    "READ_RATE_PER_SEC": 10,
    "READ_RATE_BURST": 40,
    "LIVE_RATE_PER_SEC": 6,
    "LIVE_RATE_BURST": 12,
}
IDLE_SECONDS = 600       # бакет, которым не пользовались 10 минут, удаляется
MAX_BUCKETS = 50_000     # общий предел записей; при превышении удаляются самые старые
GROUPS = ("write", "read", "live")


def load_config(env):
    """Читает настройки; неверные значения (не целое от 1 до 999999) заменяются умолчаниями."""
    config = {"invalid": []}
    for name, default in DEFAULTS.items():
        raw = (env.get(name) or "").strip()
        if not raw:
            config[name] = default
        elif re.fullmatch(r"[0-9]{1,6}", raw) and int(raw) >= 1:
            config[name] = int(raw)
        else:
            config[name] = default
            config["invalid"].append(name)
    return config


def warn_config(config):
    """Одно предупреждение при старте с именами переменных (значения не пишутся)."""
    if config["invalid"]:
        logger.warning("Неверные значения переменных: %s. Использованы значения по умолчанию",
                       ", ".join(config["invalid"]))


class RateLimiter:
    def __init__(self, config, clock=time.monotonic):
        self.rates = {"write": (config["WRITE_RATE_PER_SEC"], config["WRITE_RATE_BURST"]),
                      "read": (config["READ_RATE_PER_SEC"], config["READ_RATE_BURST"]),
                      "live": (config["LIVE_RATE_PER_SEC"], config["LIVE_RATE_BURST"])}
        self.clock = clock
        self.buckets = OrderedDict()   # (telegram_id, группа) -> [токены, время последнего обращения]
        self.lock = threading.Lock()

    def _purge(self, now):
        # записи упорядочены по последнему обращению: старые лежат в начале
        while self.buckets:
            key = next(iter(self.buckets))
            if now - self.buckets[key][1] < IDLE_SECONDS:
                break
            del self.buckets[key]

    def check(self, telegram_id, group):
        """None, если запрос разрешён, иначе число секунд до повтора (целое, не меньше 1)."""
        rate, burst = self.rates[group]
        now = self.clock()
        key = (telegram_id, group)
        with self.lock:
            self._purge(now)
            bucket = self.buckets.get(key)
            if bucket is None:
                while len(self.buckets) >= MAX_BUCKETS:
                    self.buckets.popitem(last=False)
                bucket = [float(burst), now]
                self.buckets[key] = bucket
            tokens = min(float(burst), bucket[0] + max(0.0, now - bucket[1]) * rate)
            bucket[1] = now
            self.buckets.move_to_end(key)
            if tokens >= 1.0:
                bucket[0] = tokens - 1.0
                return None
            bucket[0] = tokens
            return max(1, math.ceil((1.0 - tokens) / rate))

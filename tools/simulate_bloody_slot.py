"""Симулятор RTP Bloody slot (этап 4): гоняет настоящий движок (bot/bloody_round.py, те же правила и веса, что на сервере) для каждого режима:
основная игра и покупки (Виселица, Reign of Terror Spins, Ритуальный буст xBet 1 и xBet 2). Для каждого режима печатает RTP (выплата / цена), стандартную
ошибку и доверительный интервал, частоту выигрыша, частоту бонусов, распределение выплат и сколько раз упёрлись в потолок 16161×.

Запуск: python tools/simulate_bloody_slot.py [--rounds N] [--modes base,gibbet,reign,xbet1,xbet2] [--seed S] [--workers K] [--json файл]
По умолчанию 5 000 000 раундов на режим, все ядра процессора. Цифры не выдуманные: это прогон движка; для быстрой подгонки весов хватает --rounds 200000."""
import argparse
import json
import math
import multiprocessing
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "bot"))

import bloody_config as cfg  # noqa: E402
import bloody_round as br  # noqa: E402

MODES = ("base", "gibbet", "reign", "xbet1", "xbet2", "lucky")
TARGET_RTP = {"base": 96.0, "gibbet": 96.0, "reign": 96.0, "xbet1": 96.0, "xbet2": 96.0, "lucky": 96.0}   # решение владельца: RTP 95-97% (в процентах)
RANGE = (95.0, 97.0)                     # допустимый диапазон RTP (решение владельца)
BUCKETS = (0, 1, 2, 5, 10, 50, 100, 500, 1000, 5000)     # границы выплат в ставках
CHUNK = 20000


def cost_units(mode):
    return cfg.UNITS_PER_BET if mode == "base" else cfg.BUY_COST_UNITS[mode]


def run_chunk(args):
    mode, n, seed = args
    br.set_logging(False)
    rng = random.Random(seed)
    buy = None if mode == "base" else mode
    cost = cost_units(mode)
    agg = {"n": 0, "win": 0, "win_sq": 0.0, "hits": 0, "bonus": 0, "candle": 0, "reign": 0, "spins": 0, "capped": 0, "max": 0, "w100": 0, "buckets": [0] * (len(BUCKETS) + 1)}
    for _ in range(n):
        rnd = br.new_round(buy, rng)
        while not rnd.finished:
            br.play_spin(rnd, rng)
        agg["n"] += 1
        agg["win"] += rnd.units
        w = rnd.units / cost
        agg["win_sq"] += w * w
        agg["spins"] += rnd.spins
        if rnd.units > 0:
            agg["hits"] += 1
        if rnd.spins > 1:
            agg["bonus"] += 1
        if rnd.reign_started and mode != "reign":
            agg["reign"] += 1
        elif rnd.spins > 1 and mode != "reign":
            agg["candle"] += 1
        if rnd.capped:
            agg["capped"] += 1
        if rnd.units >= 100 * cfg.UNITS_PER_BET:
            agg["w100"] += 1
        agg["max"] = max(agg["max"], rnd.units)
        x = rnd.units / cfg.UNITS_PER_BET          # выплата в ставках
        i = 0
        while i < len(BUCKETS) and x >= BUCKETS[i] and not (i == 0 and x == 0):
            i += 1
        agg["buckets"][i if x > 0 else 0] += 1
    return mode, agg


def merge(parts):
    out = {"n": 0, "win": 0, "win_sq": 0.0, "hits": 0, "bonus": 0, "candle": 0, "reign": 0, "spins": 0, "capped": 0, "max": 0, "w100": 0, "buckets": [0] * (len(BUCKETS) + 1)}
    for a in parts:
        for k in ("n", "win", "win_sq", "hits", "bonus", "candle", "reign", "spins", "capped", "w100"):
            out[k] += a[k]
        out["max"] = max(out["max"], a["max"])
        out["buckets"] = [x + y for x, y in zip(out["buckets"], a["buckets"])]
    return out


def summarize(mode, a):
    n = a["n"]
    cost = cost_units(mode)
    rtp = a["win"] / (n * cost)
    var = max(0.0, a["win_sq"] / n - rtp * rtp)
    se = math.sqrt(var / n)
    return {"mode": mode, "rounds": n, "cost_bets": cost / cfg.UNITS_PER_BET, "rtp_pct": round(rtp * 100, 3), "se_pct": round(se * 100, 3),
            "ci95_pct": [round((rtp - 1.96 * se) * 100, 3), round((rtp + 1.96 * se) * 100, 3)], "hit_rate_pct": round(a["hits"] / n * 100, 3),
            "bonus_rounds_per_1": round(n / a["bonus"], 1) if a["bonus"] else None, "candle_rounds_per_1": round(n / a["candle"], 1) if a["candle"] else None,
            "reign_rounds_per_1": round(n / a["reign"], 1) if a["reign"] else None, "avg_spins": round(a["spins"] / n, 3),
            "capped": a["capped"], "win100_per_1": round(n / a["w100"], 1) if a["w100"] else None, "max_win_x_bet": round(a["max"] / cfg.UNITS_PER_BET, 2), "buckets": a["buckets"]}


def bucket_labels():
    labels = ["0"]
    prev = BUCKETS[0]
    for b in BUCKETS[1:]:
        labels.append("%g-%g" % (prev, b))
        prev = b
    labels.append(">=%g" % BUCKETS[-1])
    return labels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=5_000_000)
    ap.add_argument("--modes", default=",".join(MODES))
    ap.add_argument("--seed", type=int, default=20261015)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    modes = [m for m in args.modes.split(",") if m]
    for m in modes:
        if m not in MODES:
            sys.exit("неизвестный режим: " + m)
    results = []
    t0 = time.time()
    with multiprocessing.Pool(args.workers) as pool:
        for mode in modes:
            tasks = []
            left = args.rounds
            i = 0
            while left > 0:
                n = min(CHUNK, left)
                tasks.append((mode, n, "%s-%s-%d" % (args.seed, mode, i)))
                left -= n
                i += 1
            parts = [a for _, a in pool.imap_unordered(run_chunk, tasks)]
            results.append(summarize(mode, merge(parts)))
            r = results[-1]
            ok = RANGE[0] <= r["rtp_pct"] <= RANGE[1]
            print("%-7s RTP %.3f%% (±%.3f, 95%% ДИ %s)  цель %.2f%%  %s  | выигрыш в %.2f%% раундов, бонус 1 на %s, Reign 1 на %s, ×100 и больше 1 на %s, потолок %d раз, макс ×%s, %.0f с" % (
                mode, r["rtp_pct"], r["se_pct"], r["ci95_pct"], TARGET_RTP[mode], "в диапазоне" if ok else "ВНЕ ДИАПАЗОНА", r["hit_rate_pct"], r["bonus_rounds_per_1"],
                r["reign_rounds_per_1"], r["win100_per_1"], r["capped"], r["max_win_x_bet"], time.time() - t0), flush=True)
    print("\nРаспределение выплат (число раундов по ставкам выигрыша): " + " | ".join(bucket_labels()))
    for r in results:
        print("%-7s %s" % (r["mode"], " | ".join(str(x) for x in r["buckets"])))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"rounds_per_mode": args.rounds, "seed": args.seed, "target_rtp_pct": TARGET_RTP, "results": results, "bucket_labels": bucket_labels()}, f, ensure_ascii=False, indent=1)
    return 0 if all(RANGE[0] <= r["rtp_pct"] <= RANGE[1] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())

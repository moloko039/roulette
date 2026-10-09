"""Нагрузочный замер живого краша (этап 5 docs/CRASH_LIVE.md).

Поднимает настоящий сервер так же, как на Railway (uvicorn api:create_app_from_env --factory, один процесс, без access-лога) на временной базе,
затем запускает N виртуальных игроков. Каждый: открывает /api/me, опрашивает GET /api/crash/live раз в POLL_S секунд (как клиент, с токеном изменений v),
в приёме ставок ставит с вероятностью BET_SHARE (часть с автовыводом), в полёте выводит вручную. Часть игроков сидит в беседах (комнаты по GROUP_SIZE человек),
остальные вне беседы (общая анонимная комната). Печатает задержки (медиана, p95, p99, максимум) по видам запросов, коды ответов и загрузку процесса сервера.

Запуск: bot/.venv/bin/python tools/crash_load.py --players 200 --seconds 60 [--poll 1.0] [--bet-share 0.7] [--group-size 20] [--private-share 0.3]
Всё локально; боевых данных и ключей не использует (токен бота тестовый)."""
import argparse
import asyncio
import os
import random
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BOT = os.path.join(os.path.dirname(HERE), "bot")
sys.path.insert(0, BOT)

import httpx  # noqa: E402

from tg_testutil import make_init_data  # noqa: E402

T0 = time.time()
TOKEN = "123456:LOAD-TEST-TOKEN-not-real"
PORT = 8765


def start_server(tmp, journal):
    env = {k: v for k, v in os.environ.items() if k not in ("BOT_TOKEN", "DB_PATH", "ALLOWED_ORIGINS", "PUBLIC_URL", "LOCAL_POLLING")}
    env.update({"BOT_TOKEN": TOKEN, "DB_PATH": os.path.join(tmp, "load.db"), "ALLOWED_ORIGINS": "https://example.invalid", "PYTHONPATH": BOT, "PYTHONUNBUFFERED": "1",
                "PLAY_MODE": "api", "SQLITE_JOURNAL_MODE": journal, "TOMBSTONE_SECRET": "load-secret", "MEMBER_REF_SECRET": "load-ref-secret", "OWNER_CHAT_ID": "1"})
    boot = "import faulthandler, signal; faulthandler.register(signal.SIGUSR1, all_threads=True); import dotenv, sys; dotenv.load_dotenv = lambda *a, **k: False; import uvicorn; sys.argv = ['uvicorn'] + sys.argv[1:]; uvicorn.main()"
    args = ["api:create_app_from_env", "--factory", "--host", "127.0.0.1", "--port", str(PORT), "--no-access-log", "--no-server-header"]
    logfile = open(os.path.join(tmp, "server.log"), "w")      # не канал: непрочитанный канал заполняется, и сервер блокируется на записи журнала
    proc = subprocess.Popen([sys.executable, "-c", boot] + args, cwd=tmp, env=env, stdout=logfile, stderr=subprocess.STDOUT, universal_newlines=True)
    for _ in range(150):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=1).read()
            return proc, env["DB_PATH"]
        except Exception:
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("сервер не поднялся: " + open(os.path.join(tmp, "server.log")).read()[-400:])


def seed_players(db_path, n):
    conn = sqlite3.connect(db_path)
    now = int(time.time())
    for uid in range(1, n + 1):
        conn.execute("INSERT OR IGNORE INTO players (telegram_id, balance, rate, last_accrual, created_at) VALUES (?, 1000000000, 100, ?, ?)", (uid, now + 3600, now))
    conn.commit()
    conn.close()


def cpu_rss(pid):
    out = subprocess.run(["ps", "-o", "%cpu=,rss=", "-p", str(pid)], capture_output=True, text=True).stdout.split()
    return (float(out[0]), int(out[1]) // 1024) if len(out) == 2 else (0.0, 0)


class Stats:
    def __init__(self, count_from=0):
        self.lat = {}
        self.codes = {}
        self.count_from = count_from      # до этого момента (набор игроков) запросы живого краша в статистику не идут

    def add(self, kind, seconds, code):
        if kind != "me" and time.time() < self.count_from:
            return
        self.lat.setdefault(kind, []).append(seconds * 1000)
        self.codes.setdefault(kind, {})
        self.codes[kind][code] = self.codes[kind].get(code, 0) + 1


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p))]


async def player(client, stats, uid, chat, args, stop_at):
    kw = {"user_id": uid, "auth_date": int(time.time()), "first_name": "P%d" % uid}
    if chat is not None:
        kw.update(chat_type="supergroup", chat_instance=chat)
    headers = {"Authorization": "tma " + make_init_data(TOKEN, **kw)}
    rng = random.Random(uid)

    async def call(kind, method, url, **k):
        t = time.perf_counter()
        try:
            r = await client.request(method, url, headers=headers, timeout=15, **k)
            stats.add(kind, time.perf_counter() - t, r.status_code)
            return r
        except Exception as exc:
            stats.add(kind, time.perf_counter() - t, type(exc).__name__ + (':' + str(exc)[:40] if str(exc) else ''))
            return None

    await asyncio.sleep(rng.random() * args.ramp)      # игроки приходят не одновременно (всплеск «600 открыли приложение в одну секунду» не живой случай)
    await call("me", "GET", "/api/me")
    token, bet_round, cashed_round = None, None, None
    n = 0
    while time.time() < stop_at:
        r = await call("live", "GET", "/api/crash/live" + ("?v=" + token if token else ""))
        data = r.json() if r is not None and r.status_code == 200 else {}
        if data.get("v"):
            token = data["v"]
        rnd = data.get("round") or {}
        phase, rid_ = rnd.get("phase"), rnd.get("id")
        if phase == "betting" and rid_ is not None and bet_round != rid_ and rng.random() < args.bet_share:
            bet_round = rid_
            n += 1
            body = {"request_id": "load-%08d-%06d" % (uid, n), "bet": rng.choice((10, 50, 100, 500))}
            if rng.random() < 0.5:
                body["target_x100"] = rng.choice((120, 150, 200, 300, 500))
            await call("bet", "POST", "/api/crash/live/bet", json=body)
        elif phase == "flight" and bet_round == rid_ and cashed_round != rid_ and rng.random() < 0.25:
            cashed_round = rid_
            n += 1
            await call("cashout", "POST", "/api/crash/live/cashout", json={"request_id": "load-%08d-%06d" % (uid, n)})
        await asyncio.sleep(max(0.05, args.poll + rng.uniform(-0.05, 0.05)))


async def main_async(args, pid):
    stats = Stats(time.time() + args.ramp)
    limits = httpx.Limits(max_connections=args.players + 20, max_keepalive_connections=args.players + 20)
    stop_at = time.time() + args.seconds
    async with httpx.AsyncClient(base_url="http://127.0.0.1:%d" % PORT, limits=limits) as client:
        tasks = []
        for uid in range(args.first_uid, args.first_uid + args.players):
            private = (uid - args.first_uid) < int(args.players * args.private_share)
            chat = None if private else "chat-%d" % ((uid - 1) // args.group_size)
            tasks.append(asyncio.create_task(player(client, stats, uid, chat, args, stop_at)))
        samples = []
        dumped = False
        while time.time() < stop_at:
            await asyncio.sleep(2)
            if pid:
                samples.append(cpu_rss(pid))
            if args.dump_at and not dumped and time.time() - T0 > args.dump_at:
                dumped = True
                os.kill(pid, __import__("signal").SIGUSR1)         # SIGUSR1: стеки потоков сервера в его журнал
        await asyncio.gather(*tasks)
    return stats, samples


def report(args, stats, samples, elapsed):
    total = sum(len(v) for v in stats.lat.values())
    print("\nИгроков: %d, журнал SQLite: %s, время: %d с, запросов: %d (%.0f в секунду)" % (args.players, args.journal, elapsed, total, total / elapsed))
    print("%-8s %7s %8s %8s %8s %8s  коды" % ("вид", "всего", "медиана", "p95", "p99", "макс"))
    for kind in ("live", "bet", "cashout", "me"):
        v = stats.lat.get(kind)
        if v:
            print("%-8s %7d %6.0f мс %5.0f мс %5.0f мс %5.0f мс  %s" % (kind, len(v), statistics.median(v), pct(v, 0.95), pct(v, 0.99), max(v), stats.codes[kind]))
    if samples:
        print("сервер: загрузка процессора в среднем %.0f %% (макс %.0f %%), память до %d МБ" % (statistics.mean(s[0] for s in samples), max(s[0] for s in samples), max(s[1] for s in samples)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", type=int, default=100)
    ap.add_argument("--seconds", type=int, default=60)
    ap.add_argument("--poll", type=float, default=1.0)
    ap.add_argument("--bet-share", type=float, default=0.7)
    ap.add_argument("--group-size", type=int, default=20)
    ap.add_argument("--dump-at", type=float, default=0, help="через сколько секунд от старта снять стеки потоков сервера (поиск зависаний)")
    ap.add_argument("--clients", type=int, default=1, help="сколько процессов-клиентов (один процесс Python сам упирается примерно в 300 запросов в секунду)")
    ap.add_argument("--journal", default="wal", choices=("wal", "delete"), help="режим журнала SQLite сервера (на Railway включается переменной SQLITE_JOURNAL_MODE=wal)")
    ap.add_argument("--ramp", type=float, default=15, help="за сколько секунд подключаются игроки; запросы краша за это время в статистику не идут")
    ap.add_argument("--first-uid", type=int, default=1)
    ap.add_argument("--client-only", action="store_true", help="служебный режим: только игроки, без запуска сервера; печатает результат в JSON")
    ap.add_argument("--private-share", type=float, default=0.3)
    args = ap.parse_args()
    if args.client_only:
        stats, _ = asyncio.run(main_async(args, None))
        import json
        print(json.dumps({"lat": stats.lat, "codes": {k: {str(c): n for c, n in v.items()} for k, v in stats.codes.items()}}))
        return
    tmp = tempfile.mkdtemp(prefix="crash-load-")
    proc, db_path = start_server(tmp, args.journal)
    try:
        seed_players(db_path, args.players)
        t = time.time()
        import json
        per = args.players // args.clients
        procs = []
        for i in range(args.clients):
            n = per if i < args.clients - 1 else args.players - per * (args.clients - 1)
            cmd = [sys.executable, os.path.abspath(__file__), "--client-only", "--players", str(n), "--first-uid", str(1 + i * per), "--seconds", str(args.seconds),
                   "--poll", str(args.poll), "--ramp", str(args.ramp), "--bet-share", str(args.bet_share), "--group-size", str(args.group_size), "--private-share", str(args.private_share)]
            procs.append(subprocess.Popen(cmd, stdout=open(os.path.join(tmp, "client%d.json" % i), "w"), text=True))      # в файл, не в канал: большой вывод заблокировал бы клиента
        samples = []
        while any(p.poll() is None for p in procs):
            time.sleep(2)
            samples.append(cpu_rss(proc.pid))
        stats = Stats()
        for i in range(len(procs)):
            part = json.loads(open(os.path.join(tmp, "client%d.json" % i)).read().strip().splitlines()[-1])
            for kind, lat in part["lat"].items():
                stats.lat.setdefault(kind, []).extend(lat)
            for kind, codes in part["codes"].items():
                d = stats.codes.setdefault(kind, {})
                for c, n in codes.items():
                    d[c] = d.get(c, 0) + n
        report(args, stats, samples, time.time() - t)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        log = open(os.path.join(tmp, "server.log"), encoding="utf-8", errors="replace").read()
        errs = [ln for ln in (log or "").splitlines() if re.search(r"Traceback|ERROR|database is locked", ln)]
        if args.dump_at:
            open(os.path.join(tempfile.gettempdir(), "crash_load_server.log"), "w").write(log or "")
            print("журнал сервера: " + os.path.join(tempfile.gettempdir(), "crash_load_server.log"))
        print("ошибок в журнале сервера: %d%s" % (len(errs), (" (первые: %s)" % errs[:3]) if errs else ""))
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

"""Харнесс e2e: настоящий сервер FastAPI + безголовый Chrome по протоколу DevTools (CDP). Боевые файлы не меняются:
клиент копируется во временную папку, адрес API и заглушка Telegram подставляются в копии. Все секреты и ID генерируются здесь случайно."""
import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import os
import platform
import random
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

try:
    import websockets
except ImportError:   # понятное сообщение вместо трейсбэка
    websockets = None

E2E = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(E2E)
BOT = os.path.join(ROOT, "bot")
CLIENT_ROOT = ROOT      # откуда берутся index.html, style.css, script.js, fonts (run_e2e.py --client-dir подменяет: проверка «ловит ли набор поломку»)
# Допустимые сообщения консоли (все остальные ошибки и предупреждения считаются падением). Сейчас допустимых нет.
ALLOWED_CONSOLE = ()


class E2EError(AssertionError):
    pass


def find_chrome():
    """Путь к Chrome/Chromium или None. Порядок: стандартные места macOS, Linux, Windows, образы Playwright, PATH."""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for pattern in ("/opt/pw-browsers/chromium*/chrome-linux/chrome", "/opt/pw-browsers/chromium*/chrome-linux64/chrome"):
        import glob
        candidates += sorted(glob.glob(pattern))
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def sign_init_data(token, user_id, name, chat_instance, auth_date):
    """Подписанный initData по алгоритму Telegram (тестовый токен харнесса)."""
    fields = {"auth_date": str(auth_date), "query_id": "AAH-e2e", "signature": "e2e-signature", "chat_type": "group",
              "chat_instance": chat_instance, "user": json.dumps({"id": user_id, "first_name": name}, ensure_ascii=False, separators=(",", ":"))}
    check = "\n".join("%s=%s" % (k, fields[k]) for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


# ---------------------------------------------------------------- клиент во временной папке
STUB = """<script>
window.__errs = []; window.__log = [];
window.Telegram = {WebApp: {initData: (function(){ try { return localStorage.getItem('__init') || ''; } catch (e) { return ''; } })(),
  initDataUnsafe: {user: {first_name: 'Игрок'}}, ready(){}, expand(){}, isVersionAtLeast(){return true}, disableVerticalSwipes(){},
  setHeaderColor(){}, setBackgroundColor(){}, setBottomBarColor(){}, HapticFeedback: {impactOccurred(){}, notificationOccurred(){}}}};
const _fetch = window.fetch.bind(window);
window.fetch = async (u, o) => {
  const rec = {method: (o && o.method) || 'GET', path: String(u).replace(__API__, '')};
  window.__log.push(rec);
  try { const res = await _fetch(u, o); rec.status = res.status; return res; } catch (e) { rec.err = String(e); throw e; }
};
</script>"""


def build_client(dst, api_url):
    """Копия клиента с подставленным адресом API и заглушкой Telegram; файлы репозитория не меняются."""
    for name in ("index.html", "style.css", "script.js"):
        shutil.copy(os.path.join(CLIENT_ROOT, name), dst)
    shutil.copytree(os.path.join(CLIENT_ROOT, "fonts"), os.path.join(dst, "fonts"), dirs_exist_ok=True)
    js = open(os.path.join(dst, "script.js"), encoding="utf-8").read()
    js, n = re.subn(r"const API_URL = '[^']*';", "const API_URL = '%s';" % api_url, js, count=1)
    if n != 1:
        raise E2EError("в script.js не найден const API_URL")
    open(os.path.join(dst, "script.js"), "w", encoding="utf-8").write(js)
    html = open(os.path.join(dst, "index.html"), encoding="utf-8").read()
    tag = '<script src="https://telegram.org/js/telegram-web-app.js"></script>'
    if tag not in html:
        raise E2EError("в index.html не найден скрипт Telegram WebApp")
    open(os.path.join(dst, "index.html"), "w", encoding="utf-8").write(html.replace(tag, STUB.replace("__API__", json.dumps(api_url))))


class StaticServer:
    """Раздача клиента из временной папки на свободном порту (без кэша, favicon без ошибки)."""

    def __init__(self, directory):
        class Handler(SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=directory, **k)

            def log_message(self, *a):
                pass

            def end_headers(self):
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

            def do_GET(self):
                if self.path.startswith("/favicon.ico"):
                    self.send_response(204)
                    self.end_headers()
                    return
                super().do_GET()
        self.port = free_port()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


# ---------------------------------------------------------------- сервер
class Server:
    """Настоящий сервер во временной базе. script(**очереди) подставляет случайные числа игр, offset(сек) двигает серверные часы."""

    def __init__(self, tmp, origin, users, owner_id, chat):
        self.tmp = tmp
        self.port = free_port()
        self.token = "%d:%s" % (random.randint(10 ** 8, 10 ** 9), secrets.token_urlsafe(20)[:20])
        self.offset_file = os.path.join(tmp, "offset.txt")
        self.script_file = os.path.join(tmp, "script.json")
        open(self.offset_file, "w").write("0")
        open(self.script_file, "w").write("{}")
        cfg = {"bot_dir": BOT, "port": self.port, "origin": origin, "db": os.path.join(tmp, "e2e.db"), "token": self.token,
               "secret": secrets.token_hex(16), "offset_file": self.offset_file, "script_file": self.script_file,
               "owner_id": owner_id, "chat": chat, "users": users}
        cfg_path = os.path.join(tmp, "server.json")
        json.dump(cfg, open(cfg_path, "w", encoding="utf-8"))
        self.log_path = os.path.join(tmp, "server.log")
        self.proc = subprocess.Popen([sys.executable, os.path.join(E2E, "server_boot.py"), cfg_path], stdout=open(self.log_path, "w"),
                                     stderr=subprocess.STDOUT, cwd=tmp)
        self.url = "http://127.0.0.1:%d" % self.port
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise E2EError("сервер не запустился:\n" + open(self.log_path, encoding="utf-8").read()[-800:])
            try:
                urllib.request.urlopen(self.url + "/health", timeout=1).read()
                return
            except OSError:
                time.sleep(0.1)
        raise E2EError("сервер не ответил за 30 с")

    def script(self, **queues):
        """Дописывает значения в очереди (каждый вызов игры берёт одно): spin [число], crash [x100], hilo [[достоинство, масть]],
        keno [[10 чисел]], mines [[клетки с минами]], shoe [[карты сверху колоды]]."""
        data = json.load(open(self.script_file, encoding="utf-8"))
        for key, values in queues.items():
            data[key] = list(data.get(key) or []) + list(values)
        json.dump(data, open(self.script_file, "w", encoding="utf-8"))

    def offset(self, seconds):
        open(self.offset_file, "w").write(str(seconds))

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


# ---------------------------------------------------------------- Chrome и страница (CDP)
class Chrome:
    def __init__(self, path, tmp):
        self.port = free_port()
        args = [path, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=%d" % self.port,
                "--user-data-dir=" + os.path.join(tmp, "chrome-profile"), "--window-size=500,900", "about:blank"]
        if platform.system() == "Linux":
            args.insert(1, "--no-sandbox")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/json/version" % self.port, timeout=1).read()
                return
            except OSError:
                time.sleep(0.1)
        raise E2EError("Chrome не ответил по DevTools за 30 с")

    def new_page_ws(self):
        req = urllib.request.Request("http://127.0.0.1:%d/json/new?about:blank" % self.port, method="PUT")
        return json.loads(urllib.request.urlopen(req, timeout=10).read())["webSocketDebuggerUrl"]

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            self.proc.kill()


HELPERS = r"""
(() => {
  const vv = new EventTarget();
  // высота видимой области всегда считается от текущего innerHeight (мобильная эмуляция применяется после создания документа)
  const E = window.E = {};
  E.kbHeight = 0;
  Object.defineProperty(vv, 'height', {get: () => window.innerHeight - E.kbHeight});
  Object.defineProperty(vv, 'width', {get: () => window.innerWidth});
  vv.offsetTop = 0; vv.offsetLeft = 0; vv.scale = 1; vv.pageTop = 0;
  Object.defineProperty(window, 'visualViewport', {value: vv, configurable: true});
  E.kb = (h) => { E.kbHeight = h; vv.dispatchEvent(new Event('resize')); };
  E.kbOpen = false;
  // «клавиатура» как на телефоне: открывается при фокусе поля, закрывается при потере фокуса
  document.addEventListener('focusin', (e) => { if (e.target.tagName === 'INPUT') { E.kbOpen = true; setTimeout(() => E.kb(260), 30); } });
  document.addEventListener('focusout', (e) => { if (e.target.tagName === 'INPUT') { E.kbOpen = false; setTimeout(() => E.kb(0), 30); } });
  E.sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  E.waitFor = async (fn, ms) => { const t0 = performance.now(); while (performance.now() - t0 < ms) { try { if (fn()) return true; } catch (e) { /* ещё нет */ } await E.sleep(25); } return false; };
  E.frames = (n) => new Promise((r) => { let i = 0; const tick = () => (++i >= n ? r() : requestAnimationFrame(tick)); requestAnimationFrame(tick); });
  E.q = (id) => document.getElementById(id);
  E.hs = () => document.documentElement.scrollWidth - document.documentElement.clientWidth;
  E.overflow = () => [...document.querySelectorAll('.screen:not([hidden]) *, .transfer-sheet:not([hidden]) *')].filter((e) => {
    const r = e.getBoundingClientRect(); return r.width > 0 && (r.right > window.innerWidth + 0.5 || r.left < -0.5)
      && !e.closest('.history-list, .nav, .game-menu, .stats-scroll'); }).slice(0, 5).map((e) => e.tagName + '.' + e.className);
  E.rect = (sel) => { const e = document.querySelector(sel); if (!e || e.hidden) return null; const r = e.getBoundingClientRect();
    return {t: Math.round(r.top), b: Math.round(r.bottom), l: Math.round(r.left), r: Math.round(r.right)}; };
  E.center = (sel) => { const e = document.querySelector(sel); const r = e.getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2}; };
  E.clicks = []; document.addEventListener('click', (e) => E.clicks.push(e.target.id || String(e.target.className || e.target.tagName)), true);
  E.vis = 'visible';
  Object.defineProperty(document, 'visibilityState', {get: () => E.vis, configurable: true});
  E.setVis = (v) => { E.vis = v; document.dispatchEvent(new Event('visibilitychange')); };
  E.meCount = () => (window.__log || []).filter((r) => r.path.startsWith('/api/me') && r.method === 'GET').length;
  E.count = (path) => (window.__log || []).filter((r) => r.path.startsWith(path)).length;
  E.pops = []; document.addEventListener('DOMContentLoaded', () => new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach((n) => {
    if (n.classList && n.classList.contains('accrual-pop')) E.pops.push(n.textContent); }))).observe(document.body, {childList: true}));
})();
"""


class Page:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self.n = 0
        self.pending = {}
        self.problems = []    # ошибки и предупреждения консоли
        self.allowed = list(ALLOWED_CONSOLE)
        self.shots = None
        self.net = []         # запросы клиента к API по порядку: (метод, адрес, тело); переживает перезагрузку страницы

    async def open(self):
        self.ws = await websockets.connect(self.ws_url, max_size=2 ** 27)
        self.reader_task = asyncio.ensure_future(self._reader())
        for method in ("Runtime.enable", "Page.enable", "Log.enable", "Network.enable"):
            await self.send(method)
        await self.send("Network.setCacheDisabled", {"cacheDisabled": True})
        await self.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        await self.send("Page.addScriptToEvaluateOnNewDocument", {"source": HELPERS})

    async def _reader(self):
        async for raw in self.ws:
            m = json.loads(raw)
            if "id" in m and m["id"] in self.pending:
                self.pending.pop(m["id"]).set_result(m)
                continue
            ev, p = m.get("method"), m.get("params", {})
            if ev == "Network.requestWillBeSent" and p["request"]["method"] != "OPTIONS":
                self.net.append((p["request"]["method"], p["request"]["url"], p["request"].get("postData")))
            if ev == "Runtime.consoleAPICalled" and p["type"] in ("error", "warning", "assert"):
                self._problem("console." + p["type"], " ".join(str(a.get("value", a.get("description", ""))) for a in p["args"]))
            elif ev == "Runtime.exceptionThrown":
                d = p["exceptionDetails"]
                self._problem("exception", (d.get("exception") or {}).get("description") or d.get("text", ""))
            elif ev == "Log.entryAdded" and p["entry"]["level"] in ("error", "warning"):
                self._problem("log." + p["entry"]["level"], p["entry"]["text"] + " " + p["entry"].get("url", ""))

    def _problem(self, kind, text):
        if not any(re.search(pat, text) for pat in self.allowed):
            self.problems.append("%s: %s" % (kind, text[:300]))

    async def send(self, method, params=None):
        self.n += 1
        fut = asyncio.get_event_loop().create_future()
        self.pending[self.n] = fut
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params or {}}))
        resp = await fut
        if "error" in resp:
            raise E2EError("CDP %s: %s" % (method, resp["error"]))
        return resp.get("result", {})

    async def ev(self, expr):
        res = await self.send("Runtime.evaluate", {"expression": expr, "awaitPromise": True, "returnByValue": True})
        if "exceptionDetails" in res:
            d = res["exceptionDetails"]
            raise E2EError("ошибка в странице: %s | %s" % ((d.get("exception") or {}).get("description", d.get("text")), expr[:160]))
        return res["result"].get("value")

    async def wait(self, cond, timeout=10, what=None):
        """Ждёт, пока JS-выражение cond станет истинным (опрос раз в 25 мс, без фиксированных пауз). Таймаут: E2EError."""
        ok = await self.ev("E.waitFor(() => (%s), %d)" % (cond, int(timeout * 1000)))
        if not ok:
            raise E2EError("не дождались: %s (%s)" % (what or cond, cond[:140]))

    async def settle(self, frames=2):
        await self.ev("E.frames(%d)" % frames)

    async def goto(self, url):
        await self.send("Page.navigate", {"url": url})
        await self.wait("document.readyState === 'complete' && window.E && document.querySelector('.screen')", 20, "загрузка страницы")

    async def viewport(self, w, h):
        await self.send("Emulation.setDeviceMetricsOverride", {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": True})
        await self.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 5})

    async def center(self, sel):
        """Центр элемента после прокрутки его в видимую область (как делает пользователь: часть панелей лежит под навигацией)."""
        q = json.dumps(sel)
        await self.wait("(() => { const e = document.querySelector(%s); return !!e && !e.disabled && e.getBoundingClientRect().width > 0; })()" % q, 15,
                        "элемент виден и доступен: " + sel)
        await self.ev("document.querySelector(%s).scrollIntoView({block: 'nearest', inline: 'nearest', behavior: 'instant'})" % q)
        await self.settle()
        return json.loads(await self.ev("JSON.stringify(E.center(%s))" % json.dumps(sel)))

    async def tap(self, target, hold=0.05):
        """Настоящее касание по селектору или точке {x, y}: touchStart, короткая пауза, touchEnd, затем два кадра."""
        p = target if isinstance(target, dict) else await self.center(target)
        await self.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [p]})
        await asyncio.sleep(hold)
        await self.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        await self.settle()

    async def mouse(self, kind, p):
        await self.send("Input.dispatchMouseEvent", {"type": kind, "x": p["x"], "y": p["y"], "button": "left",
                                                      "buttons": 0 if kind == "mouseReleased" else 1, "clickCount": 1})

    async def drag(self, a, b):
        """Нажать мышью в точке a и отпустить в точке b (клик приходит на общего предка, если цели разные)."""
        await self.mouse("mouseMoved", a)
        await self.mouse("mousePressed", a)
        await self.mouse("mouseMoved", b)
        await self.mouse("mouseReleased", b)
        await self.settle()

    async def key(self, key, code, vk):
        for kind in ("keyDown", "keyUp"):
            await self.send("Input.dispatchKeyEvent", {"type": kind, "key": key, "code": code, "windowsVirtualKeyCode": vk,
                                                       "text": "\r" if kind == "keyDown" and key == "Enter" else ""})
        await self.settle()

    async def screenshot(self, path):
        r = await self.send("Page.captureScreenshot", {"format": "png"})
        open(path, "wb").write(base64.b64decode(r["data"]))

    async def close(self):
        self.reader_task.cancel()
        await self.ws.close()


# ---------------------------------------------------------------- мир сценария
class User:
    def __init__(self, name, **kw):
        self.name = name
        self.id = 500_000_000_000 + random.randint(0, 10 ** 9)
        self.balance = kw.get("balance", 100_000)
        self.rate = kw.get("rate", 100)
        self.xp = kw.get("xp", 3000)             # уровень 3 и выше
        self.staked = kw.get("staked", 20_000)
        self.age_days = kw.get("age_days", 5)


class World:
    """Сервер, клиент, Chrome-страница и пользователи одного сценария. users: имя -> параметры (первый пользователь «me»)."""

    def __init__(self, chrome, users=None, viewport=(390, 700), clock_mod=None):
        self.clock_mod = clock_mod      # если задано: серверные часы сдвигаются так, что секунда минуты равна этому числу
        self.tmp = tempfile.mkdtemp(prefix="e2e-")
        self.chrome = chrome
        self.users = self._users(users)
        self.owner = User("owner", balance=1000, xp=3000, staked=0)
        self.chat = "room-" + secrets.token_hex(4)
        self.viewport = viewport
        self.page = None
        self.server = None
        self.static = None

    @staticmethod
    def _users(users):
        spec = dict(users or {"me": {}})
        spec.setdefault("me", {})
        spec.setdefault("bob", {"balance": 5000, "age_days": 5})
        return {n: User(n, **kw) for n, kw in spec.items()}

    async def start(self):
        self.site = os.path.join(self.tmp, "site")
        os.makedirs(self.site)
        self.static = StaticServer(self.site)
        self.origin = "http://127.0.0.1:%d" % self.static.port
        self._start_server()
        self.page = Page(self.chrome.new_page_ws())
        await self.page.open()
        await self.page.viewport(*self.viewport)
        await self.login("me")

    def _start_server(self):
        rows = [{"id": u.id, "name": u.name, "balance": u.balance, "rate": u.rate, "age_days": u.age_days, "staked": u.staked, "xp": u.xp} for u in list(self.users.values()) + [self.owner]]
        self.server = Server(self.tmp, self.origin, rows, self.owner.id, self.chat)
        build_client(self.site, self.server.url)

    async def reseed(self, users):
        """Новый сервер с другими пользователями (чистая база), клиент открывается заново от имени «me»."""
        self.server.stop()
        shutil.rmtree(os.path.join(self.tmp, "e2e.db"), ignore_errors=True)
        for name in os.listdir(self.tmp):
            if name.startswith("e2e.db") or name in ("script.json", "offset.txt", "server.json"):
                os.remove(os.path.join(self.tmp, name))
        self.users = self._users(users)
        self._start_server()
        await self.login("me")

    def init_data(self, who):
        u = self.users.get(who) or self.owner
        return sign_init_data(self.server.token, u.id, u.name, self.chat, int(time.time()))

    async def login(self, who):
        """Открывает клиент от имени пользователя who: initData кладётся в localStorage на пустой странице того же адреса."""
        open(os.path.join(self.site, "e2e-blank.html"), "w").write("<!doctype html><title>e2e</title>")
        await self.page.send("Page.navigate", {"url": self.origin + "/e2e-blank.html"})
        await self.page.wait("document.readyState === 'complete' && window.E", 20, "страница")
        await self.page.ev("localStorage.setItem('__init', %s)" % json.dumps(self.init_data(who)))
        await self.reload()

    def sql(self, query, params=()):
        """Запись напрямую в базу e2e-сервера (подготовка данных сценария, например предметов косметики); боевой код не затрагивается."""
        import sqlite3
        conn = sqlite3.connect(os.path.join(self.tmp, "e2e.db"))
        try:
            conn.execute(query, params)
            conn.commit()
        finally:
            conn.close()

    def sql_value(self, query, params=()):
        """Первое значение первой строки (чтение базы e2e-сервера сценарием)."""
        import sqlite3
        conn = sqlite3.connect(os.path.join(self.tmp, "e2e.db"))
        try:
            row = conn.execute(query, params).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def align_clock(self):
        """Сдвигает серверные часы так, чтобы сейчас на сервере была секунда clock_mod минуты (до границы минуты остаётся 60 - clock_mod)."""
        self.server.offset((self.clock_mod - int(time.time())) % 60)

    async def reload(self):
        if self.clock_mod is not None:
            self.align_clock()
        await self.page.send("Page.navigate", {"url": self.origin + "/index.html"})
        await self.page.wait("document.readyState === 'complete' && window.E && E.count('/api/me') >= 1", 20, "первый /api/me")
        await self.page.wait("!document.querySelector('.lobby').classList.contains('booting')", 10, "приложение загрузилось")

    async def stop(self):
        if self.page:
            with contextlib.suppress(Exception):
                await self.page.close()
        if self.server:
            self.server.stop()
        if self.static:
            self.static.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)



_ORIGIN = re.compile(r"^http://127\.0\.0\.1:\d+")


def normalize_net(entries):
    """Последовательность запросов клиента к API без случайного: метод, путь, тело (ключи по алфавиту). Случайный request_id
    заменяется его формой (длина), чтобы сравнивались правила, а не значения; запросы к статике не учитываются."""
    out = []
    for method, url, body in entries:
        path = _ORIGIN.sub("", url)
        if not path.startswith("/api/"):
            continue
        text = ""
        if body:
            try:
                data = json.loads(body)
                for key, tag in (("request_id", "rid"), ("member_ref", "ref")):    # случайные значения: сравнивается форма
                    if isinstance(data, dict) and isinstance(data.get(key), str):
                        data[key] = "<%s:%d>" % (tag, len(data[key]))
                text = " " + json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            except ValueError:
                text = " <не JSON>"
        out.append("%s %s%s" % (method, path, text))
    return out


def check(name, got, expected):
    if got != expected:
        raise E2EError("%s: получили %r, ожидали %r" % (name, got, expected))


def num(text):
    """Число из показанного на экране текста: «99 990» -> 99990 (минус не учитывается, только цифры)."""
    return int(re.sub(r"\D", "", text))


async def shown(page, selector):
    return num(await page.ev("document.querySelector(%s).textContent" % json.dumps(selector)))


async def open_game(page, game):
    """Открывает игру из титульного экрана (если он виден) или через меню игр, ждёт её экран."""
    if await page.ev("!document.querySelector('[data-screen=lobby]').hidden"):
        await page.tap(".lobby-card[data-game=%s]" % game)
    else:
        await page.tap(".tab.main")
        await page.wait("document.getElementById('game-menu').classList.contains('open')", 5, "меню игр")
        await page.tap(".tile[data-game=%s]" % game)
    await page.wait("document.querySelector('[data-screen=%s]') && !document.querySelector('[data-screen=%s]').hidden" % (game, game), 5, "экран " + game)
    # меню закрывается с анимацией: пока оно видно, затемнение перехватывает касания
    await page.wait("getComputedStyle(document.getElementById('game-menu')).visibility === 'hidden'", 5, "меню игр закрылось")


async def set_bet(page, input_id, value):
    await page.ev("(() => { const i = document.getElementById(%s); i.value = %s; i.dispatchEvent(new Event('input')); })()" % (json.dumps(input_id), json.dumps(str(value))))

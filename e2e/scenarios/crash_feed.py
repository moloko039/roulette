"""Живой краш: лента ставок комнаты. В беседе видны ставки только своей беседы с именами (ставка игрока из другой беседы не видна), вне беседы лента общая и
анонимная («Игрок N», без имён и идентификаторов); ставки и выводы других игроков приходят в ленту опросом, повторный опрос с токеном изменений отдаёт маленький
ответ «unchanged» (запросы с токеном видны в журнале страницы), ставка и вывод игрока из другой беседы в раунде общие по точке краха. Чужие ставки отправляются
на сервер напрямую подписанными запросами (как будто игроки открыли игру на своих устройствах)."""
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from crash_helpers import READY, bet, flight_start, server_to, t_crash_ms
from harness import check, open_game, sign_init_data

NAME = "crash_feed"
USERS = {"me": {"rate": 0}, "bob": {"rate": 0}}
FEED = "[...document.querySelectorAll('#cr-feed li')].map(li => [...li.children].map(c => c.textContent.trim()).slice(0, 2))"


def private_init(token, user_id, name):
    """Подписанные данные игрока, открывшего игру вне беседы (chat_type: sender, без chat_instance)."""
    fields = {"auth_date": str(int(time.time())), "query_id": "AAH-e2e", "signature": "e2e-signature", "chat_type": "sender",
              "user": json.dumps({"id": user_id, "first_name": name}, ensure_ascii=False, separators=(",", ":"))}
    check_string = "\n".join("%s=%s" % (k, fields[k]) for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


def post(w, init, path, body):
    req = urllib.request.Request(w.server.url + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "tma " + init, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


async def run(w):
    p = w.page
    bob = w.users["bob"]
    w.server.script(crash_live=[100000])
    await open_game(p, "crash")
    await p.wait(READY, 10, "панель ставки")

    # --- в беседе: ставка Боба (та же беседа) видна с именем, ставка игрока другой беседы нет
    group_bob = sign_init_data(w.server.token, bob.id, bob.name, w.chat, int(time.time()))
    status, body = post(w, group_bob, "/api/crash/live/bet", {"request_id": "feed-bob-0001", "bet": 250, "target_x100": 300})
    check("ставка Боба из той же беседы принята", status, 200)
    # чужая беседа: в базу попадает игрок, которого нет в chat_members этой беседы; создаём ему профиль входом в игру и ставкой из другой беседы
    stranger = sign_init_data(w.server.token, 900_000_000_001, "Чужой", "room-other-chat", int(time.time()))
    status, body = post(w, stranger, "/api/crash/live/bet", {"request_id": "feed-str-0001", "bet": 500})
    check("игрок другой беседы ставит в том же раунде (профиль нового игрока создаётся стартовым балансом)", status, 200)
    await bet(p, 100, "")
    await p.wait("document.querySelectorAll('#cr-feed li').length === 2", 10, "в ленте беседы две ставки: Боба и моя")
    feed = await p.ev(FEED)
    check("лента беседы: имена из беседы и суммы, чужой беседы нет", feed, [[bob.name, "250"], [w.users["me"].name, "100"]])
    check("заголовок ленты", await p.ev("document.getElementById('cr-feed-title').textContent"), "Ставки в раунде: 2")

    # --- токен изменений: после ответа без изменений клиент шлёт v и получает «unchanged»; при ставке Боба приходит полный ответ
    await p.ev("(() => { window.__live = []; window.__unch = 0; const orig = window.fetch; window.fetch = (u, o) => { const q = String(u).includes('/api/crash/live?v='); if (q) window.__live.push(String(u));"
               " const r = orig(u, o); if (q) r.then((x) => x.clone().json()).then((d) => { if (d && d.unchanged === true) window.__unch += 1; }).catch(() => {}); return r; }; })()")
    await p.wait("window.__live.length >= 3", 10, "опрос с токеном изменений идёт")
    await p.wait("window.__unch >= 1", 10, "сервер отвечает «unchanged», когда ничего не изменилось")
    check("в запросе токен безопасных символов", await p.ev("window.__live.every((u) => /[?]v=[A-Za-z0-9._-]+$/.test(u))"), True)
    status, body = post(w, sign_init_data(w.server.token, bob.id, bob.name, w.chat, int(time.time())), "/api/crash/live/cashout", {"request_id": "feed-bob-0002"})
    check("вывод до полёта запрещён (раунд ещё принимает ставки)", [status, body.get("detail")], [409, "round_over"])

    # --- вывод Боба в полёте приходит в ленту
    fs = await flight_start(p)
    await server_to(w, p, fs + 1800)
    status, body = post(w, group_bob, "/api/crash/live/cashout", {"request_id": "feed-bob-0003"})
    check("Боб вывел в полёте", status, 200)
    await p.wait("document.querySelector('#cr-feed li[data-status=cashed]')", 10, "вывод Боба появился в ленте")
    res = await p.ev("document.querySelector('#cr-feed li[data-status=cashed] .cr-feed-res').textContent")
    check("в ленте множитель вывода и прибыль Боба", res.startswith("×1.") and res.split("+")[1] == str(body["payout"] - 250), True)
    await p.wait("!document.getElementById('cr-cash').disabled", 10, "моя кнопка «Забрать» доступна")

    # --- вне беседы: общая анонимная лента (те же данные входа этой страницы заменяются на личный чат), ставки бесед в неё не попадают
    me = w.users["me"]
    await server_to(w, p, fs + t_crash_ms(100000) + 4000 + 300)      # раунд закончен, пауза итога прошла
    await p.ev("localStorage.setItem('__init', %s)" % json.dumps(private_init(w.server.token, me.id, me.name)))
    await w.reload()
    if not await p.ev("document.querySelector('[data-screen=lobby]').hidden"):
        await open_game(p, "crash")
    await p.wait("!document.querySelector('[data-screen=crash]').hidden", 15, "экран краша")
    await p.wait(READY, 20, "новый раунд: приём ставок вне беседы")
    status, _ = post(w, private_init(w.server.token, bob.id, bob.name), "/api/crash/live/bet", {"request_id": "feed-bob-0004", "bet": 300})
    check("Боб ставит вне беседы", status, 200)
    await bet(p, 100, "")
    await p.wait("document.querySelectorAll('#cr-feed li').length === 2", 10, "в общей ленте две ставки")
    check("вне беседы лента анонимна: «Игрок N» по порядку подачи, ставки бесед не видны", await p.ev(FEED), [["Игрок 1", "300"], ["Игрок 2", "100"]])
    page = await p.ev("document.getElementById('cr-feed').textContent + document.getElementById('cr-banner').textContent")
    check("в тексте ленты нет имён игроков", bob.name in page or me.name in page, False)

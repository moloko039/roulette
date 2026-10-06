import asyncio
import contextlib
import hmac
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from telegram import LabeledPrice, Update

import backup
import backup_send
import ratelimit
from levels import profile_level
import notify
from auth import InvalidInitData, validate_init_data, validate_init_data_full
import db
import blackjack
import crash
import hilo
import transfers
import cosmetics
import farm
import keno
import mines
import slot
from db import (play_slot, chat_members_page, transfer_history, transfer_send, transfer_status, active_game_of, hilo_cashout, hilo_guess, hilo_start, hilo_state, settle_expired_hilo, crash_cashout, crash_start, crash_state, settle_expired_crash, blackjack_action, blackjack_start, blackjack_state, buy_upgrade, chat_top, chat_best_wins, farm_status, get_player, init_db, mines_cashout, mines_reveal,
                mines_start, play_keno, mines_state, settle_expired_blackjack, settle_expired_mines, spin_roulette, touch_chat_member,
                cosmetics_state, cosmetics_mine, equip_item, unequip_item, set_visibility, buy_with_chips, stars_offer)
import economy
from roulette import (BalanceLimit, InsufficientFunds, InvalidBets, RequestConflict, validate_bets,
                      validate_request_id)

INVOICE_INTERVAL = 10      # не чаще одной ссылки на оплату на игрока и предмет за это число секунд
HOST = "127.0.0.1"  # локально только так; на Railway адрес и порт задаёт команда запуска
PORT = 8000

MAX_BODY_BYTES = 64 * 1024  # 47 ставок занимают около 3 КБ
WEBHOOK_PATH = "/telegram/webhook"
SECRET_HEADER = "x-telegram-bot-api-secret-token"
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")  # допустимые символы secret_token у Telegram

logger = logging.getLogger("depnaya")


class BodyTooLarge(Exception):
    pass


async def read_body_limited(request):
    """Тело запроса не больше MAX_BODY_BYTES: по Content-Length отказ до чтения, иначе чтение кусками с обрывом на пределе
    (клиент с chunked-телом не заставит сервер держать в памяти больше предела)."""
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BODY_BYTES:
                raise BodyTooLarge()
        except ValueError:
            raise BodyTooLarge()
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise BodyTooLarge()
        chunks.append(chunk)
    return b"".join(chunks)


def configure_logging():
    """httpx и httpcore по умолчанию пишут адреса запросов, а в адресе Telegram есть токен бота."""
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO)
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


configure_logging()

# ---------- замеры времени запросов ----------
http_logger = logging.getLogger("depnaya.http")
SLOW_REQUEST_MS = 500
UNLOGGED_PATHS = ("/health", "/telegram/webhook")
KNOWN_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD")
_perf = time.perf_counter   # отдельное имя, чтобы тесты подменяли часы


class TimingMiddleware:
    """Одна строка лога на запрос: метод, шаблон пути, код ответа, миллисекунды, для POST /api/* ещё db_ms.

    В строке нет id игроков, имён, балансов, заголовков, параметров запроса, тел и request_id: путь берётся из
    шаблона найденного маршрута (у ненайденных маршрутов пишется «(unmatched)»). /health и вебхук не логируются.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") in UNLOGGED_PATHS:
            await self.app(scope, receive, send)
            return
        timing = {"begin": 0.0, "commit": 0.0}
        token = db.request_timing.set(timing)
        started = _perf()
        status = {"code": 500}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = int((_perf() - started) * 1000)
            db.request_timing.reset(token)
            method = scope.get("method", "")
            route = scope.get("route")
            path = getattr(route, "path", None) or "(unmatched)"
            line = "%s %s %d %dms" % (method if method in KNOWN_METHODS else "OTHER", path, status["code"], elapsed_ms)
            if (method == "POST" and path.startswith("/api/")) or timing["begin"] or timing["commit"]:
                line += " db_ms=begin:%d,commit:%d" % (int(timing["begin"] * 1000), int(timing["commit"] * 1000))
            http_logger.log(logging.WARNING if elapsed_ms > SLOW_REQUEST_MS else logging.INFO, line)

class SecurityHeadersMiddleware:
    """Заголовки ответов: nosniff и Referrer-Policy: no-referrer для всех, Cache-Control: no-store для /api/*
    (ответы личные: баланс и состояние игр не должны оседать в общих кэшах). CSP не задаётся."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = str(scope.get("path", "")).startswith("/api/")

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", [])
                           if k.lower() not in (b"x-content-type-options", b"referrer-policy")
                           and not (is_api and k.lower() == b"cache-control")]
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"no-referrer"))
                if is_api:
                    headers.append((b"cache-control", b"no-store"))
                message = dict(message, headers=headers)
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ВАЖНО: SQLite рассчитана на один экземпляр сервиса (см. db.py), число реплик = 1.


def _unauthorized():
    # одно и то же сообщение при любой причине отказа
    return HTTPException(status_code=401, detail="Unauthorized")


def _in_group(info):
    return info["chat_type"] in ("group", "supergroup") and info["chat_instance"] is not None


def _forbidden():
    return JSONResponse({"detail": "Forbidden"}, status_code=403)


async def _register_commands(bot_app):
    from bot import register_commands  # меню команд; ошибка регистрации не роняет запуск
    await register_commands(bot_app)


def make_lifespan(mode, bot_token, public_url, webhook_secret, application, maintenance=None):
    @asynccontextmanager
    async def bot_lifespan(app):
        app.state.application = None
        app.state.notify_bot = None
        if mode == "api":
            logger.info("Бот отключён: режим только API")
            yield
            return

        bot_app = application
        if bot_app is None:
            from bot import build_application  # импорт здесь: режиму «только API» бот не нужен
            bot_app = build_application(bot_token, use_updater=(mode == "polling"))

        if hasattr(bot_app, "bot_data"):  # для команды /backupnow (только владелец)
            bot_app.bot_data["backup_sender"] = getattr(app.state, "backup_sender", None)
        await bot_app.initialize()
        await bot_app.start()
        try:
            if mode == "webhook":
                try:
                    await bot_app.bot.set_webhook(
                        url=public_url + WEBHOOK_PATH,
                        secret_token=webhook_secret,
                        allowed_updates=Update.ALL_TYPES,
                    )
                except Exception as exc:
                    # в тексте ошибки может быть адрес с токеном, поэтому пишем только тип
                    logger.error("Не удалось зарегистрировать webhook: %s", type(exc).__name__)
                    raise RuntimeError("webhook registration failed") from None
                app.state.application = bot_app
                logger.info("Бот запущен в режиме webhook")
                await _register_commands(bot_app)
            else:
                # ВНИМАНИЕ: start_polling снимает webhook у бота (Telegram не отдаёт
                # обновления одновременно через webhook и getUpdates). Если тот же бот
                # работает на Railway, он перестанет получать сообщения, пока там не
                # перезапустят сервис и webhook не зарегистрируется снова.
                await bot_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
                logger.info("Бот запущен в режиме polling")
                await _register_commands(bot_app)
            app.state.notify_bot = bot_app.bot  # уведомления владельцу возможны, только пока бот работает
            yield
        finally:
            app.state.notify_bot = None
            if mode == "polling":
                await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()

    @asynccontextmanager
    async def lifespan(app):
        # фоновая задача (резервные копии и очистка) работает во всех режимах; в тестах выключена
        task = None
        app.state.backup_sender = None
        if maintenance is not None:
            notifier = None
            sender = None
            send_config = maintenance.get("send_config")
            if send_config is not None and send_config["enabled"]:
                sender = backup_send.EncryptedSender(maintenance["owner_id"], lambda: app.state.notify_bot,
                                                     maintenance["db_path"], maintenance["config"], send_config)
                app.state.backup_sender = sender
            if maintenance.get("owner_id") is not None:
                notifier = notify.Notifier(maintenance["owner_id"], lambda: app.state.notify_bot,
                                           maintenance["db_path"], maintenance["config"],
                                           encrypted_send=sender is not None)
            task = asyncio.create_task(backup.maintenance_loop(
                maintenance["config"], maintenance["db_path"], notifier=notifier, sender=sender,
                **maintenance.get("loop_args", {})))
        try:
            async with bot_lifespan(app):
                yield
        finally:
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return lifespan


def create_app(bot_token, allowed_origins, db_path=None, mode="api",
               public_url=None, webhook_secret=None, application=None, maintenance=None,
               rate_limiter=None):
    app = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=make_lifespan(mode, bot_token, public_url, webhook_secret, application, maintenance),
    )
    app.state.application = None

    # только перечисленные origin, только GET и заголовок Authorization
    # (webhook вызывает Telegram, а не браузер, ему CORS не нужен)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_middleware(TimingMiddleware)  # последним: внешний слой, измеряет весь запрос
    app.add_middleware(SecurityHeadersMiddleware)  # внешний: заголовки есть и у 4xx/5xx, и у ответов CORS

    @app.exception_handler(sqlite3.OperationalError)
    async def database_busy(request, exc):
        """База занята дольше таймаута (и после повтора BEGIN): 503 с просьбой повторить, а не 500. Остальные ошибки SQLite
        остаются 500. В лог идёт только факт, без запроса и параметров."""
        if not db.is_busy_error(exc):
            raise exc
        logger.warning("База занята: ответ 503")
        return JSONResponse({"detail": "busy"}, status_code=503, headers={"Retry-After": "1"})

    @app.get("/health")
    def health():
        return {"ok": True}

    def throttled(user_id, group):
        """Ограничение частоты (общее для всех игр). Вызывается только после проверки подписи initData.
        Возвращает ответ 429 с Retry-After или None. Без rate_limiter (тесты) ограничения нет."""
        if rate_limiter is None:
            return None
        wait = rate_limiter.check(user_id, group)
        if wait is None:
            return None
        return JSONResponse({"error": "too_many_requests"}, status_code=429, headers={"Retry-After": str(wait)})

    @app.get("/api/me")
    def me(authorization: str = Header(default=None)):
        try:
            scheme, _, init_data = (authorization or "").partition(" ")
            if scheme != "tma":
                raise InvalidInitData()
            info = validate_init_data_full(init_data, bot_token)
        except InvalidInitData:
            raise _unauthorized()
        user_id = info["user_id"]
        limited = throttled(user_id, "read")
        if limited is not None:
            return limited

        now = int(time.time())
        settle_expired_mines(user_id, now=now, db_path=db_path)  # просроченная игра в мины закрывается
        settle_expired_blackjack(user_id, now=now, db_path=db_path)  # и просроченная раздача блэкджека
        settle_expired_crash(user_id, db_path=db_path)  # и разбившийся или брошенный раунд краша
        settle_expired_hilo(user_id, now=now, db_path=db_path)  # и просроченная партия в хило
        player = get_player(user_id, now=now, db_path=db_path)
        limits, incoming = transfer_status(user_id, owner_id=notify.load_owner_id(), now=now, db_path=db_path)
        if _in_group(info):
            try:
                touch_chat_member(info["chat_instance"], user_id, info["first_name"], now=now, db_path=db_path)
            except Exception:
                pass  # рейтинг не должен ломать /api/me
        return {
            "balance": player["balance"],
            "rate": player["rate"],
            "seconds_to_next": economy.next_tick_in(now),   # до следующей минутной границы начисления (раньше: до часа)
            "level": profile_level(player["xp"]),   # уровень профиля по опыту
            "income_level": player["income_level"],
            "storage_level": player["storage_level"],
            "farm": {                         # поминутное начисление: ленивое, при этом запросе уже подтянуто
                "income_per_hour": player["rate"],
                "per_minute_estimate": economy.per_minute_estimate(player["rate"]),
                "next_tick_in_s": economy.next_tick_in(now),
                "hours_cap": farm.storage_hours(player["storage_level"]),
                "accrued_now": player["accrued"],   # сколько фишек зачислил именно этот запрос
            },
            "active_game": active_game_of(user_id, db_path=db_path),   # "mines" | "blackjack" | "crash" | "hilo" | null
            "incoming_unseen": incoming,     # {count, total}: непросмотренные входящие переводы
            "transfer_limits": limits,       # лимиты переводов для клиента (клиент констант не дублирует)
            "cosmetics": cosmetics_state(user_id, db_path=db_path),   # только внешний вид: {equipped: {слот: код}, show_in_rating}
        }

    @app.get("/api/chat/top")
    def chat_top_endpoint(authorization: str = Header(default=None)):
        try:
            scheme, _, init_data = (authorization or "").partition(" ")
            if scheme != "tma":
                raise InvalidInitData()
            info = validate_init_data_full(init_data, bot_token)
        except InvalidInitData:
            raise _unauthorized()
        limited = throttled(info["user_id"], "read")
        if limited is not None:
            return limited
        if not _in_group(info):
            return {"scope": "none"}
        # в ответе только rank, name, balance, is_me, staked и chat_staked: ни telegram_id, ни chat_instance, ни username
        return chat_top(info["chat_instance"], info["user_id"], info["first_name"], db_path=db_path)

    @app.get("/api/chat/best-wins")
    def chat_best_wins_endpoint(authorization: str = Header(default=None)):
        # отдельный маршрут (а не поле в /api/chat/top): сбой или рост рекордов не ломает основной рейтинг, клиент грузит их независимо;
        # проверки те же (initData, лимит чтения, scope none вне беседы); только чтение. В ответе нет telegram_id, chat_instance и времени
        try:
            scheme, _, init_data = (authorization or "").partition(" ")
            if scheme != "tma":
                raise InvalidInitData()
            info = validate_init_data_full(init_data, bot_token)
        except InvalidInitData:
            raise _unauthorized()
        limited = throttled(info["user_id"], "read")
        if limited is not None:
            return limited
        if not _in_group(info):
            return {"scope": "none"}
        return chat_best_wins(info["chat_instance"], info["user_id"], db_path=db_path)

    @app.get("/api/farm")
    def farm_endpoint(authorization: str = Header(default=None)):
        try:
            scheme, _, init_data = (authorization or "").partition(" ")
            if scheme != "tma":
                raise InvalidInitData()
            user_id = validate_init_data(init_data, bot_token)
        except InvalidInitData:
            raise _unauthorized()
        limited = throttled(user_id, "read")
        if limited is not None:
            return limited
        return farm_status(user_id, db_path=db_path)

    @app.post("/api/farm/buy")
    async def farm_buy(request: Request):
        try:
            scheme, _, init_data = (request.headers.get("authorization") or "").partition(" ")
            if scheme != "tma":
                raise InvalidInitData()
            user_id = validate_init_data(init_data, bot_token)
        except InvalidInitData:
            raise _unauthorized()
        limited = throttled(user_id, "write")
        if limited is not None:
            return limited

        # любая ошибка формы тела: 400 с одним и тем же текстом
        try:
            raw = await read_body_limited(request)
        except BodyTooLarge:
            return JSONResponse({"detail": "payload_too_large"}, status_code=413)
        try:
            data = json.loads(raw)
            if type(data) is not dict or set(data) != {"request_id", "kind"}:
                raise ValueError()
            request_id = validate_request_id(data["request_id"])
            kind = data["kind"]
            if type(kind) is not str or kind not in farm.KINDS:
                raise ValueError()
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)

        try:
            return await run_in_threadpool(buy_upgrade, user_id, request_id, kind, None, db_path)
        except farm.MaxLevel:
            return JSONResponse({"detail": "max_level"}, status_code=409)
        except farm.LevelLocked as exc:
            return JSONResponse({"detail": "level_locked", "required_level": exc.required_level}, status_code=409)
        except InsufficientFunds:
            return JSONResponse({"detail": "insufficient_funds"}, status_code=409)

    # ---------- косметика ----------
    # Только внешний вид (bot/cosmetics.py). Принадлежность чужих игроков не раскрывается; платежей нет.

    @app.get("/api/cosmetics/catalog")
    def cosmetics_catalog(authorization: str = Header(default=None)):
        user_id, limited = _mines_user({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return cosmetics.catalog_view()

    @app.get("/api/cosmetics/mine")
    def cosmetics_mine_endpoint(authorization: str = Header(default=None)):
        user_id, limited = _mines_user({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return cosmetics_mine(user_id, db_path=db_path)

    async def _cosmetics_post(request, keys, call):
        user_id, limited = _mines_user(request.headers, "write")
        if limited is not None:
            return limited
        try:
            raw = await read_body_limited(request)
        except BodyTooLarge:
            return JSONResponse({"detail": "payload_too_large"}, status_code=413)
        try:
            data = json.loads(raw)
            if type(data) is not dict or set(data) != keys:
                raise ValueError()
            request_id = validate_request_id(data["request_id"])
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        try:
            return await run_in_threadpool(call, user_id, request_id, data)
        except ValueError:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        except cosmetics.UnknownItem:
            return JSONResponse({"detail": "unknown_item"}, status_code=404)
        except cosmetics.TooFast:
            return JSONResponse({"error": "too_many_requests"}, status_code=429, headers={"Retry-After": "1"})
        except cosmetics.CosmeticsError as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)

    @app.post("/api/cosmetics/equip")
    async def cosmetics_equip(request: Request):
        return await _cosmetics_post(request, {"request_id", "slot", "code"},
                                     lambda uid, rid, d: equip_item(uid, rid, d["slot"], d["code"], db_path=db_path))

    @app.post("/api/cosmetics/unequip")
    async def cosmetics_unequip(request: Request):
        return await _cosmetics_post(request, {"request_id", "slot"},
                                     lambda uid, rid, d: unequip_item(uid, rid, d["slot"], db_path=db_path))

    @app.post("/api/cosmetics/visibility")
    async def cosmetics_visibility(request: Request):
        return await _cosmetics_post(request, {"request_id", "show_in_rating"},
                                     lambda uid, rid, d: set_visibility(uid, rid, d["show_in_rating"], db_path=db_path))

    @app.post("/api/cosmetics/buy")
    async def cosmetics_buy(request: Request):
        return await _cosmetics_post(request, {"request_id", "item_code"},
                                     lambda uid, rid, d: buy_with_chips(uid, rid, d["item_code"], db_path=db_path))

    invoice_cache = {}      # (игрок, предмет) -> (время, request_id, ссылка): повтор того же request_id отдаёт ту же ссылку

    @app.post("/api/cosmetics/invoice")
    async def cosmetics_invoice(request: Request):
        user_id, limited = _mines_user(request.headers, "write")
        if limited is not None:
            return limited
        try:
            raw = await read_body_limited(request)
        except BodyTooLarge:
            return JSONResponse({"detail": "payload_too_large"}, status_code=413)
        try:
            data = json.loads(raw)
            if type(data) is not dict or set(data) != {"request_id", "item_code"} or type(data["item_code"]) is not str:
                raise ValueError()
            request_id = validate_request_id(data["request_id"])
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        code = data["item_code"]
        bot = getattr(getattr(app.state, "application", None), "bot", None)
        if bot is None:
            return JSONResponse({"detail": "payments_unavailable"}, status_code=503)
        try:
            item = await run_in_threadpool(stars_offer, user_id, code, db_path)
        except cosmetics.UnknownItem:
            return JSONResponse({"detail": "unknown_item"}, status_code=404)
        except cosmetics.CosmeticsError as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)
        if item["code"] == cosmetics.TEST_ITEM["code"]:      # скрытый тестовый предмет выставляет только команда владельца
            return JSONResponse({"detail": "unknown_item"}, status_code=404)
        now = time.monotonic()
        last = invoice_cache.get((user_id, code))
        if last is not None and now - last[0] < INVOICE_INTERVAL:
            if last[1] == request_id:
                return {"invoice_url": last[2], "replayed": True}
            return JSONResponse({"error": "too_many_requests"}, status_code=429, headers={"Retry-After": str(max(1, int(INVOICE_INTERVAL - (now - last[0]))))})
        title = item["name"][:32]
        text = "Косметический предмет «%s»: меняет только внешний вид приложения, на игру не влияет." % item["name"]
        try:
            url = await bot.create_invoice_link(title=title, description=text[:255], payload=cosmetics.make_payload(user_id, code, int(time.time())),
                                                currency="XTR", prices=[LabeledPrice(title, item["price"]["amount"])], provider_token="")
        except Exception as exc:
            logger.error("Не удалось создать ссылку на оплату: %s", type(exc).__name__)
            return JSONResponse({"detail": "invoice_failed"}, status_code=502)
        invoice_cache[(user_id, code)] = (now, request_id, url)
        return {"invoice_url": url, "replayed": False}

    # ---------- мины ----------
    # Раскладка мин активной игры не попадает ни в один ответ (только завершённые игры раскрывают mine_cells)

    def _mines_user(request_headers, group):
        """id игрока из проверенной подписи и ограничение частоты; (user_id, ответ 429 или None)."""
        try:
            scheme, _, init_data = (request_headers.get("authorization") or "").partition(" ")
            if scheme != "tma":
                raise InvalidInitData()
            user_id = validate_init_data(init_data, bot_token)
        except InvalidInitData:
            raise _unauthorized()
        return user_id, throttled(user_id, group)

    async def _mines_post(request, keys, run, optional=frozenset()):
        """Общий разбор POST мин: подпись, лимит, тело с точным набором ключей, ошибки в одном формате."""
        user_id, limited = _mines_user(request.headers, "write")
        if limited is not None:
            return limited
        try:
            raw = await read_body_limited(request)
        except BodyTooLarge:
            return JSONResponse({"detail": "payload_too_large"}, status_code=413)
        try:
            data = json.loads(raw)
            if type(data) is not dict or not (keys <= set(data) <= keys | optional):
                raise ValueError()
            request_id = validate_request_id(data["request_id"])
            call = run(user_id, request_id, data)   # проверки типов и диапазонов: ValueError -> 400
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        try:
            return await run_in_threadpool(call)
        except (mines.MinesError, keno.KenoError, blackjack.BlackjackError, crash.CrashError, hilo.HiloError, slot.SlotError) as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)
        except InsufficientFunds:
            return JSONResponse({"detail": "insufficient_funds"}, status_code=409)
        except BalanceLimit:
            return JSONResponse({"detail": "balance_limit"}, status_code=409)

    @app.post("/api/mines/start")
    async def mines_start_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            bet, count = data["bet"], data["mines"]
            if (type(bet) is not int or not 1 <= bet <= mines.MINES_MAX_BET
                    or type(count) is not int or not mines.MINES_MIN_COUNT <= count <= mines.MINES_MAX_COUNT):
                raise ValueError()
            return lambda: mines_start(user_id, request_id, bet, count, db_path=db_path)
        return await _mines_post(request, {"request_id", "bet", "mines"}, prepare)

    @app.post("/api/mines/reveal")
    async def mines_reveal_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            cell = data["cell"]
            if type(cell) is not int or not 0 <= cell < mines.FIELD_CELLS:
                raise ValueError()
            return lambda: mines_reveal(user_id, request_id, cell, db_path=db_path)
        return await _mines_post(request, {"request_id", "cell"}, prepare)

    @app.post("/api/mines/cashout")
    async def mines_cashout_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            return lambda: mines_cashout(user_id, request_id, db_path=db_path)
        return await _mines_post(request, {"request_id"}, prepare)

    @app.get("/api/mines/state")
    def mines_state_endpoint(authorization: str = Header(default=None)):
        user_id, limited = _mines_user({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return mines_state(user_id, db_path=db_path)

    # ---------- кено ----------

    @app.post("/api/keno/play")
    async def keno_play_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            bet = data["bet"]
            if type(bet) is not int or not 1 <= bet <= keno.KENO_MAX_BET:
                raise ValueError()
            picks = keno.validate_picks(data["picks"])   # InvalidPicks (ValueError) -> 400
            return lambda: play_keno(user_id, request_id, bet, picks, db_path=db_path)
        return await _mines_post(request, {"request_id", "bet", "picks"}, prepare)

    @app.get("/api/keno/paytable")
    def keno_paytable_endpoint(authorization: str = Header(default=None)):
        user_id, limited = _mines_user({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return {"paytable": keno.paytable_text()}

    # ---------- Western Slot (встроенный слот раздела «Не слоты», фишки приложения) ----------
    # Весь раунд (поле, каскады, бесплатные вращения) считает сервер и сразу расплачивается; клиент только показывает присланное

    @app.post("/api/slot/spin")
    async def slot_spin_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            coin, buy = data["coin"], data["buy"]
            if type(coin) is not int or coin not in slot.COIN_VALUES or type(buy) is not bool:
                raise ValueError()
            return lambda: play_slot(user_id, request_id, coin, buy, db_path=db_path)
        return await _mines_post(request, {"request_id", "coin", "buy"}, prepare)

    # ---------- блэкджек ----------
    # Колода и скрытая карта дилера активной раздачи не попадают ни в один ответ

    @app.post("/api/blackjack/start")
    async def blackjack_start_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            bet = data["bet"]
            if type(bet) is not int or not 1 <= bet <= blackjack.BLACKJACK_MAX_BET:
                raise ValueError()
            return lambda: blackjack_start(user_id, request_id, bet, db_path=db_path)
        return await _mines_post(request, {"request_id", "bet"}, prepare)

    @app.post("/api/blackjack/action")
    async def blackjack_action_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            action = data["action"]
            if type(action) is not str or action not in blackjack.ACTIONS:
                raise ValueError()
            return lambda: blackjack_action(user_id, request_id, action, db_path=db_path)
        return await _mines_post(request, {"request_id", "action"}, prepare)

    @app.get("/api/blackjack/state")
    def blackjack_state_endpoint(authorization: str = Header(default=None)):
        user_id, limited = _mines_user({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return blackjack_state(user_id, db_path=db_path)

    # ---------- краш ----------
    # Точка краха активного раунда не попадает ни в один ответ; время считает только сервер

    @app.post("/api/crash/start")
    async def crash_start_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            bet, target = data["bet"], data.get("target_x100")
            if type(bet) is not int or not 1 <= bet <= crash.CRASH_MAX_BET:
                raise ValueError()
            if target is not None and (type(target) is not int or not crash.MIN_TARGET_X100 <= target <= crash.CAP_X100):
                raise ValueError()
            return lambda: crash_start(user_id, request_id, bet, target, db_path=db_path)
        return await _mines_post(request, {"request_id", "bet"}, prepare, optional=frozenset({"target_x100"}))

    @app.post("/api/crash/cashout")
    async def crash_cashout_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            return lambda: crash_cashout(user_id, request_id, db_path=db_path)
        return await _mines_post(request, {"request_id"}, prepare)

    @app.get("/api/crash/state")
    def crash_state_endpoint(authorization: str = Header(default=None)):
        user_id, limited = _mines_user({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return crash_state(user_id, db_path=db_path)

    # ---------- хило ----------
    # Следующей карты нет нигде до хода: она выбирается в момент действия

    @app.post("/api/hilo/start")
    async def hilo_start_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            bet = data["bet"]
            if type(bet) is not int or not 1 <= bet <= hilo.HILO_MAX_BET:
                raise ValueError()
            return lambda: hilo_start(user_id, request_id, bet, db_path=db_path)
        return await _mines_post(request, {"request_id", "bet"}, prepare)

    @app.post("/api/hilo/guess")
    async def hilo_guess_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            choice = data["choice"]
            if type(choice) is not str or choice not in hilo.CHOICES:
                raise ValueError()
            return lambda: hilo_guess(user_id, request_id, choice, db_path=db_path)
        return await _mines_post(request, {"request_id", "choice"}, prepare)

    @app.post("/api/hilo/cashout")
    async def hilo_cashout_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            return lambda: hilo_cashout(user_id, request_id, db_path=db_path)
        return await _mines_post(request, {"request_id"}, prepare)

    @app.get("/api/hilo/state")
    def hilo_state_endpoint(authorization: str = Header(default=None)):
        user_id, limited = _mines_user({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return hilo_state(user_id, db_path=db_path)

    # ---------- переводы между участниками беседы ----------
    # Идентификаторы Telegram в ответах не показываются: участник задаётся непрозрачной меткой member_ref из рейтинга беседы

    @app.post("/api/transfers/send")
    async def transfers_send_endpoint(request: Request):
        try:
            scheme, _, init_data = (request.headers.get("authorization") or "").partition(" ")
            if scheme != "tma":
                raise InvalidInitData()
            info = validate_init_data_full(init_data, bot_token)
        except InvalidInitData:
            raise _unauthorized()
        limited = throttled(info["user_id"], "write")
        if limited is not None:
            return limited
        try:
            raw = await read_body_limited(request)
        except BodyTooLarge:
            return JSONResponse({"detail": "payload_too_large"}, status_code=413)
        try:
            data = json.loads(raw)
            if type(data) is not dict or set(data) != {"request_id", "member_ref", "amount"}:
                raise ValueError()
            request_id = validate_request_id(data["request_id"])
            ref, amount = data["member_ref"], data["amount"]
            if not transfers.valid_member_ref(ref) or not transfers.valid_amount(amount):
                raise ValueError()
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        chat = info["chat_instance"] if _in_group(info) else None
        try:
            return await run_in_threadpool(lambda: transfer_send(info["user_id"], chat, info["first_name"], ref, amount,
                                                                   request_id, owner_id=notify.load_owner_id(), db_path=db_path))
        except transfers.TransferError as exc:
            return JSONResponse(dict({"detail": exc.code}, **exc.extra), status_code=409)
        except InsufficientFunds:
            return JSONResponse({"detail": "insufficient_funds"}, status_code=409)

    @app.get("/api/chat/members")
    def chat_members_endpoint(request: Request, authorization: str = Header(default=None)):
        try:
            scheme, _, init_data = (authorization or "").partition(" ")
            if scheme != "tma":
                raise InvalidInitData()
            info = validate_init_data_full(init_data, bot_token)
        except InvalidInitData:
            raise _unauthorized()
        limited = throttled(info["user_id"], "read")
        if limited is not None:
            return limited
        raw_offset = request.query_params.get("offset", "0")
        if not re.fullmatch(r"[0-9]{1,6}", raw_offset):
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        chat = info["chat_instance"] if _in_group(info) else None
        try:
            items, next_offset = chat_members_page(info["user_id"], chat, request.query_params.get("q", ""), int(raw_offset), db_path=db_path)
        except ValueError:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        except transfers.TransferError as exc:
            return JSONResponse({"detail": exc.code}, status_code=409)
        # только name и member_ref: без балансов, уровней и идентификаторов Telegram; в лог ни имён, ни запросов
        return {"items": items, "next_offset": next_offset}

    @app.get("/api/transfers")
    def transfers_list_endpoint(authorization: str = Header(default=None)):
        user_id, limited = _mines_user({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return {"items": transfer_history(user_id, db_path=db_path)}

    @app.post("/api/roulette/spin")
    async def roulette_spin(request: Request):
        # подпись проверяется так же, как в /api/me; id игрока только из проверенных данных
        try:
            scheme, _, init_data = (request.headers.get("authorization") or "").partition(" ")
            if scheme != "tma":
                raise InvalidInitData()
            user_id = validate_init_data(init_data, bot_token)
        except InvalidInitData:
            raise _unauthorized()
        limited = throttled(user_id, "write")  # повторы с тем же request_id тоже считаются
        if limited is not None:
            return limited

        # любая ошибка формы тела: 400 с одним и тем же текстом (без стандартных 422)
        try:
            raw = await read_body_limited(request)
        except BodyTooLarge:
            return JSONResponse({"detail": "payload_too_large"}, status_code=413)
        try:
            data = json.loads(raw)
            if type(data) is not dict or set(data) != {"request_id", "bets"}:
                raise InvalidBets()
            request_id = validate_request_id(data["request_id"])
            bets = validate_bets(data["bets"])
        except Exception:
            return JSONResponse({"detail": "invalid_bets"}, status_code=400)

        try:
            # база блокирующая, поэтому не в потоке обработки событий
            return await run_in_threadpool(spin_roulette, user_id, request_id, bets, None, db_path)
        except InsufficientFunds:
            return JSONResponse({"detail": "insufficient_funds"}, status_code=409)
        except BalanceLimit:
            return JSONResponse({"detail": "balance_limit"}, status_code=409)
        except RequestConflict:
            return JSONResponse({"detail": "request_conflict"}, status_code=409)

    @app.post(WEBHOOK_PATH)
    async def telegram_webhook(request: Request):
        got = request.headers.get(SECRET_HEADER)
        bot_app = app.state.application
        if (not webhook_secret or not got or bot_app is None
                or not hmac.compare_digest(got.encode(), webhook_secret.encode())):
            return _forbidden()
        try:
            update = Update.de_json(await request.json(), bot_app.bot)
        except Exception:
            update = None
        if update is None:
            return JSONResponse({"detail": "Bad request"}, status_code=400)
        # обработка не здесь: кладём в очередь, Application разберёт обновление сам
        await bot_app.update_queue.put(update)
        return {"ok": True}

    return app


def load_settings(env):
    """Читает и проверяет настройки из переменных окружения (словарь env)."""
    token = env.get("BOT_TOKEN")
    if not token:
        raise SystemExit("Не задан BOT_TOKEN в файле .env")
    origins = [o.strip() for o in env.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if "*" in origins:
        raise SystemExit("В ALLOWED_ORIGINS нельзя указывать *, перечислите origin явно")

    public_url = (env.get("PUBLIC_URL") or "").strip().rstrip("/")
    secret = env.get("WEBHOOK_SECRET") or ""
    if public_url:
        mode = "webhook"
        if not public_url.startswith("https://"):
            raise SystemExit("PUBLIC_URL должен начинаться с https://")
        if not secret:
            raise SystemExit("В режиме webhook обязательно задайте WEBHOOK_SECRET")
        if not SECRET_RE.match(secret):
            raise SystemExit("WEBHOOK_SECRET: допустимы только A-Z a-z 0-9 _ -, длина 1-256")
    elif env.get("LOCAL_POLLING") == "1":
        mode = "polling"
    else:
        mode = "api"
    if mode != "api" and not env.get("WEBAPP_URL"):
        raise SystemExit("Для работы бота задайте WEBAPP_URL")
    return {"token": token, "origins": origins, "mode": mode,
            "public_url": public_url or None, "secret": secret or None}


def _resolve_db_path():
    import db
    return db._resolve_path(None)


def create_app_from_env():
    load_dotenv()
    configure_logging()
    s = load_settings(os.environ)
    init_db()  # без таблицы первый же валидный запрос упал бы с ошибкой
    db.log_sqlite_mode()
    db_path = _resolve_db_path()
    config = backup.load_config(os.environ, db_path)
    backup.warn_config(config)
    owner_id = notify.load_owner_id(os.environ)
    notify.warn_owner(owner_id)
    send_config = backup_send.load_config(os.environ, owner_id)
    backup_send.warn_config(send_config)
    rate_config = ratelimit.load_config(os.environ)
    ratelimit.warn_config(rate_config)
    return create_app(s["token"], s["origins"], mode=s["mode"],
                      public_url=s["public_url"], webhook_secret=s["secret"],
                      rate_limiter=ratelimit.RateLimiter(rate_config),
                      maintenance={"config": config, "db_path": db_path, "owner_id": owner_id,
                                       "send_config": send_config})


if __name__ == "__main__":
    uvicorn.run(create_app_from_env(), host=HOST, port=PORT)

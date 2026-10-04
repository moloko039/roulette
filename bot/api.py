import asyncio
import contextlib
import hmac
import json
import logging
import os
import re
import time
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from telegram import Update

import backup
import backup_send
import ratelimit
from levels import profile_level
import notify
from auth import InvalidInitData, validate_init_data, validate_init_data_full
import db
import farm
import keno
import mines
from db import (buy_upgrade, chat_top, farm_status, get_player, init_db, mines_cashout, mines_reveal,
                mines_start, play_keno, mines_state, settle_expired_mines, spin_roulette, touch_chat_member)
from economy import HOUR
from roulette import (BalanceLimit, InsufficientFunds, InvalidBets, validate_bets,
                      validate_request_id)

HOST = "127.0.0.1"  # локально только так; на Railway адрес и порт задаёт команда запуска
PORT = 8000

MAX_BODY_BYTES = 64 * 1024  # 47 ставок занимают около 3 КБ
WEBHOOK_PATH = "/telegram/webhook"
SECRET_HEADER = "x-telegram-bot-api-secret-token"
SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{1,256}$")  # допустимые символы secret_token у Telegram

logger = logging.getLogger("depnaya")


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
        player = get_player(user_id, now=now, db_path=db_path)
        if _in_group(info):
            try:
                touch_chat_member(info["chat_instance"], user_id, info["first_name"], now=now, db_path=db_path)
            except Exception:
                pass  # рейтинг не должен ломать /api/me
        return {
            "balance": player["balance"],
            "rate": player["rate"],
            "seconds_to_next": max(0, player["last_accrual"] + HOUR - now),
            "level": profile_level(player["xp"]),   # уровень профиля по опыту
            "income_level": player["income_level"],
            "storage_level": player["storage_level"],
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
            raw = await request.body()
            if len(raw) > MAX_BODY_BYTES:
                raise ValueError()
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

    async def _mines_post(request, keys, run):
        """Общий разбор POST мин: подпись, лимит, тело с точным набором ключей, ошибки в одном формате."""
        user_id, limited = _mines_user(request.headers, "write")
        if limited is not None:
            return limited
        try:
            raw = await request.body()
            if len(raw) > MAX_BODY_BYTES:
                raise ValueError()
            data = json.loads(raw)
            if type(data) is not dict or set(data) != keys:
                raise ValueError()
            request_id = validate_request_id(data["request_id"])
            call = run(user_id, request_id, data)   # проверки типов и диапазонов: ValueError -> 400
        except Exception:
            return JSONResponse({"detail": "invalid_request"}, status_code=400)
        try:
            return await run_in_threadpool(call)
        except (mines.MinesError, keno.KenoError) as exc:
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
            raw = await request.body()
            if len(raw) > MAX_BODY_BYTES:
                raise InvalidBets()
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

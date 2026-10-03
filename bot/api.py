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

from auth import InvalidInitData, validate_init_data
from db import get_player, init_db, spin_roulette
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

# ВАЖНО: SQLite рассчитана на один экземпляр сервиса (см. db.py), число реплик = 1.


def _unauthorized():
    # одно и то же сообщение при любой причине отказа
    return HTTPException(status_code=401, detail="Unauthorized")


def _forbidden():
    return JSONResponse({"detail": "Forbidden"}, status_code=403)


def make_lifespan(mode, bot_token, public_url, webhook_secret, application):
    @asynccontextmanager
    async def lifespan(app):
        app.state.application = None
        if mode == "api":
            logger.info("Бот отключён: режим только API")
            yield
            return

        bot_app = application
        if bot_app is None:
            from bot import build_application  # импорт здесь: режиму «только API» бот не нужен
            bot_app = build_application(bot_token, use_updater=(mode == "polling"))

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
            else:
                # ВНИМАНИЕ: start_polling снимает webhook у бота (Telegram не отдаёт
                # обновления одновременно через webhook и getUpdates). Если тот же бот
                # работает на Railway, он перестанет получать сообщения, пока там не
                # перезапустят сервис и webhook не зарегистрируется снова.
                await bot_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
                logger.info("Бот запущен в режиме polling")
            yield
        finally:
            if mode == "polling":
                await bot_app.updater.stop()
            await bot_app.stop()
            await bot_app.shutdown()

    return lifespan


def create_app(bot_token, allowed_origins, db_path=None, mode="api",
               public_url=None, webhook_secret=None, application=None):
    app = FastAPI(
        docs_url=None, redoc_url=None, openapi_url=None,
        lifespan=make_lifespan(mode, bot_token, public_url, webhook_secret, application),
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

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/api/me")
    def me(authorization: str = Header(default=None)):
        try:
            scheme, _, init_data = (authorization or "").partition(" ")
            if scheme != "tma":
                raise InvalidInitData()
            user_id = validate_init_data(init_data, bot_token)
        except InvalidInitData:
            raise _unauthorized()

        now = int(time.time())
        player = get_player(user_id, now=now, db_path=db_path)
        return {
            "balance": player["balance"],
            "rate": player["rate"],
            "seconds_to_next": max(0, player["last_accrual"] + HOUR - now),
        }

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


def create_app_from_env():
    load_dotenv()
    configure_logging()
    s = load_settings(os.environ)
    init_db()  # без таблицы первый же валидный запрос упал бы с ошибкой
    return create_app(s["token"], s["origins"], mode=s["mode"],
                      public_url=s["public_url"], webhook_secret=s["secret"])


if __name__ == "__main__":
    uvicorn.run(create_app_from_env(), host=HOST, port=PORT)

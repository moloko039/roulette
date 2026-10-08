"""Сборка приложения FastAPI: middleware, маршруты из пакета web/, настройки из окружения и запуск."""
import logging
import os
import re

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import backup
import backup_send
import db
import notify
import ratelimit
from db import init_db
from web import (routes_account, routes_chat, routes_cosmetics, routes_chips, routes_farm, routes_games, routes_gems, routes_gifts,
                 routes_referral, routes_streak, routes_transfers, routes_webhook)
from web.context import Ctx
from web.http import (MAX_BODY_BYTES, WEBHOOK_PATH, BodyTooLarge, SecurityHeadersMiddleware,  # noqa: F401 (реэкспорт для тестов)
                      TimingMiddleware, read_body_limited)
from web.lifespan import make_lifespan
from web.routes_webhook import SECRET_HEADER  # noqa: F401

HOST = "127.0.0.1"  # локально только так; на Railway адрес и порт задаёт команда запуска
PORT = 8000
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

    ctx = Ctx(bot_token, db_path, rate_limiter, webhook_secret)
    for routes in (routes_account, routes_chat, routes_farm, routes_cosmetics, routes_gems, routes_chips, routes_gifts, routes_referral, routes_streak, routes_games, routes_transfers, routes_webhook):
        routes.register(app, ctx)
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

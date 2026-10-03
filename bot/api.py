import os
import time

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from auth import InvalidInitData, validate_init_data
from db import get_player, init_db
from economy import HOUR

HOST = "127.0.0.1"  # только локально
PORT = 8000


def _unauthorized():
    # одно и то же сообщение при любой причине отказа
    return HTTPException(status_code=401, detail="Unauthorized")


def create_app(bot_token, allowed_origins, db_path=None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    # только перечисленные origin, только GET и заголовок Authorization
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allowed_origins),
        allow_methods=["GET"],
        allow_headers=["Authorization"],
    )

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

    return app


def create_app_from_env():
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("Не задан BOT_TOKEN в файле .env")
    origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()]
    if "*" in origins:
        raise SystemExit("В ALLOWED_ORIGINS нельзя указывать *, перечислите origin явно")
    init_db()  # без таблицы первый же валидный запрос упал бы с ошибкой
    return create_app(token, origins)


if __name__ == "__main__":
    uvicorn.run(create_app_from_env(), host=HOST, port=PORT)

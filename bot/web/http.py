"""Общее для HTTP-слоя: предел тела запроса, замеры времени, заголовки безопасности, одинаковые отказы."""
import logging
import time

from fastapi import HTTPException
from fastapi.responses import JSONResponse

import db

MAX_BODY_BYTES = 64 * 1024  # 47 ставок занимают около 3 КБ
WEBHOOK_PATH = "/telegram/webhook"


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


def _unauthorized():
    # одно и то же сообщение при любой причине отказа
    return HTTPException(status_code=401, detail="Unauthorized")


def _in_group(info):
    return info["chat_type"] in ("group", "supergroup") and info["chat_instance"] is not None


def _forbidden():
    return JSONResponse({"detail": "Forbidden"}, status_code=403)

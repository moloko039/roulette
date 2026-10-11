"""Регистрация маршрута забега свинок и расширение create_app."""
import api
from web import routes_race
from web.context import Ctx


def register(app, ctx):
    """Регистрирует маршруты забега свинок в приложении FastAPI."""
    routes_race.register(app, ctx)


_orig_create_app = api.create_app


def create_app(bot_token, allowed_origins, db_path=None, mode="api",
               public_url=None, webhook_secret=None, application=None, maintenance=None,
               rate_limiter=None):
    app = _orig_create_app(bot_token, allowed_origins, db_path=db_path, mode=mode,
                           public_url=public_url, webhook_secret=webhook_secret,
                           application=application, maintenance=maintenance,
                           rate_limiter=rate_limiter)
    ctx = Ctx(bot_token, db_path, rate_limiter, webhook_secret)
    register(app, ctx)
    return app


api.create_app = create_app

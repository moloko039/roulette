"""Фасад бота: обработчики лежат в пакете tg/ (common, user, data, payments, owner, groups, app).

Здесь они собраны под прежними именами (`bot.balance`, `bot.build_application`, ...): на них ссылаются тесты и `api.py`. Подменяемое состояние
(время, WEBAPP_URL, ограничители) живёт в модулях пакета: подменяй его там (`tg.common._wall`, `tg.common.WEBAPP_URL`)."""
from tg import app, common, data, groups, owner, payments, user

for _module in (common, user, data, payments, owner, groups, app):
    globals().update({k: v for k, v in vars(_module).items() if not k.startswith("__")})
del _module

if __name__ == "__main__":
    app.main()

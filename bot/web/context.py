"""Общее состояние маршрутов: настройки приложения, проверка подписи initData и ограничение частоты."""
from fastapi.responses import JSONResponse

from auth import InvalidInitData, validate_init_data, validate_init_data_full
from web.http import _unauthorized


class Ctx:
    def __init__(self, bot_token, db_path, rate_limiter, webhook_secret=None):
        self.bot_token = bot_token
        self.db_path = db_path
        self.rate_limiter = rate_limiter
        self.webhook_secret = webhook_secret

    def throttled(self, user_id, group):
        """Ограничение частоты (общее для всех игр). Вызывается только после проверки подписи initData.
        Возвращает ответ 429 с Retry-After или None. Без rate_limiter (тесты) ограничения нет."""
        if self.rate_limiter is None:
            return None
        wait = self.rate_limiter.check(user_id, group)
        if wait is None:
            return None
        return JSONResponse({"error": "too_many_requests"}, status_code=429, headers={"Retry-After": str(wait)})

    def _init_data(self, authorization):
        scheme, _, init_data = (authorization or "").partition(" ")
        if scheme != "tma":
            raise InvalidInitData()
        return init_data

    def auth(self, authorization):
        """id игрока из проверенной подписи заголовка Authorization (одинаковый 401 при любой причине отказа)."""
        try:
            return validate_init_data(self._init_data(authorization), self.bot_token)
        except InvalidInitData:
            raise _unauthorized()

    def auth_full(self, authorization):
        """Все проверенные поля initData (user_id, chat_instance, chat_type, first_name)."""
        try:
            return validate_init_data_full(self._init_data(authorization), self.bot_token)
        except InvalidInitData:
            raise _unauthorized()

    def user_of(self, headers, group):
        """id игрока из проверенной подписи и ограничение частоты; (user_id, ответ 429 или None)."""
        user_id = self.auth(headers.get("authorization"))
        return user_id, self.throttled(user_id, group)

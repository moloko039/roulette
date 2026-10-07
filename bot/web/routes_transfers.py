"""Переводы между игроками удалены (план экономики, этап E1): старые адреса отвечают 410 Gone.

Данные уже сделанных переводов остаются в таблице transfers до конца срока хранения (очистка, /mydata, /deletemydata работают как раньше)."""
from fastapi import Request
from fastapi.responses import JSONResponse


def register(app, ctx):
    def gone():
        return JSONResponse({"detail": "gone"}, status_code=410)

    @app.post("/api/transfers/send")
    async def transfers_send_endpoint(request: Request):
        return gone()

    @app.get("/api/transfers")
    def transfers_list_endpoint():
        return gone()

    @app.get("/api/chat/members")
    def chat_members_endpoint():
        return gone()

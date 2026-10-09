"""Игры: мины, кено, слот, блэкджек, краш, хило, рулетка. Общий разбор POST: _game_post."""
import json

from fastapi import Header, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

import blackjack
import hilo
import keno
import mines
import slot
from db import (blackjack_action, blackjack_start, blackjack_state, hilo_cashout, hilo_guess, hilo_start, hilo_state, mines_cashout, mines_reveal, mines_start, mines_state, play_keno, play_slot, spin_roulette)
from roulette import BalanceLimit, InsufficientFunds, InvalidBets, RequestConflict, validate_bets, validate_request_id
from web.http import BodyTooLarge, read_body_limited


def register(app, ctx):
    db_path = ctx.db_path
    throttled = ctx.throttled
    user_of = ctx.user_of

    # ---------- мины ----------
    # Раскладка мин активной игры не попадает ни в один ответ (только завершённые игры раскрывают mine_cells)
    async def _game_post(request, keys, run, optional=frozenset()):
        """Общий разбор POST мин: подпись, лимит, тело с точным набором ключей, ошибки в одном формате."""
        user_id, limited = user_of(request.headers, "write")
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
        except (mines.MinesError, keno.KenoError, blackjack.BlackjackError, hilo.HiloError, slot.SlotError) as exc:
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
        return await _game_post(request, {"request_id", "bet", "mines"}, prepare)

    @app.post("/api/mines/reveal")
    async def mines_reveal_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            cell = data["cell"]
            if type(cell) is not int or not 0 <= cell < mines.FIELD_CELLS:
                raise ValueError()
            return lambda: mines_reveal(user_id, request_id, cell, db_path=db_path)
        return await _game_post(request, {"request_id", "cell"}, prepare)

    @app.post("/api/mines/cashout")
    async def mines_cashout_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            return lambda: mines_cashout(user_id, request_id, db_path=db_path)
        return await _game_post(request, {"request_id"}, prepare)

    @app.get("/api/mines/state")
    def mines_state_endpoint(authorization: str = Header(default=None)):
        user_id, limited = user_of({"authorization": authorization}, "read")
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
        return await _game_post(request, {"request_id", "bet", "picks"}, prepare)

    @app.get("/api/keno/paytable")
    def keno_paytable_endpoint(authorization: str = Header(default=None)):
        user_id, limited = user_of({"authorization": authorization}, "read")
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
        return await _game_post(request, {"request_id", "coin", "buy"}, prepare)

    # ---------- блэкджек ----------
    # Колода и скрытая карта дилера активной раздачи не попадают ни в один ответ

    @app.post("/api/blackjack/start")
    async def blackjack_start_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            bet = data["bet"]
            if type(bet) is not int or not 1 <= bet <= blackjack.BLACKJACK_MAX_BET:
                raise ValueError()
            return lambda: blackjack_start(user_id, request_id, bet, db_path=db_path)
        return await _game_post(request, {"request_id", "bet"}, prepare)

    @app.post("/api/blackjack/action")
    async def blackjack_action_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            action = data["action"]
            if type(action) is not str or action not in blackjack.ACTIONS:
                raise ValueError()
            return lambda: blackjack_action(user_id, request_id, action, db_path=db_path)
        return await _game_post(request, {"request_id", "action"}, prepare)

    @app.get("/api/blackjack/state")
    def blackjack_state_endpoint(authorization: str = Header(default=None)):
        user_id, limited = user_of({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return blackjack_state(user_id, db_path=db_path)

    # ---------- краш ----------
    # Прежний краш (партия на игрока) закрыт, играют в живой краш (routes_crash_live.py): старые адреса отвечают 410 Gone

    @app.post("/api/crash/start")
    @app.post("/api/crash/cashout")
    @app.get("/api/crash/state")
    def crash_gone():
        return JSONResponse({"detail": "gone"}, status_code=410)

    # ---------- хило ----------
    # Следующей карты нет нигде до хода: она выбирается в момент действия

    @app.post("/api/hilo/start")
    async def hilo_start_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            bet = data["bet"]
            if type(bet) is not int or not 1 <= bet <= hilo.HILO_MAX_BET:
                raise ValueError()
            return lambda: hilo_start(user_id, request_id, bet, db_path=db_path)
        return await _game_post(request, {"request_id", "bet"}, prepare)

    @app.post("/api/hilo/guess")
    async def hilo_guess_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            choice = data["choice"]
            if type(choice) is not str or choice not in hilo.CHOICES:
                raise ValueError()
            return lambda: hilo_guess(user_id, request_id, choice, db_path=db_path)
        return await _game_post(request, {"request_id", "choice"}, prepare)

    @app.post("/api/hilo/cashout")
    async def hilo_cashout_endpoint(request: Request):
        def prepare(user_id, request_id, data):
            return lambda: hilo_cashout(user_id, request_id, db_path=db_path)
        return await _game_post(request, {"request_id"}, prepare)

    @app.get("/api/hilo/state")
    def hilo_state_endpoint(authorization: str = Header(default=None)):
        user_id, limited = user_of({"authorization": authorization}, "read")
        if limited is not None:
            return limited
        return hilo_state(user_id, db_path=db_path)

    @app.post("/api/roulette/spin")
    async def roulette_spin(request: Request):
        # подпись проверяется так же, как в /api/me; id игрока только из проверенных данных
        user_id = ctx.auth(request.headers.get("authorization"))
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

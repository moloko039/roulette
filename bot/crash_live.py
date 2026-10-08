"""Живой краш: чистые функции и исключения."""

import hashlib
import hmac
import secrets

import crash
import economy_config


class BettingClosed(crash.CrashError):
    code = "betting_closed"


class AlreadyBet(crash.CrashError):
    code = "already_bet"


class NoBet(crash.CrashError):
    code = "no_bet"


class RoomFull(crash.CrashError):
    code = "room_full"


class TooEarly(crash.TooEarly):
    pass


class RoundOver(crash.CrashError):
    code = "round_over"


class RequestConflict(crash.RequestConflict):
    pass


GLOBAL_ROOM = "live"      # раунды общие на весь сервер: один раунд, одна точка краха, один секрет на всех (crash_rounds.room_key всегда равен этому значению)


def room_key(chat_instance=None, telegram_id=None):
    """Ключ КОМНАТЫ-ЛЕНТЫ ставок: беседа (по chat_instance) или личная комната игрока. Раунд один на всех, комната только решает, чьи ставки игрок видит."""
    if chat_instance is not None:
        base = b"crashroom:chat:" + str(chat_instance).encode("utf-8")
    elif telegram_id is not None:
        base = b"crashroom:solo:" + str(telegram_id).encode("utf-8")
    else:
        raise ValueError("Either chat_instance or telegram_id required")
    return hashlib.sha256(base).hexdigest()


def new_seed(rng=None):
    if rng is None:
        return secrets.token_bytes(32)
    return bytes(rng.randrange(256) for _ in range(32))


def commit_of(seed):
    return hashlib.sha256(seed).hexdigest()


def crash_from_seed(seed):
    u = int.from_bytes(hmac.new(seed, b"crash", hashlib.sha256).digest()[:7], "big") % crash.M
    return crash.crash_from_u(u)


def verify(seed_hex, commit_hex, crash_x100):
    try:
        seed = bytes.fromhex(seed_hex)
        return commit_of(seed) == commit_hex and crash_from_seed(seed) == crash_x100
    except ValueError:
        return False


def end_ms(round_row_like):
    """Серверное время закрытия раунда: точка краха плюс запас сети GRACE_MS. «Одно эффективное время» (now - GRACE - старт полёта) во всех проверках:
    вывод, пришедший в пределах запаса после краха по серверным часам, но отправленный до него, не проигрывает из-за задержки сети."""
    return round_row_like["crash_ms"] + crash.GRACE_MS


def next_open_ms(round_row_like):
    """Когда откроется приём ставок следующего раунда: закрытие раунда плюс пауза итога."""
    return end_ms(round_row_like) + economy_config.CRASH_LIVE_RESULT_MS


def phase_of(round_row_like, now_ms):
    if round_row_like["status"] == "closed":
        return "result" if now_ms < next_open_ms(round_row_like) else "idle"
    if now_ms < round_row_like["flight_start_ms"]:
        return "betting"
    if now_ms < end_ms(round_row_like):
        return "flight"
    return "result"

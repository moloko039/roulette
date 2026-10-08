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


def room_key(chat_instance=None, telegram_id=None):
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


def phase_of(round_row_like, now_ms):
    status = round_row_like["status"]
    if status == "closed":
        if now_ms < round_row_like["crash_ms"] + economy_config.CRASH_LIVE_RESULT_MS:
            return "result"
        return "idle"
    
    if now_ms < round_row_like["flight_start_ms"]:
        return "betting"
    if now_ms < round_row_like["crash_ms"]:
        return "flight"
    return "result"

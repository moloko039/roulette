"""Общий забег свинок: чистые правила времени. Раунд один на весь сервер, номер раунда = окно времени, расписание без базы и фоновых задач.

Окно PERIOD_MS: приём ставок BET_MS с начала окна, затем забег RACE_MS (клиент проигрывает анимацию по script), затем итог до конца окна.
Дорожек всегда LANES; выплата как в одиночном забеге (race.payout): floor(ставка * 4 * 36 / 37)."""
import crash_live
import race

PERIOD_MS = 30000
BET_MS = 20000
RACE_MS = 8000
LANES = 4
RequestConflict = race.RequestConflict


class RaceLiveError(race.RaceError):
    code = "race_live_error"


class BettingClosed(RaceLiveError):
    code = "betting_closed"


class AlreadyBet(RaceLiveError):
    code = "already_bet"


class RoundFull(RaceLiveError):
    code = "round_full"


def round_id(now_ms):
    return now_ms // PERIOD_MS


def start_ms(rid):
    return rid * PERIOD_MS


def close_ms(rid):
    return rid * PERIOD_MS + BET_MS


def phase_of(rid, now_ms):
    """bets (приём) -> race (забег идёт) -> result (итог) для раунда rid в момент now_ms."""
    off = now_ms - start_ms(rid)
    if off < BET_MS:
        return "bets"
    return "race" if off < BET_MS + RACE_MS else "result"


room_key = crash_live.room_key
PUBLIC_ROOM_KEY = crash_live.PUBLIC_ROOM_KEY
now_ms = crash_live.now_ms

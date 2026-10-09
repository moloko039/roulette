"""Помощники тестов живого краша: раунд с заданной точкой краха (подмена crash_from_seed), ставка, закрытие по времени. Время передаётся в миллисекундах явно."""
from unittest.mock import patch

import crash
import crash_live
from core.db_conn import _connect
from features.crash_live_db import advance_round, cashout, place_bet


def open_round(path, t, x100):
    """Открывает раунд в момент t с точкой краха x100 (предыдущий должен быть закрыт, а пауза итога пройти). Возвращает строку раунда (dict)."""
    with patch.object(crash_live, "crash_from_seed", lambda seed: x100):
        advance_round(t, db_path=path)
    conn = _connect(path)
    try:
        return dict(conn.execute("SELECT * FROM crash_rounds ORDER BY id DESC LIMIT 1").fetchone())
    finally:
        conn.close()


def bet(path, uid, t, amount, target=None, request_id=None):
    return place_bet(uid, crash_live.room_key(), request_id or "b-%d-%d" % (uid, t), amount, target, t, db_path=path)


def cash(path, uid, t, request_id=None):
    return cashout(uid, request_id or "c-%d-%d" % (uid, t), t, db_path=path)


def end_round(path, rnd):
    """Время сразу после краха плюс запас сети: раунд закрывается, проигравшие и автовыводы рассчитаны. Следующий раунд ещё не открывается (идёт пауза итога)."""
    t = rnd["crash_ms"] + crash.GRACE_MS + 1
    advance_round(t, db_path=path)
    return t


def next_open(rnd):
    return crash_live.next_open_ms(rnd)

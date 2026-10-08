"""Фасад работы с базой: имена db.<имя> остаются прежними (api.py, bot.py, backup.py, тесты).

Код лежит в слоях core/, features/, games/ (карта: docs/ARCHITECTURE.md): games и features зависят только от core.
Модули стандартной библиотеки и проекта ниже импортированы ради имён db.time, db.economy и т.п., которыми пользуются тесты."""

import contextvars
import json
import logging
import os
import re
import unicodedata
import secrets
import sqlite3
import time
import antiabuse
from antiabuse import COOLDOWN_SECONDS, TombstoneUnavailable
import economy
from economy import START_BALANCE, BASE_RATE, accrue
import blackjack
import crash
import hilo
import farm
import hmac as _hmac
import keno
import mines
import transfers
import wallet
import xp
from levels import profile_level
import roulette
from roulette import BalanceLimit, MAX_SAFE_INT, max_payout, settle


from core.db_conn import (
    logger, DB_PATH, _resolve_path, request_timing, BUSY_TIMEOUT_SECONDS, BUSY_RETRY_DELAY, is_busy_error, TimedConnection,
    JOURNAL_MODE_ENV, _wal_by_path, _journal_warned, wal_version_safe, _journal_warn, journal_mode_flag, _pragma_value,
    _apply_journal_mode, _is_wal, log_sqlite_mode, _connect,
)
from core.schema import init_db
from core.migrations import (
    _migrate_total_staked, _migrate_xp, _migrate_farm_levels, _migrate_transfers_seen, _migrate_minute_accrual,
)
from core.kernel import (
    _accrue_conn, _pending_accrual, get_meta, set_meta, _register_player, _accrue_write, _credit_capped, _add_xp,
)
from core.members import _member_name, NAME_MAX, DEFAULT_NAME, TOUCH_INTERVAL, MAX_CHAT_MEMBERS, _BIDI, clean_name, _touch_member
from core.players import get_player
from features.give_db import GIVE_MAX_AMOUNT, PlayerMissing, give_owner
from features.chat_db import TOP_SIZE, touch_chat_member, chat_top, chat_best_wins
from features.active_game_db import active_game_of
from features.gifts_db import GiftError, gift_recipients, send_gift
from features.streak_db import StreakError, claim_streak, streak_status
from features.stats_db import economy_stats, stats_text
from features.chips_db import ChipsError, UnknownChipPack, DailyLimit, chips_in_pack, chip_packs_state, buy_chip_pack
from features.chips_db import pack as chip_pack
from features.gems_db import (
    GemsError, UnknownPack, pack, packs_view, gems_state, record_gem_payment, gem_purchase_by_charge, refund_check, finish_gem_refund, owner_grant_gems,
)
from features.grants_db import GRANT_MAX_AMOUNT, GRANT_ID_RE, GrantExists, validate_grant, grant_preview, grant_all
from features.bot_chats_db import chat_register, chat_forget, chat_ids
from features.farm_db import buy_upgrade, farm_status
from features.cosmetics_db import (
    CHANGE_INTERVAL_SECONDS, cosmetics_state, cosmetics_mine, grant_item, equip_item, unequip_item, set_visibility,
    buy_with_chips, buy_with_gems, buy_item, stars_offer, record_stars_payment, purchase_by_charge, mark_refunded, regrant_purchase,
)
from features.data_rights_db import get_player_export, delete_player_data
from features.purge_db import PURGE_BATCH, purge_old_data
from features.referral_db import get_or_create_code, link_for, bind_referral_in
from games.roulette_db import _round_result, spin_roulette
from games.keno_db import _keno_result, play_keno
from games.slot_db import _slot_result, play_slot
from games.blackjack_db import (
    _bj_active, _bj_state, _bj_save, _bj_finish, _bj_response, _bj_settle_expired_in, settle_expired_blackjack,
    BLACKJACK_CLOSE_BATCH, close_expired_blackjack, _run_blackjack_action, blackjack_start, blackjack_action, blackjack_state,
)
from games.crash_db import (
    _now_ms, _crash_active, _crash_view, _crash_none_view, _crash_response, _crash_finish, _crash_settle_in, settle_expired_crash,
    CRASH_CLOSE_BATCH, close_expired_crash, _run_crash_action, crash_start, crash_cashout, crash_state,
)
from games.hilo_db import (
    _hilo_active, HOW_TEXT, _hilo_cards, _hilo_moves, _hilo_view, _hilo_none_view, _hilo_response, _hilo_finish, _hilo_settle_in,
    settle_expired_hilo, HILO_CLOSE_BATCH, close_expired_hilo, _run_hilo_action, hilo_start, hilo_guess, hilo_cashout, hilo_state,
)
from games.mines_db import (
    _active_game, _game_view, _last_view, _finish_game, _settle_expired_in, settle_expired_mines, MINES_CLOSE_BATCH,
    close_expired_mines, _run_mines_action, mines_start, mines_reveal, mines_cashout, mines_state,
)


def _install():
    """Тесты патчат константы через фасад (mock.patch.object(db, "BUSY_TIMEOUT_SECONDS", ...)); код читает их из своих модулей.
    Присваивание атрибута фасада поэтому дублируется в модулях слоёв, где это имя определено или импортировано."""
    import sys
    import types
    family = [m for n, m in list(sys.modules.items()) if n.startswith(("core.", "features.", "games.")) and m is not None]

    class _Facade(types.ModuleType):
        def __setattr__(self, name, value):
            for m in family:
                if name in m.__dict__:
                    m.__dict__[name] = value
            super().__setattr__(name, value)

    sys.modules[__name__].__class__ = _Facade


_install()
del _install

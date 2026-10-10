"""Схема базы: init_db создаёт таблицы и индексы (одной функцией) и вызывает миграции."""

import time

from antiabuse import COOLDOWN_SECONDS

from core.db_conn import _apply_journal_mode, _connect, _resolve_path
from core.migrations import (
    _migrate_farm_levels, _migrate_last_played_at, _migrate_minute_accrual, _migrate_referral_commission, _migrate_total_staked, _migrate_transfers_seen, _migrate_xp,
)


def init_db(db_path=None):
    conn = _connect(db_path)
    try:
        _apply_journal_mode(conn, _resolve_path(db_path))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS players (
                telegram_id  INTEGER PRIMARY KEY,
                balance      INTEGER NOT NULL,
                rate         INTEGER NOT NULL,
                last_accrual INTEGER NOT NULL,
                accrual_acc  INTEGER NOT NULL DEFAULT 0,
                created_at   INTEGER NOT NULL,
                total_staked INTEGER NOT NULL DEFAULT 0,
                xp           INTEGER NOT NULL DEFAULT 0,
                income_level INTEGER NOT NULL DEFAULT 0,
                storage_level INTEGER NOT NULL DEFAULT 0,
                transfers_seen_at INTEGER NOT NULL DEFAULT 0,
                last_played_at INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # раунды рулетки: ключ (игрок, request_id) защищает от повторного списания при повторе запроса
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS roulette_rounds (
                telegram_id  INTEGER NOT NULL,
                request_id   TEXT    NOT NULL,
                number       INTEGER NOT NULL,
                stake_total  INTEGER NOT NULL,
                payout_total INTEGER NOT NULL,
                bets_json    TEXT    NOT NULL,
                created_at   INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
        # раунды кено: ключ (игрок, request_id) защищает от повторного списания при повторе запроса
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS keno_rounds (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                request_id  TEXT    NOT NULL,
                bet         INTEGER NOT NULL,
                picks_json  TEXT    NOT NULL,
                draw_json   TEXT    NOT NULL,
                hit_count   INTEGER NOT NULL,
                payout      INTEGER NOT NULL,
                created_at  INTEGER NOT NULL,
                UNIQUE (telegram_id, request_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_keno_created ON keno_rounds(created_at)")
        # раунды Western Slot (встроенный слот на фишках приложения): весь раунд в round_json; ключ (игрок, request_id) против повторного списания
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS slot_rounds (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                request_id  TEXT    NOT NULL,
                coin        INTEGER NOT NULL,
                bought      INTEGER NOT NULL,
                cost        INTEGER NOT NULL,
                payout      INTEGER NOT NULL,
                round_json  TEXT    NOT NULL,
                created_at  INTEGER NOT NULL,
                UNIQUE (telegram_id, request_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_slot_created ON slot_rounds(created_at)")
        # покупки улучшений фермы: ключ (игрок, request_id) защищает от повторного списания при повторе запроса
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS farm_purchases (
                telegram_id INTEGER NOT NULL,
                request_id  TEXT    NOT NULL,
                kind        TEXT    NOT NULL,
                level_after INTEGER NOT NULL,
                cost        INTEGER NOT NULL,
                created_at  INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
        # игры в мины: раскладка (mine_mask) хранится только здесь и никогда не уходит клиенту до конца игры
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mines_games (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id    INTEGER NOT NULL,
                bet            INTEGER NOT NULL,
                mines_count    INTEGER NOT NULL,
                mine_mask      INTEGER NOT NULL,
                revealed_mask  INTEGER NOT NULL DEFAULT 0,
                status         TEXT    NOT NULL,
                payout         INTEGER NOT NULL DEFAULT 0,
                staked_counted INTEGER NOT NULL DEFAULT 0,
                created_at     INTEGER NOT NULL,
                updated_at     INTEGER NOT NULL,
                finished_at    INTEGER
            )
            """
        )
        # не больше одной активной игры на игрока
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_mines_active ON mines_games(telegram_id) WHERE status = 'active'"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mines_history ON mines_games(telegram_id, finished_at)"
        )
        # действия в игре: ключ (игрок, request_id) даёт идемпотентность повторов
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mines_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
        # блэкджек: порядок колоды (deck_json) хранится только здесь и только пока игра идёт (после конца очищается)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blackjack_games (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                bet         INTEGER NOT NULL,
                wager       INTEGER NOT NULL,
                deck_json   TEXT    NOT NULL,
                deck_pos    INTEGER NOT NULL,
                player_json TEXT    NOT NULL,
                dealer_json TEXT    NOT NULL,
                status      TEXT    NOT NULL,
                result      TEXT,
                payout      INTEGER,
                auto        INTEGER NOT NULL DEFAULT 0,
                created_at  INTEGER NOT NULL,
                updated_at  INTEGER NOT NULL,
                finished_at INTEGER
            )
            """
        )
        # не больше одной активной раздачи на игрока
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_blackjack_active ON blackjack_games(telegram_id) WHERE status = 'active'"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_blackjack_history ON blackjack_games(telegram_id, finished_at)"
        )
        # действия: ключ (игрок, request_id) даёт идемпотентность повторов
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blackjack_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
        # краш: точка краха (crash_x100) хранится только здесь и никогда не уходит клиенту, пока раунд идёт
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crash_games (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id   INTEGER NOT NULL,
                bet           INTEGER NOT NULL,
                mode          TEXT    NOT NULL,
                target_x100   INTEGER,
                crash_x100    INTEGER NOT NULL,
                started_at_ms INTEGER NOT NULL,
                status        TEXT    NOT NULL,
                result        TEXT,
                mult_x100     INTEGER,
                payout        INTEGER,
                auto          INTEGER NOT NULL DEFAULT 0,
                created_at    INTEGER NOT NULL,
                finished_at   INTEGER
            )
            """
        )
        # не больше одного активного раунда на игрока
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_crash_active ON crash_games(telegram_id) WHERE status = 'active'"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_crash_history ON crash_games(telegram_id, finished_at)")
        # действия: ключ (игрок, request_id) даёт идемпотентность повторов
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crash_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
        # живой краш: раунды комнат
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crash_rounds (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                room_key        TEXT    NOT NULL,
                seed_hash       TEXT    NOT NULL,
                seed            BLOB    NOT NULL,
                crash_x100      INTEGER NOT NULL,
                bet_open_ms     INTEGER NOT NULL,
                flight_start_ms INTEGER NOT NULL,
                crash_ms        INTEGER NOT NULL,
                status          TEXT    NOT NULL,
                settled_at_ms   INTEGER
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_crash_rounds_room ON crash_rounds(room_key, id)")
        # живой краш: ставки в раундах
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS crash_bets (
                round_id      INTEGER NOT NULL,
                telegram_id   INTEGER NOT NULL,
                bet           INTEGER NOT NULL,
                target_x100   INTEGER,
                cashed_x100   INTEGER,
                payout        INTEGER NOT NULL DEFAULT 0,
                status        TEXT    NOT NULL,
                request_id    TEXT    NOT NULL,
                created_at_ms INTEGER NOT NULL,
                room_key TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (round_id, telegram_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_crash_bets_player ON crash_bets(telegram_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_crash_bets_room ON crash_bets(round_id, room_key)")
        # хило: следующая карта нигде не хранится (выбирается в момент хода); множитель дробью из двух целых (текстом: числа
        # бывают длиннее 64 бит); hist_json: последние карты раунда [достоинство, масть, как выпала], текущая карта последняя
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hilo_games (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id    INTEGER NOT NULL,
                bet            INTEGER NOT NULL,
                card_rank      INTEGER NOT NULL,
                card_suit      TEXT    NOT NULL,
                steps          INTEGER NOT NULL DEFAULT 0,
                mult_num       TEXT    NOT NULL,
                mult_den       TEXT    NOT NULL,
                hist_json      TEXT    NOT NULL,
                status         TEXT    NOT NULL,
                payout         INTEGER NOT NULL DEFAULT 0,
                staked_counted INTEGER NOT NULL DEFAULT 0,
                auto           INTEGER NOT NULL DEFAULT 0,
                created_at     INTEGER NOT NULL,
                updated_at     INTEGER NOT NULL,
                finished_at    INTEGER
            )
            """
        )
        # не больше одной активной партии на игрока
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_hilo_active ON hilo_games(telegram_id) WHERE status = 'active'"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hilo_history ON hilo_games(telegram_id, finished_at)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hilo_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
        # участники бесед для рейтинга: chat_instance — глобальный id чата из подписанных данных
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_members (
                chat_instance TEXT    NOT NULL,
                telegram_id   INTEGER NOT NULL,
                first_name    TEXT    NOT NULL,
                first_seen    INTEGER NOT NULL,
                last_seen     INTEGER NOT NULL,
                PRIMARY KEY (chat_instance, telegram_id)
            )
            """
        )
        # выборка последних активных участников беседы (список «Кому перевести», поиск получателя перевода)
        conn.execute("CREATE INDEX IF NOT EXISTS chat_members_recent ON chat_members (chat_instance, last_seen)")
        # поиск записей участника по игроку (имя второй стороны в истории переводов, выгрузка и удаление данных): первичный ключ начинается с беседы и
        # не помогает; без индекса каждый такой запрос читал всю таблицу. last_seen вторым столбцом: «последняя запись игрока» без сортировки
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_members_telegram ON chat_members (telegram_id, last_seen)")
        # «надгробия» после удаления данных: только хэш идентификатора и дата удаления
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deletion_tombstones (
                key_hash   TEXT    PRIMARY KEY,
                deleted_at INTEGER NOT NULL
            )
            """
        )
        # разовые начисления владельца: одна итоговая строка на начисление, без списков игроков и сумм по игрокам
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_grants (
                grant_id    TEXT    PRIMARY KEY,
                amount      INTEGER NOT NULL,
                created_at  INTEGER NOT NULL,
                players     INTEGER NOT NULL,
                total_given INTEGER NOT NULL
            )
            """
        )
        # группы, где состоит бот: только числовой chat_id и время последнего подтверждения (для объявлений владельца)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_chats (
                chat_id INTEGER PRIMARY KEY,
                seen_at INTEGER NOT NULL
            )
            """
        )
        # переводы между участниками беседы: сумма, комиссия (получатель комиссии определяется конфигурацией, не хранится), время
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transfers (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                sender     INTEGER NOT NULL,
                recipient  INTEGER NOT NULL,
                amount     INTEGER NOT NULL,
                fee        INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                request_id TEXT    NOT NULL,
                UNIQUE (sender, request_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transfers_sender ON transfers(sender, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transfers_recipient ON transfers(recipient, created_at)")
        # косметика (bot/cosmetics.py): принадлежность, надетое по слотам, настройка показа в рейтинге, идемпотентность действий.
        # Стартовые предметы строками не хранятся. Игровой код эти таблицы не читает.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cosmetic_items (
                telegram_id INTEGER NOT NULL,
                item_code   TEXT    NOT NULL,
                source      TEXT    NOT NULL,
                payment_ref TEXT,
                acquired_at INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, item_code)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cosmetic_equipped (
                telegram_id INTEGER NOT NULL,
                slot        TEXT    NOT NULL,
                item_code   TEXT    NOT NULL,
                PRIMARY KEY (telegram_id, slot)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cosmetic_prefs (
                telegram_id    INTEGER PRIMARY KEY,
                show_in_rating INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cosmetic_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            )
            """
        )
        # журнал оплат косметики Telegram Stars: хранится для споров и возвратов и после удаления данных игрока (срок: cosmetics.PURCHASE_RETENTION_DAYS)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cosmetic_purchases (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                charge_id    TEXT    NOT NULL UNIQUE,
                telegram_id  INTEGER NOT NULL,
                item_code    TEXT    NOT NULL,
                amount_stars INTEGER NOT NULL,
                status       TEXT    NOT NULL,
                created_at   INTEGER NOT NULL,
                refunded_at  INTEGER
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cosmetic_purchases_player ON cosmetic_purchases(telegram_id, created_at)")
        # служебные отметки (время последних уведомлений владельцу); личных данных здесь нет
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS service_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        # личный рекорд игрока: лучший чистый выигрыш за один раунд по всем играм (одна строка на игрока; пишет только core.kernel._record_best_win)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS player_best_win (
                telegram_id INTEGER PRIMARY KEY,
                game        TEXT    NOT NULL,
                net_amount  INTEGER NOT NULL,
                achieved_at INTEGER NOT NULL
            )
            """
        )
        # кристаллы (план экономики, E2): журнал только дописывается, баланс-кэш пишет только wallet, баланс всегда равен сумме журнала
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gems_ledger (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                delta       INTEGER NOT NULL,
                reason      TEXT    NOT NULL,
                ref         TEXT,
                created_at  INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gems_ledger_player ON gems_ledger(telegram_id, id)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_gems_ledger_ref ON gems_ledger(telegram_id, reason, ref) WHERE ref IS NOT NULL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gem_balances (
                telegram_id INTEGER PRIMARY KEY,
                gems        INTEGER NOT NULL CHECK (gems >= 0)
            )
            """
        )
        # оплаты пакетов кристаллов Stars: charge_id уникален (повторная доставка платежа ничего не создаёт); хранится 365 дней, как журнал оплат косметики
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gem_purchases (
                charge_id    TEXT    PRIMARY KEY,
                telegram_id  INTEGER NOT NULL,
                pack_code    TEXT    NOT NULL,
                amount_stars INTEGER NOT NULL,
                gems         INTEGER NOT NULL,
                status       TEXT    NOT NULL,
                created_at   INTEGER NOT NULL,
                refunded_at  INTEGER
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gem_purchases_player ON gem_purchases(telegram_id, created_at)")
        # покупки пакетов фишек за кристаллы (E6): (игрок, request_id) уникален, повтор отдаёт то же; хранится 365 дней
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chip_purchases (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                request_id  TEXT    NOT NULL,
                pack_code   TEXT    NOT NULL,
                gems        INTEGER NOT NULL,
                chips       INTEGER NOT NULL,
                created_at  INTEGER NOT NULL,
                UNIQUE (telegram_id, request_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chip_purchases_player ON chip_purchases(telegram_id, created_at)")
        # награды серии входов: одна строка на игрока и «день» (московская дата), по ней же состояние серии; (игрок, день) уникален, повтор запроса отдаёт то же
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS streak_claims (
                telegram_id INTEGER NOT NULL,
                day         INTEGER NOT NULL,
                streak_day  INTEGER NOT NULL,
                cycle       INTEGER NOT NULL,
                chips       INTEGER NOT NULL,
                gems        INTEGER NOT NULL,
                created_at  INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, day)
            )
            """
        )
        # подарки косметикой (E-подарки): (отправитель, request_id) уникален; имя отправителя на момент подарка нужно получателю («подарок от …»); хранится 365 дней
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gifts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                from_user  INTEGER NOT NULL,
                to_user    INTEGER NOT NULL,
                from_name  TEXT    NOT NULL,
                item_code  TEXT    NOT NULL,
                gems       INTEGER NOT NULL,
                request_id TEXT    NOT NULL,
                created_at INTEGER NOT NULL,
                UNIQUE (from_user, request_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gifts_to ON gifts(to_user, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_gifts_from ON gifts(from_user, created_at)")
        # бусты бесед (E4)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_boosts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_instance TEXT    NOT NULL,
                telegram_id   INTEGER NOT NULL,
                created_at    INTEGER NOT NULL,
                expires_at    INTEGER NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_boosts_chat ON chat_boosts(chat_instance)")

        # рефералка (E5)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referral_codes (
                telegram_id INTEGER PRIMARY KEY,
                code        TEXT    NOT NULL UNIQUE,
                created_at  INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS referrals (
                invitee_id   INTEGER PRIMARY KEY,
                referrer_id  INTEGER NOT NULL,
                created_at   INTEGER NOT NULL,
                qualified_at INTEGER
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_referrals_referrer ON referrals(referrer_id)")
        # порядковый номер основателя (DESIGN.md раздел 6): выдаётся один раз, когда игрок получает первую веху рефералки (3 квалифицированных друга); номера не переиспользуются
        conn.execute("CREATE TABLE IF NOT EXISTS founder_numbers (telegram_id INTEGER PRIMARY KEY, no INTEGER NOT NULL UNIQUE)")
        conn.execute(
            "INSERT OR IGNORE INTO founder_numbers (telegram_id, no) "
            "SELECT telegram_id, (SELECT COALESCE(MAX(no), 0) FROM founder_numbers) + ROW_NUMBER() OVER (ORDER BY acquired_at, telegram_id) "
            "FROM cosmetic_items WHERE item_code = 'ref_scout' AND telegram_id NOT IN (SELECT telegram_id FROM founder_numbers)")      # игроки, получившие веху до введения номеров
        # основатель беседы: игрок, добавивший бота в группу (chat_id группы уже хранится в bot_chats); chat_instance беседы в игре привязывается, когда основатель сам открывает игру из неё
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_founders (
                chat_id       INTEGER PRIMARY KEY,
                founder_id    INTEGER NOT NULL,
                added_at      INTEGER NOT NULL,
                chat_instance TEXT,
                rewarded_at   INTEGER
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_founders_founder ON chat_founders(founder_id, added_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_founders_instance ON chat_founders(chat_instance)")

        # достижения (Клуб ×1.01)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS achievement_progress (
                telegram_id INTEGER NOT NULL,
                code        TEXT    NOT NULL,
                count       INTEGER NOT NULL,
                streak      INTEGER NOT NULL,
                done_at     INTEGER,
                PRIMARY KEY (telegram_id, code)
            )
            """
        )
        
        _migrate_total_staked(conn)
        _migrate_xp(conn)
        _migrate_farm_levels(conn)
        _migrate_transfers_seen(conn)
        _migrate_minute_accrual(conn)
        _migrate_last_played_at(conn)
        _migrate_referral_commission(conn)
        # записи старше срока защиты не нужны
        conn.execute(
            "DELETE FROM deletion_tombstones WHERE deleted_at + ? <= ?",
            (COOLDOWN_SECONDS, int(time.time())),
        )
    finally:
        conn.close()

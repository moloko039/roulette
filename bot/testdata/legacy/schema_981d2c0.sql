-- Фикстура схемы старой версии (коммит 981d2c0) с вымышленными данными: идентификаторы 9000000xx, имена и суммы выдуманы, настоящих данных нет.
-- Сгенерирована кодом того коммита (init_db) и дампом sqlite3; тест test_legacy_migration.py открывает её текущей init_db и сверяет строки.
BEGIN TRANSACTION;
CREATE TABLE admin_grants (
                grant_id    TEXT    PRIMARY KEY,
                amount      INTEGER NOT NULL,
                created_at  INTEGER NOT NULL,
                players     INTEGER NOT NULL,
                total_given INTEGER NOT NULL
            );
CREATE TABLE blackjack_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            );
CREATE TABLE blackjack_games (
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
            );
CREATE TABLE bot_chats (
                chat_id INTEGER PRIMARY KEY,
                seen_at INTEGER NOT NULL
            );
CREATE TABLE chat_members (
                chat_instance TEXT    NOT NULL,
                telegram_id   INTEGER NOT NULL,
                first_name    TEXT    NOT NULL,
                first_seen    INTEGER NOT NULL,
                last_seen     INTEGER NOT NULL,
                PRIMARY KEY (chat_instance, telegram_id)
            );
INSERT INTO "chat_members" VALUES('legacy-chat',900000001,'Тест',1759999800,1759999900);
INSERT INTO "chat_members" VALUES('legacy-chat',900000002,'Другой',1759999800,1759999910);
CREATE TABLE cosmetic_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            );
CREATE TABLE cosmetic_equipped (
                telegram_id INTEGER NOT NULL,
                slot        TEXT    NOT NULL,
                item_code   TEXT    NOT NULL,
                PRIMARY KEY (telegram_id, slot)
            );
INSERT INTO "cosmetic_equipped" VALUES(900000002,'chip','chip_ring');
CREATE TABLE cosmetic_items (
                telegram_id INTEGER NOT NULL,
                item_code   TEXT    NOT NULL,
                source      TEXT    NOT NULL,
                payment_ref TEXT,
                acquired_at INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, item_code)
            );
INSERT INTO "cosmetic_items" VALUES(900000002,'chip_ring','owner_gift',NULL,1759999960);
CREATE TABLE cosmetic_prefs (
                telegram_id    INTEGER PRIMARY KEY,
                show_in_rating INTEGER NOT NULL DEFAULT 1
            );
INSERT INTO "cosmetic_prefs" VALUES(900000002,0);
CREATE TABLE cosmetic_purchases (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                charge_id    TEXT    NOT NULL UNIQUE,
                telegram_id  INTEGER NOT NULL,
                item_code    TEXT    NOT NULL,
                amount_stars INTEGER NOT NULL,
                status       TEXT    NOT NULL,
                created_at   INTEGER NOT NULL,
                refunded_at  INTEGER
            );
CREATE TABLE crash_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            );
CREATE TABLE crash_games (
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
            );
CREATE TABLE deletion_tombstones (
                key_hash   TEXT    PRIMARY KEY,
                deleted_at INTEGER NOT NULL
            );
CREATE TABLE farm_purchases (
                telegram_id INTEGER NOT NULL,
                request_id  TEXT    NOT NULL,
                kind        TEXT    NOT NULL,
                level_after INTEGER NOT NULL,
                cost        INTEGER NOT NULL,
                created_at  INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            );
CREATE TABLE hilo_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            );
CREATE TABLE hilo_games (
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
            );
CREATE TABLE keno_rounds (
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
            );
INSERT INTO "keno_rounds" VALUES(1,900000002,'legacy-keno-0001',100,'[1,2,3]','[1,2,3,4,5,6,7,8,9,10]',3,367,1759999950);
CREATE TABLE mines_actions (
                telegram_id   INTEGER NOT NULL,
                request_id    TEXT    NOT NULL,
                action        TEXT    NOT NULL,
                params        TEXT    NOT NULL,
                response_json TEXT    NOT NULL,
                created_at    INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            );
CREATE TABLE mines_games (
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
            );
CREATE TABLE players (
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
                transfers_seen_at INTEGER NOT NULL DEFAULT 0
            );
INSERT INTO "players" VALUES(900000001,12345,100,1760864000,0,1759740800,1000,480,0,0,0);
INSERT INTO "players" VALUES(900000002,1000000,250,1760864000,0,1759740800,250000,90000,3,2,0);
INSERT INTO "players" VALUES(900000003,0,100,1760864000,0,1759740800,0,0,0,0,0);
CREATE TABLE roulette_rounds (
                telegram_id  INTEGER NOT NULL,
                request_id   TEXT    NOT NULL,
                number       INTEGER NOT NULL,
                stake_total  INTEGER NOT NULL,
                payout_total INTEGER NOT NULL,
                bets_json    TEXT    NOT NULL,
                created_at   INTEGER NOT NULL,
                PRIMARY KEY (telegram_id, request_id)
            );
INSERT INTO "roulette_rounds" VALUES(900000001,'legacy-req-0001',17,100,0,'[{"type":"red","value":null,"amount":100}]',1759999900);
CREATE TABLE service_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
CREATE TABLE transfers (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                sender     INTEGER NOT NULL,
                recipient  INTEGER NOT NULL,
                amount     INTEGER NOT NULL,
                fee        INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                request_id TEXT    NOT NULL,
                UNIQUE (sender, request_id)
            );
CREATE INDEX idx_keno_created ON keno_rounds(created_at);
CREATE UNIQUE INDEX idx_mines_active ON mines_games(telegram_id) WHERE status = 'active';
CREATE INDEX idx_mines_history ON mines_games(telegram_id, finished_at);
CREATE UNIQUE INDEX idx_blackjack_active ON blackjack_games(telegram_id) WHERE status = 'active';
CREATE INDEX idx_blackjack_history ON blackjack_games(telegram_id, finished_at);
CREATE UNIQUE INDEX idx_crash_active ON crash_games(telegram_id) WHERE status = 'active';
CREATE INDEX idx_crash_history ON crash_games(telegram_id, finished_at);
CREATE UNIQUE INDEX idx_hilo_active ON hilo_games(telegram_id) WHERE status = 'active';
CREATE INDEX idx_hilo_history ON hilo_games(telegram_id, finished_at);
CREATE INDEX chat_members_recent ON chat_members (chat_instance, last_seen);
CREATE INDEX idx_transfers_sender ON transfers(sender, created_at);
CREATE INDEX idx_transfers_recipient ON transfers(recipient, created_at);
CREATE INDEX idx_cosmetic_purchases_player ON cosmetic_purchases(telegram_id, created_at);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('keno_rounds',1);
COMMIT;

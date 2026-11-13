"""Ordered schema migrations.

RULES: never edit an existing migration after release; append a new (version, sql) entry.
Migrations must only add/alter structure — never delete user progress.
"""

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE users (
            user_id             INTEGER PRIMARY KEY,          -- Telegram user id
            username            TEXT,
            first_name          TEXT,
            registered_at       TEXT NOT NULL,                -- UTC ISO timestamp
            level               TEXT,                         -- A1..C2, NULL until chosen
            state               TEXT NOT NULL DEFAULT 'new',  -- new | idle | learning
            last_learning_date  TEXT                          -- YYYY-MM-DD of last finished daily lesson
        );

        CREATE TABLE user_settings (
            user_id             INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
            daily_target        INTEGER NOT NULL DEFAULT 2,
            notify_enabled      INTEGER NOT NULL DEFAULT 0,
            notify_time         TEXT NOT NULL DEFAULT '19:00',
            last_notified_date  TEXT,
            mode                TEXT NOT NULL DEFAULT 'general'  -- 'general' now; 'ielts' later
        );

        CREATE TABLE user_words (
            user_id         INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            word_id         TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'learning',  -- learning | reviewing | known
            first_learned   TEXT NOT NULL,
            last_review     TEXT,
            next_review     TEXT NOT NULL,
            correct_count   INTEGER NOT NULL DEFAULT 0,
            incorrect_count INTEGER NOT NULL DEFAULT 0,
            stage           INTEGER NOT NULL DEFAULT 0,        -- repetition stage
            reviewed_ok     INTEGER NOT NULL DEFAULT 0,        -- successful scheduled reviews
            last_ex_type    TEXT,
            PRIMARY KEY (user_id, word_id)
        );
        CREATE INDEX idx_user_words_due ON user_words(user_id, next_review);

        -- A learning run (daily lesson / manual review / extra practice) and its saved position.
        CREATE TABLE activities (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            kind        TEXT NOT NULL,                  -- daily | review | practice
            day         TEXT NOT NULL,                  -- YYYY-MM-DD (user's local day)
            status      TEXT NOT NULL DEFAULT 'active', -- active | done | abandoned
            rev         INTEGER NOT NULL DEFAULT 0,     -- bumped on every step; makes old buttons stale
            state       TEXT NOT NULL,                  -- JSON
            created_at  TEXT NOT NULL,
            finished_at TEXT
        );
        -- Exactly one daily lesson per user per day => the daily limit cannot be bypassed.
        CREATE UNIQUE INDEX uq_daily_per_day ON activities(user_id, day) WHERE kind = 'daily';
        CREATE INDEX idx_activities_user ON activities(user_id, kind, status);
        """,
    ),
    (
        2,
        """
        -- Was the user already offered to choose a reminder time (after the first finished lesson)?
        ALTER TABLE user_settings ADD COLUMN notify_prompted INTEGER NOT NULL DEFAULT 0;
        """,
    ),
]

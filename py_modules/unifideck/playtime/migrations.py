"""Schema migrations for the activity tracking database."""

import logging

logger = logging.getLogger("unifideck")

# Each migration is a tuple of (version, description, list_of_sql_statements)
MIGRATIONS = [
    (1, "Initial schema", [
        # Migration tracking
        """CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER NOT NULL,
            applied_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            description TEXT
        )""",

        # Canonical game registry
        """CREATE TABLE IF NOT EXISTS games (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store           TEXT NOT NULL,
            store_game_id   TEXT NOT NULL,
            steam_app_id    INTEGER,
            real_steam_appid INTEGER,
            title           TEXT NOT NULL,
            cover_image_url TEXT,
            platform        TEXT NOT NULL DEFAULT 'windows',
            ownership_type  TEXT,
            first_seen_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            last_synced_at  TEXT,
            is_hidden       INTEGER NOT NULL DEFAULT 0,
            metadata_json   TEXT,
            UNIQUE(store, store_game_id)
        )""",

        # Individual play sessions
        """CREATE TABLE IF NOT EXISTS play_sessions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id       INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            steam_user_id TEXT,
            started_at    TEXT NOT NULL,
            ended_at      TEXT,
            duration_secs INTEGER,
            end_reason    TEXT NOT NULL DEFAULT 'unknown',
            proton_tool   TEXT,
            session_note  TEXT,
            is_manual     INTEGER NOT NULL DEFAULT 0,
            created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )""",

        # Pre-computed daily aggregates per game
        """CREATE TABLE IF NOT EXISTS daily_stats (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id              INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            date                 TEXT NOT NULL,
            total_secs           INTEGER NOT NULL DEFAULT 0,
            session_count        INTEGER NOT NULL DEFAULT 0,
            longest_session_secs INTEGER NOT NULL DEFAULT 0,
            UNIQUE(game_id, date)
        )""",

        # Pre-computed lifetime stats per game
        """CREATE TABLE IF NOT EXISTS game_stats (
            game_id             INTEGER PRIMARY KEY REFERENCES games(id) ON DELETE CASCADE,
            total_secs          INTEGER NOT NULL DEFAULT 0,
            total_sessions      INTEGER NOT NULL DEFAULT 0,
            avg_session_secs    INTEGER NOT NULL DEFAULT 0,
            min_session_secs    INTEGER,
            max_session_secs    INTEGER NOT NULL DEFAULT 0,
            first_played_at     TEXT,
            last_played_at      TEXT,
            current_streak_days INTEGER NOT NULL DEFAULT 0,
            longest_streak_days INTEGER NOT NULL DEFAULT 0,
            updated_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )""",

        # Game lifecycle events
        """CREATE TABLE IF NOT EXISTS game_events (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id      INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            event_type   TEXT NOT NULL,
            occurred_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            details_json TEXT,
            source       TEXT
        )""",

        # Device/plugin lifecycle events
        """CREATE TABLE IF NOT EXISTS device_events (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type    TEXT NOT NULL,
            occurred_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            details_json  TEXT,
            steam_user_id TEXT
        )""",

        # Generic audit/activity log
        """CREATE TABLE IF NOT EXISTS activity_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            category     TEXT NOT NULL,
            action       TEXT NOT NULL,
            details_json TEXT,
            occurred_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )""",

        # Connected store accounts
        """CREATE TABLE IF NOT EXISTS store_accounts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            store           TEXT NOT NULL,
            account_name    TEXT,
            connected_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            disconnected_at TEXT,
            is_active       INTEGER NOT NULL DEFAULT 1,
            steam_user_id   TEXT,
            UNIQUE(store, steam_user_id)
        )""",

        # Future cloud sync tracking
        """CREATE TABLE IF NOT EXISTS sync_state (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type        TEXT NOT NULL,
            entity_id          INTEGER NOT NULL,
            local_version      INTEGER NOT NULL DEFAULT 1,
            remote_version     INTEGER NOT NULL DEFAULT 0,
            last_synced_at     TEXT,
            sync_status        TEXT NOT NULL DEFAULT 'pending',
            conflict_data_json TEXT,
            UNIQUE(entity_type, entity_id)
        )""",

        # Game associations (cross-store same game)
        """CREATE TABLE IF NOT EXISTS game_associations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            group_name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )""",

        """CREATE TABLE IF NOT EXISTS game_association_members (
            association_id INTEGER NOT NULL REFERENCES game_associations(id) ON DELETE CASCADE,
            game_id        INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
            is_primary     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (association_id, game_id)
        )""",

        # Indexes
        "CREATE INDEX IF NOT EXISTS idx_games_steam_app_id ON games(steam_app_id)",
        "CREATE INDEX IF NOT EXISTS idx_games_store ON games(store)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_game_id ON play_sessions(game_id)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON play_sessions(started_at)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_ended_at ON play_sessions(ended_at)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_user ON play_sessions(steam_user_id)",
        "CREATE INDEX IF NOT EXISTS idx_daily_stats_date ON daily_stats(date)",
        "CREATE INDEX IF NOT EXISTS idx_daily_stats_game_id ON daily_stats(game_id)",
        "CREATE INDEX IF NOT EXISTS idx_game_events_game_id ON game_events(game_id)",
        "CREATE INDEX IF NOT EXISTS idx_game_events_type ON game_events(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_game_events_occurred_at ON game_events(occurred_at)",
        "CREATE INDEX IF NOT EXISTS idx_device_events_type ON device_events(event_type)",
        "CREATE INDEX IF NOT EXISTS idx_device_events_occurred_at ON device_events(occurred_at)",
        "CREATE INDEX IF NOT EXISTS idx_activity_log_category ON activity_log(category)",
        "CREATE INDEX IF NOT EXISTS idx_sync_state_status ON sync_state(sync_status)",
    ]),
]


def run_migrations(conn):
    """Apply pending schema migrations.

    Args:
        conn: sqlite3.Connection instance
    """
    cursor = conn.cursor()

    # Check if schema_version table exists
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    )
    has_version_table = cursor.fetchone() is not None

    current_version = 0
    if has_version_table:
        cursor.execute("SELECT MAX(version) FROM schema_version")
        row = cursor.fetchone()
        if row and row[0] is not None:
            current_version = row[0]

    applied = 0
    for version, description, statements in MIGRATIONS:
        if version <= current_version:
            continue

        logger.info(f"[PLAYTIME] Applying migration v{version}: {description}")
        for sql in statements:
            cursor.execute(sql)

        # Record the migration (schema_version table is created by migration 1)
        cursor.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (version, description),
        )
        applied += 1

    if applied > 0:
        conn.commit()
        logger.info(f"[PLAYTIME] Applied {applied} migration(s), now at v{version}")
    else:
        logger.info(f"[PLAYTIME] Schema up to date at v{current_version}")

    return current_version if applied == 0 else version

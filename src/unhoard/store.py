"""SQLite storage: bookmarks, enrichments and the task proposals ("actions") shown as cards."""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
from pathlib import Path

from .config import Paths, resolve_paths

SCHEMA_VERSION = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS bookmarks(
  tweet_id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  author_id TEXT,
  author_username TEXT,
  author_name TEXT,
  tweet_created_at TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  source_url TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','enriched','error')),
  error TEXT,
  author_avatar_url TEXT,
  author_verified INTEGER,
  metrics_json TEXT,
  text_display TEXT,
  links_json TEXT
);

CREATE TABLE IF NOT EXISTS media(
  media_key TEXT PRIMARY KEY,
  tweet_id TEXT NOT NULL REFERENCES bookmarks(tweet_id) ON DELETE CASCADE,
  media_type TEXT,
  url TEXT,
  alt_text TEXT,
  position INTEGER NOT NULL DEFAULT 0,
  video_url TEXT,
  width INTEGER,
  height INTEGER,
  duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS enrichments(
  tweet_id TEXT PRIMARY KEY REFERENCES bookmarks(tweet_id) ON DELETE CASCADE,
  summary TEXT NOT NULL,
  why_saved TEXT NOT NULL,
  confidence REAL NOT NULL,
  enriched_at TEXT NOT NULL,
  link_summary TEXT NOT NULL DEFAULT ''
);

-- One proposed task. status: proposed -> accepted (Go) | skipped (Skip).
-- Later keeps status=proposed and sets snoozed_until. card_decided_at is set
-- for every answer given on a card (Later included) and drives the daily limit.
CREATE TABLE IF NOT EXISTS actions(
  action_id TEXT PRIMARY KEY,
  tweet_id TEXT NOT NULL REFERENCES bookmarks(tweet_id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  brief TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  priority TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL DEFAULT 'proposed' CHECK(status IN ('proposed','accepted','skipped')),
  created_at TEXT NOT NULL,
  decided_at TEXT,
  snoozed_until TEXT,
  card_decided_at TEXT,
  launch_ref TEXT,
  launch_status TEXT,
  launch_error TEXT,
  note TEXT
);

CREATE INDEX IF NOT EXISTS bookmarks_state_idx ON bookmarks(state, first_seen_at DESC);
CREATE INDEX IF NOT EXISTS actions_status_idx ON actions(status, created_at DESC);
CREATE INDEX IF NOT EXISTS actions_tweet_idx ON actions(tweet_id);
"""


# Columns added after schema 1. A database created by an older release lacks them; ``migrate`` adds each one
# (only when it is missing) inside one write transaction, so two processes starting together cannot both ALTER.
ADDED_COLUMNS = (
    ("enrichments", "link_summary", "TEXT NOT NULL DEFAULT ''"),
    ("bookmarks", "author_avatar_url", "TEXT"),
    ("bookmarks", "author_verified", "INTEGER"),
    ("bookmarks", "metrics_json", "TEXT"),
    ("bookmarks", "text_display", "TEXT"),
    ("bookmarks", "links_json", "TEXT"),
    ("media", "position", "INTEGER NOT NULL DEFAULT 0"),
    ("media", "video_url", "TEXT"),
    ("media", "width", "INTEGER"),
    ("media", "height", "INTEGER"),
    ("media", "duration_ms", "INTEGER"),
)


def migrate(db: sqlite3.Connection) -> None:
    """Bring an older database up to ``SCHEMA_VERSION``. Safe to run concurrently and repeatedly.

    Takes the write lock first (``BEGIN IMMEDIATE``), then checks which columns exist, so a second process that
    waited for the lock finds the columns already there. ``user_version`` is only ever raised.
    """
    if db.execute("PRAGMA user_version").fetchone()[0] >= SCHEMA_VERSION:
        return  # already migrated (checked without the lock; user_version is set in the same transaction)
    db.execute("BEGIN IMMEDIATE")
    try:
        for table, column, ddl in ADDED_COLUMNS:
            if column not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        current = db.execute("PRAGMA user_version").fetchone()[0]
        if current < SCHEMA_VERSION:
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def timestamp(when: dt.datetime | None = None) -> str:
    """UTC, whole seconds, fixed width: safe to compare as plain strings in SQL."""
    when = when or dt.datetime.now(dt.timezone.utc)
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(paths: Paths | Path | str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the database. Accepts Paths, or a file path for tests."""
    if paths is None:
        paths = resolve_paths()
    db_file = paths.db_file if isinstance(paths, Paths) else Path(paths)
    fresh = not db_file.exists()
    db_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(db_file, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    migrate(db)
    if fresh:
        try:
            os.chmod(db_file, 0o600)  # bookmarks are private reading history
        except OSError:
            pass
    return db


def get_meta(db: sqlite3.Connection, key: str) -> str | None:
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_meta(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, value))


def counts(db: sqlite3.Connection) -> dict[str, int]:
    def one(sql: str) -> int:
        return db.execute(sql).fetchone()[0]

    return {
        "bookmarks": one("SELECT count(*) FROM bookmarks"),
        "pending": one("SELECT count(*) FROM bookmarks WHERE state='pending'"),
        "errors": one("SELECT count(*) FROM bookmarks WHERE state='error'"),
        "enriched": one("SELECT count(*) FROM bookmarks WHERE state='enriched'"),
        "proposed": one("SELECT count(*) FROM actions WHERE status='proposed'"),
        "accepted": one("SELECT count(*) FROM actions WHERE status='accepted'"),
        "skipped": one("SELECT count(*) FROM actions WHERE status='skipped'"),
    }

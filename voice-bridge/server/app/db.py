import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import get_settings

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, title TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'active', created_at REAL NOT NULL, updated_at REAL NOT NULL,
  deleted_at REAL
);
CREATE INDEX IF NOT EXISTS idx_conversations_device ON conversations(device_id, state, updated_at DESC);
CREATE TABLE IF NOT EXISTS conversation_usage (
  conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
  audio_used REAL NOT NULL DEFAULT 0, audio_reserved REAL NOT NULL DEFAULT 0,
  tokens_used INTEGER NOT NULL DEFAULT 0, tokens_reserved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS device_usage (
  device_id TEXT PRIMARY KEY, audio_used REAL NOT NULL DEFAULT 0,
  audio_reserved REAL NOT NULL DEFAULT 0, tokens_used INTEGER NOT NULL DEFAULT 0,
  tokens_reserved INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS translation_requests (
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
  client_request_id TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, reconcile_deadline REAL,
  audio_reserved REAL NOT NULL DEFAULT 0, tokens_reserved INTEGER NOT NULL DEFAULT 0,
  audio_charged REAL NOT NULL DEFAULT 0, tokens_charged INTEGER NOT NULL DEFAULT 0,
  provider_request_id TEXT, transcript TEXT, translation TEXT, summary_json TEXT,
  audio_key TEXT, error TEXT, UNIQUE(device_id, client_request_id)
);
CREATE TABLE IF NOT EXISTS voices (
  id TEXT PRIMARY KEY, device_id TEXT NOT NULL, name TEXT NOT NULL,
  description TEXT NOT NULL, provider_voice_id TEXT NOT NULL, status TEXT NOT NULL,
  authorized INTEGER NOT NULL, hidden INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_requests_device ON translation_requests(device_id, created_at DESC);
"""


def connect() -> sqlite3.Connection:
    path: Path = get_settings().sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db() -> None:
    conn = connect()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


@contextmanager
def transaction(immediate: bool = False) -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


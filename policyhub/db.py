"""SQLite persistence.

Design rules enforced at the storage boundary:
* All temporal facts are explicit [valid_from, valid_to) half-open intervals;
  nothing is mutated in place when a policy is revised (a new version row is
  inserted, the previous row is retained for point-in-time traceability).
* Revocations are separate rows with their own intervals, never deletes.
* Schema versioning and the rule epoch (cache invalidation counter) live in
  the same meta table.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id        INTEGER PRIMARY KEY,
    code      TEXT UNIQUE NOT NULL,
    name      TEXT NOT NULL,
    parent_id INTEGER REFERENCES positions(id)
);

CREATE TABLE IF NOT EXISTS users (
    id        INTEGER PRIMARY KEY,
    username  TEXT UNIQUE NOT NULL,
    name      TEXT NOT NULL
);

-- A user holds a position only inside an interval; 调岗 closes the old row.
CREATE TABLE IF NOT EXISTS user_assignments (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    position_id  INTEGER NOT NULL REFERENCES positions(id),
    valid_from   INTEGER NOT NULL,
    valid_to     INTEGER
);

CREATE TABLE IF NOT EXISTS policies (
    id          INTEGER PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    title       TEXT NOT NULL,
    category    TEXT NOT NULL,
    -- metadata visible to anonymous users on the public page
    public_meta INTEGER NOT NULL DEFAULT 0,
    is_system   INTEGER NOT NULL DEFAULT 0,  -- 1 = human-authored foundational rule
    created_by  TEXT NOT NULL DEFAULT 'admin'
);

CREATE TABLE IF NOT EXISTS policy_versions (
    id              INTEGER PRIMARY KEY,
    policy_id       INTEGER NOT NULL REFERENCES policies(id),
    version_no      INTEGER NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    content_hash    TEXT NOT NULL,
    valid_from      INTEGER NOT NULL,
    valid_to        INTEGER,              -- NULL => still open
    published_by    TEXT NOT NULL DEFAULT 'admin',
    published_at    INTEGER NOT NULL,
    UNIQUE(policy_id, version_no)
);

-- Applicability scope rows: position membership + equipment/scene sets.
CREATE TABLE IF NOT EXISTS policy_scope (
    id                INTEGER PRIMARY KEY,
    policy_version_id INTEGER NOT NULL REFERENCES policy_versions(id),
    position_id       INTEGER NOT NULL REFERENCES positions(id),
    equipment_codes   TEXT NOT NULL DEFAULT '[]',  -- JSON list; ["*"] = any
    scene_codes       TEXT NOT NULL DEFAULT '[]'
);

-- Exceptions (personal or positional) to a *specific* version.
CREATE TABLE IF NOT EXISTS policy_exceptions (
    id                INTEGER PRIMARY KEY,
    policy_version_id INTEGER NOT NULL REFERENCES policy_versions(id),
    subject_type      TEXT NOT NULL CHECK (subject_type IN ('USER','POSITION')),
    subject_id        INTEGER NOT NULL,
    grant             INTEGER NOT NULL,   -- 0 = DENY, 1 = GRANT
    reason            TEXT NOT NULL,
    valid_from        INTEGER NOT NULL,
    valid_to          INTEGER              -- NULL exception remains open
);

-- A reading session is required BEFORE acknowledgement (no blind one-click).
CREATE TABLE IF NOT EXISTS ack_sessions (
    token            TEXT PRIMARY KEY,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    policy_version_id INTEGER NOT NULL REFERENCES policy_versions(id),
    content_hash     TEXT NOT NULL,
    issued_at        INTEGER NOT NULL,
    expires_at       INTEGER NOT NULL,
    consumed         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS policy_acks (
    id                INTEGER PRIMARY KEY,
    user_id           INTEGER NOT NULL REFERENCES users(id),
    policy_version_id INTEGER NOT NULL REFERENCES policy_versions(id),
    content_hash      TEXT NOT NULL,
    acked_at          INTEGER NOT NULL,
    UNIQUE(user_id, policy_version_id)
);

-- Training links are single-use and expire; training is per version.
CREATE TABLE IF NOT EXISTS training_links (
    token        TEXT PRIMARY KEY,
    version_id   INTEGER NOT NULL REFERENCES policy_versions(id),
    issued_at    INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    consumed     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS policy_training (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id),
    version_id      INTEGER NOT NULL REFERENCES policy_versions(id),
    completed_at    INTEGER NOT NULL,
    via_link        TEXT,
    UNIQUE(user_id, version_id)
);

-- Actual authorization to perform work. Independently granted and revocable
-- inside an interval; distinct from reading/training.
CREATE TABLE IF NOT EXISTS work_authorizations (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    policy_id    INTEGER NOT NULL REFERENCES policies(id),
    granted_by   TEXT NOT NULL,
    valid_from   INTEGER NOT NULL,
    valid_to     INTEGER
);
CREATE TABLE IF NOT EXISTS auth_revocations (
    id        INTEGER PRIMARY KEY,
    auth_id   INTEGER NOT NULL REFERENCES work_authorizations(id),
    revoked_at INTEGER NOT NULL,
    reason    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attachments (
    id                INTEGER PRIMARY KEY,
    policy_version_id INTEGER NOT NULL REFERENCES policy_versions(id),
    filename          TEXT NOT NULL,
    content           BLOB NOT NULL,
    content_hash      TEXT NOT NULL
);

-- Finite-lived offline access grant (snapshot hash pinned).
CREATE TABLE IF NOT EXISTS offline_grants (
    token        TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    attachment_id INTEGER NOT NULL REFERENCES attachments(id),
    snapshot_hash TEXT NOT NULL,
    issued_at    INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL,
    revoked      INTEGER NOT NULL DEFAULT 0
);

-- Per-position precomputed applicability snapshots.
CREATE TABLE IF NOT EXISTS position_sets (
    snapshot_id    TEXT PRIMARY KEY,        -- position_id:rule_epoch
    position_id    INTEGER NOT NULL,
    rule_epoch     INTEGER NOT NULL,
    built_at       INTEGER NOT NULL,
    payload_json   TEXT NOT NULL
);
"""


def connect(path: str = ":memory:", check_same_thread: bool = True) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '1')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('rule_epoch', '1')"
    )
    conn.commit()


# --- meta helpers ---------------------------------------------------------
def get_meta(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()


def bump_rule_epoch(conn: sqlite3.Connection) -> int:
    """Invalidate every precomputed position set / cached derivation."""
    row = conn.execute(
        "UPDATE meta SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT) "
        "WHERE key='rule_epoch' RETURNING value"
    ).fetchone()
    conn.commit()
    return int(row["value"])


def jloads(text):
    return json.loads(text) if text else []

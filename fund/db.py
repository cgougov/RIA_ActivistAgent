"""Database connection and schema. Nine tables, nothing speculative."""
import contextlib
import sqlite3
from datetime import datetime, timezone

from fund.config import DB_PATH


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextlib.contextmanager
def connect(db_path=DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    try:
        yield connection
    finally:
        connection.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS funds (
    fund_id     TEXT PRIMARY KEY,
    fund_name   TEXT NOT NULL,
    manager_name TEXT NOT NULL,
    notes       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id      TEXT PRIMARY KEY,
    fund_id     TEXT NOT NULL REFERENCES funds (fund_id),
    file_name   TEXT NOT NULL,
    sha256      TEXT,
    doc_type    TEXT NOT NULL,           -- factsheet | presentation
    title       TEXT NOT NULL,
    doc_date    TEXT,
    page_count  INTEGER,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    doc_id      TEXT NOT NULL REFERENCES documents (doc_id),
    page_number INTEGER NOT NULL,
    text        TEXT,
    image_path  TEXT,                    -- rendered page image, if any
    PRIMARY KEY (doc_id, page_number)
);

-- Proposal layer: everything the LLM suggests, pre-approval.
CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES documents (doc_id),
    fund_id     TEXT NOT NULL REFERENCES funds (fund_id),
    scope       TEXT NOT NULL,
    field_key   TEXT NOT NULL,
    value       TEXT NOT NULL,
    value_num   REAL,
    unit        TEXT,
    as_of_date  TEXT,
    page        INTEGER,
    quote       TEXT,
    confidence  REAL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | approved | rejected
    review_note TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposed_returns (
    row_id      TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES documents (doc_id),
    fund_id     TEXT NOT NULL REFERENCES funds (fund_id),
    period_type TEXT NOT NULL,           -- monthly | quarterly | annual | ytd
    period_start TEXT,
    period_end  TEXT NOT NULL,
    return_pct  REAL NOT NULL,           -- percent points: 1.23 means 1.23%
    return_type TEXT NOT NULL DEFAULT 'unknown',   -- net | gross | unknown
    share_class TEXT NOT NULL DEFAULT '',
    page        INTEGER,
    quote       TEXT,
    confidence  REAL,
    status      TEXT NOT NULL DEFAULT 'pending',
    review_note TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at  TEXT NOT NULL
);

-- Source-of-truth layer: approved only. One value per field per fund,
-- enforced structurally. Approving again = revising, and updates in place.
CREATE TABLE IF NOT EXISTS facts (
    fund_id     TEXT NOT NULL REFERENCES funds (fund_id),
    field_key   TEXT NOT NULL,
    value       TEXT NOT NULL,
    value_num   REAL,
    unit        TEXT,
    as_of_date  TEXT,
    doc_id      TEXT REFERENCES documents (doc_id),
    page        INTEGER,
    quote       TEXT,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    PRIMARY KEY (fund_id, field_key)
);

CREATE TABLE IF NOT EXISTS returns (
    fund_id     TEXT NOT NULL REFERENCES funds (fund_id),
    share_class TEXT NOT NULL DEFAULT '',
    period_type TEXT NOT NULL,
    period_end  TEXT NOT NULL,
    period_start TEXT,
    return_pct  REAL NOT NULL,
    return_type TEXT NOT NULL DEFAULT 'unknown',
    doc_id      TEXT REFERENCES documents (doc_id),
    page        INTEGER,
    quote       TEXT,
    approved_by TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    PRIMARY KEY (fund_id, share_class, period_type, period_end)
);

-- Every LLM call, no exceptions.
CREATE TABLE IF NOT EXISTS llm_calls (
    call_id     TEXT PRIMARY KEY,
    call_type   TEXT NOT NULL,           -- extract:<scope> | extract:returns | analyze
    model       TEXT NOT NULL,
    doc_id      TEXT,
    fund_id     TEXT,
    prompt_chars INTEGER,
    prompt_head TEXT,                    -- first 400 chars, for auditing
    raw_output  TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    status      TEXT NOT NULL,           -- ok | failed
    error       TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    analysis_id TEXT PRIMARY KEY,
    question    TEXT NOT NULL,
    fund_ids    TEXT NOT NULL,           -- comma-separated
    snapshot_hashes TEXT NOT NULL,       -- comma-separated, ties output to exact factsheet state
    model       TEXT NOT NULL,
    output_text TEXT NOT NULL,
    call_id     TEXT REFERENCES llm_calls (call_id),
    created_at  TEXT NOT NULL
);
"""


def create_schema(connection):
    connection.executescript(SCHEMA)
    connection.commit()

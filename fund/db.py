"""Database connection, schema, and migrations."""
import contextlib
import shutil
import sqlite3
from datetime import datetime, timezone

from fund.config import DB_PATH

CURRENT_SCHEMA_VERSION = 2


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextlib.contextmanager
def connect(db_path=DB_PATH, verify=True):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    try:
        if verify:
            ensure_schema_current(connection)
        yield connection
    finally:
        connection.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    description TEXT NOT NULL,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS funds (
    fund_id      TEXT PRIMARY KEY,
    fund_name    TEXT NOT NULL,
    manager_name TEXT NOT NULL,
    notes        TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    doc_id              TEXT PRIMARY KEY,
    fund_id             TEXT NOT NULL REFERENCES funds (fund_id),
    file_name           TEXT NOT NULL,
    original_file_name  TEXT NOT NULL,
    stored_path         TEXT NOT NULL,
    sha256              TEXT,
    doc_type            TEXT NOT NULL,
    title               TEXT NOT NULL,
    doc_date            TEXT,
    page_count          INTEGER,
    is_current          INTEGER NOT NULL DEFAULT 0,
    supersedes_doc_id   TEXT REFERENCES documents (doc_id),
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    doc_id       TEXT NOT NULL REFERENCES documents (doc_id),
    page_number  INTEGER NOT NULL,
    text         TEXT,
    image_path   TEXT,
    PRIMARY KEY (doc_id, page_number)
);

CREATE TABLE IF NOT EXISTS proposals (
    proposal_id  TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL REFERENCES documents (doc_id),
    fund_id      TEXT NOT NULL REFERENCES funds (fund_id),
    scope        TEXT NOT NULL,
    field_key    TEXT NOT NULL,
    share_class  TEXT NOT NULL DEFAULT '',
    value        TEXT NOT NULL,
    value_num    REAL,
    unit         TEXT,
    as_of_date   TEXT,
    page         INTEGER,
    quote        TEXT,
    confidence   REAL,
    quote_verified      INTEGER,
    quote_verify_score  REAL,
    quote_verify_status TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    review_note  TEXT,
    reviewed_by  TEXT,
    reviewed_at  TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposed_returns (
    row_id       TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL REFERENCES documents (doc_id),
    fund_id      TEXT NOT NULL REFERENCES funds (fund_id),
    period_type  TEXT NOT NULL,
    period_start TEXT,
    period_end   TEXT NOT NULL,
    return_pct   REAL NOT NULL,
    return_type  TEXT NOT NULL DEFAULT 'unknown',
    share_class  TEXT NOT NULL DEFAULT '',
    page         INTEGER,
    quote        TEXT,
    confidence   REAL,
    quote_verified      INTEGER,
    quote_verify_score  REAL,
    quote_verify_status TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    review_note  TEXT,
    reviewed_by  TEXT,
    reviewed_at  TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS facts (
    fund_id      TEXT NOT NULL REFERENCES funds (fund_id),
    field_key    TEXT NOT NULL,
    share_class  TEXT NOT NULL DEFAULT '',
    value        TEXT NOT NULL,
    value_num    REAL,
    unit         TEXT,
    as_of_date   TEXT,
    doc_id       TEXT REFERENCES documents (doc_id),
    page         INTEGER,
    quote        TEXT,
    approved_by  TEXT NOT NULL,
    approved_at  TEXT NOT NULL,
    PRIMARY KEY (fund_id, field_key, share_class)
);

CREATE TABLE IF NOT EXISTS returns (
    fund_id      TEXT NOT NULL REFERENCES funds (fund_id),
    share_class  TEXT NOT NULL DEFAULT '',
    period_type  TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    period_start TEXT,
    return_pct   REAL NOT NULL,
    return_type  TEXT NOT NULL DEFAULT 'unknown',
    doc_id       TEXT REFERENCES documents (doc_id),
    page         INTEGER,
    quote        TEXT,
    approved_by  TEXT NOT NULL,
    approved_at  TEXT NOT NULL,
    PRIMARY KEY (fund_id, share_class, period_type, period_end)
);

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id       TEXT PRIMARY KEY,
    call_type     TEXT NOT NULL,
    model         TEXT NOT NULL,
    doc_id        TEXT,
    fund_id       TEXT,
    prompt_chars  INTEGER,
    prompt_head   TEXT,
    raw_output    TEXT,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    status        TEXT NOT NULL,
    error         TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS analyses (
    analysis_id     TEXT PRIMARY KEY,
    question        TEXT NOT NULL,
    fund_ids        TEXT NOT NULL,
    snapshot_hashes TEXT NOT NULL,
    model           TEXT NOT NULL,
    output_text     TEXT NOT NULL,
    call_id         TEXT REFERENCES llm_calls (call_id),
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshot_embeddings (
    snapshot_hash    TEXT NOT NULL,
    fund_id          TEXT NOT NULL REFERENCES funds (fund_id),
    embedding_model  TEXT NOT NULL,
    embedding_vector TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (snapshot_hash, embedding_model)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_documents_fund_type_date_current
    ON documents (fund_id, doc_type, doc_date, is_current);
CREATE INDEX IF NOT EXISTS idx_pages_doc_page
    ON pages (doc_id, page_number);
CREATE INDEX IF NOT EXISTS idx_proposals_fund_status_field_share
    ON proposals (fund_id, status, field_key, share_class);
CREATE INDEX IF NOT EXISTS idx_proposed_returns_fund_status_period_share
    ON proposed_returns (fund_id, status, period_type, period_end, share_class);
CREATE INDEX IF NOT EXISTS idx_facts_fund_field_share
    ON facts (fund_id, field_key, share_class);
CREATE INDEX IF NOT EXISTS idx_returns_fund_period_share
    ON returns (fund_id, period_type, period_end, share_class);
CREATE INDEX IF NOT EXISTS idx_llm_calls_fund_type_created
    ON llm_calls (fund_id, call_type, created_at);
CREATE INDEX IF NOT EXISTS idx_analyses_created
    ON analyses (created_at);
CREATE INDEX IF NOT EXISTS idx_snapshot_embeddings_fund_model
    ON snapshot_embeddings (fund_id, embedding_model);
"""


def _table_exists(connection, table_name):
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def _is_empty_database(connection):
    row = connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()
    return row[0] == 0


def _table_columns(connection, table_name):
    if not _table_exists(connection, table_name):
        return {}
    return {row["name"]: dict(row) for row in connection.execute(f"PRAGMA table_info({table_name})")}


def _ensure_column(connection, table_name, column_name, column_sql):
    columns = _table_columns(connection, table_name)
    if column_name not in columns:
        connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")


def current_schema_version(connection):
    if not _table_exists(connection, "schema_version"):
        return None
    row = connection.execute("SELECT MAX(version) AS version FROM schema_version").fetchone()
    return row["version"] if row and row["version"] is not None else None


def ensure_schema_current(connection):
    if _is_empty_database(connection):
        raise RuntimeError("Database is not initialized. Run: fund init")
    version = current_schema_version(connection)
    if version is None:
        raise RuntimeError("Database schema is outdated. Run: fund migrate")
    if version != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema version {version} is not current ({CURRENT_SCHEMA_VERSION}). Run: fund migrate"
        )


def _record_schema_version(connection, version, description):
    connection.execute(
        """
        INSERT OR REPLACE INTO schema_version (version, description, applied_at)
        VALUES (?, ?, ?)
        """,
        (version, description, utc_now()),
    )


def create_schema(connection):
    connection.executescript(SCHEMA)
    connection.executescript(INDEXES)
    _record_schema_version(connection, CURRENT_SCHEMA_VERSION, "initial explicit migration schema")
    connection.commit()


def _migrate_facts_share_class(connection):
    columns = _table_columns(connection, "facts")
    primary_key = [name for name, row in columns.items() if row["pk"]]
    if "share_class" in columns and primary_key == ["fund_id", "field_key", "share_class"]:
        return
    connection.execute("ALTER TABLE facts RENAME TO facts_old")
    connection.execute(
        """
        CREATE TABLE facts (
            fund_id      TEXT NOT NULL REFERENCES funds (fund_id),
            field_key    TEXT NOT NULL,
            share_class  TEXT NOT NULL DEFAULT '',
            value        TEXT NOT NULL,
            value_num    REAL,
            unit         TEXT,
            as_of_date   TEXT,
            doc_id       TEXT REFERENCES documents (doc_id),
            page         INTEGER,
            quote        TEXT,
            approved_by  TEXT NOT NULL,
            approved_at  TEXT NOT NULL,
            PRIMARY KEY (fund_id, field_key, share_class)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO facts (
            fund_id, field_key, share_class, value, value_num, unit, as_of_date,
            doc_id, page, quote, approved_by, approved_at
        )
        SELECT fund_id, field_key, '', value, value_num, unit, as_of_date,
               doc_id, page, quote, approved_by, approved_at
        FROM facts_old
        """
    )
    connection.execute("DROP TABLE facts_old")


def _latest_doc_by_family(connection, fund_id, doc_type):
    return connection.execute(
        """
        SELECT doc_id FROM documents
        WHERE fund_id = ? AND doc_type = ?
        ORDER BY COALESCE(doc_date, '') DESC, created_at DESC, doc_id DESC
        LIMIT 1
        """,
        (fund_id, doc_type),
    ).fetchone()


def _normalize_current_documents(connection):
    funds = [row["fund_id"] for row in connection.execute("SELECT fund_id FROM funds").fetchall()]
    for fund_id in funds:
        for doc_type in ("factsheet", "presentation"):
            connection.execute(
                "UPDATE documents SET is_current = 0 WHERE fund_id = ? AND doc_type = ?",
                (fund_id, doc_type),
            )
            latest = _latest_doc_by_family(connection, fund_id, doc_type)
            if latest:
                connection.execute(
                    "UPDATE documents SET is_current = 1 WHERE doc_id = ?",
                    (latest["doc_id"],),
                )


def backup_database(db_path=DB_PATH):
    if not db_path.exists():
        return None
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}_pre_migrate_{stamp}{db_path.suffix}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def migrate_to_latest(connection, organize_files=True):
    from fund.ingest import organize_existing_documents

    if _is_empty_database(connection):
        create_schema(connection)
        return {"created": True, "migrated": True, "version": CURRENT_SCHEMA_VERSION}

    version = current_schema_version(connection)
    if version == CURRENT_SCHEMA_VERSION:
        return {"created": False, "migrated": False, "version": version}

    connection.executescript(SCHEMA)
    _ensure_column(connection, "proposals", "share_class", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(connection, "proposals", "quote_verified", "INTEGER")
    _ensure_column(connection, "proposals", "quote_verify_score", "REAL")
    _ensure_column(connection, "proposals", "quote_verify_status", "TEXT")
    _ensure_column(connection, "proposed_returns", "quote_verified", "INTEGER")
    _ensure_column(connection, "proposed_returns", "quote_verify_score", "REAL")
    _ensure_column(connection, "proposed_returns", "quote_verify_status", "TEXT")
    _ensure_column(connection, "documents", "original_file_name", "TEXT")
    _ensure_column(connection, "documents", "stored_path", "TEXT")
    _ensure_column(connection, "documents", "is_current", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "documents", "supersedes_doc_id", "TEXT")
    _migrate_facts_share_class(connection)

    connection.execute(
        "UPDATE documents SET original_file_name = COALESCE(original_file_name, file_name)"
    )
    connection.execute(
        "UPDATE documents SET stored_path = COALESCE(stored_path, file_name)"
    )
    _normalize_current_documents(connection)
    if organize_files:
        organize_existing_documents(connection)

    connection.executescript(INDEXES)
    _record_schema_version(connection, CURRENT_SCHEMA_VERSION, "explicit migration applied")
    connection.commit()
    return {"created": False, "migrated": True, "version": CURRENT_SCHEMA_VERSION}

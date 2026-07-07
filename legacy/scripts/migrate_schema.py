from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect_db, utc_now
from core.schema import create_schema, seed_taxonomy


REQUIRED_COLUMNS = {
    "source_documents": {
        "original_file_name": "TEXT",
        "file_sha256": "TEXT",
        "file_size_bytes": "INTEGER",
        "intake_status": "TEXT NOT NULL DEFAULT 'registered'",
        "page_ingestion_status": "TEXT NOT NULL DEFAULT 'pending'",
        "llm_extraction_status": "TEXT NOT NULL DEFAULT 'not_started'",
        "last_scanned_at": "TEXT",
    },
    "extracted_facts": {
        "structured_payload_json": "TEXT",
    },
    "approved_facts": {
        "structured_payload_json": "TEXT",
        "promotion_status": "TEXT NOT NULL DEFAULT 'pending'",
        "promoted_to_table": "TEXT",
        "promoted_record_id": "TEXT",
        "promoted_at": "TEXT",
    },
    "performance_returns": {
        "period_start_date": "TEXT",
        "source_import_id": "TEXT",
        "return_value_bps": "INTEGER",
        "raw_value_text": "TEXT",
        "currency": "TEXT",
        "benchmark_name": "TEXT",
        "evidence_text": "TEXT",
        "proposed_row_id": "TEXT",
    },
    "benchmark_returns": {
        "return_value_bps": "INTEGER",
        "raw_value_text": "TEXT",
        "currency": "TEXT",
        "evidence_text": "TEXT",
    },
    "analysis_runs": {
        "comparison_fund_ids": "TEXT",
        "input_snapshot_json": "TEXT",
        "external_context_json": "TEXT",
    },
    "fund_flags": {
        "severity": "TEXT NOT NULL DEFAULT 'review'",
    },
}


def table_exists(connection, table_name):
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


def table_columns(connection, table_name):
    if not table_exists(connection, table_name):
        return set()
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}


def add_missing_columns(connection):
    added = []
    for table_name, columns in REQUIRED_COLUMNS.items():
        if not table_exists(connection, table_name):
            continue
        existing = table_columns(connection, table_name)
        for column_name, column_type in columns.items():
            if column_name not in existing:
                connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                added.append(f"{table_name}.{column_name}")
    return added


def main():
    with connect_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                notes TEXT
            )
            """
        )
        create_schema(connection)
        added = add_missing_columns(connection)
        seed_taxonomy(connection)
        migration_id = f"mig_{utc_now()}_{uuid4().hex[:8]}"
        connection.execute(
            "INSERT INTO schema_migrations (migration_id, notes) VALUES (?, ?)",
            (migration_id, f"Non-destructive schema migration. Added columns: {', '.join(added) or 'none'}"),
        )
        connection.commit()

    print("Migration complete")
    if added:
        print("Added columns:")
        for column in added:
            print(f"  {column}")
    else:
        print("Added columns: none")


if __name__ == "__main__":
    main()

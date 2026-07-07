from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect_db
from core.db_backup import create_sqlite_backup


PRESERVED_TABLES = [
    "funds",
    "source_documents",
    "document_pages",
    "document_page_images",
]


RESET_TABLES = [
    "analysis_runs",
    "campaign_events",
    "public_campaigns",
    "benchmark_returns",
    "fact_conflicts",
    "change_history",
    "fund_flags",
    "fund_writeups",
    "fund_exposures",
    "fund_metrics",
    "performance_returns",
    "fund_strategy",
    "fund_terms",
    "fund_people",
    "fund_profile_attributes",
    "fund_characteristics",
    "approved_facts",
    "proposed_return_rows",
    "extracted_facts",
    "extraction_runs",
    "data_imports",
    "document_classification_suggestions",
]


def _count_table(connection, table_name):
    return connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]


def _counts(connection, table_names):
    return {table_name: _count_table(connection, table_name) for table_name in table_names}


def _print_counts(title, counts):
    print(title)
    for table_name, count in counts.items():
        print(f"- {table_name}: {count}")


def _reset_document_statuses(connection):
    connection.execute(
        """
        UPDATE source_documents
        SET llm_extraction_status = 'not_started'
        """
    )


def _apply_reset(connection):
    for table_name in RESET_TABLES:
        connection.execute(f"DELETE FROM {table_name}")
    _reset_document_statuses(connection)


def main():
    parser = argparse.ArgumentParser(
        description="Reset the database to an evidence-preserving baseline. Keeps funds, documents, pages, and page images."
    )
    parser.add_argument("--apply", action="store_true", help="Apply the reset after creating a backup.")
    parser.add_argument(
        "--label",
        default="evidence_baseline_reset",
        help="Backup label to use when creating the SQLite backup.",
    )
    args = parser.parse_args()

    tracked_tables = PRESERVED_TABLES + RESET_TABLES
    with connect_db() as connection:
        before_counts = _counts(connection, tracked_tables)
        print("Evidence-preserving reset plan")
        print("Preserve:")
        for table_name in PRESERVED_TABLES:
            print(f"- {table_name}")
        print("Reset:")
        for table_name in RESET_TABLES:
            print(f"- {table_name}")
        print()
        _print_counts("Current row counts", before_counts)
        print()

        if not args.apply:
            print("Dry run only. Re-run with --apply to create a backup and reset the proposal/approval/derived layers.")
            return

        backup_path = create_sqlite_backup(connection, label=args.label)
        print(f"BACKUP CREATED: {backup_path}")
        _apply_reset(connection)
        connection.commit()

        after_counts = _counts(connection, tracked_tables)
        print()
        _print_counts("Post-reset row counts", after_counts)
        print()
        print("Reset complete. Evidence tables were preserved and proposal/approval/derived tables were cleared.")


if __name__ == "__main__":
    main()

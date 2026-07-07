import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import DB_PATH, PDF_ROOT
from core.db import connect_db


REQUIRED_TABLES = [
    "funds",
    "schema_migrations",
    "characteristic_groups",
    "characteristic_definitions",
    "source_documents",
    "document_classification_suggestions",
    "document_pages",
    "document_page_images",
    "citations",
    "extraction_runs",
    "extracted_facts",
    "approved_facts",
    "fund_characteristics",
    "fund_profile_attributes",
    "fund_people",
    "fund_terms",
    "fund_strategy",
    "performance_returns",
    "benchmark_returns",
    "fund_metrics",
    "fund_exposures",
    "fund_writeups",
    "fund_flags",
    "data_imports",
    "external_sources",
    "fact_conflicts",
    "public_campaigns",
    "campaign_events",
    "analysis_runs",
    "change_history",
]


def table_columns(connection, table_name):
    return [row["name"] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id")
    args = parser.parse_args()

    if not DB_PATH.exists():
        raise SystemExit(f"Missing database: {DB_PATH}")

    with connect_db() as connection:
        existing = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = sorted(set(REQUIRED_TABLES) - existing)
        if missing:
            raise SystemExit(f"Missing tables: {missing}")

        print("Schema: READY")
        print(f"Funds: {connection.execute('SELECT COUNT(*) FROM funds').fetchone()[0]}")
        print(f"Documents: {connection.execute('SELECT COUNT(*) FROM source_documents').fetchone()[0]}")
        print(f"Taxonomy facts: {connection.execute('SELECT COUNT(*) FROM characteristic_definitions').fetchone()[0]}")

        if args.document_id:
            doc = connection.execute(
                "SELECT * FROM source_documents WHERE document_id = ?",
                (args.document_id,),
            ).fetchone()
            if not doc:
                raise SystemExit(f"No document row for {args.document_id}")
            pdf_path = PDF_ROOT / Path(doc["file_name"]).name
            page_count = connection.execute(
                "SELECT COUNT(*) FROM document_pages WHERE document_id = ?",
                (args.document_id,),
            ).fetchone()[0]
            print(f"Document: {doc['document_id']} / {doc['document_title']}")
            print(f"PDF exists: {pdf_path.exists()} ({pdf_path})")
            print(f"Stored pages: {page_count}")
            if not pdf_path.exists():
                raise SystemExit("Document check failed: missing PDF")

    print("Result: READY")


if __name__ == "__main__":
    main()

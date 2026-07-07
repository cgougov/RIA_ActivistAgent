import argparse
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from core.config import DB_PATH, SEED_DIR
from core.schema import create_schema, seed_taxonomy


def load_seed_data(connection):
    funds_csv = SEED_DIR / "seed_funds.csv"
    docs_csv = SEED_DIR / "source_documents.csv"
    funds = pd.read_csv(funds_csv)
    documents = pd.read_csv(docs_csv)

    funds.to_sql("funds", connection, if_exists="append", index=False)

    documents["file_path"] = documents["file_name"]
    documents["original_file_name"] = documents["file_name"]
    documents["uploaded_by"] = "system"
    documents["confidentiality_level"] = "internal"
    documents["intake_status"] = "registered"
    documents["page_ingestion_status"] = "pending"
    documents["llm_extraction_status"] = "not_started"
    documents.to_sql("source_documents", connection, if_exists="append", index=False)


def reset_database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    with sqlite3.connect(DB_PATH) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        create_schema(connection)
        seed_taxonomy(connection)
        load_seed_data(connection)
        connection.commit()
    print(f"Created fresh database: {DB_PATH}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-reset", action="store_true")
    args = parser.parse_args()
    if not args.confirm_reset:
        print("Refusing to reset database without --confirm-reset.")
        print("This is intentional. New project setup command:")
        print("  python scripts/init_db.py --confirm-reset")
        return
    reset_database()


if __name__ == "__main__":
    main()

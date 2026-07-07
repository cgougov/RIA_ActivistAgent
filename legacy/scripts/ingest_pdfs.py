import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect_db
from core.documents import ingest_document_pages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    with connect_db() as connection:
        if args.all:
            document_ids = [
                row["document_id"]
                for row in connection.execute(
                    "SELECT document_id FROM source_documents ORDER BY document_id"
                ).fetchall()
            ]
        elif args.document_id:
            document_ids = [args.document_id]
        else:
            raise SystemExit("Use --document-id doc_003 or --all")

        for document_id in document_ids:
            result = ingest_document_pages(connection, document_id, force=args.force)
            print(f"{result['document_id']}: {result['status']} ({result['pages']} pages)")
        connection.commit()


if __name__ == "__main__":
    main()

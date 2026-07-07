import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect_db
from core.documents import ingest_document_pages
from core.intake import register_pdf_file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_path")
    parser.add_argument("--fund-id")
    parser.add_argument("--document-type", required=True)
    parser.add_argument("--document-title", required=True)
    parser.add_argument("--document-date")
    parser.add_argument("--confidentiality-level", default="internal")
    parser.add_argument("--uploaded-by", default="system")
    parser.add_argument("--notes", default="")
    parser.add_argument("--ingest-pages", action="store_true")
    args = parser.parse_args()

    with connect_db() as connection:
        result = register_pdf_file(
            connection,
            args.pdf_path,
            fund_id=args.fund_id,
            document_type=args.document_type,
            document_title=args.document_title,
            document_date=args.document_date,
            confidentiality_level=args.confidentiality_level,
            uploaded_by=args.uploaded_by,
            notes=args.notes,
        )
        if result["status"] == "registered" and args.ingest_pages:
            ingest_result = ingest_document_pages(connection, result["document_id"])
            result["page_ingestion"] = ingest_result
        connection.commit()

    print(result)


if __name__ == "__main__":
    main()


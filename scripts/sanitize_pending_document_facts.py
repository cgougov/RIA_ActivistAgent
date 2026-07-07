import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect_db
from core.proposal_cleanup import sanitize_pending_document_facts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--reviewer", default="christian")
    args = parser.parse_args()

    with connect_db() as connection:
        summary = sanitize_pending_document_facts(
            connection,
            args.document_id,
            reviewer=args.reviewer,
            apply=args.apply,
        )
        if args.apply:
            connection.commit()

    mode = "APPLY" if args.apply else "DRY RUN"
    print(
        f"{mode}: document={summary['document_id']} "
        f"pending={summary['pending_count']} keep={summary['kept_count']} "
        f"duplicates={summary['duplicate_count']}"
    )
    for row in summary["duplicate_rows"]:
        value = row.get("normalized_value") or row.get("raw_value")
        print(
            f"- {row['fact_id']} | {row['fact_category']}.{row['fact_name']} | "
            f"page {row.get('page_number') or '?'} | {value}"
        )


if __name__ == "__main__":
    main()

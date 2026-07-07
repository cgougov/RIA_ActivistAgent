import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect_db
from core.promotion import promote_all_pending


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default="system")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    with connect_db() as connection:
        promoted, skipped = promote_all_pending(connection, user=args.user)
        if args.dry_run:
            connection.rollback()
        else:
            connection.commit()

    print(f"Promoted: {len(promoted)}")
    for approved_fact_id, table_name, record_id in promoted:
        print(f"  {approved_fact_id} -> {table_name}.{record_id}")
    print(f"Skipped: {len(skipped)}")
    for approved_fact_id, category, name, reason in skipped:
        print(f"  {approved_fact_id}: {category}.{name} ({reason})")
    if args.dry_run:
        print("Dry run only. No database changes saved.")


if __name__ == "__main__":
    main()

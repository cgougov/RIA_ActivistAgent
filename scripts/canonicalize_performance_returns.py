import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect_db
from core.return_canonicalization import canonicalize_performance_returns


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fund-id")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--user", default="christian")
    args = parser.parse_args()

    with connect_db() as connection:
        summary = canonicalize_performance_returns(
            connection,
            fund_id=args.fund_id,
            user=args.user,
            apply=args.apply,
        )
        if args.apply:
            connection.commit()

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"{mode}: duplicate return groups found = {len(summary)}")
    if not summary:
        return
    for item in summary:
        print(
            f"- {item['fund_id']} | {item['share_class'] or '(blank share class)'} | "
            f"{item['period_type']} | {item['period_end_date']} | "
            f"keep={item['canonical_return_id']} ({item['canonical_return_type']}) | "
            f"drop={len(item['duplicate_return_ids'])}"
        )
        for duplicate_return_id, duplicate_return_type in zip(item["duplicate_return_ids"], item["duplicate_return_types"]):
            print(f"  - duplicate {duplicate_return_id} ({duplicate_return_type})")


if __name__ == "__main__":
    main()

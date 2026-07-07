import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import DEFAULT_MODEL
from core.db import connect_db
from core.return_table_extraction import extract_return_table_from_page_image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("document_id")
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()

    with connect_db() as connection:
        run_id, inserted, skipped = extract_return_table_from_page_image(
            connection,
            args.document_id,
            args.page,
            model_name=args.model,
            dpi=args.dpi,
            mock=args.mock,
        )
        connection.commit()

    print(f"Extraction run: {run_id}")
    print(f"Pending return facts created: {inserted}")
    if skipped:
        print(f"Skipped {len(skipped)} row(s):")
        for index, reason in skipped:
            print(f"  row {index}: {reason}")


if __name__ == "__main__":
    main()


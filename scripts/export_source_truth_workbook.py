import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect_db
from core.workbooks import export_source_truth_workbook


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    parser.add_argument("--fund-id", action="append", default=[])
    args = parser.parse_args()

    with connect_db() as connection:
        output = export_source_truth_workbook(
            connection,
            output_path=args.output,
            fund_ids=args.fund_id,
        )
    print(f"Exported: {output}")


if __name__ == "__main__":
    main()

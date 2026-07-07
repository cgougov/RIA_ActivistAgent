import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect_db
from core.workbooks import export_analysis_workbook


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--analysis-id")
    parser.add_argument("--fund-id", action="append", default=[])
    parser.add_argument("--output")
    args = parser.parse_args()

    with connect_db() as connection:
        output = export_analysis_workbook(
            connection,
            analysis_id=args.analysis_id,
            fund_ids=args.fund_id,
            output_path=args.output,
        )
    print(f"Exported: {output}")


if __name__ == "__main__":
    main()


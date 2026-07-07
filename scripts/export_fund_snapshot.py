import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import EXPORT_DIR
from core.db import connect_db
from core.fund_snapshot import FundSnapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("fund_id")
    args = parser.parse_args()

    EXPORT_DIR.mkdir(exist_ok=True)
    output_path = EXPORT_DIR / f"{args.fund_id}_snapshot.json"
    with connect_db() as connection:
        snapshot = FundSnapshot(connection, args.fund_id)
        output_path.write_text(snapshot.to_json(), encoding="utf-8")
    print(f"Exported: {output_path}")


if __name__ == "__main__":
    main()

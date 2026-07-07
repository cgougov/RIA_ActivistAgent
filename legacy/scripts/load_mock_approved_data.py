import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect_db
from core.mock_data import load_mock_approved_data
from core.promotion import promote_all_pending


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if not args.confirm:
        print("Refusing to load mock data without --confirm.")
        return

    with connect_db() as connection:
        approved_ids = load_mock_approved_data(connection)
        promoted, skipped = promote_all_pending(connection, user="mock")
        connection.commit()

    print(f"Mock approved facts created: {len(approved_ids)}")
    print(f"Promoted: {len(promoted)}")
    print(f"Skipped: {len(skipped)}")


if __name__ == "__main__":
    main()


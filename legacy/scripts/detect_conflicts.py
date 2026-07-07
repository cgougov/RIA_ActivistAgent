from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.conflicts import detect_fact_conflicts
from core.db import connect_db


def main():
    with connect_db() as connection:
        created = detect_fact_conflicts(connection)
        connection.commit()
    print(f"Conflicts created: {len(created)}")
    for conflict_id in created:
        print(f"  {conflict_id}")


if __name__ == "__main__":
    main()


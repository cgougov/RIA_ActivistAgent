import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from core.comparison import compare_funds
from core.config import EXPORT_DIR
from core.db import connect_db


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fund-id", action="append", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    EXPORT_DIR.mkdir(exist_ok=True)
    output = Path(args.output) if args.output else EXPORT_DIR / "fund_comparison.xlsx"
    with connect_db() as connection:
        comparison = compare_funds(connection, args.fund_id)
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            pd.DataFrame(comparison["profiles"]).to_excel(writer, sheet_name="profiles", index=False)
            pd.DataFrame(comparison["return_statistics"]).to_excel(writer, sheet_name="return_stats", index=False)
            pd.DataFrame(comparison["analysis_readiness_scores"]).to_excel(writer, sheet_name="readiness_scores", index=False)
            pd.DataFrame(comparison["similarity_matrix"]).to_excel(writer, sheet_name="similarity", index=False)
            pd.DataFrame([{"paragraph": comparison["comparison_paragraph"]}]).to_excel(writer, sheet_name="summary", index=False)
    print(f"Exported: {output}")


if __name__ == "__main__":
    main()


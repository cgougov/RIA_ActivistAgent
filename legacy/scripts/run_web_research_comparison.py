import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.analysis import run_web_research_comparison
from core.config import DEFAULT_ANALYSIS_MODEL, DEFAULT_REASONING_EFFORT
from core.db import connect_db


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fund-id", required=True)
    parser.add_argument("--question", default="Compare the fund's stated strategy and characteristics against public evidence of activist behavior.")
    parser.add_argument("--model", default=DEFAULT_ANALYSIS_MODEL)
    parser.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT)
    parser.add_argument("--created-by", default="system")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()

    with connect_db() as connection:
        analysis_id, output_text = run_web_research_comparison(
            connection,
            args.fund_id,
            args.question,
            model_name=args.model,
            reasoning_effort=args.reasoning_effort,
            created_by=args.created_by,
            mock=args.mock,
        )
        connection.commit()
    print(f"Analysis saved: {analysis_id}")
    print(output_text)


if __name__ == "__main__":
    main()


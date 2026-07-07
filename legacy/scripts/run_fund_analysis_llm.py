import argparse
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from core.config import DEFAULT_ANALYSIS_MODEL, DEFAULT_REASONING_EFFORT
from core.db import connect_db, utc_now
from core.fund_snapshot import FundSnapshot


def build_prompt(primary_snapshot, comparison_snapshots):
    return f"""
You are analyzing approved source-backed data about Japan activist funds.

Rules:
- Use only the supplied snapshots.
- Distinguish sourced facts from gaps.
- Do not invent missing data.
- Identify useful comparisons, unusual characteristics, missing diligence items, and evidence gaps.
- Return JSON with keys: summary, comparisons, unique_insights, data_gaps, diligence_questions.

Primary fund snapshot:
{json.dumps(primary_snapshot, indent=2, ensure_ascii=False)}

Comparison fund snapshots:
{json.dumps(comparison_snapshots, indent=2, ensure_ascii=False)}
""".strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fund-id", required=True)
    parser.add_argument("--compare-fund-id", action="append", default=[])
    parser.add_argument("--analysis-type", default="fund_comparison")
    parser.add_argument("--model", default=os.getenv("OPENAI_ANALYSIS_MODEL", DEFAULT_ANALYSIS_MODEL))
    parser.add_argument("--reasoning-effort", default=os.getenv("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT))
    parser.add_argument("--created-by", default="system")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if load_dotenv:
        load_dotenv()

    with connect_db() as connection:
        primary = FundSnapshot(connection, args.fund_id).build()
        comparisons = [
            FundSnapshot(connection, fund_id).build()
            for fund_id in args.compare_fund_id
        ]
        prompt = build_prompt(primary, comparisons)

        print("Analysis preflight passed")
        print(f"Primary fund: {args.fund_id}")
        print(f"Comparison funds: {', '.join(args.compare_fund_id) or 'none'}")
        print(f"Prompt characters: {len(prompt)}")

        if args.dry_run:
            print("Dry run only. No OpenAI call made.")
            return

        if not os.getenv("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY is not set.")

        from openai import OpenAI

        client = OpenAI()
        response = client.responses.create(
            model=args.model,
            input=prompt,
            reasoning={"effort": args.reasoning_effort, "summary": "auto"},
            text={"format": {"type": "json_object"}},
        )
        output_text = response.output_text
        analysis_id = f"analysis_{uuid4().hex}"
        connection.execute(
            """
            INSERT INTO analysis_runs (
                analysis_id,
                analysis_type,
                fund_id,
                comparison_fund_ids,
                prompt_version,
                model_name,
                input_snapshot_json,
                output_text,
                output_json,
                run_status,
                created_by,
                created_at
            )
            VALUES (?, ?, ?, ?, 'fund_analysis_v1', ?, ?, ?, ?, 'complete', ?, ?)
            """,
            (
                analysis_id,
                args.analysis_type,
                args.fund_id,
                json.dumps(args.compare_fund_id),
                args.model,
                json.dumps({"primary": primary, "comparisons": comparisons}, ensure_ascii=False),
                output_text,
                output_text,
                args.created_by,
                utc_now(),
            ),
        )
        connection.commit()

    print(f"Analysis saved: {analysis_id}")


if __name__ == "__main__":
    main()

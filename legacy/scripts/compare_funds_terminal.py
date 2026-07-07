import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from core.comparison import compare_funds
from core.db import connect_db


def _load_funds(connection):
    rows = connection.execute(
        """
        SELECT fund_id, fund_name, manager_name, primary_strategy, status
        FROM funds
        ORDER BY fund_name
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _list_funds(funds, filter_text=None):
    if filter_text:
        lowered = filter_text.lower()
        funds = [
            fund for fund in funds
            if lowered in (fund["fund_name"] or "").lower()
            or lowered in (fund["manager_name"] or "").lower()
            or lowered in (fund["primary_strategy"] or "").lower()
            or lowered in (fund["status"] or "").lower()
        ]
    if not funds:
        print("No funds matched.")
        return
    print(pd.DataFrame(funds).fillna("").to_string(index=False))


def _resolve_fund_token(funds, token):
    exact_id = [fund for fund in funds if fund["fund_id"] == token]
    if exact_id:
        return exact_id[0]
    exact_name = [fund for fund in funds if (fund["fund_name"] or "").lower() == token.lower()]
    if exact_name:
        return exact_name[0]
    partial = [
        fund for fund in funds
        if token.lower() in (fund["fund_name"] or "").lower()
    ]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise ValueError(f"No fund matched {token!r}")
    matches = ", ".join(f"{fund['fund_name']} ({fund['fund_id']})" for fund in partial)
    raise ValueError(f"Ambiguous fund token {token!r}. Matches: {matches}")


def _name_map(funds):
    return {fund["fund_id"]: fund["fund_name"] for fund in funds}


def _rename_columns(df, name_by_id):
    if df.empty:
        return df
    renamed = df.copy()
    if "fund_id" in renamed.columns:
        renamed["fund_id"] = renamed["fund_id"].map(lambda value: name_by_id.get(value, value))
        renamed = renamed.rename(columns={"fund_id": "Fund"})
    renamed = renamed.rename(columns={column: name_by_id[column] for column in renamed.columns if column in name_by_id})
    renamed = renamed.rename(columns={column: column.replace("_", " ").title() for column in renamed.columns if "_minus_" not in column})
    renamed = renamed.rename(
        columns={
            column: " minus ".join(name_by_id.get(part, part) for part in column.split("_minus_"))
            for column in renamed.columns
            if "_minus_" in column
        }
    )
    return renamed


def main():
    parser = argparse.ArgumentParser(description="Compare approved fund data in terminal.")
    parser.add_argument("--list", action="store_true", help="List available funds.")
    parser.add_argument("--filter", help="Filter fund list by name, manager, strategy, or status.")
    parser.add_argument("--fund", action="append", help="Fund id or fund name. Repeat for multiple funds.")
    args = parser.parse_args()

    with connect_db() as connection:
        funds = _load_funds(connection)
        if args.list:
            _list_funds(funds, filter_text=args.filter)
            return

        if not args.fund:
            raise ValueError("Provide --fund at least once, or use --list.")

        selected_funds = [_resolve_fund_token(funds, token) for token in args.fund]
        fund_ids = [fund["fund_id"] for fund in selected_funds]
        comparison = compare_funds(connection, fund_ids)
        name_by_id = _name_map(funds)

    print("Funds selected:")
    for fund in selected_funds:
        print(f"- {fund['fund_name']} ({fund['fund_id']})")
    print()
    print("Summary")
    print(comparison["comparison_paragraph"])
    print()

    print("Return Statistics")
    print(_rename_columns(pd.DataFrame(comparison["return_statistics"]), name_by_id).fillna("").to_string(index=False))
    print()

    print("Data Readiness")
    print(_rename_columns(pd.DataFrame(comparison["analysis_readiness_scores"]), name_by_id).fillna("").to_string(index=False))
    print()

    print("Characteristic Similarity")
    print(_rename_columns(pd.DataFrame(comparison["similarity_matrix"]), name_by_id).fillna("").to_string(index=False))
    print()

    aligned = pd.DataFrame(comparison["aligned_returns"])
    print("Aligned Returns")
    if aligned.empty:
        print("No overlapping approved return periods found.")
    else:
        print(_rename_columns(aligned, name_by_id).fillna("").to_string(index=False))


if __name__ == "__main__":
    main()

"""Stage 5 of the pipeline: deterministic comparison. No LLM calls here —
everything is computed from approved data. One decimal place for summary stats.
"""
from fund.factsheet import build_factsheet, return_statistics
from fund.schema import FIELDS, SECTION_ORDER, SECTION_TITLES, section_fields


def compare_funds(connection, fund_ids, field_keys=None):
    sheets = {fund_id: build_factsheet(connection, fund_id) for fund_id in fund_ids}
    rows = []
    keys = field_keys or [k for s in SECTION_ORDER for k in section_fields(s)]
    for key in keys:
        if key not in FIELDS:
            raise ValueError(f"Unknown field: {key}")
        values = {}
        for fund_id, sheet in sheets.items():
            entry = next(
                e for e in sheet["sections"][FIELDS[key][0]] if e["field_key"] == key
            )
            values[fund_id] = entry["value"]
        if any(v is not None for v in values.values()):
            rows.append({"field_key": key, "label": FIELDS[key][1],
                         "section": FIELDS[key][0], "values": values})
    stats = {fund_id: sheet["return_statistics"] for fund_id, sheet in sheets.items()}
    overlap = overlapping_returns(sheets)
    return {"fund_ids": list(fund_ids), "sheets": sheets, "rows": rows,
            "return_statistics": stats, "overlapping_returns": overlap}


def overlapping_returns(sheets, period_type="monthly"):
    """Months where every compared fund has an approved observation."""
    by_fund = {}
    for fund_id, sheet in sheets.items():
        by_fund[fund_id] = {
            row["period_end"]: row["return_pct"]
            for row in sheet["returns"] if row["period_type"] == period_type
        }
    if not by_fund:
        return []
    common = set.intersection(*(set(d) for d in by_fund.values()))
    return [
        {"period_end": period, **{fund_id: by_fund[fund_id][period] for fund_id in by_fund}}
        for period in sorted(common)
    ]


def format_comparison(comparison):
    fund_ids = comparison["fund_ids"]
    names = {fid: comparison["sheets"][fid]["fund_name"] for fid in fund_ids}
    width = max(28, *(len(names[f]) + 2 for f in fund_ids))

    lines = ["", " " * 26 + "".join(names[f][: width - 2].ljust(width) for f in fund_ids), "=" * (26 + width * len(fund_ids))]
    current_section = None
    for row in comparison["rows"]:
        if row["section"] != current_section:
            current_section = row["section"]
            lines.append(f"\n{SECTION_TITLES[current_section]}")
        cells = "".join(
            (str(row["values"][f])[: width - 2] if row["values"][f] is not None else "—").ljust(width)
            for f in fund_ids
        )
        lines.append(f"  {row['label']:<24}{cells}")

    lines.append(f"\n{SECTION_TITLES['returns']} (approved rows only, 1dp)")
    header = f"  {'':<24}" + "".join(names[f][: width - 2].ljust(width) for f in fund_ids)
    lines.append(header)
    for stat in ("count", "average", "volatility", "sharpe_like", "best", "worst"):
        cells = "".join(
            str(comparison["return_statistics"][f].get(stat, "—")).ljust(width) for f in fund_ids
        )
        lines.append(f"  {stat:<24}{cells}")

    overlap = comparison["overlapping_returns"]
    if overlap:
        lines.append(f"\nOverlapping monthly observations: {len(overlap)}")
        for row in overlap[-12:]:
            cells = "".join(f"{row[f]:.1f}%".ljust(width) for f in fund_ids)
            lines.append(f"  {row['period_end']:<24}{cells}")
    else:
        lines.append("\nNo overlapping monthly observations yet.")
    return "\n".join(lines)

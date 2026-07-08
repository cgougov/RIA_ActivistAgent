"""Stage 5 of the pipeline: deterministic comparison. No LLM calls here —
everything is computed from approved data. One decimal place for summary stats.

Return comparison is always over the COMMON period — the months where every
compared fund has an approved observation — so the numbers are apples-to-apples.
Each fund's own full-history stats are still available on its factsheet.
"""
import statistics

from fund.factsheet import build_factsheet
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
    overlap = overlapping_returns(sheets)
    common_stats = common_period_statistics(overlap, fund_ids)
    return {"fund_ids": list(fund_ids), "sheets": sheets, "rows": rows,
            "overlapping_returns": overlap, "common_period_statistics": common_stats,
            "own_history_statistics": {fid: sheets[fid]["return_statistics"] for fid in fund_ids}}


def common_period_statistics(overlap, fund_ids):
    """Return stats computed strictly over the common overlapping months."""
    if len(overlap) < 2:
        return {"count": len(overlap), "period_start": None, "period_end": None, "by_fund": {}}
    by_fund = {}
    for fund_id in fund_ids:
        values = [row[fund_id] for row in overlap]
        average = statistics.mean(values)
        volatility = statistics.stdev(values)
        by_fund[fund_id] = {
            "average": round(average, 1),
            "volatility": round(volatility, 1),
            "sharpe_like": round(average / volatility, 1) if volatility else None,
            "best": round(max(values), 1),
            "worst": round(min(values), 1),
            "cumulative": round((_compound(values) - 1) * 100, 1),
        }
    return {"count": len(overlap), "period_start": overlap[0]["period_end"],
            "period_end": overlap[-1]["period_end"], "by_fund": by_fund}


def _compound(percent_values):
    total = 1.0
    for value in percent_values:
        total *= (1 + value / 100)
    return total


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

    common = comparison["common_period_statistics"]
    header = f"  {'':<24}" + "".join(names[f][: width - 2].ljust(width) for f in fund_ids)
    if common["count"] >= 2:
        lines.append(
            f"\nReturns — COMMON PERIOD ONLY: {common['count']} monthly observations, "
            f"{common['period_start']} → {common['period_end']} (apples-to-apples, 1dp)"
        )
        lines.append(header)
        labels = {"cumulative": "cumulative return %", "average": "avg monthly %",
                  "volatility": "volatility %", "sharpe_like": "sharpe-like",
                  "best": "best month %", "worst": "worst month %"}
        for stat, label in labels.items():
            cells = "".join(str(common["by_fund"][f].get(stat, "—")).ljust(width) for f in fund_ids)
            lines.append(f"  {label:<24}{cells}")
        lines.append(f"\n  Monthly returns over the common period:")
        lines.append(header)
        for row in comparison["overlapping_returns"]:
            cells = "".join(f"{row[f]:.1f}%".ljust(width) for f in fund_ids)
            lines.append(f"  {row['period_end']:<24}{cells}")
    else:
        lines.append(
            f"\nReturns: fewer than 2 common monthly observations across these funds, "
            f"so no apples-to-apples comparison. Each fund's own history:"
        )
        lines.append(header)
        for stat in ("count", "average", "volatility", "best", "worst"):
            cells = "".join(
                str(comparison["own_history_statistics"][f].get(stat, "—")).ljust(width)
                for f in fund_ids
            )
            lines.append(f"  {stat + ' (own)':<24}{cells}")
    return "\n".join(lines)

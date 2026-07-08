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
            "calendar_year": calendar_year_comparison(sheets, fund_ids),
            "own_history_statistics": {fid: sheets[fid]["return_statistics"] for fid in fund_ids}}


def _annual_stats(values):
    """Summary over a fund's annual (calendar-year) returns, in percent points."""
    if not values:
        return {"count": 0}
    stats = {"count": len(values), "average": round(statistics.mean(values), 1),
             "best": round(max(values), 1), "worst": round(min(values), 1),
             "cumulative": round((_compound(values) - 1) * 100, 1)}
    stats["volatility"] = round(statistics.stdev(values), 1) if len(values) > 1 else None
    return stats


def _group_by_class(rows):
    classes = {}
    for row in rows:
        classes.setdefault(row["share_class"], []).append(row)
    return classes


def _annual_from_monthly(monthly_rows):
    """Calendar-year returns compounded from monthly returns — complete years only
    (12 observations). Deterministic; used when a fund reports no annual figure."""
    by_year = {}
    for row in monthly_rows:
        by_year.setdefault(row["period_end"][:4], []).append(row["return_pct"])
    return {year: round((_compound(vals) - 1) * 100, 1)
            for year, vals in by_year.items() if len(vals) == 12}


def annual_series(sheets):
    """One representative annual series per fund. Prefer the fund's reported annual
    returns (longest share-class series); otherwise compound complete calendar years
    from monthly returns. Returns (by_fund {year: pct}, class_used, source)."""
    by_fund, class_used, source = {}, {}, {}
    for fund_id, sheet in sheets.items():
        annual = _group_by_class([r for r in sheet["returns"] if r["period_type"] == "annual"])
        if annual:
            best = max(annual, key=lambda c: len(annual[c]))
            by_fund[fund_id] = {row["period_end"][:4]: row["return_pct"] for row in annual[best]}
            class_used[fund_id], source[fund_id] = best or "(unspecified class)", "reported"
            continue
        monthly = _group_by_class([r for r in sheet["returns"] if r["period_type"] == "monthly"])
        if monthly:
            best = max(monthly, key=lambda c: len(monthly[c]))
            computed = _annual_from_monthly(monthly[best])
            by_fund[fund_id] = computed
            class_used[fund_id] = (best or "(unspecified class)") if computed else None
            source[fund_id] = "computed from monthly" if computed else None
        else:
            by_fund[fund_id], class_used[fund_id], source[fund_id] = {}, None, None
    return by_fund, class_used, source


def calendar_year_comparison(sheets, fund_ids):
    """Compare funds on calendar-year (annual) returns — the cleanest apples-to-
    apples view. Rows span the union of years; stats cover each fund's own history
    plus the years every fund shares."""
    by_fund, class_used, source = annual_series(sheets)
    years = sorted({year for series in by_fund.values() for year in series})
    rows = [{"year": year, **{fid: by_fund[fid].get(year) for fid in fund_ids}}
            for year in years]
    own_stats = {fid: _annual_stats([by_fund[fid][y] for y in sorted(by_fund[fid])])
                 for fid in fund_ids}
    common_years = [y for y in years
                    if all(by_fund[fid].get(y) is not None for fid in fund_ids)]
    common_stats = {fid: _annual_stats([by_fund[fid][y] for y in common_years])
                    for fid in fund_ids} if common_years else {}
    return {"years": years, "rows": rows, "class_used": class_used, "source": source,
            "own_stats": own_stats, "common_years": common_years,
            "common_stats": common_stats}


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


def short_name(fund_name):
    """The typeable handle for a fund: first meaningful word of its name."""
    skip = {"the", "japan", "capital"}
    for word in fund_name.replace(",", " ").split():
        cleaned = word.strip().lower()
        if cleaned and cleaned not in skip and cleaned.isalpha():
            return cleaned
    return fund_name.split()[0].lower()


def format_comparison(comparison):
    fund_ids = comparison["fund_ids"]
    full = {fid: comparison["sheets"][fid]["fund_name"] for fid in fund_ids}
    names = {fid: short_name(full[fid]) for fid in fund_ids}
    width = max(22, *(len(names[f]) + 2 for f in fund_ids))

    legend = "\n".join(f"  {names[f]:<12} {full[f]}" for f in fund_ids)
    lines = ["", "Funds:", legend, "",
             " " * 26 + "".join(names[f].ljust(width) for f in fund_ids),
             "=" * (26 + width * len(fund_ids))]
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

    header = f"  {'':<24}" + "".join(names[f][: width - 2].ljust(width) for f in fund_ids)

    # Calendar-year (annual) returns — the headline apples-to-apples comparison.
    cy = comparison["calendar_year"]
    if cy["years"]:
        classes = "; ".join(
            f"{names[f]}: {cy['class_used'][f]} ({cy['source'][f]})"
            for f in fund_ids if cy["class_used"][f])
        lines.append(f"\nReturns — CALENDAR YEAR (annual %, net where available)")
        if classes:
            lines.append(f"  series used -> {classes}")
        lines.append(header)
        for row in cy["rows"]:
            cells = "".join(
                (f"{row[f]:+.1f}%" if row[f] is not None else "—").ljust(width) for f in fund_ids)
            lines.append(f"  {row['year']:<24}{cells}")
        for stat, label in (("average", "avg annual %"), ("best", "best year %"),
                            ("worst", "worst year %"), ("volatility", "volatility %"),
                            ("cumulative", "cumulative % (own)"), ("count", "years of history")):
            cells = "".join(
                str(cy["own_stats"][f].get(stat, "—")).ljust(width) for f in fund_ids)
            lines.append(f"  {label:<24}{cells}")
        if cy["common_years"]:
            span = f"{cy['common_years'][0]}–{cy['common_years'][-1]}"
            lines.append(f"\n  Common years only ({len(cy['common_years'])}: {span}, apples-to-apples):")
            for stat, label in (("average", "avg annual %"), ("cumulative", "cumulative %")):
                cells = "".join(
                    str(cy["common_stats"][f].get(stat, "—")).ljust(width) for f in fund_ids)
                lines.append(f"  {label:<24}{cells}")
        else:
            lines.append("\n  No calendar year is shared by all funds — see own-history stats above.")

    common = comparison["common_period_statistics"]
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

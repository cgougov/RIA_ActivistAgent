"""Stage 5 of the pipeline: deterministic comparison. No LLM calls here —
everything is computed from approved data. One decimal place for summary stats.

Return comparison is always over the COMMON period — the months where every
compared fund has an approved observation — so the numbers are apples-to-apples.
Each fund's own full-history stats are still available on its factsheet.
"""

from fund.analytics import (
    calendar_year_comparison,
    common_period_statistics,
    month_key,
)
from fund.factsheet import build_factsheet
from fund.schema import FIELDS, SECTION_ORDER, SECTION_TITLES, section_fields
from fund.universe import classifications


def compare_funds(connection, fund_ids, field_keys=None):
    sheets = {fund_id: build_factsheet(connection, fund_id) for fund_id in fund_ids}
    rows = []
    keys = field_keys or [k for s in SECTION_ORDER for k in section_fields(s)]
    for key in keys:
        if key not in FIELDS:
            raise ValueError(f"Unknown field: {key}")
        fund_level_values = {fund_id: _entry_value(sheets[fund_id], key) for fund_id in fund_ids}
        if any(v is not None for v in fund_level_values.values()):
            rows.append({"field_key": key, "label": FIELDS[key][1],
                         "section": FIELDS[key][0], "values": fund_level_values})
        for share_class in _share_classes_for_field(sheets, key):
            values = {fund_id: _entry_value(sheets[fund_id], key, share_class=share_class) for fund_id in fund_ids}
            if any(v is not None for v in values.values()):
                rows.append({
                    "field_key": key,
                    "label": f"{FIELDS[key][1]} [{share_class}]",
                    "section": FIELDS[key][0],
                    "values": values,
                    "share_class": share_class,
                })
    overlap = overlapping_returns(sheets)
    common_stats = common_period_statistics(overlap, fund_ids)
    return {"fund_ids": list(fund_ids), "sheets": sheets, "rows": rows,
            "classifications": {row["fund_id"]: row for row in classifications(connection, fund_ids)},
            "overlapping_returns": overlap, "common_period_statistics": common_stats,
            "calendar_year": calendar_year_comparison(sheets, fund_ids),
            "own_history_statistics": {fid: sheets[fid]["return_statistics"] for fid in fund_ids}}


def _entry_value(sheet, field_key, share_class=""):
    if share_class:
        for entry in sheet.get("share_classes", {}).get(share_class, {}).get("sections", {}).get(FIELDS[field_key][0], []):
            if entry["field_key"] == field_key:
                return entry["value"]
        return None
    entry = next(
        e for e in sheet["sections"][FIELDS[field_key][0]] if e["field_key"] == field_key
    )
    return entry["value"]


def _share_classes_for_field(sheets, field_key):
    classes = set()
    section = FIELDS[field_key][0]
    for sheet in sheets.values():
        for share_class, payload in sheet.get("share_classes", {}).items():
            if any(entry["field_key"] == field_key for entry in payload.get("sections", {}).get(section, [])):
                classes.add(share_class)
    return sorted(classes)


def overlapping_returns(sheets, period_type="monthly"):
    """Months where every compared fund has an approved observation."""
    by_fund = {}
    display_period = {}
    for fund_id, sheet in sheets.items():
        rows = [row for row in sheet["returns"] if row["period_type"] == period_type]
        by_month = {}
        for row in rows:
            key = month_key(row["period_end"]) if period_type == "monthly" else row["period_end"]
            if key is None:
                continue
            by_month[key] = row["return_pct"]
            display_period[key] = key
        by_fund[fund_id] = by_month
    if not by_fund:
        return []
    common = set.intersection(*(set(d) for d in by_fund.values()))
    return [
        {"period": display_period[period], **{fund_id: by_fund[fund_id][period] for fund_id in by_fund}}
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


def comparison_summary(comparison):
    fund_ids = comparison["fund_ids"]
    sheets = comparison["sheets"]
    names = [sheets[fid]["fund_name"] for fid in fund_ids]
    lead = ", ".join(names[:-1]) + f", and {names[-1]}" if len(names) > 2 else " and ".join(names)

    def first_value(fid, key):
        for entry in sheets[fid]["sections"][FIELDS[key][0]]:
            if entry["field_key"] == key:
                return entry["value"]
        return None

    strategy_bits = []
    for fid in fund_ids[:3]:
        style = first_value(fid, "activism_style") or first_value(fid, "primary_strategy")
        if style:
            strategy_bits.append(f"{short_name(sheets[fid]['fund_name'])} is positioned as {style}")
    strategy_text = "; ".join(strategy_bits) if strategy_bits else "approved strategy descriptors are limited"

    fee_bits = []
    for fid in fund_ids[:3]:
        mgmt = first_value(fid, "management_fee")
        perf = first_value(fid, "performance_fee")
        if mgmt or perf:
            fee_bits.append(
                f"{short_name(sheets[fid]['fund_name'])} charges {mgmt or 'n/a'} management and {perf or 'n/a'} performance"
            )
    fee_text = "; ".join(fee_bits) if fee_bits else "fee disclosures are patchy across the selected funds"

    posture_bits = []
    for fid in fund_ids[:3]:
        net = first_value(fid, "net_exposure")
        aum = first_value(fid, "aum")
        parts = []
        if net:
            parts.append(f"net exposure {net}%")
        if aum:
            parts.append(f"AUM {aum}")
        if parts:
            posture_bits.append(f"{short_name(sheets[fid]['fund_name'])} reports " + ", ".join(parts))
    posture_text = "; ".join(posture_bits) if posture_bits else None

    common = comparison["common_period_statistics"]
    if common["count"] >= 2:
        best_fund = max(fund_ids, key=lambda fid: common["by_fund"][fid]["cumulative"])
        common_text = (
            f"Over the shared monthly window from {common['period_start']} to {common['period_end']}, "
            f"{short_name(sheets[best_fund]['fund_name'])} has the stronger cumulative approved return "
            f"({common['by_fund'][best_fund]['cumulative']}%)."
        )
    else:
        common_text = "There is not enough overlapping approved monthly return history for a clean apples-to-apples performance read."

    summary = (
        f"{lead} differ most clearly in stated strategy and terms: {strategy_text}. "
        f"On fund terms, {fee_text}. "
    )
    if posture_text:
        summary += f"On size and positioning, {posture_text}. "
    summary += common_text
    return summary


def format_comparison(comparison):
    fund_ids = comparison["fund_ids"]
    full = {fid: comparison["sheets"][fid]["fund_name"] for fid in fund_ids}
    names = {fid: short_name(full[fid]) for fid in fund_ids}
    width = max(22, *(len(names[f]) + 2 for f in fund_ids))

    legend = "\n".join(f"  {names[f]:<12} {full[f]}" for f in fund_ids)
    lines = ["", "Funds:", legend, "", comparison_summary(comparison), ""]

    classifications = comparison.get("classifications") or {}
    lines.extend(["Universe status", "---------------"])
    for fund_id in fund_ids:
        row = classifications.get(fund_id, {})
        lines.append(
            f"  {names[fund_id]:<12} "
            f"{row.get('activist_universe_status') or 'unclassified'}; "
            f"{row.get('activity_status') or 'unclassified'}"
        )
    lines.append("")

    header = f"  {'':<24}" + "".join(names[f][: width - 2].ljust(width) for f in fund_ids)

    for section_name, title in (
        ("terms", "Terms"),
        ("metrics", "Reported Metrics"),
    ):
        section_rows = [row for row in comparison["rows"] if row["section"] == section_name]
        if not section_rows:
            continue
        lines.append(title)
        lines.append("-" * len(title))
        lines.append(header)
        for row in section_rows:
            cells = "".join(
                (str(row["values"][f])[: width - 2] if row["values"][f] is not None else "—").ljust(width)
                for f in fund_ids
            )
            lines.append(f"  {row['label']:<24}{cells}")
        lines.append("")

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
            f"\nReturns - COMMON PERIOD ONLY: {common['count']} monthly observations, "
            f"{common['period_start']} -> {common['period_end']} "
            f"(apples-to-apples, computed internal; annualized where noted)"
        )
        lines.append(header)
        labels = {"cumulative": "cumulative return %", "average_period_return": "avg monthly %",
                  "annualized_volatility": "annualized vol %",
                  "annualized_sharpe": "annualized Sharpe",
                  "sortino": "Sortino", "best": "best month %", "worst": "worst month %"}
        for stat, label in labels.items():
            cells = "".join(str(common["by_fund"][f].get(stat, "—")).ljust(width) for f in fund_ids)
            lines.append(f"  {label:<24}{cells}")
        assumption = next(iter(common["by_fund"].values()))
        lines.append(
            f"  {'assumptions':<24}"
            + "".join(
                f"rf={assumption.get('risk_free_rate', 0.0)}%, p/y={assumption.get('periods_per_year', 12)}".ljust(width)
                for _ in fund_ids
            )
        )
        lines.append(f"\n  Monthly returns over the common period:")
        lines.append(header)
        for row in comparison["overlapping_returns"]:
            cells = "".join(f"{row[f]:.1f}%".ljust(width) for f in fund_ids)
            lines.append(f"  {row['period']:<24}{cells}")
        correlations = common.get("correlations") or []
        if correlations:
            lines.append("\n  Return correlation (Pearson, same monthly window; computed internal):")
            for row in correlations:
                label = f"{names[row['left']]} vs {names[row['right']]}"
                if row["correlation"] is None:
                    value = f"unavailable (<6 observations; n={row['observations']})"
                else:
                    value = f"{row['correlation']:+.2f} (n={row['observations']})"
                lines.append(f"  {label:<24}{value}")
    else:
        lines.append(
            f"\nReturns: fewer than 2 common monthly observations across these funds, "
            f"so no apples-to-apples comparison. Each fund's own history:"
        )
        lines.append(header)
        for stat in ("count", "average_period_return", "annualized_volatility", "best", "worst"):
            cells = "".join(
                str(comparison["own_history_statistics"][f].get(stat, "—")).ljust(width)
                for f in fund_ids
            )
            lines.append(f"  {stat + ' (own)':<24}{cells}")
    return "\n".join(lines)

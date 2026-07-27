"""Deterministic fund screener over approved facts and computed analytics."""
import re

from fund.factsheet import build_factsheet
from fund.universe import classifications, is_eligible


PRESETS = {
    "proven-engagement": {
        "description": "Has approved engagement examples/track record and enough return history.",
        "required_any": ["example_engagements", "track_record_highlights"],
        "min_months": 36,
    },
    "concentrated-activist": {
        "description": "Activism plus concentration/top-holdings evidence.",
        "required_any": ["activism_style", "primary_strategy"],
        "text_any": [("top_holdings", ""), ("flag_concentration_risk", "")],
    },
    "small-cap-activist": {
        "description": "Small/mid-cap activism signal.",
        "required_any": ["activism_style", "primary_strategy"],
        "text_any": [("market_cap_focus", "small"), ("market_cap_focus", "mid")],
    },
    "governance-focused": {
        "description": "Governance or capital-allocation engagement language.",
        "required_any": ["activism_style", "value_creation_approach"],
        "text_any": [
            ("activism_style", "governance"),
            ("value_creation_approach", "governance"),
            ("value_creation_approach", "capital"),
            ("strategy_description", "shareholder"),
        ],
    },
}


def _field_value(sheet, field_key):
    for entries in sheet["sections"].values():
        for entry in entries:
            if entry["field_key"] == field_key and entry["value"] is not None:
                return entry
    return None


def _first_analytics(sheet):
    analytics = sheet.get("computed_return_analytics") or []
    return analytics[0] if analytics else {}


def _metric_value(sheet, field):
    fact = _field_value(sheet, field)
    if fact and fact.get("value_num") is not None:
        return fact["value_num"]
    if fact:
        parsed = _parse_number(fact["value"])
        if parsed is not None:
            return parsed
    analytics = _first_analytics(sheet)
    if field == "max_drawdown":
        return (analytics.get("max_drawdown") or {}).get("max_drawdown")
    if field == "years_history":
        count = analytics.get("count")
        return round(count / 12, 1) if count is not None else None
    return analytics.get(field)


def _parse_number(value):
    match = re.search(r"-?\d+(\.\d+)?", str(value or "").replace(",", ""))
    return float(match.group()) if match else None


def _passes_where(sheet, condition):
    match = re.fullmatch(r"\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(<=|>=|=|<|>)\s*(-?\d+(\.\d+)?)\s*", condition)
    if not match:
        raise ValueError(f"Unsupported screen condition: {condition!r}")
    field, op, raw = match.group(1), match.group(2), float(match.group(3))
    value = _metric_value(sheet, field)
    if value is None:
        return False
    return {
        "<": value < raw,
        "<=": value <= raw,
        ">": value > raw,
        ">=": value >= raw,
        "=": value == raw,
    }[op]


def _matches_preset(sheet, preset):
    spec = PRESETS[preset]
    matched = []
    required_any = spec.get("required_any") or []
    if required_any:
        hits = [key for key in required_any if _field_value(sheet, key)]
        if not hits:
            return False, matched
        matched.extend(hits)
    min_months = spec.get("min_months")
    if min_months is not None:
        count = _first_analytics(sheet).get("count", 0)
        if count < min_months:
            return False, matched
        matched.append(f"{count} monthly returns")
    text_any = spec.get("text_any") or []
    if text_any:
        hits = []
        for key, needle in text_any:
            entry = _field_value(sheet, key)
            value = (entry or {}).get("value") or ""
            if entry and (not needle or needle.lower() in value.lower()):
                hits.append(key)
        if not hits:
            return False, matched
        matched.extend(hits)
    return True, matched


def screen_funds(connection, where=None, preset=None, min_history_years=None, sort_field=None,
                 descending=True, activist_only=False, include_uncertain=False,
                 include_candidates=False):
    fund_ids = [
        row["fund_id"]
        for row in connection.execute("SELECT fund_id FROM funds ORDER BY fund_id").fetchall()
    ]
    classes = {row["fund_id"]: row for row in classifications(connection, fund_ids)}
    rows = []
    for fund_id in fund_ids:
        classification = classes[fund_id]
        if activist_only and not is_eligible(
                classification, include_uncertain=include_uncertain,
                include_candidates=include_candidates):
            continue
        sheet = build_factsheet(connection, fund_id)
        analytics = _first_analytics(sheet)
        if min_history_years is not None and (analytics.get("count", 0) / 12) < min_history_years:
            continue
        if where and not all(_passes_where(sheet, condition) for condition in where):
            continue
        matched = []
        if preset:
            ok, matched = _matches_preset(sheet, preset)
            if not ok:
                continue
        rows.append(_screen_row(sheet, matched, classification))
    if sort_field:
        def sort_key(row):
            value = row["metrics"].get(sort_field)
            if value is None:
                return float("-inf") if descending else float("inf")
            return value
        rows.sort(key=sort_key, reverse=descending)
    return {
        "preset": preset, "where": where or [], "rows": rows,
        "activist_only": activist_only, "include_uncertain": include_uncertain,
        "include_candidates": include_candidates,
    }


def _screen_row(sheet, matched, classification):
    analytics = _first_analytics(sheet)
    fields = {
        key: (_field_value(sheet, key) or {}).get("value")
        for key in (
            "primary_strategy",
            "activism_style",
            "market_cap_focus",
            "aum",
            "management_fee",
            "performance_fee",
        )
    }
    metrics = {
        "annualized_sharpe": analytics.get("annualized_sharpe"),
        "annualized_volatility": analytics.get("annualized_volatility"),
        "cumulative": analytics.get("cumulative"),
        "max_drawdown": (analytics.get("max_drawdown") or {}).get("max_drawdown"),
        "years_history": round((analytics.get("count") or 0) / 12, 1),
    }
    return {
        "fund_id": sheet["fund_id"],
        "fund_name": sheet["fund_name"],
        "manager_name": sheet["manager_name"],
        "fields": fields,
        "metrics": metrics,
        "matched": sorted(set(matched)),
        "approved_fields": sheet["coverage"]["fund_level_fields_filled"],
        "return_rows": sheet["coverage"]["return_rows"],
        "classification": classification,
    }


def format_screen(result):
    rows = result["rows"]
    lines = []
    heading = "Fund screen"
    if result.get("preset"):
        heading += f" - {result['preset']}"
    lines.extend([heading, "-" * len(heading)])
    if result.get("where"):
        lines.append("Filters: " + ", ".join(result["where"]))
    if result.get("activist_only"):
        lines.append("Universe: verified activists with active status"
                     + ("; uncertain included" if result.get("include_uncertain") else "")
                     + ("; candidates included" if result.get("include_candidates") else ""))
    if not rows:
        lines.append("No funds matched.")
        return "\n".join(lines)
    lines.append(
        f"  {'fund':<12} {'ann Sharpe':>10} {'ann vol':>8} {'max DD':>8} "
        f"{'years':>6} {'fields':>7}  universe / strategy"
    )
    for row in rows:
        metrics = row["metrics"]
        strategy = row["fields"].get("activism_style") or row["fields"].get("primary_strategy") or "-"
        matched = f" | matched: {', '.join(row['matched'])}" if row["matched"] else ""
        classification = row["classification"]
        status = (classification.get("activist_universe_status") or "unclassified") + "/" + (
            classification.get("activity_status") or "unclassified"
        )
        lines.append(
            f"  {row['fund_id']:<12} "
            f"{_fmt(metrics.get('annualized_sharpe')):>10} "
            f"{_fmt(metrics.get('annualized_volatility')):>8} "
            f"{_fmt(metrics.get('max_drawdown')):>8} "
            f"{_fmt(metrics.get('years_history')):>6} "
            f"{row['approved_fields']:>7}  {status}; {strategy[:48]}{matched}"
        )
    return "\n".join(lines)


def _fmt(value):
    return "-" if value is None else str(value)

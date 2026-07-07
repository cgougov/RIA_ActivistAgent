import statistics

from core.fund_brief import build_fund_brief
from core.factsheet_schema import STANDARDIZED_FIELD_SPECS, STANDARDIZED_SECTION_ORDER
from core.fund_snapshot import FundSnapshot
from core.return_rows import MONTH_ORDER


def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_one(value):
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return round(numeric, 1)


def _display_numeric(value, unit=None, decimals=1):
    numeric = _safe_float(value)
    if numeric is None:
        return None
    rounded = round(numeric, decimals)
    if unit == "percent":
        return f"{rounded:.{decimals}f}%"
    return f"{rounded:.{decimals}f}"


def format_return_cell(value):
    numeric = _safe_float(value)
    if numeric is None:
        return value
    rounded = round(numeric, 1)
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.1f}"


def _date_key(row, *fields):
    for field in fields:
        value = _clean(row.get(field))
        if value:
            return value
    return ""


def _metric_rows(snapshot):
    latest = {}
    rows = sorted(
        snapshot.get("metrics", []),
        key=lambda row: (_clean(row.get("metric_name")) or "", _date_key(row, "as_of_date")),
        reverse=True,
    )
    for row in rows:
        metric_name = row.get("metric_name")
        if metric_name and metric_name not in latest:
            latest[metric_name] = row
    return latest


def _reported_metric(snapshot, metric_name):
    row = _metric_rows(snapshot).get(metric_name)
    if not row:
        return None
    numeric_value = row.get("metric_value")
    if numeric_value is None:
        numeric_value = row.get("normalized_value") or row.get("raw_value")
    return {
        "metric_name": metric_name,
        "source": "reported",
        "numeric_value": _safe_float(numeric_value),
        "display_value": _clean(row.get("normalized_value")) or _clean(row.get("raw_value")),
        "unit": row.get("unit"),
        "as_of_date": row.get("as_of_date"),
        "source_document_id": row.get("source_document_id"),
        "page_number": row.get("page_number"),
    }


def _formatted_metric(metric_name, metric):
    if not metric:
        return None
    numeric_value = metric.get("numeric_value")
    unit = metric.get("unit")
    if numeric_value is None:
        return metric
    percent_metrics = {"volatility", "max_drawdown"}
    ratio_metrics = {"beta", "sharpe_ratio", "benchmark_correlation", "information_ratio"}
    if metric_name in percent_metrics:
        metric["display_value"] = _display_numeric(numeric_value, "percent")
    elif metric_name in ratio_metrics:
        metric["display_value"] = _display_numeric(numeric_value)
    return metric


def _calculated_return_statistics(returns):
    values = [_safe_float(row.get("return_value")) for row in returns]
    values = [value for value in values if value is not None]
    if not values:
        return {
            "return_count": 0,
            "average_return": None,
            "volatility": None,
            "sharpe_like": None,
            "best_return": None,
            "worst_return": None,
        }
    average = statistics.mean(values)
    volatility = statistics.stdev(values) if len(values) >= 2 else None
    sharpe_like = average / volatility if volatility not in (None, 0) else None
    return {
        "return_count": len(values),
        "average_return": _round_one(average),
        "volatility": _round_one(volatility),
        "sharpe_like": _round_one(sharpe_like),
        "best_return": _round_one(max(values)),
        "worst_return": _round_one(min(values)),
    }


def _preferred_metric(snapshot, calculated_stats, metric_name, calculated_key=None):
    reported = _reported_metric(snapshot, metric_name)
    if reported and reported.get("numeric_value") is not None:
        return reported
    if calculated_key is None:
        return reported
    calculated_value = calculated_stats.get(calculated_key)
    if calculated_value is None:
        return reported
    return {
        "metric_name": metric_name,
        "source": "calculated",
        "numeric_value": calculated_value,
        "display_value": _display_numeric(calculated_value, "percent" if calculated_key in {"average_return", "volatility", "best_return", "worst_return"} else None),
        "unit": "percent" if calculated_key in {"average_return", "volatility", "best_return", "worst_return"} else None,
        "as_of_date": None,
        "source_document_id": None,
        "page_number": None,
    }


def _column_sort_key(label):
    if label is None:
        return (99, "")
    text = str(label).strip().lower()
    return (MONTH_ORDER.get(text, 90), text)


def _row_sort_key(label, date_hint):
    if date_hint:
        return (date_hint, str(label or ""))
    text = str(label or "").strip()
    digits = "".join(char for char in text if char.isdigit())
    return (digits or text, text)


def build_return_table_previews(rows_data):
    if not rows_data:
        return []
    grouped = {}
    for row in rows_data:
        key = (row.get("table_name") or "Extracted Return Table", row.get("share_class") or "")
        grouped.setdefault(key, []).append(row)

    previews = []
    for (table_name, share_class), group_rows in grouped.items():
        ordered = sorted(
            group_rows,
            key=lambda row: (
                row.get("period_end_date") or "",
                _row_sort_key(row.get("row_label"), row.get("period_end_date")),
                _column_sort_key(row.get("column_label")),
            ),
        )
        row_order = []
        column_order = []
        values = {}
        for row in ordered:
            row_label = row.get("row_label") or row.get("period_end_date") or "Unlabeled"
            column_label = row.get("column_label") or row.get("period_type") or "Value"
            if row_label not in row_order:
                row_order.append(row_label)
            if column_label not in column_order:
                column_order.append(column_label)
            values[(row_label, column_label)] = row.get("return_value")
        if share_class and table_name and table_name.strip().lower() == share_class.strip().lower():
            display_name = share_class
        elif share_class:
            display_name = f"{table_name} · {share_class}"
        else:
            display_name = table_name
        previews.append(
            {
                "table_name": table_name,
                "share_class": share_class,
                "display_name": display_name,
                "row_order": row_order,
                "column_order": sorted(column_order, key=_column_sort_key),
                "values": values,
            }
        )
    return previews


def _entry_source_label(source):
    if source == "reported":
        return "approved reported value"
    if source == "calculated":
        return "calculated from approved returns"
    return "approved value"


def _build_approved_section_entries(brief, preferred_metrics):
    overview_map = dict(brief["overview"])
    strategy_map = dict(brief["strategy"])
    terms_map = dict(brief["terms"])
    notes = brief["notes"]

    metrics_map = {
        "beta": preferred_metrics["beta"],
        "volatility": preferred_metrics["volatility"],
        "sharpe_ratio": preferred_metrics["sharpe_ratio"],
        "information_ratio": None,
        "benchmark_correlation": preferred_metrics["benchmark_correlation"],
        "max_drawdown": preferred_metrics["max_drawdown"],
    }

    sections = {}
    for section in STANDARDIZED_SECTION_ORDER:
        entries = []
        for field_key, label, _matches in STANDARDIZED_FIELD_SPECS.get(section, []):
            display_value = None
            source = "missing"
            if section == "overview":
                display_value = overview_map.get(label)
            elif section == "strategy":
                display_value = strategy_map.get(label)
            elif section == "terms":
                display_value = terms_map.get(label)
            elif section == "metrics":
                metric = metrics_map.get(field_key)
                if metric:
                    display_value = metric.get("display_value")
                    source = metric.get("source") or "missing"
            elif section == "notes_flags":
                if field_key == "neutral_summary":
                    display_value = notes["neutral_summary"]
                elif field_key == "differentiating_edge":
                    display_value = notes["differentiating_edge"]
            entries.append(
                {
                    "field_key": field_key,
                    "label": label,
                    "display_value": display_value,
                    "source": _entry_source_label(source),
                    "has_approved_value": bool(display_value and not str(display_value).lower().startswith("not yet approved")),
                }
            )

        if section == "manager":
            people_text = "; ".join(brief["people"]) if brief["people"] else None
            entries = [
                {
                    "field_key": "people_roles",
                    "label": "People / roles",
                    "display_value": people_text,
                    "source": "approved value",
                    "has_approved_value": bool(people_text),
                }
            ]
        elif section == "notes_flags":
            flags_text = "; ".join(notes["flags"]) if notes["flags"] else None
            entries.append(
                {
                    "field_key": "flags",
                    "label": "Flags / missing disclosures",
                    "display_value": flags_text,
                    "source": "approved value",
                    "has_approved_value": bool(flags_text and not str(flags_text).lower().startswith("not yet approved")),
                }
            )
        sections[section] = entries
    return sections


def build_fund_view(connection, fund_id):
    snapshot = FundSnapshot(connection, fund_id).build()
    returns = list(snapshot.get("returns", []))
    calculated_stats = _calculated_return_statistics(returns)
    brief = build_fund_brief(snapshot)
    preferred_metrics = {
        "aum": _formatted_metric("aum", _reported_metric(snapshot, "aum")),
        "nav": _formatted_metric("nav", _reported_metric(snapshot, "nav")),
        "beta": _formatted_metric("beta", _preferred_metric(snapshot, calculated_stats, "beta")),
        "volatility": _formatted_metric("volatility", _preferred_metric(snapshot, calculated_stats, "volatility", calculated_key="volatility")),
        "sharpe_ratio": _formatted_metric("sharpe_ratio", _preferred_metric(snapshot, calculated_stats, "sharpe_ratio", calculated_key="sharpe_like")),
        "benchmark_correlation": _formatted_metric("benchmark_correlation", _preferred_metric(snapshot, calculated_stats, "benchmark_correlation")),
        "max_drawdown": _formatted_metric("max_drawdown", _preferred_metric(snapshot, calculated_stats, "max_drawdown")),
    }
    performance_rows = [
        {
            "label": "AUM",
            "value": preferred_metrics["aum"]["display_value"] if preferred_metrics["aum"] else "No approved value",
            "source": preferred_metrics["aum"]["source"] if preferred_metrics["aum"] else "missing",
        },
        {
            "label": "NAV",
            "value": preferred_metrics["nav"]["display_value"] if preferred_metrics["nav"] else "No approved value",
            "source": preferred_metrics["nav"]["source"] if preferred_metrics["nav"] else "missing",
        },
        {
            "label": "Beta",
            "value": preferred_metrics["beta"]["display_value"] if preferred_metrics["beta"] else "No approved value",
            "source": preferred_metrics["beta"]["source"] if preferred_metrics["beta"] else "missing",
        },
        {
            "label": "Volatility",
            "value": preferred_metrics["volatility"]["display_value"] if preferred_metrics["volatility"] else "No approved value",
            "source": preferred_metrics["volatility"]["source"] if preferred_metrics["volatility"] else "missing",
        },
        {
            "label": "Sharpe",
            "value": preferred_metrics["sharpe_ratio"]["display_value"] if preferred_metrics["sharpe_ratio"] else "No approved value",
            "source": preferred_metrics["sharpe_ratio"]["source"] if preferred_metrics["sharpe_ratio"] else "missing",
        },
        {
            "label": "Benchmark correlation",
            "value": preferred_metrics["benchmark_correlation"]["display_value"] if preferred_metrics["benchmark_correlation"] else "No approved value",
            "source": preferred_metrics["benchmark_correlation"]["source"] if preferred_metrics["benchmark_correlation"] else "missing",
        },
        {
            "label": "Max drawdown",
            "value": preferred_metrics["max_drawdown"]["display_value"] if preferred_metrics["max_drawdown"] else "No approved value",
            "source": preferred_metrics["max_drawdown"]["source"] if preferred_metrics["max_drawdown"] else "missing",
        },
        {
            "label": "Return rows",
            "value": calculated_stats["return_count"],
            "source": "calculated",
        },
        {
            "label": "Average return",
            "value": _display_numeric(calculated_stats["average_return"], "percent") if calculated_stats["average_return"] is not None else "No approved value",
            "source": "calculated" if calculated_stats["average_return"] is not None else "missing",
        },
        {
            "label": "Best / worst",
            "value": (
                f"{_display_numeric(calculated_stats['best_return'], 'percent')} / {_display_numeric(calculated_stats['worst_return'], 'percent')}"
                if calculated_stats["best_return"] is not None and calculated_stats["worst_return"] is not None
                else "No approved value"
            ),
            "source": "calculated" if calculated_stats["best_return"] is not None and calculated_stats["worst_return"] is not None else "missing",
        },
    ]
    return {
        "fund_id": fund_id,
        "snapshot": snapshot,
        "brief": brief,
        "preferred_metrics": preferred_metrics,
        "calculated_return_statistics": calculated_stats,
        "performance_rows": performance_rows,
        "return_table_previews": build_return_table_previews(returns),
        "approved_factsheet": {
            "sections": _build_approved_section_entries(brief, preferred_metrics),
            "return_table_previews": build_return_table_previews(returns),
            "return_statistics": calculated_stats,
        },
    }

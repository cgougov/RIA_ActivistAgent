"""Markdown exports for approved factsheets and comparisons.

Exports are assembled views over approved data. They do not create new source
of truth and they do not call the LLM.
"""
import statistics

from fund.analytics import best_monthly_series_by_class
from fund.compare import compare_funds, short_name
from fund.factsheet import build_factsheet
from fund.schema import SECTION_ORDER, SECTION_TITLES


def _md_escape(value):
    return str(value if value is not None else "").replace("|", "\\|")


def _fmt_pct(value):
    if value is None:
        return "-"
    return f"{value:+.1f}%"


def _source_ref(source):
    if not source:
        return ""
    page = f" p.{source['page']}" if source.get("page") is not None else ""
    return f"{source['doc_id']}{page}"


def _primary_monthly_stats(sheet):
    analytics = sheet.get("computed_return_analytics") or best_monthly_series_by_class(sheet.get("returns") or [])
    return analytics[0] if analytics else None


def _stat_value(stats, key):
    if not stats:
        return None
    if key == "max_drawdown":
        drawdown = stats.get("max_drawdown") or {}
        return drawdown.get("max_drawdown")
    return stats.get(key)


def peer_context(connection, fund_id):
    rows = connection.execute("SELECT fund_id FROM funds ORDER BY fund_id").fetchall()
    sheets = {row["fund_id"]: build_factsheet(connection, row["fund_id"]) for row in rows}
    stats_by_fund = {
        fid: _primary_monthly_stats(sheet)
        for fid, sheet in sheets.items()
    }
    context = {}
    for key, lower_is_better in (
        ("annualized_sharpe", False),
        ("cumulative", False),
        ("annualized_volatility", True),
        ("max_drawdown", True),
    ):
        values = {
            fid: _stat_value(stats, key)
            for fid, stats in stats_by_fund.items()
            if _stat_value(stats, key) is not None
        }
        if fund_id not in values:
            continue
        ordered = sorted(values, key=lambda fid: values[fid], reverse=not lower_is_better)
        context[key] = {
            "value": values[fund_id],
            "peer_median": round(statistics.median(values.values()), 1),
            "rank": ordered.index(fund_id) + 1,
            "peer_count": len(ordered),
        }
    return context


def _peer_note(peer, key, suffix=""):
    item = peer.get(key)
    if not item:
        return "-"
    value = item["value"]
    value_text = f"{value}{suffix}" if suffix else str(value)
    median_text = f"{item['peer_median']}{suffix}" if suffix else str(item["peer_median"])
    return (
        f"{value_text} (peer median {median_text}, "
        f"rank {item['rank']}/{item['peer_count']})"
    )


def _metric_note(stats, peer, key, suffix=""):
    item = peer.get(key)
    if item:
        return _peer_note(peer, key, suffix=suffix)
    value = _stat_value(stats, key)
    if value is None:
        return "-"
    return f"{value}{suffix}" if suffix else str(value)


def markdown_factsheet(sheet, peer=None):
    peer = peer or {}
    lines = [
        f"# {sheet['fund_name']}",
        "",
        f"Manager: {sheet['manager_name']}",
        f"Fund ID: `{sheet['fund_id']}`",
        f"Coverage: {sheet['coverage']['fund_level_fields_filled']}/{sheet['coverage']['fields_total']} "
        f"fund-level fields, {sheet['coverage']['return_rows']} approved return rows",
        "",
    ]
    sources = []
    for section in SECTION_ORDER:
        entries = [entry for entry in sheet["sections"][section] if entry["value"] is not None]
        if not entries:
            continue
        lines.extend([f"## {SECTION_TITLES[section]}", "", "| Field | Value | Source |", "| --- | --- | --- |"])
        for entry in entries:
            source = entry.get("source")
            ref = _source_ref(source)
            value = entry["value"]
            if entry["unit"] == "percent" and "%" not in value:
                value = f"{value}%"
            if entry["as_of_date"]:
                value = f"{value} (as of {entry['as_of_date']})"
            lines.append(f"| {_md_escape(entry['label'])} | {_md_escape(value)} | {_md_escape(ref)} |")
            if source:
                sources.append(source)
        lines.append("")

    for share_class, payload in (sheet.get("share_classes") or {}).items():
        lines.extend([f"## Share Class: {share_class}", ""])
        for section in SECTION_ORDER:
            entries = payload["sections"].get(section, [])
            if not entries:
                continue
            lines.extend([f"### {SECTION_TITLES[section]}", "", "| Field | Value | Source |", "| --- | --- | --- |"])
            for entry in entries:
                source = entry.get("source")
                lines.append(f"| {_md_escape(entry['label'])} | {_md_escape(entry['value'])} | {_md_escape(_source_ref(source))} |")
                if source:
                    sources.append(source)
            lines.append("")

    lines.extend(["## Returns", ""])
    annual_blocks = sheet.get("returns") or []
    annual_rows = [row for row in annual_blocks if row["period_type"] == "annual"]
    if annual_rows:
        lines.extend(["| Year | Share class | Return | Type | Source |", "| --- | --- | ---: | --- | --- |"])
        for row in annual_rows:
            lines.append(
                f"| {row['period_end'][:4]} | {_md_escape(row.get('share_class') or '(unspecified)')} | "
                f"{row['return_pct']:.1f}% | {_md_escape(row['return_type'])} | {row['doc_id']} p.{row['page']} |"
            )
        lines.append("")
    computed = sheet.get("computed_return_analytics") or []
    if computed:
        stats = computed[0]
        drawdown = stats.get("max_drawdown") or {}
        lines.extend([
            "### Computed Monthly Analytics",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Annualized Sharpe | {_metric_note(stats, peer, 'annualized_sharpe')} |",
            f"| Cumulative return | {_metric_note(stats, peer, 'cumulative', '%')} |",
            f"| Annualized volatility | {_metric_note(stats, peer, 'annualized_volatility', '%')} |",
            f"| Max drawdown | {_metric_note(stats, peer, 'max_drawdown', '%')} |",
            f"| Sortino | {stats.get('sortino') if stats.get('sortino') is not None else '-'} |",
            f"| Positive months | {stats.get('positive_periods_pct') if stats.get('positive_periods_pct') is not None else '-'}% |",
            f"| Period | {stats.get('period_start')} to {stats.get('period_end')} |",
            "",
        ])
        if drawdown:
            lines.append(
                f"Drawdown window: {drawdown.get('peak_date') or 'n/a'} to "
                f"{drawdown.get('trough_date') or 'n/a'}, recovered "
                f"{drawdown.get('recovery_date') or 'not yet/unknown'}."
            )
            lines.append("")

    if sources:
        lines.extend(["## Sources Appendix", ""])
        seen = set()
        for source in sources:
            key = (source.get("doc_id"), source.get("page"), source.get("quote"))
            if key in seen:
                continue
            seen.add(key)
            quote = source.get("quote") or ""
            lines.append(f"- `{_source_ref(source)}`: {quote}")
    return "\n".join(lines).rstrip() + "\n"


def markdown_compare(connection, fund_ids):
    comparison = compare_funds(connection, fund_ids)
    names = {fid: short_name(comparison["sheets"][fid]["fund_name"]) for fid in fund_ids}
    lines = [
        "# Fund Comparison",
        "",
        "| Fund | Name |",
        "| --- | --- |",
    ]
    for fid in fund_ids:
        lines.append(f"| `{names[fid]}` | {_md_escape(comparison['sheets'][fid]['fund_name'])} |")
    lines.append("")
    lines.append(comparison["calendar_year"] and "## Calendar-Year Returns" or "## Returns")
    lines.append("")
    cy = comparison["calendar_year"]
    if cy["years"]:
        lines.append("| Year | " + " | ".join(_md_escape(names[fid]) for fid in fund_ids) + " |")
        lines.append("| --- | " + " | ".join("---:" for _ in fund_ids) + " |")
        for row in cy["rows"]:
            lines.append("| " + row["year"] + " | " + " | ".join(
                _fmt_pct(row[fid]) for fid in fund_ids
            ) + " |")
        lines.append("")
    lines.extend(["## Terms and Metrics", ""])
    rows = [row for row in comparison["rows"] if row["section"] in ("terms", "metrics")]
    if rows:
        lines.append("| Field | " + " | ".join(_md_escape(names[fid]) for fid in fund_ids) + " |")
        lines.append("| --- | " + " | ".join("---" for _ in fund_ids) + " |")
        for row in rows:
            lines.append("| " + _md_escape(row["label"]) + " | " + " | ".join(
                _md_escape(row["values"][fid] if row["values"][fid] is not None else "-")
                for fid in fund_ids
            ) + " |")
    common = comparison["common_period_statistics"]
    if common["count"] >= 2:
        lines.extend(["", "## Common Monthly Period", ""])
        lines.append(f"{common['period_start']} to {common['period_end']} ({common['count']} observations).")
        lines.append("")
        lines.append("| Metric | " + " | ".join(_md_escape(names[fid]) for fid in fund_ids) + " |")
        lines.append("| --- | " + " | ".join("---:" for _ in fund_ids) + " |")
        for key in ("cumulative", "annualized_volatility", "annualized_sharpe", "sortino"):
            lines.append("| " + key.replace("_", " ").title() + " | " + " | ".join(
                str(common["by_fund"][fid].get(key, "-")) for fid in fund_ids
            ) + " |")
    return "\n".join(lines).rstrip() + "\n"

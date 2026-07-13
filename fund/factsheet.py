"""Stage 4 of the pipeline: the standardized factsheet.

A factsheet is never stored as editable state — it is assembled on demand
from the approved facts and returns tables, shaped by the vocabulary in
schema.py. Snapshotting writes the assembled factsheet to a hashed JSON file
so an analysis can be tied to the exact data it saw.
"""
import hashlib
import json

from fund.analytics import (
    annual_display_series,
    annual_stats_from_rows,
    assumptions as analytics_assumptions,
    best_monthly_series_by_class,
    group_by_class,
    return_statistics,
)
from fund.config import FACTSHEET_DIR
from fund.db import utc_now
from fund.schema import FIELDS, SECTION_ORDER, SECTION_TITLES, section_fields
from fund.verification import verification_label


def build_factsheet(connection, fund_id):
    fund = connection.execute("SELECT * FROM funds WHERE fund_id = ?", (fund_id,)).fetchone()
    if fund is None:
        raise ValueError(f"Unknown fund_id: {fund_id}")

    raw_facts = [dict(row) for row in connection.execute("SELECT * FROM facts WHERE fund_id = ?", (fund_id,))]
    facts = {}
    share_class_facts = {}
    for row in raw_facts:
        share_class = row.get("share_class") or ""
        if share_class:
            share_class_facts.setdefault(share_class, {})[row["field_key"]] = row
        else:
            facts[row["field_key"]] = row
    doc_types = {row["doc_id"]: row["doc_type"] for row in connection.execute(
        "SELECT doc_id, doc_type FROM documents WHERE fund_id = ?", (fund_id,))}
    sections = {}
    filled = 0
    for section in SECTION_ORDER:
        entries = []
        for key in section_fields(section):
            fact = facts.get(key)
            if fact:
                filled += 1
            entries.append(_fact_entry(key, fact, doc_types))
        sections[section] = entries

    share_classes = {}
    for share_class in sorted(share_class_facts):
        class_sections = {}
        class_filled = 0
        for section in SECTION_ORDER:
            entries = []
            for key in section_fields(section):
                fact = share_class_facts[share_class].get(key)
                if fact:
                    class_filled += 1
                    entries.append(_fact_entry(key, fact, doc_types))
            if entries:
                class_sections[section] = entries
        share_classes[share_class] = {
            "sections": class_sections,
            "fields_filled": class_filled,
        }

    returns = [dict(row) for row in connection.execute(
        """SELECT share_class, period_type, period_end, period_start, return_pct, return_type,
                  doc_id, page
           FROM returns WHERE fund_id = ?
           ORDER BY share_class, period_type, period_end""",
        (fund_id,),
    )]

    return {
        "fund_id": fund_id,
        "fund_name": fund["fund_name"],
        "manager_name": fund["manager_name"],
        "sections": sections,
        "share_classes": share_classes,
        "returns": returns,
        "return_statistics": return_statistics(returns),
        "computed_return_analytics": best_monthly_series_by_class(returns),
        "analytics_assumptions": analytics_assumptions(),
        "coverage": {
            "fund_level_fields_filled": filled,
            "share_class_fact_rows": sum(item["fields_filled"] for item in share_classes.values()),
            "fields_total": len(FIELDS),
            "return_rows": len(returns),
        },
    }


def _fact_entry(key, fact, doc_types):
    return {
        "field_key": key,
        "label": FIELDS[key][1],
        "value": fact["value"] if fact else None,
        "value_num": fact["value_num"] if fact else None,
        "unit": fact["unit"] if fact else None,
        "as_of_date": fact["as_of_date"] if fact else None,
        "share_class": fact["share_class"] if fact else "",
        "source": {
            "doc_id": fact["doc_id"],
            "page": fact["page"],
            "quote": fact["quote"],
            "doc_type": doc_types.get(fact["doc_id"]),
        } if fact else None,
    }


def snapshot_factsheet(connection, fund_id):
    """Freeze the assembled factsheet to one current hashed JSON file.

    Older files for the same fund are removed after a new current snapshot is
    saved. The retained snapshot includes a compact change summary versus the
    previous file, so the folder stays lean without losing the "what changed"
    signal during refreshes.
    """
    sheet = build_factsheet(connection, fund_id)
    previous = _latest_snapshot(fund_id)
    previous_sheet = _read_snapshot(previous) if previous else None
    canonical = json.dumps(sheet, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    FACTSHEET_DIR.mkdir(parents=True, exist_ok=True)
    path = FACTSHEET_DIR / f"{fund_id}_{digest}.json"
    if not path.exists():
        sheet["changes"] = _snapshot_changes(previous_sheet, sheet)
        sheet["snapshot"] = {"hash": digest, "created_at": utc_now()}
        path.write_text(json.dumps(sheet, indent=2, ensure_ascii=False))
    _remove_stale_snapshots(fund_id, keep_path=path)
    return path, digest


def _latest_snapshot(fund_id):
    if not FACTSHEET_DIR.exists():
        return None
    paths = sorted(
        FACTSHEET_DIR.glob(f"{fund_id}_*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return paths[0] if paths else None


def _read_snapshot(path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _remove_stale_snapshots(fund_id, keep_path):
    if not FACTSHEET_DIR.exists():
        return
    for path in FACTSHEET_DIR.glob(f"{fund_id}_*.json"):
        if path != keep_path:
            path.unlink()


def _snapshot_changes(previous, current):
    if not previous:
        return {
            "previous_hash": None,
            "summary": "Initial saved snapshot for this fund.",
            "field_changes": [],
            "return_changes": {},
            "coverage_before": None,
            "coverage_after": current.get("coverage"),
        }
    before_fields = _snapshot_field_values(previous)
    after_fields = _snapshot_field_values(current)
    field_changes = []
    for key in sorted(set(before_fields) | set(after_fields)):
        if before_fields.get(key) == after_fields.get(key):
            continue
        field_changes.append({
            "field": key,
            "before": before_fields.get(key),
            "after": after_fields.get(key),
        })
    before_returns = _snapshot_return_keys(previous)
    after_returns = _snapshot_return_keys(current)
    return_changes = {
        "before_count": len(before_returns),
        "after_count": len(after_returns),
        "added_rows": len(after_returns - before_returns),
        "removed_rows": len(before_returns - after_returns),
    }
    coverage_before = previous.get("coverage") or {}
    coverage_after = current.get("coverage") or {}
    changed_bits = []
    if field_changes:
        changed_bits.append(f"{len(field_changes)} fields changed")
    if return_changes["added_rows"] or return_changes["removed_rows"]:
        changed_bits.append(
            f"returns {return_changes['before_count']} -> {return_changes['after_count']}"
        )
    if coverage_before != coverage_after:
        changed_bits.append("coverage changed")
    return {
        "previous_hash": (previous.get("snapshot") or {}).get("hash"),
        "summary": "; ".join(changed_bits) if changed_bits else "No approved-data changes detected.",
        "field_changes": field_changes[:40],
        "field_changes_truncated": len(field_changes) > 40,
        "return_changes": return_changes,
        "coverage_before": coverage_before,
        "coverage_after": coverage_after,
    }


def _snapshot_field_values(sheet):
    values = {}
    for section_entries in (sheet.get("sections") or {}).values():
        for entry in section_entries:
            if entry.get("value") is not None:
                values[entry["field_key"]] = entry.get("value")
    for share_class, payload in (sheet.get("share_classes") or {}).items():
        for section_entries in (payload.get("sections") or {}).values():
            for entry in section_entries:
                if entry.get("value") is not None:
                    values[f"{entry['field_key']}[{share_class}]"] = entry.get("value")
    return values


def _snapshot_return_keys(sheet):
    keys = set()
    for row in sheet.get("returns") or []:
        keys.add((
            row.get("share_class") or "",
            row.get("period_type") or "",
            row.get("period_end") or "",
            row.get("return_pct"),
            row.get("return_type") or "",
        ))
    return keys


def format_factsheet(sheet, show_sources=False):
    """Plain-text rendering for the terminal."""
    lines = [
        f"{sheet['fund_name']}  ({sheet['fund_id']})",
        f"Manager: {sheet['manager_name']}",
        f"Coverage: {sheet['coverage']['fund_level_fields_filled']}/{sheet['coverage']['fields_total']} fund-level fields, "
        f"{sheet['coverage']['share_class_fact_rows']} share-class fact rows, "
        f"{sheet['coverage']['return_rows']} approved return rows",
        "",
    ]
    for section in SECTION_ORDER:
        entries = [e for e in sheet["sections"][section] if e["value"] is not None]
        if not entries:
            continue
        lines.append(SECTION_TITLES[section])
        lines.append("-" * len(SECTION_TITLES[section]))
        for entry in entries:
            value = entry["value"]
            if entry["unit"] == "percent" and "%" not in value:
                value = f"{value}%"
            if entry["as_of_date"]:
                value = f"{value}  (as of {entry['as_of_date']})"
            # Stats carry their provenance inline — the reviewer sees where each came from.
            if section == "metrics" and entry["source"]:
                value = f"{value}  (from {_source_kind(entry['source'])})"
            lines.append(f"  {entry['label']:<24} {value}")
            if show_sources and entry["source"]:
                source = entry["source"]
                lines.append(f"  {'':<24} [{source['doc_id']} p.{source['page']} · {_source_kind(source)}]")
        lines.append("")

    lines.extend(_factsheet_share_class_blocks(sheet, show_sources=show_sources))

    lines.extend(_factsheet_returns_block(sheet))
    return "\n".join(lines)


def _source_kind(source):
    """Human label for a fact's source document type."""
    return {"factsheet": "fact sheet", "presentation": "presentation"}.get(
        source.get("doc_type"), source.get("doc_type") or "source")


def _by_class(rows):
    """[(share_class, [rows])] in class order."""
    return group_by_class(rows)


def _factsheet_share_class_blocks(sheet, show_sources=False):
    share_classes = sheet.get("share_classes") or {}
    if not share_classes:
        return []
    lines = ["Share Classes", "-------------"]
    for share_class, payload in share_classes.items():
        lines.append(f"  {share_class}")
        for section in SECTION_ORDER:
            entries = payload["sections"].get(section, [])
            if not entries:
                continue
            lines.append(f"    {SECTION_TITLES[section]}")
            for entry in entries:
                value = entry["value"]
                if entry["unit"] == "percent" and "%" not in value:
                    value = f"{value}%"
                if entry["as_of_date"]:
                    value = f"{value}  (as of {entry['as_of_date']})"
                if section == "metrics" and entry["source"]:
                    value = f"{value}  (from {_source_kind(entry['source'])})"
                lines.append(f"      {entry['label']:<22} {value}")
                if show_sources and entry["source"]:
                    source = entry["source"]
                    lines.append(f"      {'':<22} [{source['doc_id']} p.{source['page']} · {_source_kind(source)}]")
        lines.append("")
    return lines


def _factsheet_returns_block(sheet):
    """Returns on the factsheet: yearly first (easy to read), YTD kept separate,
    monthly/quarterly detail left to `fund returns`."""
    returns = sheet["returns"]
    ytd = [r for r in returns if r["period_type"] == "ytd"]
    monthly = [r for r in returns if r["period_type"] == "monthly"]
    quarterly = [r for r in returns if r["period_type"] == "quarterly"]
    annual_display = annual_display_series(returns)

    def header(title):
        return [title, "-" * len(title)]

    lines = header("Returns — annual")
    if annual_display:
        for block in annual_display:
            share_class = block["share_class"]
            rows = block["rows"]
            source = block["source"]
            source_note = "" if source == "reported annual" else f" [{source}]"
            lines.append(f"  {share_class or '(unspecified class)'}:{source_note}")
            for row in rows:
                lines.append(f"    {row['period_end'][:4]}   {row['return_pct']:>7.2f}%  ({row['return_type']})")
            stats = annual_stats_from_rows(rows)
            if stats.get("count", 0) >= 2:
                lines.append(
                    f"    {stats['count']} years | avg {stats['average']}%  "
                    f"best {stats['best']}%  worst {stats['worst']}%  vol {stats['volatility']}%")
    else:
        lines.append("  No approved annual returns yet.")
    lines.append("")

    computed = sheet.get("computed_return_analytics") or []
    if computed:
        assumptions = sheet.get("analytics_assumptions") or {}
        lines.extend(header(
            "Computed return analytics (internal; monthly series; annualized)"
        ))
        lines.append(
            f"  assumptions: rf={assumptions.get('risk_free_rate', 0.0)}% annual, "
            f"periods/year={assumptions.get('periods_per_year', 12)}"
        )
        for stats in computed:
            if stats.get("count", 0) < 2:
                continue
            share_class = stats.get("share_class") or "(unspecified class)"
            dd = stats.get("max_drawdown") or {}
            warning = " [mixed return types; dominant series used]" if stats.get("mixed_return_types") else ""
            lines.append(
                f"  {share_class}: {stats['count']} months, "
                f"{stats.get('period_start')} to {stats.get('period_end')} "
                f"({stats.get('return_type_used') or 'unknown'}{warning})"
            )
            lines.append(
                f"    cumulative {stats.get('cumulative')}% | "
                f"annual vol {stats.get('annualized_volatility')}% | "
                f"annualized Sharpe {stats.get('annualized_sharpe')} | "
                f"Sortino {stats.get('sortino')} | "
                f"positive months {stats.get('positive_periods_pct')}%"
            )
            if dd:
                lines.append(
                    f"    max drawdown {dd.get('max_drawdown')}% "
                    f"({dd.get('peak_date') or 'n/a'} to {dd.get('trough_date') or 'n/a'}; "
                    f"recovered {dd.get('recovery_date') or 'not yet/unknown'})"
                )
        lines.append("")

    if ytd:
        lines.extend(header("Returns — year to date"))
        for share_class, rows in _by_class(ytd):
            latest = max(rows, key=lambda r: r["period_end"])
            lines.append(f"  {share_class or '(unspecified class)'}:  "
                         f"{latest['period_end'][:4]} YTD {latest['return_pct']:>+.2f}%  "
                         f"(as of {latest['period_end']})")
        lines.append("")

    detail = len(monthly) + len(quarterly)
    if detail:
        parts = [f"{len(monthly)} monthly" for _ in [0] if monthly] + \
                [f"{len(quarterly)} quarterly" for _ in [0] if quarterly]
        lines.append(f"  {' + '.join(parts)} rows stored — see: fund returns {sheet['fund_id']}")
    elif not returns:
        lines.append("  No approved return rows yet.")
    lines.append("")
    return lines


_PERIOD_ORDER = {"annual": 0, "quarterly": 1, "monthly": 2, "ytd": 3}


def format_returns_table(returns, indent=""):
    """Full return history grouped by share class, then by period type, for the
    terminal. Annual, quarterly, monthly and YTD are kept as separate blocks."""
    if not returns:
        return f"{indent}No approved return rows."
    lines = []
    for share_class, rows in _by_class(returns):
        lines.append(f"{indent}{share_class or '(unspecified class)'}:")
        ptypes = sorted({r["period_type"] for r in rows}, key=lambda p: _PERIOD_ORDER.get(p, 9))
        for ptype in ptypes:
            lines.append(f"{indent}  {ptype}:")
            for row in sorted((r for r in rows if r["period_type"] == ptype),
                              key=lambda r: r["period_end"]):
                lines.append(
                    f"{indent}    {row['period_end']}  {row['return_pct']:>7.2f}%  ({row['return_type']})")
    return "\n".join(lines)


def build_proposed_factsheet(connection, fund_id):
    """Assemble the PENDING proposals for a fund into the same section shape as an
    approved factsheet, so a reviewer can inspect the whole document at once.

    When two documents propose different values for one field, the value from the
    most recent source document is marked as the winner; the older ones are marked
    superseded, so approving the whole factsheet keeps the current truth."""
    from fund.review import doc_meta, resolve_field_conflicts

    fund = connection.execute("SELECT * FROM funds WHERE fund_id = ?", (fund_id,)).fetchone()
    if fund is None:
        raise ValueError(f"Unknown fund_id: {fund_id}")
    proposals = [dict(row) for row in connection.execute(
        "SELECT * FROM proposals WHERE fund_id = ? AND status = 'pending' ORDER BY field_key",
        (fund_id,),
    )]
    meta = doc_meta(connection, fund_id)
    _, superseded = resolve_field_conflicts(proposals, meta)
    by_section = {section: [] for section in SECTION_ORDER}
    share_classes = {}
    for prop in proposals:
        prop["doc_date"] = (meta.get(prop["doc_id"]) or {}).get("date")
        winner = superseded.get(prop["proposal_id"])
        prop["superseded_by"] = (
            {"doc_id": winner["doc_id"], "doc_date": (meta.get(winner["doc_id"]) or {}).get("date"),
             "value": winner["value"]} if winner else None)
        section = FIELDS[prop["field_key"]][0]
        share_class = prop.get("share_class") or ""
        if share_class:
            share_classes.setdefault(share_class, {name: [] for name in SECTION_ORDER})
            share_classes[share_class][section].append(prop)
        else:
            by_section[section].append(prop)
    returns = [dict(row) for row in connection.execute(
        """SELECT * FROM proposed_returns WHERE fund_id = ? AND status = 'pending'
           ORDER BY share_class, period_type, period_end""",
        (fund_id,),
    )]
    return {"fund_id": fund_id, "fund_name": fund["fund_name"],
            "sections": by_section, "share_classes": share_classes, "returns": returns,
            "count": len(proposals), "return_count": len(returns)}


def format_proposed_factsheet(proposed):
    """Render the pending proposals as a reviewable factsheet.

    This is intentionally human-first: show the proposed values clearly, keep
    internal proposal ids out of the way, and surface source / supersession
    notes only as supporting context.
    """
    lines = [
        f"PROPOSED factsheet for {proposed['fund_name']} ({proposed['fund_id']})",
        f"{proposed['count']} pending fields, {proposed['return_count']} pending return rows",
        "",
    ]
    for section in SECTION_ORDER:
        props = proposed["sections"][section]
        if not props:
            continue
        lines.append(SECTION_TITLES[section])
        lines.append("-" * len(SECTION_TITLES[section]))
        for prop in props:
            value = prop["value"]
            label = FIELDS[prop["field_key"]][1]
            superseded = prop.get("superseded_by")
            marker = "  [superseded]" if superseded else ""
            lines.append(f"  {label:<24} {value}{marker}")
            date = f" ({prop['doc_date']})" if prop.get("doc_date") else ""
            lines.append(f"  {'':<24} source: {prop['doc_id']} p.{prop['page']}{date}")
            lines.append(f"  {'':<24} quote check: {verification_label(prop)}")
            if superseded:
                sup_date = f" ({superseded['doc_date']})" if superseded["doc_date"] else ""
                lines.append(
                    f"  {'':<24} newer source: {superseded['doc_id']}{sup_date} -> {superseded['value']}"
                )
        lines.append("")
    if proposed.get("share_classes"):
        lines.append("Share Classes")
        lines.append("-------------")
        for share_class in sorted(proposed["share_classes"]):
            lines.append(f"  {share_class}")
            class_sections = proposed["share_classes"][share_class]
            for section in SECTION_ORDER:
                props = class_sections.get(section, [])
                if not props:
                    continue
                lines.append(f"    {SECTION_TITLES[section]}")
                for prop in props:
                    value = prop["value"]
                    label = FIELDS[prop["field_key"]][1]
                    superseded = prop.get("superseded_by")
                    marker = "  [superseded]" if superseded else ""
                    lines.append(f"      {label:<22} {value}{marker}")
                    date = f" ({prop['doc_date']})" if prop.get("doc_date") else ""
                    lines.append(f"      {'':<22} source: {prop['doc_id']} p.{prop['page']}{date}")
                    lines.append(f"      {'':<22} quote check: {verification_label(prop)}")
                    if superseded:
                        sup_date = f" ({superseded['doc_date']})" if superseded["doc_date"] else ""
                        lines.append(
                            f"      {'':<22} newer source: {superseded['doc_id']}{sup_date} -> {superseded['value']}"
                        )
                lines.append("")
    if proposed["returns"]:
        lines.append(SECTION_TITLES["returns"])
        lines.append("-" * len(SECTION_TITLES["returns"]))
        for share_class, rows in _by_class(proposed["returns"]):
            ptypes = sorted({r["period_type"] for r in rows}, key=lambda p: _PERIOD_ORDER.get(p, 9))
            parts = []
            for ptype in ptypes:
                prows = sorted((r for r in rows if r["period_type"] == ptype),
                               key=lambda r: r["period_end"])
                span = (f"{prows[0]['period_end']}…{prows[-1]['period_end']}"
                        if len(prows) > 1 else prows[0]["period_end"])
                parts.append(f"{len(prows)} {ptype} ({span})")
            status_counts = {}
            for row in rows:
                status = row.get("quote_verify_status") or "not checked"
                status_counts[status] = status_counts.get(status, 0) + 1
            status_text = ", ".join(f"{key}: {value}" for key, value in sorted(status_counts.items()))
            lines.append(f"  {share_class or '(unspecified class)'}: {', '.join(parts)}")
            lines.append(f"  {'':<24} quote checks: {status_text}")
        lines.append("")
    return "\n".join(lines)

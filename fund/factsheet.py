"""Stage 4 of the pipeline: the standardized factsheet.

A factsheet is never stored as editable state — it is assembled on demand
from the approved facts and returns tables, shaped by the vocabulary in
schema.py. Snapshotting writes the assembled factsheet to a hashed JSON file
so an analysis can be tied to the exact data it saw.
"""
import hashlib
import json
import statistics

from fund.config import FACTSHEET_DIR
from fund.db import utc_now
from fund.schema import FIELDS, SECTION_ORDER, SECTION_TITLES, section_fields


def build_factsheet(connection, fund_id):
    fund = connection.execute("SELECT * FROM funds WHERE fund_id = ?", (fund_id,)).fetchone()
    if fund is None:
        raise ValueError(f"Unknown fund_id: {fund_id}")

    facts = {
        row["field_key"]: dict(row)
        for row in connection.execute("SELECT * FROM facts WHERE fund_id = ?", (fund_id,))
    }
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
            entries.append({
                "field_key": key,
                "label": FIELDS[key][1],
                "value": fact["value"] if fact else None,
                "value_num": fact["value_num"] if fact else None,
                "unit": fact["unit"] if fact else None,
                "as_of_date": fact["as_of_date"] if fact else None,
                "source": {"doc_id": fact["doc_id"], "page": fact["page"], "quote": fact["quote"],
                           "doc_type": doc_types.get(fact["doc_id"])}
                          if fact else None,
            })
        sections[section] = entries

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
        "returns": returns,
        "return_statistics": return_statistics(returns),
        "coverage": {"fields_filled": filled, "fields_total": len(FIELDS),
                     "return_rows": len(returns)},
    }


def return_statistics(returns, period_type="monthly", share_class=None):
    """Deterministic stats over approved return rows. One decimal place."""
    values = [
        row["return_pct"] for row in returns
        if row["period_type"] == period_type
        and (share_class is None or row["share_class"] == share_class)
    ]
    if len(values) < 2:
        return {"period_type": period_type, "count": len(values)}
    average = statistics.mean(values)
    volatility = statistics.stdev(values)
    return {
        "period_type": period_type,
        "count": len(values),
        "average": round(average, 1),
        "volatility": round(volatility, 1),
        "sharpe_like": round(average / volatility, 1) if volatility else None,
        "best": round(max(values), 1),
        "worst": round(min(values), 1),
    }


def snapshot_factsheet(connection, fund_id):
    """Freeze the assembled factsheet to a hashed JSON file. Returns (path, hash)."""
    sheet = build_factsheet(connection, fund_id)
    canonical = json.dumps(sheet, sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:12]
    FACTSHEET_DIR.mkdir(parents=True, exist_ok=True)
    path = FACTSHEET_DIR / f"{fund_id}_{digest}.json"
    if not path.exists():
        sheet["snapshot"] = {"hash": digest, "created_at": utc_now()}
        path.write_text(json.dumps(sheet, indent=2, ensure_ascii=False))
    return path, digest


def format_factsheet(sheet, show_sources=False):
    """Plain-text rendering for the terminal."""
    lines = [
        f"{sheet['fund_name']}  ({sheet['fund_id']})",
        f"Manager: {sheet['manager_name']}",
        f"Coverage: {sheet['coverage']['fields_filled']}/{sheet['coverage']['fields_total']} fields, "
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

    lines.extend(_factsheet_returns_block(sheet))
    return "\n".join(lines)


def _source_kind(source):
    """Human label for a fact's source document type."""
    return {"factsheet": "fact sheet", "presentation": "presentation"}.get(
        source.get("doc_type"), source.get("doc_type") or "source")


def _by_class(rows):
    """[(share_class, [rows])] in class order."""
    return [(c, [r for r in rows if r["share_class"] == c])
            for c in sorted({r["share_class"] for r in rows})]


def _factsheet_returns_block(sheet):
    """Returns on the factsheet: yearly first (easy to read), YTD kept separate,
    monthly/quarterly detail left to `fund returns`."""
    returns = sheet["returns"]
    annual = [r for r in returns if r["period_type"] == "annual"]
    ytd = [r for r in returns if r["period_type"] == "ytd"]
    monthly = [r for r in returns if r["period_type"] == "monthly"]
    quarterly = [r for r in returns if r["period_type"] == "quarterly"]

    def header(title):
        return [title, "-" * len(title)]

    lines = header("Returns — annual")
    if annual:
        for share_class, rows in _by_class(annual):
            lines.append(f"  {share_class or '(unspecified class)'}:")
            for row in sorted(rows, key=lambda r: r["period_end"]):
                lines.append(f"    {row['period_end'][:4]}   {row['return_pct']:>7.2f}%  ({row['return_type']})")
            stats = return_statistics(returns, period_type="annual", share_class=share_class)
            if stats.get("count", 0) >= 2:
                lines.append(
                    f"    {stats['count']} years | avg {stats['average']}%  "
                    f"best {stats['best']}%  worst {stats['worst']}%  vol {stats['volatility']}%")
    else:
        lines.append("  No approved annual returns yet.")
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
    for prop in proposals:
        prop["doc_date"] = (meta.get(prop["doc_id"]) or {}).get("date")
        winner = superseded.get(prop["proposal_id"])
        prop["superseded_by"] = (
            {"doc_id": winner["doc_id"], "doc_date": (meta.get(winner["doc_id"]) or {}).get("date"),
             "value": winner["value"]} if winner else None)
        section = FIELDS[prop["field_key"]][0]
        by_section[section].append(prop)
    returns = [dict(row) for row in connection.execute(
        """SELECT * FROM proposed_returns WHERE fund_id = ? AND status = 'pending'
           ORDER BY share_class, period_type, period_end""",
        (fund_id,),
    )]
    return {"fund_id": fund_id, "fund_name": fund["fund_name"],
            "sections": by_section, "returns": returns,
            "count": len(proposals), "return_count": len(returns)}


def format_proposed_factsheet(proposed):
    """Render the pending proposals as a reviewable factsheet with short ids."""
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
            if len(value) > 90:
                value = value[:90] + "…"
            superseded = prop.get("superseded_by")
            marker = "  (superseded)" if superseded else ""
            lines.append(f"  [{prop['proposal_id']}] {FIELDS[prop['field_key']][1]}: {value}{marker}")
            date = f" ({prop['doc_date']})" if prop.get("doc_date") else ""
            lines.append(f"      from {prop['doc_id']} p.{prop['page']}{date}")
            if superseded:
                sup_date = f" ({superseded['doc_date']})" if superseded["doc_date"] else ""
                lines.append(f"      -> newer {superseded['doc_id']}{sup_date} says: {superseded['value'][:70]}")
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
            lines.append(f"  {share_class or '(unspecified class)'}: {', '.join(parts)}")
        lines.append("")
    return "\n".join(lines)

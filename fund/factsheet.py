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
                "source": {"doc_id": fact["doc_id"], "page": fact["page"], "quote": fact["quote"]}
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
            lines.append(f"  {entry['label']:<24} {value}")
            if show_sources and entry["source"]:
                source = entry["source"]
                lines.append(f"  {'':<24} [{source['doc_id']} p.{source['page']}]")
        lines.append("")

    lines.append(SECTION_TITLES["returns"])
    lines.append("-" * len(SECTION_TITLES["returns"]))
    stats = sheet["return_statistics"]
    if stats.get("count", 0) >= 2:
        lines.append(
            f"  {stats['count']} {stats['period_type']} observations | "
            f"avg {stats['average']}%  vol {stats['volatility']}%  "
            f"sharpe-like {stats['sharpe_like']}  best {stats['best']}%  worst {stats['worst']}%"
        )
        lines.append("")
    if sheet["returns"]:
        lines.append(format_returns_table(sheet["returns"], indent="  "))
    else:
        lines.append("  No approved return rows yet.")
    lines.append("")
    return "\n".join(lines)


def format_returns_table(returns, indent=""):
    """Chronological return rows grouped by share class, for the terminal."""
    if not returns:
        return f"{indent}No approved return rows."
    lines = []
    classes = sorted({row["share_class"] for row in returns})
    for share_class in classes:
        rows = [r for r in returns if r["share_class"] == share_class]
        label = share_class or "(unspecified class)"
        lines.append(f"{indent}{label}:")
        for row in sorted(rows, key=lambda r: (r["period_type"], r["period_end"])):
            lines.append(
                f"{indent}  {row['period_type']:<10} {row['period_end']}  "
                f"{row['return_pct']:>7.2f}%  ({row['return_type']})"
            )
    return "\n".join(lines)


def build_proposed_factsheet(connection, fund_id):
    """Assemble the PENDING proposals for a fund into the same section shape as an
    approved factsheet, so a reviewer can inspect the whole document at once."""
    fund = connection.execute("SELECT * FROM funds WHERE fund_id = ?", (fund_id,)).fetchone()
    if fund is None:
        raise ValueError(f"Unknown fund_id: {fund_id}")
    proposals = [dict(row) for row in connection.execute(
        "SELECT * FROM proposals WHERE fund_id = ? AND status = 'pending' ORDER BY field_key",
        (fund_id,),
    )]
    by_section = {section: [] for section in SECTION_ORDER}
    for prop in proposals:
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
            lines.append(f"  [{prop['proposal_id']}] {FIELDS[prop['field_key']][1]}: {value}")
            lines.append(f"      from {prop['doc_id']} p.{prop['page']}")
        lines.append("")
    if proposed["returns"]:
        lines.append(SECTION_TITLES["returns"])
        lines.append("-" * len(SECTION_TITLES["returns"]))
        classes = sorted({row["share_class"] for row in proposed["returns"]})
        for share_class in classes:
            rows = [r for r in proposed["returns"] if r["share_class"] == share_class]
            lines.append(f"  {share_class or '(unspecified class)'}: "
                         f"{len(rows)} rows ({rows[0]['period_end']} … {rows[-1]['period_end']})")
        lines.append("")
    return "\n".join(lines)

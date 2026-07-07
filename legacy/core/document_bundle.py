from collections import defaultdict

from core.db import many, one
from core.factsheet_schema import STANDARDIZED_FIELD_SPECS, STANDARDIZED_SECTION_ORDER
from core.fund_views import build_fund_view


RETURN_FACT_NAMES = {"monthly_return", "quarterly_return", "annual_return", "ytd_return"}
SECTION_ORDER = ["overview", "manager", "strategy", "terms", "metrics", "notes_flags", "other"]


SECTION_RULES = {
    "overview": {("profile", None)},
    "manager": {("people", None)},
    "strategy": {("strategy", None), ("exposure", None)},
    "terms": {("terms", None)},
    "metrics": {("performance", "not_returns")},
    "notes_flags": {("writeup", None), ("flag", None)},
}


def _section_for_fact(fact):
    category = fact["fact_category"]
    fact_name = fact["fact_name"]
    if category == "profile":
        return "overview"
    if category == "people":
        return "manager"
    if category in {"strategy", "exposure"}:
        return "strategy"
    if category == "terms":
        return "terms"
    if category == "performance" and fact_name not in RETURN_FACT_NAMES:
        return "metrics"
    if category in {"writeup", "flag"}:
        return "notes_flags"
    return "other"


def _status_counts(rows, status_field):
    counts = {"pending": 0, "approved": 0, "rejected": 0}
    for row in rows:
        status = row.get(status_field)
        if status in counts:
            counts[status] += 1
    return counts


def _status_summary(rows, status_field, *, empty_text):
    counts = _status_counts(rows, status_field)
    parts = [f"{status}={count}" for status, count in counts.items() if count]
    return ", ".join(parts) if parts else empty_text


def _clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _row_display_value(row):
    value = _clean_text(row.get("normalized_value")) or _clean_text(row.get("raw_value"))
    unit = _clean_text(row.get("unit"))
    if unit and unit.lower() in {"none", "null", "unknown"}:
        unit = None
    if value and unit and unit.lower() not in value.lower():
        value = f"{value} {unit}"
    return value


def _dedupe_by_key(rows, key_fn):
    grouped = defaultdict(list)
    ordered_keys = []
    for row in rows:
        key = key_fn(row)
        if key not in grouped:
            ordered_keys.append(key)
        grouped[key].append(row)
    return [_best_review_row(grouped[key]) for key in ordered_keys]


def _dedupe_exact_rows(rows):
    return _dedupe_by_key(
        rows,
        lambda row: (
            row.get("fact_category"),
            row.get("fact_name"),
            (_row_display_value(row) or "").lower(),
        ),
    )


def _dedupe_people_rows(rows):
    role_specific_names = set()
    for row in rows:
        if row.get("fact_name") in {"chief_investment_officer", "portfolio_manager", "founder"}:
            value = _clean_text(row.get("normalized_value")) or _clean_text(row.get("raw_value"))
            if value:
                role_specific_names.add(value.lower())

    filtered = []
    for row in rows:
        value = _clean_text(row.get("normalized_value")) or _clean_text(row.get("raw_value"))
        fact_name = row.get("fact_name")
        if fact_name == "person_name" and value and value.lower() in role_specific_names:
            continue
        if fact_name == "role_title":
            continue
        filtered.append(row)
    return _dedupe_by_key(
        filtered,
        lambda row: (
            row.get("fact_name"),
            (_clean_text(row.get("normalized_value")) or _clean_text(row.get("raw_value")) or "").lower(),
        ),
    )


def _writeup_priority(row):
    value = _clean_text(row.get("normalized_value")) or _clean_text(row.get("raw_value")) or ""
    quote = _clean_text(row.get("quoted_text")) or ""
    length_penalty = abs(len(value) - 240)
    quote_bonus = 0 if quote else 500
    return (quote_bonus + length_penalty, row.get("page_number") or 9999, row.get("fact_id") or "")


def _review_priority(row):
    status_priority = {"pending": 0, "approved": 1, "rejected": 2}
    value = _row_display_value(row) or ""
    quote = _clean_text(row.get("quoted_text")) or ""
    return (
        status_priority.get(row.get("approval_status"), 9),
        0 if quote else 1,
        row.get("page_number") or 9999,
        -len(value),
        row.get("fact_id") or "",
    )


def _dedupe_note_rows(rows):
    grouped = defaultdict(list)
    other_rows = []
    for row in rows:
        fact_name = row.get("fact_name")
        if fact_name in {"neutral_summary", "differentiating_edge", "data_limitations"}:
            grouped[fact_name].append(row)
        else:
            other_rows.append(row)
    deduped = list(other_rows)
    for fact_name in ("neutral_summary", "differentiating_edge", "data_limitations"):
        candidates = grouped.get(fact_name, [])
        if candidates:
            deduped.append(sorted(candidates, key=_writeup_priority)[0])
    return _dedupe_exact_rows(deduped)


def _dedupe_section_rows(section, rows):
    if section == "manager":
        return _dedupe_people_rows(rows)
    if section == "notes_flags":
        return _dedupe_note_rows(rows)
    return _dedupe_exact_rows(rows)


def _best_review_row(rows):
    if not rows:
        return None
    return sorted(rows, key=_review_priority)[0]


def _field_rows(rows, matches):
    return [
        row
        for row in rows
        if (row.get("fact_category"), row.get("fact_name")) in matches
    ]


def _build_field_entry(field_key, label, rows, *, empty_text):
    best_row = _best_review_row(rows)
    return {
        "field_key": field_key,
        "label": label,
        "rows": rows,
        "best_row": best_row,
        "display_value": _row_display_value(best_row) if best_row else None,
        "status_summary": _status_summary(rows, "approval_status", empty_text=empty_text),
        "candidate_count": len(rows),
        "empty_text": empty_text,
        "approved_display_value": None,
        "approved_source": None,
    }


def _build_manager_entries(rows, *, empty_text):
    grouped = defaultdict(list)
    for row in rows:
        value = _row_display_value(row)
        if value:
            grouped[value.lower()].append(row)

    best_rows = [
        _best_review_row(grouped[key])
        for key in sorted(grouped, key=lambda name: _review_priority(_best_review_row(grouped[name])))
    ]
    rendered_values = []
    for row in best_rows:
        value = _row_display_value(row)
        if not value:
            continue
        if row.get("fact_name") in {"chief_investment_officer", "portfolio_manager", "founder"}:
            label = row.get("display_name") or row.get("fact_name", "").replace("_", " ").title()
            rendered_values.append(f"{value} - {label}")
        else:
            rendered_values.append(value)

    display_value = "; ".join(rendered_values) if rendered_values else None
    return [
        {
            "field_key": "people_roles",
            "label": "People / roles",
            "rows": best_rows or rows,
            "best_row": _best_review_row(best_rows or rows),
            "display_value": display_value,
            "status_summary": _status_summary(best_rows or rows, "approval_status", empty_text=empty_text),
            "candidate_count": len(best_rows or rows),
            "empty_text": empty_text,
            "approved_display_value": None,
            "approved_source": None,
        }
    ]


def _build_note_flag_entries(rows, *, empty_text):
    entries = []
    for field_key, label, matches in STANDARDIZED_FIELD_SPECS["notes_flags"]:
        entries.append(_build_field_entry(field_key, label, _field_rows(rows, matches), empty_text=empty_text))
    flag_rows = [row for row in rows if row.get("fact_category") == "flag"]
    entries.append(
        {
            "field_key": "flags",
            "label": "Flags / missing disclosures",
            "rows": flag_rows,
            "best_row": _best_review_row(flag_rows),
            "display_value": "; ".join(_row_display_value(row) for row in flag_rows if _row_display_value(row)) or None,
            "status_summary": _status_summary(flag_rows, "approval_status", empty_text=empty_text),
            "candidate_count": len(flag_rows),
            "empty_text": empty_text,
            "approved_display_value": None,
            "approved_source": None,
        }
    )
    return entries


def _build_standardized_section(section, rows, *, empty_text):
    if section == "manager":
        return _build_manager_entries(rows, empty_text=empty_text)
    if section == "notes_flags":
        return _build_note_flag_entries(rows, empty_text=empty_text)

    entries = []
    for field_key, label, matches in STANDARDIZED_FIELD_SPECS.get(section, []):
        entries.append(
            _build_field_entry(
                field_key,
                label,
                _field_rows(rows, matches),
                empty_text=empty_text,
            )
        )
    return entries


def _merge_approved_baseline(standardized_sections, approved_factsheet):
    if not approved_factsheet:
        return standardized_sections
    approved_by_section = approved_factsheet.get("sections", {})
    approved_index = {
        section: {entry["field_key"]: entry for entry in approved_by_section.get(section, [])}
        for section in STANDARDIZED_SECTION_ORDER
    }
    merged = {}
    for section, entries in standardized_sections.items():
        merged_entries = []
        for entry in entries:
            approved_entry = approved_index.get(section, {}).get(entry["field_key"])
            updated = dict(entry)
            if approved_entry and approved_entry.get("has_approved_value"):
                updated["approved_display_value"] = approved_entry.get("display_value")
                updated["approved_source"] = approved_entry.get("source")
            merged_entries.append(updated)
        merged[section] = merged_entries
    return merged


def build_document_bundle(connection, document_id, pending_only=False):
    document = one(
        connection,
        """
        SELECT sd.*, f.fund_name, f.manager_name
        FROM source_documents sd
        LEFT JOIN funds f
            ON f.fund_id = sd.fund_id
        WHERE sd.document_id = ?
        """,
        (document_id,),
    )
    if document is None:
        raise ValueError(f"Unknown document_id: {document_id}")
    fund_id = document.get("fund_id")

    descriptive_rows = many(
        connection,
        """
        SELECT ef.fact_id, ef.fact_category, ef.fact_name, ef.raw_value, ef.normalized_value,
               ef.unit, ef.as_of_date, ef.page_number, ef.quoted_text, ef.approval_status,
               ef.reviewed_by, ef.reviewed_at, ef.review_note,
               cd.display_name
        FROM extracted_facts ef
        LEFT JOIN characteristic_definitions cd
            ON cd.fact_category = ef.fact_category
           AND cd.fact_name = ef.fact_name
        WHERE ef.source_document_id = ?
          AND NOT (
              ef.fact_category = 'performance'
              AND ef.fact_name IN ('monthly_return', 'quarterly_return', 'annual_return', 'ytd_return')
          )
        ORDER BY ef.page_number, ef.fact_category, ef.fact_name, ef.created_at
        """,
        (document_id,),
    )

    section_rows = defaultdict(list)
    for row in descriptive_rows:
        section_rows[_section_for_fact(row)].append(row)

    return_rows = many(
        connection,
        """
        SELECT proposed_row_id, page_number, share_class, period_type, period_start_date, period_end_date,
               return_type, return_value, return_value_bps, unit, evidence_text,
               table_name, row_label, column_label, status, reviewed_by, reviewed_at, review_note
        FROM proposed_return_rows
        WHERE source_document_id = ?
        ORDER BY period_end_date, share_class, table_name, row_label, column_label
        """,
        (document_id,),
    )

    if pending_only:
        for section in list(section_rows):
            section_rows[section] = [row for row in section_rows[section] if row.get("approval_status") == "pending"]
        return_rows = [row for row in return_rows if row.get("status") == "pending"]

    sections = {
        section: _dedupe_section_rows(section, section_rows.get(section, []))
        for section in SECTION_ORDER
    }
    empty_text = "No pending proposal" if pending_only else "No proposal rows"
    standardized_sections = {
        section: _build_standardized_section(section, sections[section], empty_text=empty_text)
        for section in ("overview", "manager", "strategy", "terms", "metrics", "notes_flags")
    }
    approved_factsheet = None
    if fund_id:
        approved_factsheet = build_fund_view(connection, fund_id).get("approved_factsheet")
        standardized_sections = _merge_approved_baseline(standardized_sections, approved_factsheet)

    return {
        "document": document,
        "pending_only": pending_only,
        "section_order": SECTION_ORDER,
        "sections": sections,
        "standardized_sections": standardized_sections,
        "approved_factsheet": approved_factsheet,
        "section_status_counts": {
            section: _status_counts(rows, "approval_status")
            for section, rows in sections.items()
        },
        "pending_fact_ids_by_section": {
            section: [row["fact_id"] for row in rows if row.get("approval_status") == "pending"]
            for section, rows in sections.items()
        },
        "returns": return_rows,
        "return_status_counts": _status_counts(return_rows, "status"),
        "pending_return_row_ids": [row["proposed_row_id"] for row in return_rows if row.get("status") == "pending"],
    }

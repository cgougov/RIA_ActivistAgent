"""Stage 2 of the pipeline: LLM extraction (documents -> proposals).

This is LLM touchpoint #1 of exactly two in the system. Text scopes propose
descriptive field values; the returns extractor reads a rendered page image
and proposes return-table rows. Everything lands in the proposal layer as
'pending' — nothing here touches source-of-truth tables.

Duplicate prevention happens here, at the entry point:
- within a run: one best proposal per field (highest confidence wins)
- against the DB: skip values already approved or already pending for the fund
"""
import base64
import re
from uuid import uuid4

from fund.config import EXTRACT_MODEL
from fund.db import utc_now
from fund.ingest import render_page_image
from fund.llm import call, parse_json
from fund.schema import FIELDS, SCOPE_GUIDANCE, field_schema_text, fields_for_scope


def parse_number(value):
    if value is None:
        return None
    match = re.search(r"-?\d+(\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def _normalize(value):
    return " ".join(str(value).lower().split())


def _load_document(connection, doc_id):
    doc = connection.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    if doc is None:
        raise ValueError(f"Unknown doc_id: {doc_id}")
    return dict(doc)


def preflight(connection, doc_id, scope):
    """Checks that must pass before spending an LLM call. Returns (document, pages)."""
    document = _load_document(connection, doc_id)
    pages = connection.execute(
        "SELECT page_number, text FROM pages WHERE doc_id = ? ORDER BY page_number", (doc_id,)
    ).fetchall()
    if not pages:
        raise ValueError(f"{doc_id} has no ingested pages. Run: fund ingest")
    if not any((row["text"] or "").strip() for row in pages):
        raise ValueError(f"{doc_id} pages contain no text; OCR would be needed.")
    prior = connection.execute(
        "SELECT COUNT(*) FROM proposals WHERE doc_id = ? AND scope = ? AND status != 'rejected'",
        (doc_id, scope),
    ).fetchone()[0]
    if prior:
        raise ValueError(
            f"{prior} proposals already exist for {doc_id}/{scope}. Use --force to re-extract."
        )
    return document, pages


def build_scope_prompt(document, pages, scope):
    page_text = "\n\n".join(
        f"[PAGE {row['page_number']}]\n{(row['text'] or '')[:6000]}" for row in pages
    )
    return f"""
You extract proposed fund facts from source PDFs for a human approval queue.

Extraction scope: {scope}
Scope guidance: {SCOPE_GUIDANCE[scope]}

Rules:
- Do not invent data. Use only the supplied pages.
- Propose at most ONE value per field — the best-supported one.
- Every fact must include page_number and a short quoted_text supporting it.
- If a value is not present in the pages, omit the field entirely. Do not guess.
- Use only field keys from the list below.
- Return strict JSON: {{"facts": [...]}}

Document: {document['title']} ({document['doc_type']}, date: {document['doc_date'] or 'unknown'})

Fields for this scope:
{field_schema_text(scope)}

Each fact object:
{{
  "field_key": "one of the listed keys",
  "value": "the value as written in the source",
  "unit": "percent|currency|date|months|days|null",
  "as_of_date": "YYYY-MM-DD if stated, otherwise null",
  "page_number": 1,
  "quoted_text": "short source excerpt",
  "confidence": 0.0
}}

Pages:
{page_text}
""".strip()


def _dedup_against_db(connection, fund_id, field_key, value):
    """Return a skip reason if this value is already approved or already pending."""
    fact = connection.execute(
        "SELECT value FROM facts WHERE fund_id = ? AND field_key = ?", (fund_id, field_key)
    ).fetchone()
    if fact and _normalize(fact["value"]) == _normalize(value):
        return "already approved with the same value"
    pending = connection.execute(
        """SELECT value FROM proposals
           WHERE fund_id = ? AND field_key = ? AND status = 'pending'""",
        (fund_id, field_key),
    ).fetchall()
    for row in pending:
        if _normalize(row["value"]) == _normalize(value):
            return "identical proposal already pending"
    return None


def save_proposals(connection, document, scope, facts):
    """Validate, dedup, and stage proposals. Returns (inserted, skipped) lists."""
    valid_keys = set(fields_for_scope(scope))
    best = {}
    skipped = []
    for fact in facts:
        key = str(fact.get("field_key", "")).strip()
        value = str(fact.get("value", "")).strip()
        if key not in valid_keys:
            skipped.append((key or "?", f"unknown field for scope {scope}"))
            continue
        if not value:
            skipped.append((key, "empty value"))
            continue
        current = best.get(key)
        if current is None or (fact.get("confidence") or 0) > (current.get("confidence") or 0):
            if current is not None:
                skipped.append((key, "duplicate in same run (kept higher confidence)"))
            best[key] = fact
        else:
            skipped.append((key, "duplicate in same run (kept higher confidence)"))

    inserted = []
    for key, fact in best.items():
        value = str(fact["value"]).strip()
        reason = _dedup_against_db(connection, document["fund_id"], key, value)
        if reason:
            skipped.append((key, reason))
            continue
        connection.execute(
            """
            INSERT INTO proposals (proposal_id, doc_id, fund_id, scope, field_key, value,
                                   value_num, unit, as_of_date, page, quote, confidence,
                                   created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"prop_{uuid4().hex[:12]}", document["doc_id"], document["fund_id"], scope,
                key, value,
                parse_number(value) if FIELDS[key][2] == "number" else None,
                fact.get("unit"), fact.get("as_of_date"), fact.get("page_number"),
                fact.get("quoted_text"), fact.get("confidence"), utc_now(),
            ),
        )
        inserted.append(key)
    connection.commit()
    return inserted, skipped


def extract_scope(connection, doc_id, scope, force=False, dry_run=False):
    """Run one scoped extraction. dry_run does every check but the API call."""
    if force:
        document = _load_document(connection, doc_id)
        pages = connection.execute(
            "SELECT page_number, text FROM pages WHERE doc_id = ? ORDER BY page_number", (doc_id,)
        ).fetchall()
    else:
        document, pages = preflight(connection, doc_id, scope)
    prompt = build_scope_prompt(document, pages, scope)
    if dry_run:
        return {"doc_id": doc_id, "scope": scope, "prompt_chars": len(prompt), "dry_run": True}
    call_id, output = call(
        connection, call_type=f"extract:{scope}", model=EXTRACT_MODEL,
        prompt_input=prompt, doc_id=doc_id, fund_id=document["fund_id"],
    )
    facts = parse_json(call_id, output).get("facts", [])
    inserted, skipped = save_proposals(connection, document, scope, facts)
    return {"doc_id": doc_id, "scope": scope, "call_id": call_id,
            "proposed": len(facts), "inserted": inserted, "skipped": skipped}


RETURNS_PROMPT_RULES = """
Definitions:
- period_type: monthly, quarterly, annual, or ytd.
- period_end_date: final date in the return period (YYYY-MM-DD). Required for every row.
- return_value: numeric return in percent points. Store 1.23 for 1.23%, not 0.0123.
- return_type: net, gross, or unknown. Use net only if the source clearly says net.
- share_class: share class/currency/class label if the table separates classes.

Rules:
- Do not invent rows. Extract only return observations visible in the image.
- Extract each monthly/quarterly/annual/YTD cell as a separate row when the date meaning is clear.
- If a date is shown as a month or quarter, normalize period_end_date to the period's final day.
- If the table is cumulative performance rather than periodic returns, omit it unless the period meaning is clear.
- If no return table is visible, return {"rows": []}.
- Return strict JSON: {"rows": [{"period_type": "...", "period_start_date": null,
  "period_end_date": "YYYY-MM-DD", "return_value": 1.23, "return_type": "net|gross|unknown",
  "share_class": "text or null", "quoted_text": "source cell/row text", "confidence": 0.0}]}
""".strip()


def extract_returns(connection, doc_id, page_number, force=False, dry_run=False):
    """Vision extraction of a return table from one rendered page image."""
    document = _load_document(connection, doc_id)
    prior = connection.execute(
        """SELECT COUNT(*) FROM proposed_returns
           WHERE doc_id = ? AND page = ? AND status != 'rejected'""",
        (doc_id, page_number),
    ).fetchone()[0]
    if prior and not force:
        raise ValueError(
            f"{prior} proposed return rows already exist for {doc_id} page {page_number}. Use --force."
        )
    image_path = render_page_image(connection, doc_id, page_number)
    connection.commit()
    if dry_run:
        return {"doc_id": doc_id, "page": page_number, "image": str(image_path), "dry_run": True}

    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    prompt = (
        f"Extract the performance return table from this PDF page image.\n"
        f"Document: {document['title']} ({document['doc_type']}, date: {document['doc_date'] or 'unknown'}), "
        f"page {page_number}.\n\n{RETURNS_PROMPT_RULES}"
    )
    call_id, output = call(
        connection, call_type="extract:returns", model=EXTRACT_MODEL,
        prompt_input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:image/png;base64,{encoded}"},
            ],
        }],
        doc_id=doc_id, fund_id=document["fund_id"],
    )
    rows = parse_json(call_id, output).get("rows", [])

    inserted, skipped = 0, []
    for index, row in enumerate(rows):
        period_type = str(row.get("period_type", "")).lower().strip()
        period_end = row.get("period_end_date")
        value = parse_number(row.get("return_value"))
        if period_type not in ("monthly", "quarterly", "annual", "ytd"):
            skipped.append((index, f"unrecognized period_type: {row.get('period_type')!r}"))
            continue
        if not period_end:
            skipped.append((index, "missing period_end_date"))
            continue
        if value is None:
            skipped.append((index, "missing return_value"))
            continue
        share_class = (row.get("share_class") or "").strip()
        duplicate = connection.execute(
            """SELECT 1 FROM proposed_returns
               WHERE fund_id = ? AND share_class = ? AND period_type = ? AND period_end = ?
                 AND status = 'pending'
               UNION
               SELECT 1 FROM returns
               WHERE fund_id = ? AND share_class = ? AND period_type = ? AND period_end = ?
                 AND return_pct = ?""",
            (document["fund_id"], share_class, period_type, period_end,
             document["fund_id"], share_class, period_type, period_end, value),
        ).fetchone()
        if duplicate:
            skipped.append((index, f"{period_type} {period_end} already staged or approved"))
            continue
        connection.execute(
            """
            INSERT INTO proposed_returns (row_id, doc_id, fund_id, period_type, period_start,
                                          period_end, return_pct, return_type, share_class,
                                          page, quote, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"ret_{uuid4().hex[:12]}", doc_id, document["fund_id"], period_type,
                row.get("period_start_date"), period_end, value,
                (row.get("return_type") or "unknown").lower(), share_class,
                page_number, row.get("quoted_text"), row.get("confidence"), utc_now(),
            ),
        )
        inserted += 1
    connection.commit()
    return {"doc_id": doc_id, "page": page_number, "call_id": call_id,
            "proposed": len(rows), "inserted": inserted, "skipped": skipped}

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
from fund.schema import (
    FIELDS,
    SCOPE_GUIDANCE,
    field_schema_text,
    fields_for_scope,
    supports_share_class,
)
from fund.verification import verify_quote


def parse_number(value):
    if value is None:
        return None
    match = re.search(r"-?\d+(\.\d+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def _normalize(value):
    return " ".join(str(value).lower().split())


def _normalize_share_class(field_key, share_class):
    if not supports_share_class(field_key):
        return ""
    return " ".join(str(share_class or "").split())


def _proposal_label(field_key, share_class):
    share_class = _normalize_share_class(field_key, share_class)
    return f"{field_key}[{share_class}]" if share_class else field_key


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
- Propose at most ONE value per field or per field/share class pair — the best-supported one.
- Every fact must include page_number and a short quoted_text supporting it.
- If a value is not present in the pages, omit the field entirely. Do not guess.
- Use only field keys from the list below.
- If a value applies to a specific share class, include share_class exactly as labeled in the source.
- Do not collapse multiple share classes into one blended value. Keep class-specific terms or metrics separate.
- Use share_class only when it materially changes the value. Otherwise set it to null.
- Return strict JSON: {{"facts": [...]}}

Document: {document['title']} ({document['doc_type']}, date: {document['doc_date'] or 'unknown'})

Fields for this scope:
{field_schema_text(scope)}

Each fact object:
{{
  "field_key": "one of the listed keys",
  "share_class": "Class A | USD | Founder Class | null",
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


def _dedup_against_db(connection, fund_id, field_key, value, share_class=""):
    """Return a skip reason if this value is already approved or already pending."""
    share_class = _normalize_share_class(field_key, share_class)
    fact = connection.execute(
        "SELECT value FROM facts WHERE fund_id = ? AND field_key = ? AND share_class = ?",
        (fund_id, field_key, share_class),
    ).fetchone()
    if fact and _normalize(fact["value"]) == _normalize(value):
        return "already approved with the same value"
    pending = connection.execute(
        """SELECT value FROM proposals
           WHERE fund_id = ? AND field_key = ? AND share_class = ? AND status = 'pending'""",
        (fund_id, field_key, share_class),
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
        share_class = _normalize_share_class(key, fact.get("share_class"))
        if key not in valid_keys:
            skipped.append((key or "?", f"unknown field for scope {scope}"))
            continue
        if not value:
            skipped.append((_proposal_label(key, share_class), "empty value"))
            continue
        dedup_key = (key, share_class)
        current = best.get(dedup_key)
        if current is None or (fact.get("confidence") or 0) > (current.get("confidence") or 0):
            if current is not None:
                skipped.append((_proposal_label(key, share_class), "duplicate in same run (kept higher confidence)"))
            fact = dict(fact)
            fact["share_class"] = share_class
            best[dedup_key] = fact
        else:
            skipped.append((_proposal_label(key, share_class), "duplicate in same run (kept higher confidence)"))

    inserted = []
    for (key, share_class), fact in best.items():
        value = str(fact["value"]).strip()
        reason = _dedup_against_db(connection, document["fund_id"], key, value, share_class=share_class)
        if reason:
            skipped.append((_proposal_label(key, share_class), reason))
            continue
        verification = verify_quote(
            connection,
            doc_id=document["doc_id"],
            page=fact.get("page_number"),
            quote=fact.get("quoted_text"),
            source_mode="text",
        )
        connection.execute(
            """
            INSERT INTO proposals (proposal_id, doc_id, fund_id, scope, field_key, share_class, value,
                                   value_num, unit, as_of_date, page, quote, confidence,
                                   quote_verified, quote_verify_score, quote_verify_status,
                                   created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"prop_{uuid4().hex[:12]}", document["doc_id"], document["fund_id"], scope,
                key, share_class, value,
                parse_number(value) if FIELDS[key][2] == "number" else None,
                fact.get("unit"), fact.get("as_of_date"), fact.get("page_number"),
                fact.get("quoted_text"), fact.get("confidence"),
                verification["quote_verified"],
                verification["quote_verify_score"],
                verification["quote_verify_status"],
                utc_now(),
            ),
        )
        inserted.append(_proposal_label(key, share_class))
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

Capture EVERYTHING — high recall is the goal:
- Extract the COMPLETE return history in the image, not just the most recent year.
  Performance tables often show many years of monthly rows plus a right-hand annual
  or YTD column. Emit a row for EVERY populated cell across ALL years shown.
- If a year shows only a single annual figure (e.g. 2008: +12.3%), still emit it as an
  annual row with period_end_date = that year's Dec 31.
- Capture per-year YTD/annual totals AND the monthly cells — they are different rows.
- If the same period appears for multiple share classes, emit one row per share class.
- Never stop early or summarize. If the table has 200 cells, return ~200 rows.

Bias toward inclusion under uncertainty (a human approves every row afterwards):
- If a cell is clearly a periodic return but you are unsure whether it is net or gross,
  emit it with return_type "unknown" — do NOT drop it.
- If you are unsure of the exact period-end date but the period is identifiable (a given
  month or year), emit it with your best-guess date and a lower confidence — do NOT drop it.
- Only omit a cell if it is plainly not a periodic return (e.g. an index level, AUM,
  or a cumulative-since-inception line whose per-period value cannot be recovered).
  When in doubt, INCLUDE it with low confidence and note the doubt in quoted_text.
- The fund's own series may appear beside benchmark or index series. Extract ONLY the
  fund or share-class series. Do not emit benchmark, index, or comparator rows such as
  TOPIX, MSCI, Nikkei, S&P, Russell, or other market benchmarks.

Rules:
- Do not invent numbers. Every row must correspond to a value visible in the image.
- If a date is shown as a month or quarter, normalize period_end_date to the period's final day.
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
    inserted, skipped = _save_return_rows(connection, document, page_number, rows, source_mode="vision")
    return {"doc_id": doc_id, "page": page_number, "call_id": call_id,
            "proposed": len(rows), "inserted": inserted, "skipped": skipped}


def _save_return_rows(connection, document, page_number, rows, source_mode="vision"):
    """Validate, dedup and stage proposed return rows. Shared by the vision and
    text return extractors. Returns (inserted_count, skipped_list)."""
    fund_id = document["fund_id"]
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
        if _looks_like_benchmark(share_class, row.get("quoted_text")):
            skipped.append((index, "benchmark/index row"))
            continue
        duplicate = connection.execute(
            """SELECT 1 FROM proposed_returns
               WHERE fund_id = ? AND share_class = ? AND period_type = ? AND period_end = ?
                 AND status = 'pending'
               UNION
               SELECT 1 FROM returns
               WHERE fund_id = ? AND share_class = ? AND period_type = ? AND period_end = ?
                 AND return_pct = ?""",
            (fund_id, share_class, period_type, period_end,
             fund_id, share_class, period_type, period_end, value),
        ).fetchone()
        if duplicate:
            skipped.append((index, f"{period_type} {period_end} already staged or approved"))
            continue
        verification = verify_quote(
            connection,
            doc_id=document["doc_id"],
            page=page_number,
            quote=row.get("quoted_text"),
            source_mode=source_mode,
        )
        connection.execute(
            """
            INSERT INTO proposed_returns (row_id, doc_id, fund_id, period_type, period_start,
                                          period_end, return_pct, return_type, share_class,
                                          page, quote, confidence, quote_verified,
                                          quote_verify_score, quote_verify_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"ret_{uuid4().hex[:12]}", document["doc_id"], fund_id, period_type,
                row.get("period_start_date"), period_end, value,
                (row.get("return_type") or "unknown").lower(), share_class,
                page_number, row.get("quoted_text"), row.get("confidence"),
                verification["quote_verified"],
                verification["quote_verify_score"],
                verification["quote_verify_status"],
                utc_now(),
            ),
        )
        inserted += 1
    connection.commit()
    return inserted, skipped


def _looks_like_benchmark(label, quote=None):
    text = " ".join(part for part in [label or "", quote or ""] if part).lower()
    benchmark_terms = (
        " benchmark",
        " index",
        "topix",
        "msci",
        "nikkei",
        "s&p",
        "russell",
        "ftse",
        "jp small cap",
    )
    return any(term in text for term in benchmark_terms)


RETURNS_TEXT_NOTE = """
The performance table did NOT read reliably as an image, so you are given the raw
page TEXT below instead. Reconstruct the periodic return rows from it.
- The table is usually a year-by-month grid: a year label, then that year's monthly
  returns in order (Jan..Dec), then the year's total/YTD, and sometimes a trailing
  benchmark figure.
- EXCLUDE benchmark/index columns and rows (e.g. TOPIX, MSCI, Nikkei, S&P) — extract
  ONLY the fund's own returns.
- Emit one monthly row per month shown and one annual row per completed year
  (period_end = that year's Dec 31). For the current, partial year, emit its YTD as a
  ytd row with period_end set to the latest month-end stated on the page.
"""


def extract_returns_from_text(connection, doc_id, page_number, force=False, dry_run=False):
    """Text-based return extraction, for tables that are vision-hostile but whose
    numbers are present in the extracted page text. Same proposal/dedup path."""
    document = _load_document(connection, doc_id)
    page = connection.execute(
        "SELECT text FROM pages WHERE doc_id = ? AND page_number = ?", (doc_id, page_number)
    ).fetchone()
    if page is None or not (page["text"] or "").strip():
        raise ValueError(f"{doc_id} page {page_number} has no extractable text.")
    prior = connection.execute(
        """SELECT COUNT(*) FROM proposed_returns
           WHERE doc_id = ? AND page = ? AND status != 'rejected'""",
        (doc_id, page_number),
    ).fetchone()[0]
    if prior and not force:
        raise ValueError(
            f"{prior} proposed return rows already exist for {doc_id} page {page_number}. Use --force.")
    if dry_run:
        return {"doc_id": doc_id, "page": page_number, "mode": "text", "dry_run": True}
    prompt = (
        f"Extract the performance return table from this fund page.\n"
        f"Document: {document['title']} ({document['doc_type']}, "
        f"date: {document['doc_date'] or 'unknown'}), page {page_number}.\n\n"
        f"{RETURNS_PROMPT_RULES}\n\n{RETURNS_TEXT_NOTE}\n\nPAGE TEXT:\n{page['text'][:9000]}"
    )
    call_id, output = call(
        connection, call_type="extract:returns_text", model=EXTRACT_MODEL,
        prompt_input=prompt, doc_id=doc_id, fund_id=document["fund_id"],
    )
    rows = parse_json(call_id, output).get("rows", [])
    inserted, skipped = _save_return_rows(connection, document, page_number, rows, source_mode="text")
    return {"doc_id": doc_id, "page": page_number, "mode": "text", "call_id": call_id,
            "proposed": len(rows), "inserted": inserted, "skipped": skipped}


_MONTHS_RE = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")
_YEAR_RE = re.compile(r"\b20[0-2]\d\b")


def _return_page_score(text):
    """How much a page looks like a performance/return table."""
    t = text or ""
    years = len(set(_YEAR_RE.findall(t)))
    months = len(_MONTHS_RE.findall(t))
    percents = t.count("%")
    keywords = sum(t.lower().count(k) for k in ("return", "performance", "ytd", "net", "monthly"))
    return years * 3 + months + percents + keywords


def find_return_pages(connection, doc_id, limit=3):
    """Rank a document's pages by return-table likelihood, best first. Always
    includes the single best page (a fact sheet's returns are somewhere), and adds
    further pages only when they score strongly — so genuine multi-page return
    tables are caught but commentary/chart pages are not."""
    pages = connection.execute(
        "SELECT page_number, text FROM pages WHERE doc_id = ? ORDER BY page_number", (doc_id,)
    ).fetchall()
    scored = sorted(((p["page_number"], _return_page_score(p["text"])) for p in pages),
                    key=lambda item: item[1], reverse=True)
    if not scored or scored[0][1] == 0:
        return []
    best = scored[0]
    strong = max(100, best[1] * 0.5)
    return [best] + [(page, score) for page, score in scored[1:limit] if score >= strong]


def extract_returns_auto(connection, doc_id, page_number, force=False, dry_run=False):
    """Vision first; if it yields no rows, fall back to text extraction automatically.
    This is the judgment that used to be manual (notice 0 rows -> retry --from-text)."""
    result = extract_returns(connection, doc_id, page_number, force=force, dry_run=dry_run)
    if dry_run or result.get("proposed", 0) > 0:
        return result
    fallback = extract_returns_from_text(connection, doc_id, page_number, force=True)
    fallback["fell_back_from_vision"] = True
    return fallback


# Which extraction scopes run against each document type. Fact sheets are the
# authoritative source for terms/metrics/returns; presentations add the qualitative
# story (strategy, people, what makes the fund unique) but not stale numbers.
SCOPES_BY_DOC_TYPE = {
    "factsheet": ["profile_terms", "strategy_people", "metrics", "notes_flags"],
    "presentation": ["strategy_people", "notes_flags", "presentation"],
}


def _extraction_logged(connection, doc_id, call_type):
    """True if a successful extraction of this type already ran on this document —
    so onboarding stays idempotent even for scopes that yield no proposals."""
    return connection.execute(
        "SELECT 1 FROM llm_calls WHERE doc_id = ? AND call_type = ? AND status = 'ok' LIMIT 1",
        (doc_id, call_type)).fetchone() is not None


def onboard_plan(connection, fund_id):
    """What onboarding will do for a fund, per document — no LLM calls."""
    docs = connection.execute(
        "SELECT doc_id, doc_type, title FROM documents WHERE fund_id = ? ORDER BY doc_id",
        (fund_id,)).fetchall()
    plan = []
    for doc in docs:
        return_pages = ([page for page, _ in find_return_pages(connection, doc["doc_id"])]
                        if doc["doc_type"] == "factsheet" else [])
        plan.append({"doc_id": doc["doc_id"], "doc_type": doc["doc_type"], "title": doc["title"],
                     "scopes": SCOPES_BY_DOC_TYPE.get(doc["doc_type"], []),
                     "return_pages": return_pages})
    return plan


def onboard_fund(connection, fund_id, force=False, dry_run=False):
    """Run a fund's whole extraction plan: doc-type-routed scopes + auto return
    extraction (vision->text fallback) on detected fact-sheet pages. Everything
    lands as proposals for human review. Already-extracted scopes/pages are skipped
    unless force=True."""
    results = []
    for item in onboard_plan(connection, fund_id):
        doc_id = item["doc_id"]
        scope_results, return_results = [], []
        for scope in item["scopes"]:
            if dry_run:
                scope_results.append({"scope": scope, "dry_run": True})
                continue
            if not force and _extraction_logged(connection, doc_id, f"extract:{scope}"):
                scope_results.append({"scope": scope, "skipped": "already extracted (logged)"})
                continue
            try:
                scope_results.append(extract_scope(connection, doc_id, scope, force=force))
            except (ValueError, RuntimeError) as exc:
                scope_results.append({"scope": scope, "skipped": str(exc)})
        for page in item["return_pages"]:
            if dry_run:
                return_results.append({"page": page, "dry_run": True})
                continue
            try:
                return_results.append(extract_returns_auto(connection, doc_id, page, force=force))
            except (ValueError, RuntimeError) as exc:
                return_results.append({"page": page, "skipped": str(exc)})
        results.append({**item, "scope_results": scope_results, "return_results": return_results})
    return results

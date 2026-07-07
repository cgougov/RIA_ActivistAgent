import json
import os
from uuid import uuid4

from core.config import DEFAULT_MODEL, PROMPT_VERSION
from core.db import utc_now
from core.fact_dedup import find_matching_approved_fact, find_matching_extracted_fact
from core.taxonomy import extraction_schema_text, extraction_scope_names, valid_fact_names


def _clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _fact_value(row):
    return _clean_text(row.get("normalized_value")) or _clean_text(row.get("raw_value"))


def _fact_priority(row):
    confidence = row.get("confidence_score")
    try:
        confidence_score = float(confidence) if confidence is not None else -1.0
    except (TypeError, ValueError):
        confidence_score = -1.0
    quote = _clean_text(row.get("quoted_text")) or ""
    value = _fact_value(row) or ""
    return (
        confidence_score,
        1 if quote else 0,
        len(quote),
        len(value),
        -(row.get("page_number") or 9999),
    )


def _best_fact(rows):
    return max(rows, key=_fact_priority)


def sanitize_proposed_facts(facts):
    if not facts:
        return []

    exact_deduped = []
    seen = set()
    for fact in facts:
        value = (_fact_value(fact) or "").lower()
        quote = (_clean_text(fact.get("quoted_text")) or "").lower()
        key = (
            fact.get("fact_category"),
            fact.get("fact_name"),
            value,
            fact.get("page_number"),
            quote,
        )
        if key in seen:
            continue
        seen.add(key)
        exact_deduped.append(fact)

    role_specific_people = {
        (_fact_value(fact) or "").lower()
        for fact in exact_deduped
        if fact.get("fact_category") == "people"
        and fact.get("fact_name") in {"chief_investment_officer", "portfolio_manager", "founder"}
        and _fact_value(fact)
    }

    filtered = []
    for fact in exact_deduped:
        category = fact.get("fact_category")
        fact_name = fact.get("fact_name")
        value = (_fact_value(fact) or "").lower()
        if category == "people" and fact_name == "role_title":
            continue
        if category == "people" and fact_name == "person_name" and value in role_specific_people:
            continue
        filtered.append(fact)

    grouped_writeups = {}
    output = []
    for fact in filtered:
        category = fact.get("fact_category")
        fact_name = fact.get("fact_name")
        if category == "writeup" and fact_name in {"neutral_summary", "differentiating_edge", "data_limitations"}:
            grouped_writeups.setdefault(fact_name, []).append(fact)
            continue
        output.append(fact)

    for fact_name, rows in grouped_writeups.items():
        output.append(_best_fact(rows))

    return output


def preflight_for_extraction(connection, document_id, require_api_key=True):
    document = connection.execute(
        "SELECT * FROM source_documents WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    if document is None:
        raise ValueError(f"Unknown document_id: {document_id}")

    pages = connection.execute(
        """
        SELECT page_number, page_text
        FROM document_pages
        WHERE document_id = ?
        ORDER BY page_number
        """,
        (document_id,),
    ).fetchall()
    if not pages:
        raise ValueError("No document_pages found. Run ingest_pdfs.py first.")
    if not any((row["page_text"] or "").strip() for row in pages):
        raise ValueError("Document pages contain no text. OCR support may be needed.")
    if require_api_key and not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not set.")

    return dict(document), [dict(row) for row in pages]


SCOPE_GUIDANCE = {
    "profile_terms": (
        "Extract only stable, explicitly disclosed identity, structure, service provider, fee, liquidity, "
        "and investor-term facts. Prefer one best fact per canonical field."
    ),
    "strategy_people": (
        "Extract only named people with meaningful roles, concise strategy descriptions, activism style, "
        "geography/market-cap focus, and material exposure descriptors. Avoid separate duplicate people facts "
        "when one role-specific fact captures the same person."
    ),
    "performance": (
        "Extract only point-in-time performance metrics such as AUM, NAV, beta, volatility, Sharpe ratio, "
        "information ratio, benchmark correlation, and max drawdown. Do not extract monthly, quarterly, "
        "annual, or YTD return observations; returns are handled by the separate return-table or CSV workflow."
    ),
    "writeups_flags": (
        "Write at most one neutral_summary and at most one differentiating_edge for this document. Extract flags only "
        "for real diligence issues, source inconsistencies, or missing important disclosures that materially affect review. "
        "Do not create generic data-limitations notes or flags for ordinary omissions."
    ),
}


def build_prompt(document, pages, scope="profile_terms"):
    page_text = "\n\n".join(
        f"[PAGE {row['page_number']}]\n{row['page_text'][:6000]}" for row in pages
    )
    return f"""
You extract proposed fund characteristics from source PDFs for a human approval queue.

Extraction scope: {scope}
Scope guidance: {SCOPE_GUIDANCE.get(scope, 'Extract only facts listed in the taxonomy for this scope.')}

Rules:
- Do not invent data.
- Use only the supplied pages.
- Every extracted fact must include page_number and quoted_text when available.
- Use only fact_category/fact_name pairs from the scoped canonical taxonomy below.
- If a value is not present, omit it. Do not guess.
- Current supported source documents are factsheets and presentations.
- Produce a minimal high-signal review set, not an exhaustive list.
- Avoid duplicate or redundant facts. If the same value appears in multiple places, extract the best-supported version once.
- Do not extract "exists", "available", or placeholder facts when a named canonical fact captures the same information.
- Do not extract monthly, quarterly, annual, or YTD return observations in this workflow.
- For writeup.neutral_summary, write at most one short paragraph that neutrally describes the fund based only on the source.
- For writeup.differentiating_edge, write at most one short paragraph about what appears distinctive, if supported by the source.
- For flags, use neutral language such as "The document does not disclose..." or "The document reports..." and avoid accusations.
- Extract flags only for real diligence issues, missing important disclosures, or source inconsistencies that a reviewer should inspect.
- Do not create flags for every missing taxonomy field or for ordinary factsheet brevity.
- Return strict JSON with one top-level key: facts.

Document:
- document_id: {document['document_id']}
- fund_id: {document['fund_id']}
- title: {document['document_title']}
- document_type: {document['document_type']}
- document_date: {document['document_date']}

Canonical taxonomy:
{extraction_schema_text(scope)}

Each fact object:
{{
  "fact_category": "profile|people|terms|strategy|exposure|performance|writeup|flag",
  "fact_name": "canonical_fact_name",
  "raw_value": "as written",
  "normalized_value": "cleaned if obvious, otherwise null",
  "unit": "percent|currency|date|months|days|null",
  "as_of_date": "YYYY-MM-DD if available, otherwise null",
  "page_number": 1,
  "quoted_text": "short source excerpt",
  "confidence_score": 0.0
}}

Pages:
{page_text}
""".strip()


def validate_llm_fact(row):
    category = str(row.get("fact_category", "")).strip()
    name = str(row.get("fact_name", "")).strip()
    if (category, name) not in valid_fact_names():
        return None
    raw_value = row.get("raw_value")
    if raw_value is None or str(raw_value).strip() == "":
        return None
    return {
        "fact_category": category,
        "fact_name": name,
        "raw_value": str(raw_value).strip(),
        "normalized_value": row.get("normalized_value"),
        "unit": row.get("unit"),
        "as_of_date": row.get("as_of_date"),
        "page_number": row.get("page_number"),
        "quoted_text": row.get("quoted_text"),
        "structured_payload_json": json.dumps(row.get("structured_payload")) if row.get("structured_payload") else None,
        "confidence_score": row.get("confidence_score"),
    }


def save_extraction_run(connection, document, facts, model_name, raw_output, scope):
    facts = sanitize_proposed_facts(facts)
    run_id = f"run_{uuid4().hex}"
    now = utc_now()
    connection.execute(
        """
        INSERT INTO extraction_runs (
            run_id, document_id, extraction_scope, extraction_method, model_name,
            prompt_version, run_status, created_at, finished_at, raw_output
        )
        VALUES (?, ?, ?, 'llm_pdf_text', ?, ?, 'complete', ?, ?, ?)
        """,
        (run_id, document["document_id"], scope, model_name, PROMPT_VERSION, now, now, raw_output),
    )
    for fact in facts:
        duplicate_match = find_matching_extracted_fact(
            connection,
            fund_id=document["fund_id"],
            fact_category=fact["fact_category"],
            fact_name=fact["fact_name"],
            value=fact.get("normalized_value") or fact["raw_value"],
        )
        duplicate_approved = None
        if duplicate_match is None:
            duplicate_approved = find_matching_approved_fact(
                connection,
                fund_id=document["fund_id"],
                fact_category=fact["fact_category"],
                fact_name=fact["fact_name"],
                value=fact.get("normalized_value") or fact["raw_value"],
            )
        approval_status = "pending"
        reviewed_by = None
        reviewed_at = None
        review_note = None
        if duplicate_match:
            approval_status = "rejected"
            reviewed_by = "system"
            reviewed_at = now
            review_note = (
                f"Auto-rejected exact duplicate of {duplicate_match['approval_status']} fact "
                f"{duplicate_match['fact_id']} from {duplicate_match['source_document_id']}."
            )
        elif duplicate_approved:
            approval_status = "rejected"
            reviewed_by = "system"
            reviewed_at = now
            review_note = (
                f"Auto-rejected exact duplicate of approved fact "
                f"{duplicate_approved['approved_fact_id']} from {duplicate_approved['source_document_id']}."
            )
        connection.execute(
            """
            INSERT INTO extracted_facts (
                fact_id, run_id, fund_id, document_id, fact_category, fact_name,
                raw_value, normalized_value, unit, as_of_date, source_document_id,
                page_number, quoted_text, structured_payload_json, extraction_method, confidence_score,
                approval_status, reviewed_by, reviewed_at, review_note, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'llm_pdf_text', ?, ?, ?, ?, ?, ?)
            """,
            (
                f"fact_{uuid4().hex}",
                run_id,
                document["fund_id"],
                document["document_id"],
                fact["fact_category"],
                fact["fact_name"],
                fact["raw_value"],
                fact["normalized_value"],
                fact["unit"],
                fact["as_of_date"],
                document["document_id"],
                fact["page_number"],
                fact["quoted_text"],
                fact["structured_payload_json"],
                fact["confidence_score"],
                approval_status,
                reviewed_by,
                reviewed_at,
                review_note,
                now,
            ),
        )
    connection.execute(
        "UPDATE source_documents SET llm_extraction_status = 'has_proposed_facts' WHERE document_id = ?",
        (document["document_id"],),
    )
    return run_id


def extract_with_openai(connection, document_id, model_name=DEFAULT_MODEL, force=False, scope="profile_terms"):
    if scope not in extraction_scope_names():
        raise ValueError(f"Unknown extraction scope: {scope}")

    document, pages = preflight_for_extraction(connection, document_id, require_api_key=True)

    existing = connection.execute(
        """
        SELECT COUNT(*)
        FROM extracted_facts ef
        JOIN extraction_runs er
            ON er.run_id = ef.run_id
        WHERE ef.source_document_id = ?
          AND er.extraction_scope = ?
          AND ef.approval_status IN ('pending', 'approved', 'revised')
        """,
        (document_id, scope),
    ).fetchone()[0]
    if existing and not force:
        raise ValueError(f"{existing} reviewable facts already exist for scope {scope}. Use --force to run again.")

    from openai import OpenAI

    prompt = build_prompt(document, pages, scope=scope)
    client = OpenAI()
    response = client.responses.create(
        model=model_name,
        input=prompt,
        text={"format": {"type": "json_object"}},
    )
    raw_output = response.output_text
    parsed = json.loads(raw_output)
    facts = [fact for fact in (validate_llm_fact(row) for row in parsed.get("facts", [])) if fact]
    run_id = save_extraction_run(connection, document, facts, model_name, raw_output, scope)
    return run_id, len(facts)

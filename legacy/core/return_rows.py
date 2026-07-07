import json
from uuid import uuid4

from core.approval import approve_fact_record
from core.db import utc_now
from core.promotion import promote_approved_fact_ids
from core.taxonomy import RETURN_FACTS


MONTH_ORDER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
    "q1": 21,
    "q2": 22,
    "q3": 23,
    "q4": 24,
    "ytd": 31,
    "itd": 32,
    "since inception": 33,
}


PERIOD_TO_FACT_NAME = {value: key for key, value in RETURN_FACTS.items() if key != "ytd_performance"}


def parse_return_value(value):
    if value is None:
        return None
    text = str(value).replace("%", "").replace(",", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def percent_to_bps(value):
    parsed = parse_return_value(value)
    if parsed is None:
        return None
    return int(round(parsed * 100))


def upsert_proposed_return_row(
    connection,
    *,
    fund_id,
    source_document_id,
    page_number,
    period_type,
    period_start_date,
    period_end_date,
    return_type,
    return_value,
    raw_value_text,
    unit,
    evidence_text,
    run_id=None,
    share_class=None,
    currency=None,
    benchmark_name=None,
    table_name=None,
    row_label=None,
    column_label=None,
    source_image_id=None,
    source_import_id=None,
    legacy_fact_id=None,
    status="pending",
    reviewed_by=None,
    reviewed_at=None,
    review_note=None,
):
    now = utc_now()
    payload = {
        "proposed_row_id": f"prop_return_{uuid4().hex}",
        "legacy_fact_id": legacy_fact_id,
        "run_id": run_id,
        "fund_id": fund_id,
        "source_document_id": source_document_id,
        "page_number": page_number,
        "share_class": share_class,
        "period_type": period_type,
        "period_start_date": period_start_date,
        "period_end_date": period_end_date,
        "return_type": return_type or "unknown",
        "return_value": parse_return_value(return_value),
        "return_value_bps": percent_to_bps(return_value),
        "raw_value_text": str(raw_value_text),
        "unit": unit or "percent",
        "currency": currency,
        "benchmark_name": benchmark_name,
        "evidence_text": evidence_text,
        "table_name": table_name,
        "row_label": row_label,
        "column_label": column_label,
        "source_image_id": source_image_id,
        "source_import_id": source_import_id,
        "status": status,
        "reviewed_by": reviewed_by,
        "reviewed_at": reviewed_at,
        "review_note": review_note,
        "created_at": now,
        "updated_at": now,
    }
    if payload["return_value"] is None or not period_end_date:
        raise ValueError("Proposed return rows require return_value and period_end_date.")
    if payload["source_image_id"]:
        image_exists = connection.execute(
            "SELECT 1 FROM document_page_images WHERE image_id = ?",
            (payload["source_image_id"],),
        ).fetchone()
        if image_exists is None:
            payload["source_image_id"] = None

    if legacy_fact_id:
        existing = connection.execute(
            "SELECT proposed_row_id FROM proposed_return_rows WHERE legacy_fact_id = ?",
            (legacy_fact_id,),
        ).fetchone()
        if existing is not None:
            connection.execute(
                """
                UPDATE proposed_return_rows
                SET run_id = ?, fund_id = ?, source_document_id = ?, page_number = ?, share_class = ?,
                    period_type = ?, period_start_date = ?, period_end_date = ?, return_type = ?,
                    return_value = ?, return_value_bps = ?, raw_value_text = ?, unit = ?, currency = ?,
                    benchmark_name = ?, evidence_text = ?, table_name = ?, row_label = ?, column_label = ?,
                    source_image_id = ?, source_import_id = ?, status = ?, reviewed_by = ?, reviewed_at = ?,
                    review_note = ?, updated_at = ?
                WHERE legacy_fact_id = ?
                """,
                (
                    payload["run_id"],
                    payload["fund_id"],
                    payload["source_document_id"],
                    payload["page_number"],
                    payload["share_class"],
                    payload["period_type"],
                    payload["period_start_date"],
                    payload["period_end_date"],
                    payload["return_type"],
                    payload["return_value"],
                    payload["return_value_bps"],
                    payload["raw_value_text"],
                    payload["unit"],
                    payload["currency"],
                    payload["benchmark_name"],
                    payload["evidence_text"],
                    payload["table_name"],
                    payload["row_label"],
                    payload["column_label"],
                    payload["source_image_id"],
                    payload["source_import_id"],
                    payload["status"],
                    payload["reviewed_by"],
                    payload["reviewed_at"],
                    payload["review_note"],
                    payload["updated_at"],
                    legacy_fact_id,
                ),
            )
            return existing["proposed_row_id"]

    connection.execute(
        """
        INSERT INTO proposed_return_rows (
            proposed_row_id, legacy_fact_id, run_id, fund_id, source_document_id, page_number,
            share_class, period_type, period_start_date, period_end_date, return_type,
            return_value, return_value_bps, raw_value_text, unit, currency, benchmark_name,
            evidence_text, table_name, row_label, column_label, source_image_id, source_import_id,
            status, reviewed_by, reviewed_at, review_note, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        tuple(payload[key] for key in [
            "proposed_row_id",
            "legacy_fact_id",
            "run_id",
            "fund_id",
            "source_document_id",
            "page_number",
            "share_class",
            "period_type",
            "period_start_date",
            "period_end_date",
            "return_type",
            "return_value",
            "return_value_bps",
            "raw_value_text",
            "unit",
            "currency",
            "benchmark_name",
            "evidence_text",
            "table_name",
            "row_label",
            "column_label",
            "source_image_id",
            "source_import_id",
            "status",
            "reviewed_by",
            "reviewed_at",
            "review_note",
            "created_at",
            "updated_at",
        ]),
    )
    return payload["proposed_row_id"]


def stage_return_rows(
    connection,
    *,
    document,
    page_number,
    rows,
    model_name,
    raw_output,
    image_id,
    extraction_method="llm_pdf_page_image",
):
    run_id = f"run_{uuid4().hex}"
    now = utc_now()
    connection.execute(
        """
        INSERT INTO extraction_runs (
            run_id, document_id, extraction_scope, extraction_method, model_name,
            prompt_version, run_status, created_at, finished_at, raw_output
        )
        VALUES (?, ?, 'visual_return_table', ?, ?, ?, 'complete', ?, ?, ?)
        """,
        (run_id, document["document_id"], extraction_method, model_name, "returns_v2", now, now, raw_output),
    )

    inserted = 0
    skipped = []
    for index, row in enumerate(rows):
        period_type = str(row.get("period_type", "")).lower().strip()
        if period_type not in PERIOD_TO_FACT_NAME:
            skipped.append((index, f"unrecognized period_type: {row.get('period_type')!r}"))
            continue
        period_end_date = row.get("period_end_date")
        return_value = row.get("return_value")
        if not period_end_date:
            skipped.append((index, "missing period_end_date"))
            continue
        if parse_return_value(return_value) is None:
            skipped.append((index, "missing return_value"))
            continue
        structured_context = row.get("structured_context") or {}
        upsert_proposed_return_row(
            connection,
            run_id=run_id,
            fund_id=document["fund_id"],
            source_document_id=document["document_id"],
            page_number=page_number,
            share_class=row.get("share_class"),
            period_type=period_type,
            period_start_date=row.get("period_start_date"),
            period_end_date=period_end_date,
            return_type=row.get("return_type") or "unknown",
            return_value=return_value,
            raw_value_text=row.get("raw_value_text") or str(return_value),
            unit=row.get("unit") or "percent",
            currency=row.get("currency"),
            benchmark_name=row.get("benchmark_name"),
            evidence_text=row.get("quoted_text"),
            table_name=structured_context.get("table_name"),
            row_label=structured_context.get("row_label"),
            column_label=structured_context.get("column_label"),
            source_image_id=image_id,
            source_import_id=row.get("source_import_id"),
        )
        inserted += 1

    if skipped:
        summary = "; ".join(f"row {index}: {reason}" for index, reason in skipped)
        connection.execute(
            "UPDATE extraction_runs SET error_message = ? WHERE run_id = ?",
            (summary, run_id),
        )
    return run_id, inserted, skipped


def backfill_legacy_return_rows(connection):
    rows = connection.execute(
        """
        SELECT ef.fact_id, ef.run_id, ef.fund_id, ef.source_document_id, ef.page_number,
               ef.raw_value, ef.unit, ef.quoted_text, ef.approval_status, ef.reviewed_by,
               ef.reviewed_at, ef.review_note, ef.created_at, ef.structured_payload_json
        FROM extracted_facts ef
        WHERE ef.fact_category = 'performance'
          AND ef.fact_name IN ('monthly_return', 'quarterly_return', 'annual_return', 'ytd_return')
        ORDER BY ef.created_at
        """
    ).fetchall()
    inserted = 0
    for row in rows:
        existing = connection.execute(
            "SELECT 1 FROM proposed_return_rows WHERE legacy_fact_id = ?",
            (row["fact_id"],),
        ).fetchone()
        if existing is not None:
            continue
        payload = json.loads(row["structured_payload_json"]) if row["structured_payload_json"] else {}
        structured_context = payload.get("structured_context") or {}
        try:
            proposed_id = upsert_proposed_return_row(
                connection,
                legacy_fact_id=row["fact_id"],
                run_id=row["run_id"],
                fund_id=row["fund_id"],
                source_document_id=row["source_document_id"],
                page_number=row["page_number"],
                share_class=payload.get("share_class"),
                period_type=payload.get("period_type"),
                period_start_date=payload.get("period_start_date"),
                period_end_date=payload.get("period_end_date"),
                return_type=payload.get("return_type") or "unknown",
                return_value=payload.get("return_value") or row["raw_value"],
                raw_value_text=payload.get("raw_value_text") or row["raw_value"],
                unit=payload.get("unit") or row["unit"] or "percent",
                currency=payload.get("currency"),
                benchmark_name=payload.get("benchmark_name"),
                evidence_text=payload.get("quoted_text") or row["quoted_text"],
                table_name=structured_context.get("table_name"),
                row_label=structured_context.get("row_label"),
                column_label=structured_context.get("column_label"),
                source_image_id=payload.get("source_image_id"),
                source_import_id=payload.get("source_import_id"),
                status=row["approval_status"],
                reviewed_by=row["reviewed_by"],
                reviewed_at=row["reviewed_at"],
                review_note=row["review_note"],
            )
        except ValueError:
            continue
        if proposed_id:
            inserted += 1
    return inserted


def _proposed_row_to_fact(row):
    fact_name = PERIOD_TO_FACT_NAME.get(row["period_type"])
    if not fact_name:
        raise ValueError(f"Unsupported period_type: {row['period_type']}")
    payload = {
        "period_type": row["period_type"],
        "period_start_date": row["period_start_date"],
        "period_end_date": row["period_end_date"],
        "return_type": row["return_type"],
        "share_class": row["share_class"],
        "return_value": row["return_value"],
        "return_value_bps": row["return_value_bps"],
        "raw_value_text": row["raw_value_text"],
        "unit": row["unit"],
        "currency": row["currency"],
        "benchmark_name": row["benchmark_name"],
        "quoted_text": row["evidence_text"],
        "structured_context": {
            "table_name": row["table_name"],
            "row_label": row["row_label"],
            "column_label": row["column_label"],
        },
        "source_image_id": row["source_image_id"],
        "source_import_id": row["source_import_id"],
        "proposed_row_id": row["proposed_row_id"],
    }
    return {
        "fact_id": None,
        "fund_id": row["fund_id"],
        "fact_category": "performance",
        "fact_name": fact_name,
        "raw_value": row["raw_value_text"],
        "normalized_value": str(row["return_value"]),
        "unit": row["unit"],
        "as_of_date": row["period_end_date"],
        "source_document_id": row["source_document_id"],
        "page_number": row["page_number"],
        "quoted_text": row["evidence_text"],
        "citation_id": None,
        "structured_payload_json": json.dumps(payload),
    }


def approve_proposed_return_rows(connection, proposed_row_ids, reviewer, note=""):
    approved_fact_ids = []
    for proposed_row_id in proposed_row_ids:
        row = connection.execute(
            "SELECT * FROM proposed_return_rows WHERE proposed_row_id = ?",
            (proposed_row_id,),
        ).fetchone()
        if row is None:
            continue
        row = dict(row)
        if row["status"] != "pending":
            continue
        approved_fact_id = approve_fact_record(
            connection,
            _proposed_row_to_fact(row),
            reviewer=reviewer,
            note=note,
            approved_value=str(row["return_value"]),
            normalized_value=str(row["return_value"]),
        )
        connection.execute(
            """
            UPDATE proposed_return_rows
            SET status = 'approved',
                reviewed_by = ?,
                reviewed_at = ?,
                review_note = ?,
                updated_at = ?
            WHERE proposed_row_id = ?
            """,
            (reviewer, utc_now(), note, utc_now(), proposed_row_id),
        )
        approved_fact_ids.append(approved_fact_id)
    promoted, skipped = promote_approved_fact_ids(connection, approved_fact_ids, user=reviewer)
    return approved_fact_ids, promoted, skipped


def reject_proposed_return_rows(connection, proposed_row_ids, reviewer, note=""):
    now = utc_now()
    connection.executemany(
        """
        UPDATE proposed_return_rows
        SET status = 'rejected',
            reviewed_by = ?,
            reviewed_at = ?,
            review_note = ?,
            updated_at = ?
        WHERE proposed_row_id = ?
          AND status = 'pending'
        """,
        [(reviewer, now, note, now, proposed_row_id) for proposed_row_id in proposed_row_ids],
    )


def revise_proposed_return_row(
    connection,
    proposed_row_id,
    *,
    reviewer=None,
    note="",
    share_class=None,
    period_type=None,
    period_start_date=None,
    period_end_date=None,
    return_type=None,
    return_value=None,
    raw_value_text=None,
    unit=None,
    currency=None,
    benchmark_name=None,
    evidence_text=None,
    table_name=None,
    row_label=None,
    column_label=None,
):
    row = connection.execute(
        "SELECT * FROM proposed_return_rows WHERE proposed_row_id = ?",
        (proposed_row_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Missing proposed return row: {proposed_row_id}")
    row = dict(row)
    if row["status"] != "pending":
        raise ValueError("Only pending return rows can be revised.")

    updated = dict(row)
    allowed_period_types = set(PERIOD_TO_FACT_NAME)
    if period_type is not None:
        normalized_period_type = str(period_type).strip().lower()
        if normalized_period_type not in allowed_period_types:
            raise ValueError(f"Unsupported period_type: {period_type}")
        updated["period_type"] = normalized_period_type

    for field_name, value in [
        ("share_class", share_class),
        ("period_start_date", period_start_date),
        ("period_end_date", period_end_date),
        ("return_type", return_type),
        ("unit", unit),
        ("currency", currency),
        ("benchmark_name", benchmark_name),
        ("evidence_text", evidence_text),
        ("table_name", table_name),
        ("row_label", row_label),
        ("column_label", column_label),
    ]:
        if value is not None:
            updated[field_name] = value

    if return_value is not None:
        parsed_return_value = parse_return_value(return_value)
        if parsed_return_value is None:
            raise ValueError("return_value must be parseable as a number.")
        updated["return_value"] = parsed_return_value
        updated["return_value_bps"] = percent_to_bps(parsed_return_value)
        if raw_value_text is None:
            updated["raw_value_text"] = str(return_value)

    if raw_value_text is not None:
        updated["raw_value_text"] = str(raw_value_text)
        if return_value is None:
            parsed_return_value = parse_return_value(raw_value_text)
            if parsed_return_value is not None:
                updated["return_value"] = parsed_return_value
                updated["return_value_bps"] = percent_to_bps(parsed_return_value)

    if updated.get("return_value") is None:
        raise ValueError("Pending return rows require a numeric return_value.")
    if not updated.get("period_end_date"):
        raise ValueError("Pending return rows require period_end_date.")

    connection.execute(
        """
        UPDATE proposed_return_rows
        SET share_class = ?, period_type = ?, period_start_date = ?, period_end_date = ?,
            return_type = ?, return_value = ?, return_value_bps = ?, raw_value_text = ?,
            unit = ?, currency = ?, benchmark_name = ?, evidence_text = ?, table_name = ?,
            row_label = ?, column_label = ?, reviewed_by = COALESCE(?, reviewed_by),
            reviewed_at = ?, review_note = ?, updated_at = ?
        WHERE proposed_row_id = ?
        """,
        (
            updated["share_class"],
            updated["period_type"],
            updated["period_start_date"],
            updated["period_end_date"],
            updated["return_type"],
            updated["return_value"],
            updated["return_value_bps"],
            updated["raw_value_text"],
            updated["unit"],
            updated["currency"],
            updated["benchmark_name"],
            updated["evidence_text"],
            updated["table_name"],
            updated["row_label"],
            updated["column_label"],
            reviewer,
            utc_now(),
            note,
            utc_now(),
            proposed_row_id,
        ),
    )
    return updated

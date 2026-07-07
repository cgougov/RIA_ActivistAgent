from core.approval import reject_pending_facts
from core.extraction import sanitize_proposed_facts


def pending_descriptive_facts(connection, document_id):
    rows = connection.execute(
        """
        SELECT fact_id, fact_category, fact_name, raw_value, normalized_value, unit,
               as_of_date, page_number, quoted_text, confidence_score, approval_status
        FROM extracted_facts
        WHERE source_document_id = ?
          AND approval_status = 'pending'
          AND NOT (
              fact_category = 'performance'
              AND fact_name IN ('monthly_return', 'quarterly_return', 'annual_return', 'ytd_return')
          )
        ORDER BY page_number, fact_category, fact_name, created_at
        """,
        (document_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def sanitize_pending_document_facts(connection, document_id, reviewer="system", apply=False):
    pending_rows = pending_descriptive_facts(connection, document_id)
    kept_rows = sanitize_proposed_facts(pending_rows)
    keep_ids = {row["fact_id"] for row in kept_rows}
    duplicate_rows = [row for row in pending_rows if row["fact_id"] not in keep_ids]
    duplicate_ids = [row["fact_id"] for row in duplicate_rows]

    summary = {
        "document_id": document_id,
        "pending_count": len(pending_rows),
        "kept_count": len(kept_rows),
        "duplicate_count": len(duplicate_rows),
        "kept_fact_ids": sorted(keep_ids),
        "duplicate_fact_ids": duplicate_ids,
        "duplicate_rows": duplicate_rows,
    }

    if apply and duplicate_ids:
        reject_pending_facts(
            connection,
            duplicate_ids,
            reviewer=reviewer,
            note="Rejected as duplicate/noisy pending proposal during queue cleanup.",
        )

    return summary

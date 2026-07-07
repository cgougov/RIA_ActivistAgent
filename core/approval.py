from uuid import uuid4

from core.db import utc_now
from core.fact_dedup import find_matching_approved_fact


def _insert_approved_fact(connection, fact, approved_value, normalized_value, reviewer, note=""):
    approved_fact_id = f"appr_{uuid4().hex}"
    reviewed_at = utc_now()
    connection.execute(
        """
        INSERT INTO approved_facts (
            approved_fact_id, source_fact_id, fund_id, fact_category, fact_name,
            approved_value, normalized_value, unit, as_of_date, source_document_id,
            page_number, quoted_text, citation_id, approved_by, approved_at,
            approval_note, structured_payload_json, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            approved_fact_id,
            fact.get("fact_id"),
            fact["fund_id"],
            fact["fact_category"],
            fact["fact_name"],
            approved_value,
            normalized_value,
            fact["unit"],
            fact["as_of_date"],
            fact["source_document_id"],
            fact["page_number"],
            fact["quoted_text"],
            fact["citation_id"],
            reviewer,
            reviewed_at,
            note,
            fact["structured_payload_json"],
            reviewed_at,
            reviewed_at,
        ),
    )
    connection.execute(
        """
        UPDATE extracted_facts
        SET approval_status = 'approved',
            normalized_value = ?,
            reviewed_by = ?,
            reviewed_at = ?,
            review_note = ?
        WHERE fact_id = ?
        """,
        (normalized_value, reviewer, reviewed_at, note, fact["fact_id"]),
    )
    return approved_fact_id


def _mark_fact_as_approved(connection, fact_id, normalized_value, reviewer, note=""):
    reviewed_at = utc_now()
    connection.execute(
        """
        UPDATE extracted_facts
        SET approval_status = 'approved',
            normalized_value = ?,
            reviewed_by = ?,
            reviewed_at = ?,
            review_note = ?
        WHERE fact_id = ?
        """,
        (normalized_value, reviewer, reviewed_at, note, fact_id),
    )


def approve_fact_record(connection, fact, reviewer, note="", approved_value=None, normalized_value=None):
    approved_value = approved_value if approved_value is not None else fact["raw_value"]
    normalized_value = normalized_value if normalized_value is not None else (fact.get("normalized_value") or fact["raw_value"])
    existing = find_matching_approved_fact(
        connection,
        fund_id=fact["fund_id"],
        fact_category=fact["fact_category"],
        fact_name=fact["fact_name"],
        value=normalized_value,
    )
    if existing:
        review_note = note or f"Matched existing approved fact {existing['approved_fact_id']}."
        if fact.get("fact_id"):
            _mark_fact_as_approved(connection, fact["fact_id"], normalized_value, reviewer, review_note)
        return existing["approved_fact_id"]
    return _insert_approved_fact(connection, fact, approved_value, normalized_value, reviewer, note)


def approve_extracted_fact(connection, fact_id, approved_value, normalized_value, reviewer, note=""):
    fact = connection.execute(
        "SELECT * FROM extracted_facts WHERE fact_id = ?",
        (fact_id,),
    ).fetchone()
    if fact is None:
        raise ValueError(f"Missing extracted fact: {fact_id}")
    fact = dict(fact)
    return approve_fact_record(connection, fact, reviewer, note, approved_value, normalized_value)


def revise_pending_fact(connection, fact_id, raw_value=None, normalized_value=None, reviewer=None, note=""):
    fact = connection.execute(
        "SELECT * FROM extracted_facts WHERE fact_id = ?",
        (fact_id,),
    ).fetchone()
    if fact is None:
        raise ValueError(f"Missing extracted fact: {fact_id}")
    fact = dict(fact)
    if fact["approval_status"] != "pending":
        raise ValueError("Only pending facts can be revised.")

    new_raw_value = raw_value if raw_value is not None else fact["raw_value"]
    new_normalized_value = normalized_value if normalized_value is not None else (fact["normalized_value"] or new_raw_value)
    reviewed_at = utc_now()
    connection.execute(
        """
        UPDATE extracted_facts
        SET raw_value = ?,
            normalized_value = ?,
            reviewed_by = COALESCE(?, reviewed_by),
            reviewed_at = ?,
            review_note = ?
        WHERE fact_id = ?
        """,
        (new_raw_value, new_normalized_value, reviewer, reviewed_at, note, fact_id),
    )
    return {
        "fact_id": fact_id,
        "raw_value": new_raw_value,
        "normalized_value": new_normalized_value,
    }


def approve_pending_facts(connection, fact_ids, reviewer, note="", approved_value_fn=None, normalized_value_fn=None):
    approved_fact_ids = []
    for fact_id in fact_ids:
        fact = connection.execute(
            "SELECT * FROM extracted_facts WHERE fact_id = ?",
            (fact_id,),
        ).fetchone()
        if fact is None:
            continue
        fact = dict(fact)
        if fact["approval_status"] != "pending":
            continue
        approved_value = approved_value_fn(fact) if approved_value_fn else fact["raw_value"]
        normalized_value = normalized_value_fn(fact) if normalized_value_fn else (fact["normalized_value"] or fact["raw_value"])
        approved_fact_ids.append(
            approve_fact_record(
                connection,
                fact,
                reviewer=reviewer,
                note=note,
                approved_value=approved_value,
                normalized_value=normalized_value,
            )
        )
    return approved_fact_ids


def reject_extracted_fact(connection, fact_id, reviewer, note=""):
    reviewed_at = utc_now()
    connection.execute(
        """
        UPDATE extracted_facts
        SET approval_status = 'rejected',
            reviewed_by = ?,
            reviewed_at = ?,
            review_note = ?
        WHERE fact_id = ?
        """,
        (reviewer, reviewed_at, note, fact_id),
    )


def reject_pending_facts(connection, fact_ids, reviewer, note=""):
    reviewed_at = utc_now()
    connection.executemany(
        """
        UPDATE extracted_facts
        SET approval_status = 'rejected',
            reviewed_by = ?,
            reviewed_at = ?,
            review_note = ?
        WHERE fact_id = ?
          AND approval_status = 'pending'
        """,
        [(reviewer, reviewed_at, note, fact_id) for fact_id in fact_ids],
    )


def reopen_rejected_fact(connection, fact_id, raw_value, normalized_value, reviewer, note=""):
    fact = connection.execute(
        "SELECT approval_status FROM extracted_facts WHERE fact_id = ?",
        (fact_id,),
    ).fetchone()
    if fact is None:
        raise ValueError(f"Missing extracted fact: {fact_id}")
    if fact["approval_status"] != "rejected":
        raise ValueError("Only rejected facts can be reopened.")

    reviewed_at = utc_now()
    review_note = note or "Reopened from rejected status for another approval review."
    connection.execute(
        """
        UPDATE extracted_facts
        SET approval_status = 'pending',
            raw_value = ?,
            normalized_value = ?,
            reviewed_by = ?,
            reviewed_at = ?,
            review_note = ?
        WHERE fact_id = ?
        """,
        (raw_value, normalized_value, reviewer, reviewed_at, review_note, fact_id),
    )

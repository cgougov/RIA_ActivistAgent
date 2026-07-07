"""Stage 3 of the pipeline: human approval (proposals -> source of truth).

Approving a descriptive proposal upserts into facts — the (fund_id, field_key)
primary key makes duplicates structurally impossible; re-approving a field is
a revision, not a second row. Approving a return row upserts into returns the
same way, preferring net over gross over unknown when a period already exists
(the one good dedup pattern from the legacy system, kept).
"""
from fund.db import utc_now
from fund.extract import parse_number
from fund.schema import FIELDS

_RETURN_QUALITY = {"net": 2, "gross": 1, "unknown": 0}


def pending_proposals(connection, doc_id=None, fund_id=None):
    where, params = ["status = 'pending'"], []
    if doc_id:
        where.append("doc_id = ?")
        params.append(doc_id)
    if fund_id:
        where.append("fund_id = ?")
        params.append(fund_id)
    return [dict(row) for row in connection.execute(
        f"SELECT * FROM proposals WHERE {' AND '.join(where)} ORDER BY scope, field_key",
        params,
    ).fetchall()]


def pending_returns(connection, doc_id=None, fund_id=None):
    where, params = ["status = 'pending'"], []
    if doc_id:
        where.append("doc_id = ?")
        params.append(doc_id)
    if fund_id:
        where.append("fund_id = ?")
        params.append(fund_id)
    return [dict(row) for row in connection.execute(
        f"""SELECT * FROM proposed_returns WHERE {' AND '.join(where)}
            ORDER BY share_class, period_type, period_end""",
        params,
    ).fetchall()]


def approve_proposal(connection, proposal_id, reviewer, value=None, note=""):
    """Approve one proposal (optionally revising its value) into facts."""
    row = connection.execute(
        "SELECT * FROM proposals WHERE proposal_id = ? AND status = 'pending'", (proposal_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No pending proposal: {proposal_id}")
    row = dict(row)
    final_value = (value if value is not None else row["value"]).strip()
    value_num = parse_number(final_value) if FIELDS[row["field_key"]][2] == "number" else None
    now = utc_now()

    connection.execute(
        """
        INSERT INTO facts (fund_id, field_key, value, value_num, unit, as_of_date,
                           doc_id, page, quote, approved_by, approved_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (fund_id, field_key) DO UPDATE SET
            value = excluded.value, value_num = excluded.value_num, unit = excluded.unit,
            as_of_date = excluded.as_of_date, doc_id = excluded.doc_id, page = excluded.page,
            quote = excluded.quote, approved_by = excluded.approved_by,
            approved_at = excluded.approved_at
        """,
        (row["fund_id"], row["field_key"], final_value, value_num, row["unit"],
         row["as_of_date"], row["doc_id"], row["page"], row["quote"], reviewer, now),
    )
    connection.execute(
        """UPDATE proposals SET status = 'approved', review_note = ?, reviewed_by = ?,
           reviewed_at = ? WHERE proposal_id = ?""",
        (note, reviewer, now, proposal_id),
    )
    return row["field_key"]


def reject_proposal(connection, proposal_id, reviewer, note=""):
    changed = connection.execute(
        """UPDATE proposals SET status = 'rejected', review_note = ?, reviewed_by = ?,
           reviewed_at = ? WHERE proposal_id = ? AND status = 'pending'""",
        (note, reviewer, utc_now(), proposal_id),
    ).rowcount
    if not changed:
        raise ValueError(f"No pending proposal: {proposal_id}")


def reopen_proposal(connection, proposal_id, reviewer, note=""):
    changed = connection.execute(
        """UPDATE proposals SET status = 'pending', review_note = ?, reviewed_by = ?,
           reviewed_at = ? WHERE proposal_id = ? AND status = 'rejected'""",
        (note or "Reopened for another review.", reviewer, utc_now(), proposal_id),
    ).rowcount
    if not changed:
        raise ValueError(f"No rejected proposal: {proposal_id}")


def approve_return_row(connection, row_id, reviewer, note=""):
    """Approve one proposed return row into returns, with quality-aware upsert."""
    row = connection.execute(
        "SELECT * FROM proposed_returns WHERE row_id = ? AND status = 'pending'", (row_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"No pending return row: {row_id}")
    row = dict(row)
    now = utc_now()

    existing = connection.execute(
        """SELECT return_type FROM returns
           WHERE fund_id = ? AND share_class = ? AND period_type = ? AND period_end = ?""",
        (row["fund_id"], row["share_class"], row["period_type"], row["period_end"]),
    ).fetchone()
    outcome = "inserted"
    if existing:
        if _RETURN_QUALITY[row["return_type"]] < _RETURN_QUALITY.get(existing["return_type"], 0):
            outcome = "kept_existing_higher_quality"
        else:
            outcome = "replaced_existing"
    if outcome != "kept_existing_higher_quality":
        connection.execute(
            """
            INSERT INTO returns (fund_id, share_class, period_type, period_end, period_start,
                                 return_pct, return_type, doc_id, page, quote,
                                 approved_by, approved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (fund_id, share_class, period_type, period_end) DO UPDATE SET
                period_start = excluded.period_start, return_pct = excluded.return_pct,
                return_type = excluded.return_type, doc_id = excluded.doc_id,
                page = excluded.page, quote = excluded.quote,
                approved_by = excluded.approved_by, approved_at = excluded.approved_at
            """,
            (row["fund_id"], row["share_class"], row["period_type"], row["period_end"],
             row["period_start"], row["return_pct"], row["return_type"], row["doc_id"],
             row["page"], row["quote"], reviewer, now),
        )
    connection.execute(
        """UPDATE proposed_returns SET status = 'approved', review_note = ?, reviewed_by = ?,
           reviewed_at = ? WHERE row_id = ?""",
        (note or outcome, reviewer, now, row_id),
    )
    return outcome


def reject_return_row(connection, row_id, reviewer, note=""):
    changed = connection.execute(
        """UPDATE proposed_returns SET status = 'rejected', review_note = ?, reviewed_by = ?,
           reviewed_at = ? WHERE row_id = ? AND status = 'pending'""",
        (note, reviewer, utc_now(), row_id),
    ).rowcount
    if not changed:
        raise ValueError(f"No pending return row: {row_id}")

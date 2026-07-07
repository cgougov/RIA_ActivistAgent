from core.db import utc_now
from core.promotion import change_history


def _clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _return_quality_score(return_type):
    normalized = (_clean_text(return_type) or "unknown").lower()
    if normalized == "net":
        return 3
    if normalized == "gross":
        return 2
    return 1


def _row_priority(row):
    return (
        _return_quality_score(row.get("return_type")),
        1 if _clean_text(row.get("proposed_row_id")) else 0,
        1 if _clean_text(row.get("evidence_text")) else 0,
        1 if row.get("page_number") is not None else 0,
        1 if _clean_text(row.get("raw_value_text")) else 0,
        _clean_text(row.get("updated_at")) or "",
        _clean_text(row.get("created_at")) or "",
        _clean_text(row.get("return_id")) or "",
    )


def duplicate_return_groups(connection, fund_id=None):
    params = []
    fund_filter = ""
    if fund_id:
        fund_filter = "WHERE fund_id = ?"
        params.append(fund_id)

    keys = connection.execute(
        f"""
        SELECT fund_id, COALESCE(share_class, '') AS share_class_key, period_type, period_end_date, COUNT(*) AS row_count
        FROM performance_returns
        {fund_filter}
        GROUP BY fund_id, COALESCE(share_class, ''), period_type, period_end_date
        HAVING COUNT(*) > 1
        ORDER BY fund_id, period_end_date, share_class_key, period_type
        """,
        params,
    ).fetchall()

    groups = []
    for key in keys:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT *
                FROM performance_returns
                WHERE fund_id = ?
                  AND COALESCE(share_class, '') = ?
                  AND period_type = ?
                  AND period_end_date = ?
                ORDER BY created_at, return_id
                """,
                (
                    key["fund_id"],
                    key["share_class_key"],
                    key["period_type"],
                    key["period_end_date"],
                ),
            ).fetchall()
        ]
        if len(rows) <= 1:
            continue
        canonical_row = max(rows, key=_row_priority)
        duplicate_rows = [row for row in rows if row["return_id"] != canonical_row["return_id"]]
        groups.append(
            {
                "fund_id": key["fund_id"],
                "share_class": canonical_row.get("share_class"),
                "period_type": key["period_type"],
                "period_end_date": key["period_end_date"],
                "canonical_row": canonical_row,
                "duplicate_rows": duplicate_rows,
            }
        )
    return groups


def canonicalize_performance_returns(connection, fund_id=None, user="system", apply=False):
    groups = duplicate_return_groups(connection, fund_id=fund_id)
    summary = []
    for group in groups:
        canonical_row = group["canonical_row"]
        duplicate_rows = group["duplicate_rows"]
        item = {
            "fund_id": group["fund_id"],
            "share_class": group["share_class"],
            "period_type": group["period_type"],
            "period_end_date": group["period_end_date"],
            "canonical_return_id": canonical_row["return_id"],
            "canonical_return_type": canonical_row.get("return_type"),
            "canonical_approved_fact_id": canonical_row.get("approved_fact_id"),
            "duplicate_return_ids": [row["return_id"] for row in duplicate_rows],
            "duplicate_return_types": [row.get("return_type") for row in duplicate_rows],
            "duplicate_approved_fact_ids": [row.get("approved_fact_id") for row in duplicate_rows],
        }
        summary.append(item)

        if not apply:
            continue

        for duplicate_row in duplicate_rows:
            duplicate_fact_id = duplicate_row.get("approved_fact_id")
            if duplicate_fact_id:
                connection.execute(
                    """
                    UPDATE approved_facts
                    SET promoted_record_id = ?, updated_at = ?
                    WHERE approved_fact_id = ?
                    """,
                    (canonical_row["return_id"], utc_now(), duplicate_fact_id),
                )
                change_history(
                    connection,
                    "approved_facts",
                    duplicate_fact_id,
                    "promoted_record_id",
                    duplicate_row["return_id"],
                    canonical_row["return_id"],
                    user,
                    f"Canonicalized duplicate performance return {duplicate_row['return_id']} to {canonical_row['return_id']}",
                )

            change_history(
                connection,
                "performance_returns",
                duplicate_row["return_id"],
                "deduplicated_to",
                duplicate_row["return_id"],
                canonical_row["return_id"],
                user,
                "Removed duplicate approved return row during canonicalization.",
            )
            connection.execute(
                "DELETE FROM performance_returns WHERE return_id = ?",
                (duplicate_row["return_id"],),
            )
    return summary

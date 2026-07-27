"""Approved activist-universe classifications and eligibility rules.

Classifications remain ordinary sourced facts. This module only reads those
facts and applies the published inclusion rule; it creates no parallel truth.
"""

ACTIVITY_VALUES = {"active", "uncertain", "inactive"}
ACTIVIST_VALUES = {"candidate", "verified_activist", "excluded"}


def classifications(connection, fund_ids=None):
    where, params = "", []
    if fund_ids:
        placeholders = ", ".join("?" for _ in fund_ids)
        where, params = f"WHERE f.fund_id IN ({placeholders})", list(fund_ids)
    rows = connection.execute(
        f"""
        SELECT f.fund_id, f.fund_name,
               a.value AS activity_status, a.doc_id AS activity_doc_id,
               a.page AS activity_page, a.quote AS activity_quote,
               u.value AS activist_universe_status, u.doc_id AS universe_doc_id,
               u.page AS universe_page, u.quote AS universe_quote
        FROM funds f
        LEFT JOIN facts a ON a.fund_id = f.fund_id AND a.field_key = 'activity_status'
                         AND a.share_class = ''
        LEFT JOIN facts u ON u.fund_id = f.fund_id AND u.field_key = 'activist_universe_status'
                         AND u.share_class = ''
        {where}
        ORDER BY f.fund_name
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def is_eligible(row, include_uncertain=False, include_candidates=False):
    activity = row.get("activity_status")
    universe = row.get("activist_universe_status")
    activity_ok = activity == "active" or (include_uncertain and activity == "uncertain")
    universe_ok = universe == "verified_activist" or (
        include_candidates and universe == "candidate"
    )
    return activity_ok and universe_ok


def eligible_fund_ids(connection, include_uncertain=False, include_candidates=False):
    return [
        row["fund_id"]
        for row in classifications(connection)
        if is_eligible(row, include_uncertain=include_uncertain,
                       include_candidates=include_candidates)
    ]


def eligibility_label(row):
    activity = row.get("activity_status") or "unclassified"
    universe = row.get("activist_universe_status") or "unclassified"
    return f"{universe}; {activity}"

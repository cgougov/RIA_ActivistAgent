from itertools import combinations
from uuid import uuid4


def comparable_value(fact):
    return (fact["normalized_value"] or fact["approved_value"] or "").strip().lower()


def conflict_key(fact):
    # Different dated observations can both be true. Same date/same field but different
    # value should be reviewed.
    return (
        fact["fund_id"],
        fact["fact_category"],
        fact["fact_name"],
        fact["as_of_date"] or "undated",
    )


def existing_conflict_pairs(connection):
    rows = connection.execute(
        """
        SELECT first_approved_fact_id, second_approved_fact_id
        FROM fact_conflicts
        """
    ).fetchall()
    pairs = set()
    for row in rows:
        pairs.add(tuple(sorted([row["first_approved_fact_id"], row["second_approved_fact_id"]])))
    return pairs


def detect_fact_conflicts(connection):
    facts = [
        dict(row)
        for row in connection.execute(
            """
            SELECT *
            FROM approved_facts
            WHERE promotion_status IN ('pending', 'promoted')
            ORDER BY fund_id, fact_category, fact_name, as_of_date, approved_at
            """
        ).fetchall()
    ]

    groups = {}
    for fact in facts:
        groups.setdefault(conflict_key(fact), []).append(fact)

    existing = existing_conflict_pairs(connection)
    created = []

    for group_facts in groups.values():
        unique_values = {comparable_value(fact) for fact in group_facts}
        if len(unique_values) <= 1:
            continue

        for first, second in combinations(group_facts, 2):
            if comparable_value(first) == comparable_value(second):
                continue
            pair = tuple(sorted([first["approved_fact_id"], second["approved_fact_id"]]))
            if pair in existing:
                continue

            conflict_id = f"conflict_{uuid4().hex}"
            connection.execute(
                """
                INSERT INTO fact_conflicts (
                    conflict_id,
                    fund_id,
                    fact_category,
                    fact_name,
                    first_approved_fact_id,
                    second_approved_fact_id,
                    first_value,
                    second_value
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conflict_id,
                    first["fund_id"],
                    first["fact_category"],
                    first["fact_name"],
                    first["approved_fact_id"],
                    second["approved_fact_id"],
                    first["approved_value"],
                    second["approved_value"],
                ),
            )
            created.append(conflict_id)
            existing.add(pair)

    return created


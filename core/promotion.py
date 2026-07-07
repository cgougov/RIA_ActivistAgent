import json
import re
from uuid import uuid4

from core.db import utc_now
from core.taxonomy import CORE_PROFILE_FIELDS, RETURN_FACTS, STRATEGY_FIELD_MAP, TERMS_FIELD_MAP


def parse_number(value):
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    match = re.search(r"-?\d+(\.\d+)?", text)
    return float(match.group()) if match else None


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


def mark_promoted(connection, fact, target_table, target_record_id):
    connection.execute(
        """
        UPDATE approved_facts
        SET promotion_status = 'promoted',
            promoted_to_table = ?,
            promoted_record_id = ?,
            promoted_at = ?,
            updated_at = ?
        WHERE approved_fact_id = ?
        """,
        (target_table, target_record_id, utc_now(), utc_now(), fact["approved_fact_id"]),
    )


def change_history(connection, table_name, record_id, field_name, old_value, new_value, user, reason):
    connection.execute(
        """
        INSERT INTO change_history (
            change_id, table_name, record_id, field_name, old_value, new_value,
            changed_by, changed_at, change_reason
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"chg_{uuid4().hex}",
            table_name,
            record_id,
            field_name,
            old_value,
            new_value,
            user,
            utc_now(),
            reason,
        ),
    )


def unpromoted_approved_facts(connection):
    rows = connection.execute(
        """
        SELECT af.*
        FROM approved_facts af
        WHERE af.promotion_status = 'pending'
        ORDER BY af.approved_at
        """
    ).fetchall()
    return [dict(row) for row in rows]


def value_for(fact):
    return fact["normalized_value"] or fact["approved_value"]


def is_return_fact(fact):
    return fact["fact_category"] == "performance" and fact["fact_name"] in RETURN_FACTS


def structured_payload(fact):
    if not fact.get("structured_payload_json"):
        return {}
    return json.loads(fact["structured_payload_json"])


def skip_reason(fact):
    if is_return_fact(fact):
        payload = structured_payload(fact)
        expected = RETURN_FACTS.get(fact["fact_name"])
        if not payload:
            return "missing structured_payload_json"
        if payload.get("period_type") != expected:
            return f"period_type mismatch: payload={payload.get('period_type')!r} expected={expected!r}"
        if not (payload.get("period_end_date") or fact["as_of_date"]):
            return "missing period_end_date/as_of_date"
        if parse_number(value_for(fact)) is None:
            return "return_value not parseable as a number"
        return "unknown return-promotion failure"
    return "no matching promotion handler for this fact_category/fact_name"


def promote_fund_characteristic(connection, fact):
    definition = connection.execute(
        """
        SELECT group_id, display_name
        FROM characteristic_definitions
        WHERE fact_category = ?
          AND fact_name = ?
        """,
        (fact["fact_category"], fact["fact_name"]),
    ).fetchone()
    if definition is None:
        return None

    record_id = f"char_{uuid4().hex}"
    value = value_for(fact)
    connection.execute(
        """
        INSERT INTO fund_characteristics (
            characteristic_id, fund_id, group_id, fact_category, fact_name,
            display_name, value_text, value_number, unit, as_of_date,
            source_document_id, page_number, quoted_text, approved_fact_id,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            fact["fund_id"],
            definition["group_id"],
            fact["fact_category"],
            fact["fact_name"],
            definition["display_name"],
            value,
            parse_number(value),
            fact["unit"],
            fact["as_of_date"],
            fact["source_document_id"],
            fact["page_number"],
            fact["quoted_text"],
            fact["approved_fact_id"],
            utc_now(),
            utc_now(),
        ),
    )
    return "fund_characteristics", record_id


def promote_core_profile(connection, fact, user):
    column = CORE_PROFILE_FIELDS.get(fact["fact_name"])
    if not column:
        return None

    old = connection.execute(
        f"SELECT {column} FROM funds WHERE fund_id = ?",
        (fact["fund_id"],),
    ).fetchone()[0]
    new = value_for(fact)
    connection.execute(
        f"UPDATE funds SET {column} = ?, updated_at = ? WHERE fund_id = ?",
        (new, utc_now(), fact["fund_id"]),
    )
    change_history(
        connection,
        "funds",
        fact["fund_id"],
        column,
        old,
        new,
        user,
        f"Promoted from {fact['approved_fact_id']}",
    )
    return "funds", fact["fund_id"]


def promote_profile_attribute(connection, fact):
    if fact["fact_category"] != "profile":
        return None

    record_id = f"attr_{uuid4().hex}"
    connection.execute(
        """
        INSERT INTO fund_profile_attributes (
            attribute_id, fund_id, attribute_name, attribute_value, normalized_value,
            effective_date, source_document_id, page_number, approved_fact_id,
            created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            fact["fund_id"],
            fact["fact_name"],
            fact["approved_value"],
            value_for(fact),
            fact["as_of_date"],
            fact["source_document_id"],
            fact["page_number"],
            fact["approved_fact_id"],
            utc_now(),
            utc_now(),
        ),
    )
    return "fund_profile_attributes", record_id


def promote_person(connection, fact):
    if fact["fact_category"] != "people":
        return None

    record_id = f"person_{uuid4().hex}"
    role_title = fact["fact_name"].replace("_", " ")
    connection.execute(
        """
        INSERT INTO fund_people (
            person_id, fund_id, person_name, role_title, source_document_id,
            page_number, approved_fact_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            fact["fund_id"],
            value_for(fact),
            role_title,
            fact["source_document_id"],
            fact["page_number"],
            fact["approved_fact_id"],
            utc_now(),
            utc_now(),
        ),
    )
    return "fund_people", record_id


def promote_terms(connection, fact):
    column = TERMS_FIELD_MAP.get(fact["fact_name"])
    if not column:
        return None

    raw_value = value_for(fact)
    stored_value = parse_number(raw_value) if column in {"management_fee", "performance_fee"} else raw_value
    record_id = f"terms_{uuid4().hex}"
    connection.execute(
        f"""
        INSERT INTO fund_terms (
            terms_id, fund_id, {column}, effective_date, source_document_id,
            page_number, approved_fact_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            fact["fund_id"],
            stored_value,
            fact["as_of_date"],
            fact["source_document_id"],
            fact["page_number"],
            fact["approved_fact_id"],
            utc_now(),
            utc_now(),
        ),
    )
    return "fund_terms", record_id


def promote_strategy(connection, fact):
    column = STRATEGY_FIELD_MAP.get(fact["fact_name"])
    if not column:
        return None

    record_id = f"strategy_{uuid4().hex}"
    connection.execute(
        f"""
        INSERT INTO fund_strategy (
            strategy_id, fund_id, {column}, source_document_id, page_number,
            approved_fact_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            fact["fund_id"],
            value_for(fact),
            fact["source_document_id"],
            fact["page_number"],
            fact["approved_fact_id"],
            utc_now(),
            utc_now(),
        ),
    )
    return "fund_strategy", record_id


def promote_return(connection, fact):
    period_type = RETURN_FACTS.get(fact["fact_name"])
    if not period_type:
        return None
    payload = structured_payload(fact)
    payload_period_type = payload.get("period_type")
    period_end_date = payload.get("period_end_date") or fact["as_of_date"]
    if not payload or payload_period_type != period_type:
        return None
    if not period_end_date or not fact["as_of_date"]:
        return None
    number = parse_number(value_for(fact))
    if number is None:
        return None

    share_class = _clean_text(payload.get("share_class"))
    incoming_return_type = payload.get("return_type") or "unknown"
    existing_rows = connection.execute(
        """
        SELECT return_id, return_type, approved_fact_id
        FROM performance_returns
        WHERE fund_id = ?
          AND COALESCE(share_class, '') = COALESCE(?, '')
          AND period_type = ?
          AND period_end_date = ?
        ORDER BY created_at, return_id
        """,
        (
            fact["fund_id"],
            share_class,
            period_type,
            period_end_date,
        ),
    ).fetchall()
    if existing_rows:
        canonical = dict(existing_rows[0])
        existing_score = _return_quality_score(canonical.get("return_type"))
        incoming_score = _return_quality_score(incoming_return_type)
        if incoming_score >= existing_score:
            connection.execute(
                """
                UPDATE performance_returns
                SET share_class = ?, period_start_date = ?, period_end_date = ?, return_type = ?,
                    return_value = ?, return_value_bps = ?, raw_value_text = ?, unit = ?, currency = ?,
                    benchmark_name = ?, evidence_text = ?, source_document_id = ?, page_number = ?,
                    approved_fact_id = ?, proposed_row_id = ?, source_import_id = ?, updated_at = ?
                WHERE return_id = ?
                """,
                (
                    share_class,
                    payload.get("period_start_date"),
                    period_end_date,
                    incoming_return_type,
                    number,
                    payload.get("return_value_bps"),
                    payload.get("raw_value_text") or fact["approved_value"],
                    fact["unit"],
                    payload.get("currency"),
                    payload.get("benchmark_name"),
                    payload.get("quoted_text") or fact["quoted_text"],
                    fact["source_document_id"],
                    fact["page_number"],
                    fact["approved_fact_id"],
                    payload.get("proposed_row_id"),
                    payload.get("source_import_id"),
                    utc_now(),
                    canonical["return_id"],
                ),
            )
        return "performance_returns", canonical["return_id"]

    record_id = f"return_{uuid4().hex}"
    connection.execute(
        """
        INSERT INTO performance_returns (
            return_id, fund_id, share_class, period_type, period_start_date,
            period_end_date, return_type, return_value, return_value_bps, raw_value_text,
            unit, currency, benchmark_name, evidence_text, source_document_id,
            page_number, approved_fact_id, proposed_row_id, source_import_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            fact["fund_id"],
            share_class,
            period_type,
            payload.get("period_start_date"),
            period_end_date,
            incoming_return_type,
            number,
            payload.get("return_value_bps"),
            payload.get("raw_value_text") or fact["approved_value"],
            fact["unit"],
            payload.get("currency"),
            payload.get("benchmark_name"),
            payload.get("quoted_text") or fact["quoted_text"],
            fact["source_document_id"],
            fact["page_number"],
            fact["approved_fact_id"],
            payload.get("proposed_row_id"),
            payload.get("source_import_id"),
            utc_now(),
            utc_now(),
        ),
    )
    return "performance_returns", record_id


def promote_metric(connection, fact):
    if fact["fact_category"] != "performance":
        return None
    if is_return_fact(fact):
        return None

    record_id = f"metric_{uuid4().hex}"
    connection.execute(
        """
        INSERT INTO fund_metrics (
            metric_id, fund_id, metric_name, metric_value, raw_value,
            normalized_value, unit, as_of_date, source_document_id, page_number,
            approved_fact_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            fact["fund_id"],
            fact["fact_name"],
            parse_number(value_for(fact)),
            fact["approved_value"],
            value_for(fact),
            fact["unit"],
            fact["as_of_date"],
            fact["source_document_id"],
            fact["page_number"],
            fact["approved_fact_id"],
            utc_now(),
            utc_now(),
        ),
    )
    return "fund_metrics", record_id


def promote_exposure(connection, fact):
    if fact["fact_category"] != "exposure":
        return None

    record_id = f"exposure_{uuid4().hex}"
    connection.execute(
        """
        INSERT INTO fund_exposures (
            exposure_id, fund_id, exposure_name, exposure_value, raw_value,
            normalized_value, unit, as_of_date, source_document_id, page_number,
            approved_fact_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            fact["fund_id"],
            fact["fact_name"],
            parse_number(value_for(fact)),
            fact["approved_value"],
            value_for(fact),
            fact["unit"],
            fact["as_of_date"],
            fact["source_document_id"],
            fact["page_number"],
            fact["approved_fact_id"],
            utc_now(),
            utc_now(),
        ),
    )
    return "fund_exposures", record_id


def promote_writeup(connection, fact):
    if fact["fact_category"] != "writeup":
        return None

    record_id = f"writeup_{uuid4().hex}"
    connection.execute(
        """
        INSERT INTO fund_writeups (
            writeup_id, fund_id, writeup_type, writeup_text, source_document_id,
            page_number, approved_fact_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            fact["fund_id"],
            fact["fact_name"],
            value_for(fact),
            fact["source_document_id"],
            fact["page_number"],
            fact["approved_fact_id"],
            utc_now(),
            utc_now(),
        ),
    )
    return "fund_writeups", record_id


def promote_flag(connection, fact):
    if fact["fact_category"] != "flag":
        return None

    payload = {}
    if fact.get("structured_payload_json"):
        payload = json.loads(fact["structured_payload_json"])

    record_id = f"flag_{uuid4().hex}"
    connection.execute(
        """
        INSERT INTO fund_flags (
            flag_id, fund_id, flag_type, flag_text, severity, source_document_id,
            page_number, approved_fact_id, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            fact["fund_id"],
            fact["fact_name"],
            value_for(fact),
            payload.get("severity") or "review",
            fact["source_document_id"],
            fact["page_number"],
            fact["approved_fact_id"],
            utc_now(),
            utc_now(),
        ),
    )
    return "fund_flags", record_id


def promote_fact(connection, fact, user):
    if is_return_fact(fact):
        result = promote_return(connection, fact)
        if result:
            mark_promoted(connection, fact, result[0], result[1])
            return result
        return None

    canonical_result = promote_fund_characteristic(connection, fact)
    handlers = [
        promote_core_profile,
        lambda conn, row, _user: promote_profile_attribute(conn, row),
        lambda conn, row, _user: promote_person(conn, row),
        lambda conn, row, _user: promote_terms(conn, row),
        lambda conn, row, _user: promote_strategy(conn, row),
        lambda conn, row, _user: promote_return(conn, row),
        lambda conn, row, _user: promote_metric(conn, row),
        lambda conn, row, _user: promote_exposure(conn, row),
        lambda conn, row, _user: promote_writeup(conn, row),
        lambda conn, row, _user: promote_flag(conn, row),
    ]

    for handler in handlers:
        result = handler(connection, fact, user)
        if result:
            mark_promoted(connection, fact, result[0], result[1])
            return result
    if canonical_result:
        mark_promoted(connection, fact, canonical_result[0], canonical_result[1])
        return canonical_result
    return None


def promote_approved_fact_ids(connection, approved_fact_ids, user="system"):
    promoted = []
    skipped = []
    for approved_fact_id in approved_fact_ids:
        row = connection.execute(
            """
            SELECT *
            FROM approved_facts
            WHERE approved_fact_id = ?
              AND promotion_status = 'pending'
            """,
            (approved_fact_id,),
        ).fetchone()
        if row is None:
            continue
        fact = dict(row)
        result = promote_fact(connection, fact, user)
        if result:
            promoted.append((fact["approved_fact_id"], result[0], result[1]))
        else:
            skipped.append((fact["approved_fact_id"], fact["fact_category"], fact["fact_name"], skip_reason(fact)))
    return promoted, skipped


def promote_all_pending(connection, user="system"):
    return promote_approved_fact_ids(
        connection,
        [fact["approved_fact_id"] for fact in unpromoted_approved_facts(connection)],
        user=user,
    )

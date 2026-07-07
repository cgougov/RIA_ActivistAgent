def normalize_fact_value(value):
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    return text.lower() if text else None


def display_fact_value(row):
    return row.get("normalized_value") or row.get("raw_value") or row.get("approved_value")


def find_matching_extracted_fact(
    connection,
    *,
    fund_id,
    fact_category,
    fact_name,
    value,
    statuses=("pending", "approved"),
    exclude_fact_id=None,
):
    normalized = normalize_fact_value(value)
    if not normalized or not statuses:
        return None
    placeholders = ", ".join("?" for _ in statuses)
    params = [fund_id, fact_category, fact_name, *statuses]
    query = f"""
        SELECT fact_id, approval_status, normalized_value, raw_value, source_document_id
        FROM extracted_facts
        WHERE fund_id = ?
          AND fact_category = ?
          AND fact_name = ?
          AND approval_status IN ({placeholders})
    """
    if exclude_fact_id:
        query += " AND fact_id != ?"
        params.append(exclude_fact_id)
    rows = connection.execute(query, params).fetchall()
    for row in rows:
        row = dict(row)
        if normalize_fact_value(display_fact_value(row)) == normalized:
            return row
    return None


def find_matching_approved_fact(
    connection,
    *,
    fund_id,
    fact_category,
    fact_name,
    value,
    exclude_approved_fact_id=None,
):
    normalized = normalize_fact_value(value)
    if not normalized:
        return None
    params = [fund_id, fact_category, fact_name]
    query = """
        SELECT approved_fact_id, source_fact_id, normalized_value, approved_value, source_document_id
        FROM approved_facts
        WHERE fund_id = ?
          AND fact_category = ?
          AND fact_name = ?
    """
    if exclude_approved_fact_id:
        query += " AND approved_fact_id != ?"
        params.append(exclude_approved_fact_id)
    rows = connection.execute(query, params).fetchall()
    for row in rows:
        row = dict(row)
        if normalize_fact_value(display_fact_value(row)) == normalized:
            return row
    return None

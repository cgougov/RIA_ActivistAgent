import json
from uuid import uuid4

from core.db import utc_now


def insert_approved_fact(connection, fund_id, category, name, value, source_document_id, **kwargs):
    approved_fact_id = f"appr_mock_{uuid4().hex}"
    now = utc_now()
    connection.execute(
        """
        INSERT INTO approved_facts (
            approved_fact_id, fund_id, fact_category, fact_name, approved_value,
            normalized_value, unit, as_of_date, source_document_id, page_number,
            quoted_text, structured_payload_json, approved_by, approved_at,
            approval_note, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'mock', ?, 'mock approved data', ?, ?)
        """,
        (
            approved_fact_id,
            fund_id,
            category,
            name,
            str(value),
            kwargs.get("normalized_value", str(value)),
            kwargs.get("unit"),
            kwargs.get("as_of_date"),
            source_document_id,
            kwargs.get("page_number", 1),
            kwargs.get("quoted_text", "Mock sourced value for UI testing."),
            json.dumps(kwargs.get("structured_payload")) if kwargs.get("structured_payload") else None,
            now,
            now,
            now,
        ),
    )
    return approved_fact_id


def load_mock_approved_data(connection):
    rows = [
        ("fund_002", "profile", "domicile", "Cayman Islands", "doc_003", {}),
        ("fund_002", "terms", "management_fee", "1.5", "doc_003", {"unit": "percent"}),
        ("fund_002", "terms", "performance_fee", "20", "doc_003", {"unit": "percent"}),
        ("fund_002", "strategy", "activism_style", "Engagement-focused Japanese equities", "doc_003", {}),
        ("fund_002", "performance", "monthly_return", "1.2", "doc_003", {"unit": "percent", "as_of_date": "2025-09-30", "structured_payload": {"period_type": "monthly", "period_end_date": "2025-09-30", "return_type": "net", "share_class": "USD", "return_value": "1.2", "unit": "percent"}}),
        ("fund_002", "performance", "monthly_return", "-0.4", "doc_003", {"unit": "percent", "as_of_date": "2025-08-31", "structured_payload": {"period_type": "monthly", "period_end_date": "2025-08-31", "return_type": "net", "share_class": "USD", "return_value": "-0.4", "unit": "percent"}}),
        ("fund_002", "performance", "aum", "100", "doc_003", {"unit": "USD million", "as_of_date": "2025-09-30"}),
        ("fund_002", "writeup", "neutral_summary", "The fund is represented as a Japan-focused value-up strategy in the reviewed material.", "doc_003", {}),
        ("fund_002", "flag", "missing_key_terms", "The reviewed page does not fully disclose all subscription and redemption terms.", "doc_003", {}),
        ("fund_003", "profile", "domicile", "Cayman Islands", "doc_005", {}),
        ("fund_003", "terms", "management_fee", "1.0", "doc_005", {"unit": "percent"}),
        ("fund_003", "terms", "performance_fee", "15", "doc_005", {"unit": "percent"}),
        ("fund_003", "strategy", "activism_style", "Public engagement and activist investing in Japanese equities", "doc_005", {}),
        ("fund_003", "performance", "monthly_return", "2.1", "doc_005", {"unit": "percent", "as_of_date": "2026-05-31", "structured_payload": {"period_type": "monthly", "period_end_date": "2026-05-31", "return_type": "net", "share_class": "USD", "return_value": "2.1", "unit": "percent"}}),
        ("fund_003", "performance", "monthly_return", "0.8", "doc_005", {"unit": "percent", "as_of_date": "2026-04-30", "structured_payload": {"period_type": "monthly", "period_end_date": "2026-04-30", "return_type": "net", "share_class": "USD", "return_value": "0.8", "unit": "percent"}}),
        ("fund_003", "performance", "aum", "250", "doc_005", {"unit": "USD million", "as_of_date": "2026-05-31"}),
        ("fund_003", "writeup", "neutral_summary", "The fund is represented as a Japan activist strategy with source-backed monthly reporting.", "doc_005", {}),
        ("fund_003", "flag", "unclear_aum_basis", "The reviewed data should be checked for whether AUM is fund-level or firm-level.", "doc_005", {}),
    ]
    approved_ids = [
        insert_approved_fact(connection, fund_id, category, name, value, document_id, **kwargs)
        for fund_id, category, name, value, document_id, kwargs in rows
    ]
    return approved_ids

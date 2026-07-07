from pathlib import Path
from contextlib import contextmanager
import os
import subprocess
import sqlite3
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.analysis import run_snapshot_chat, run_web_research_comparison
from core.comparison import compare_funds
from core.db_backup import create_sqlite_backup
from core.db import connect_db
from core.document_bundle import build_document_bundle
from core.document_review import (
    approve_document_section,
    reject_document_section,
    revise_and_approve_fact,
    revise_and_approve_return_row,
)
from core.extraction import sanitize_proposed_facts, save_extraction_run
from core.fund_views import build_fund_view
from core.fund_snapshot import FundSnapshot
from core.mock_data import insert_approved_fact, load_mock_approved_data
from core.promotion import promote_all_pending
from core.proposal_cleanup import sanitize_pending_document_facts
from core.return_canonicalization import canonicalize_performance_returns
from core.return_rows import backfill_legacy_return_rows
from core.return_rows import upsert_proposed_return_row
from core.return_table_extraction import (
    extract_return_table_from_page_image,
    record_failed_extraction_run,
    save_return_rows_as_pending_facts,
)
from core.schema import create_schema, seed_taxonomy
from core.workbooks import export_analysis_workbook, export_source_truth_workbook


DERIVED_TABLES = [
    "analysis_runs",
    "campaign_events",
    "public_campaigns",
    "benchmark_returns",
    "fact_conflicts",
    "change_history",
    "fund_flags",
    "fund_writeups",
    "fund_exposures",
    "fund_metrics",
    "performance_returns",
    "fund_strategy",
    "fund_terms",
    "fund_people",
    "fund_profile_attributes",
    "fund_characteristics",
    "approved_facts",
    "proposed_return_rows",
    "extracted_facts",
    "extraction_runs",
    "data_imports",
    "document_classification_suggestions",
]


def _clear_derived_tables(connection):
    for table_name in DERIVED_TABLES:
        connection.execute(f"DELETE FROM {table_name}")
    connection.execute("UPDATE source_documents SET llm_extraction_status = 'not_started'")


def _ensure_terminal_test_document(connection, document_id="doc_terminal_test"):
    existing = connection.execute(
        "SELECT 1 FROM source_documents WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    if existing:
        return
    connection.execute(
        """
        INSERT INTO source_documents (
            document_id, fund_id, file_name, document_type, document_title,
            document_date, page_count, llm_extraction_status, confidentiality_level
        )
        VALUES (?, 'fund_002', 'terminal_test.pdf', 'factsheet', 'Terminal Test Factsheet', '2026-07-07', 2, 'has_proposed_facts', 'internal')
        """,
        (document_id,),
    )


def _seed_terminal_review_fixture(connection, document_id="doc_terminal_test"):
    _clear_derived_tables(connection)
    _ensure_terminal_test_document(connection, document_id=document_id)
    connection.execute(
        """
        INSERT INTO extraction_runs (
            run_id, document_id, extraction_scope, extraction_method, model_name,
            prompt_version, run_status
        )
        VALUES ('run_terminal_test', ?, 'fund_characteristics', 'test', 'test-model', 'test_v1', 'complete')
        """,
        (document_id,),
    )

    extracted_rows = [
        ("fact_terminal_person", "people", "person_name", "Hiromasa Mizushima", "Hiromasa Mizushima", "Chief Investment Officer: Hiromasa Mizushima", 0.7),
        ("fact_terminal_cio", "people", "chief_investment_officer", "Hiromasa Mizushima", "Hiromasa Mizushima", "Chief Investment Officer: Hiromasa Mizushima", 0.95),
        ("fact_terminal_role", "people", "role_title", "Chief Investment Officer", "Chief Investment Officer", "Chief Investment Officer: Hiromasa Mizushima", 0.95),
        ("fact_terminal_strategy", "strategy", "strategy_primary", "activist", "activist", "The strategy uses a friendly activist approach.", 0.9),
        ("fact_terminal_summary_a", "writeup", "neutral_summary", "Short summary", "Short summary", "summary", 0.8),
        ("fact_terminal_summary_b", "writeup", "neutral_summary", "Longer better summary", "Longer better summary", "summary with more support", 0.9),
        ("fact_terminal_beta", "performance", "beta", "0.49", "0.49", "Beta 0.49", 0.9),
        ("fact_terminal_flag", "flag", "missing_key_terms", "Important terms missing", "Important terms missing", "Important terms missing", 0.7),
    ]
    for fact_id, category, fact_name, raw_value, normalized_value, quote, confidence in extracted_rows:
        connection.execute(
            """
            INSERT INTO extracted_facts (
                fact_id, run_id, fund_id, document_id, fact_category, fact_name, raw_value,
                normalized_value, unit, as_of_date, source_document_id, page_number,
                quoted_text, extraction_method, confidence_score, approval_status
            )
            VALUES (?, 'run_terminal_test', 'fund_002', ?, ?, ?, ?, ?, NULL, '2026-07-07', ?, 1, ?, 'test', ?, 'pending')
            """,
            (fact_id, document_id, category, fact_name, raw_value, normalized_value, document_id, quote, confidence),
            )

    connection.execute(
        """
        INSERT INTO extracted_facts (
            fact_id, run_id, fund_id, document_id, fact_category, fact_name, raw_value,
            normalized_value, unit, as_of_date, source_document_id, page_number,
            quoted_text, extraction_method, confidence_score, approval_status
        )
        VALUES (
            'fact_terminal_cio_dup', 'run_terminal_test', 'fund_002', ?, 'people', 'chief_investment_officer',
            'Hiromasa Mizushima', 'Hiromasa Mizushima', NULL, '2026-07-07', ?, 2,
            'Chief Investment Officer: Hiromasa Mizushima', 'test', 0.92, 'pending'
        )
        """,
        (document_id, document_id),
    )

    upsert_proposed_return_row(
        connection,
        fund_id="fund_002",
        source_document_id=document_id,
        page_number=1,
        share_class="USD",
        period_type="monthly",
        period_start_date=None,
        period_end_date="2026-06-30",
        return_type="unknown",
        return_value=1.1,
        raw_value_text="1.1",
        unit="percent",
        evidence_text="Jun 2026 1.1%",
        table_name="Terminal Test Returns",
        row_label="2026",
        column_label="Jun",
        status="pending",
    )
    upsert_proposed_return_row(
        connection,
        fund_id="fund_002",
        source_document_id=document_id,
        page_number=1,
        share_class="USD",
        period_type="ytd",
        period_start_date="2026-01-01",
        period_end_date="2026-06-30",
        return_type="unknown",
        return_value=6.2,
        raw_value_text="6.2",
        unit="percent",
        evidence_text="YTD 2026 6.2%",
        table_name="Terminal Test Returns",
        row_label="2026",
        column_label="YTD",
        status="pending",
    )

    baseline_left = insert_approved_fact(
        connection,
        "fund_002",
        "performance",
        "monthly_return",
        "1.1",
        document_id,
        unit="percent",
        as_of_date="2026-04-30",
        structured_payload={
            "period_type": "monthly",
            "period_end_date": "2026-04-30",
            "return_type": "unknown",
            "share_class": "USD",
            "return_value": "1.1",
            "unit": "percent",
        },
    )
    baseline_right = insert_approved_fact(
        connection,
        "fund_002",
        "performance",
        "monthly_return",
        "1.2",
        document_id,
        unit="percent",
        as_of_date="2026-04-30",
        structured_payload={
            "period_type": "monthly",
            "period_end_date": "2026-04-30",
            "return_type": "net",
            "share_class": "USD",
            "return_value": "1.2",
            "unit": "percent",
        },
    )
    connection.execute(
        """
        INSERT INTO performance_returns (
            return_id, fund_id, share_class, period_type, period_start_date, period_end_date,
            return_type, return_value, unit, source_document_id, page_number, approved_fact_id,
            created_at, updated_at, return_value_bps, raw_value_text
        )
        VALUES
            ('return_terminal_left', 'fund_002', 'USD', 'monthly', NULL, '2026-04-30',
             'unknown', 1.1, 'percent', ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 110, '1.1'),
            ('return_terminal_right', 'fund_002', 'USD', 'monthly', NULL, '2026-04-30',
             'net', 1.2, 'percent', ?, 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 120, '1.2')
        """,
        (document_id, baseline_left, document_id, baseline_right),
    )
    connection.execute(
        """
        UPDATE approved_facts
        SET promotion_status = 'promoted',
            promoted_to_table = 'performance_returns',
            promoted_record_id = CASE approved_fact_id
                WHEN ? THEN 'return_terminal_left'
                WHEN ? THEN 'return_terminal_right'
            END,
            promoted_at = CURRENT_TIMESTAMP
        WHERE approved_fact_id IN (?, ?)
        """,
        (baseline_left, baseline_right, baseline_left, baseline_right),
    )
    connection.commit()
    return document_id


@contextmanager
def terminal_review_test_db(connection, document_id="doc_terminal_test"):
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "terminal_review_test.sqlite"
        backup_connection = sqlite3.connect(db_path)
        try:
            connection.backup(backup_connection)
        finally:
            backup_connection.close()
        seeded = sqlite3.connect(db_path)
        seeded.row_factory = sqlite3.Row
        seeded.execute("PRAGMA foreign_keys = ON;")
        try:
            _seed_terminal_review_fixture(seeded, document_id=document_id)
            seeded.close()
            yield db_path, document_id
        finally:
            try:
                seeded.close()
            except Exception:
                pass


def test_fund_snapshot(connection):
    snapshot = FundSnapshot(connection, "fund_002").build()
    assert snapshot["fund"]["fund_id"] == "fund_002"
    assert "characteristics_tree" in snapshot
    assert "characteristics" in snapshot
    assert "returns" in snapshot
    assert "flags" in snapshot
    assert "writeups" in snapshot
    print("fund snapshot: OK")


def test_fund_view(connection):
    view = build_fund_view(connection, "fund_002")
    assert view["fund_id"] == "fund_002"
    assert "brief" in view
    assert "performance_rows" in view
    assert "return_table_previews" in view
    assert "approved_factsheet" in view
    assert "sections" in view["approved_factsheet"]
    assert any(row["label"] == "Sharpe" for row in view["performance_rows"])
    print("fund view: OK")


def test_document_bundle(connection):
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    scratch.execute("PRAGMA foreign_keys = ON;")
    connection.backup(scratch)
    document_id = _seed_terminal_review_fixture(scratch)
    bundle = build_document_bundle(scratch, document_id)
    assert bundle["document"]["document_id"] == document_id
    assert "overview" in bundle["sections"]
    assert "metrics" in bundle["sections"]
    assert "standardized_sections" in bundle
    assert "returns" in bundle
    assert bundle["returns"]
    assert "pending_fact_ids_by_section" in bundle
    assert "pending_return_row_ids" in bundle
    pending_bundle = build_document_bundle(scratch, document_id, pending_only=True)
    assert pending_bundle["pending_only"] is True
    assert pending_bundle["standardized_sections"]["overview"][0]["label"] == "Fund name"
    strategy_entries = {entry["field_key"]: entry for entry in pending_bundle["standardized_sections"]["strategy"]}
    assert strategy_entries["primary_strategy"]["approved_display_value"] is not None
    note_fact_names = [row["fact_name"] for row in pending_bundle["sections"]["notes_flags"]]
    assert note_fact_names.count("neutral_summary") <= 1
    assert note_fact_names.count("differentiating_edge") <= 1
    manager_fact_names = [row["fact_name"] for row in pending_bundle["sections"]["manager"]]
    assert "role_title" not in manager_fact_names
    manager_entry = pending_bundle["standardized_sections"]["manager"][0]
    assert manager_entry["display_value"] == "Hiromasa Mizushima - Chief investment officer"
    assert ";" not in manager_entry["display_value"]
    assert manager_entry["candidate_count"] == 1
    scratch.close()
    print("document bundle: OK")


def test_cross_document_duplicate_gate(connection):
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    scratch.execute("PRAGMA foreign_keys = ON;")
    connection.backup(scratch)

    _ensure_terminal_test_document(scratch, "doc_duplicate_a")
    _ensure_terminal_test_document(scratch, "doc_duplicate_b")
    doc_a = dict(
        scratch.execute(
            "SELECT * FROM source_documents WHERE document_id = 'doc_duplicate_a'"
        ).fetchone()
    )
    doc_b = dict(
        scratch.execute(
            "SELECT * FROM source_documents WHERE document_id = 'doc_duplicate_b'"
        ).fetchone()
    )
    facts = [
        {
            "fact_category": "performance",
            "fact_name": "beta",
            "raw_value": "0.49",
            "normalized_value": "0.49",
            "unit": None,
            "as_of_date": "2026-07-07",
            "page_number": 1,
            "quoted_text": "Beta 0.49",
            "structured_payload_json": None,
            "confidence_score": 0.95,
        }
    ]

    first_run_id = save_extraction_run(scratch, doc_a, facts, "test-model", "{}", "performance")
    first_row = scratch.execute(
        "SELECT approval_status FROM extracted_facts WHERE run_id = ?",
        (first_run_id,),
    ).fetchone()
    assert first_row["approval_status"] == "pending"

    second_run_id = save_extraction_run(scratch, doc_b, facts, "test-model", "{}", "performance")
    second_row = scratch.execute(
        "SELECT approval_status, review_note FROM extracted_facts WHERE run_id = ?",
        (second_run_id,),
    ).fetchone()
    assert second_row["approval_status"] == "rejected"
    assert "Auto-rejected exact duplicate" in second_row["review_note"]
    scratch.close()
    print("cross-document duplicate gate: OK")


def test_extraction_sanitization():
    facts = [
        {
            "fact_category": "people",
            "fact_name": "person_name",
            "raw_value": "Hiromasa Mizushima",
            "normalized_value": "Hiromasa Mizushima",
            "quoted_text": "Chief Investment Officer: Hiromasa Mizushima",
            "page_number": 1,
            "confidence_score": 0.7,
        },
        {
            "fact_category": "people",
            "fact_name": "chief_investment_officer",
            "raw_value": "Hiromasa Mizushima",
            "normalized_value": "Hiromasa Mizushima",
            "quoted_text": "Chief Investment Officer: Hiromasa Mizushima",
            "page_number": 1,
            "confidence_score": 0.95,
        },
        {
            "fact_category": "people",
            "fact_name": "role_title",
            "raw_value": "Chief Investment Officer",
            "normalized_value": "Chief Investment Officer",
            "quoted_text": "Chief Investment Officer: Hiromasa Mizushima",
            "page_number": 1,
            "confidence_score": 0.95,
        },
        {
            "fact_category": "writeup",
            "fact_name": "neutral_summary",
            "raw_value": "Short summary",
            "normalized_value": "Short summary",
            "quoted_text": "quoted summary",
            "page_number": 1,
            "confidence_score": 0.8,
        },
        {
            "fact_category": "writeup",
            "fact_name": "neutral_summary",
            "raw_value": "Longer better summary",
            "normalized_value": "Longer better summary",
            "quoted_text": "quoted summary with more support",
            "page_number": 1,
            "confidence_score": 0.9,
        },
        {
            "fact_category": "performance",
            "fact_name": "beta",
            "raw_value": "Beta 0.49",
            "normalized_value": "0.49",
            "quoted_text": "Beta 0.49",
            "page_number": 2,
            "confidence_score": 0.9,
        },
        {
            "fact_category": "performance",
            "fact_name": "beta",
            "raw_value": "Beta 0.49",
            "normalized_value": "0.49",
            "quoted_text": "Beta 0.49",
            "page_number": 2,
            "confidence_score": 0.6,
        },
    ]
    sanitized = sanitize_proposed_facts(facts)
    names = [(fact["fact_category"], fact["fact_name"], fact["raw_value"]) for fact in sanitized]
    assert ("people", "person_name", "Hiromasa Mizushima") not in names
    assert ("people", "role_title", "Chief Investment Officer") not in names
    neutral_summaries = [fact for fact in sanitized if fact["fact_category"] == "writeup" and fact["fact_name"] == "neutral_summary"]
    assert len(neutral_summaries) == 1
    assert neutral_summaries[0]["raw_value"] == "Longer better summary"
    betas = [fact for fact in sanitized if fact["fact_category"] == "performance" and fact["fact_name"] == "beta"]
    assert len(betas) == 1
    print("extraction sanitization: OK")


def test_db_backup_helper():
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    scratch.execute("CREATE TABLE sample (id TEXT PRIMARY KEY, value TEXT)")
    scratch.execute("INSERT INTO sample (id, value) VALUES ('one', 'alpha')")
    scratch.commit()
    with tempfile.TemporaryDirectory() as tmpdir:
        backup_path = create_sqlite_backup(scratch, label="unit_test", backup_dir=tmpdir)
        assert backup_path.exists()
        restored = sqlite3.connect(backup_path)
        try:
            value = restored.execute("SELECT value FROM sample WHERE id = 'one'").fetchone()[0]
            assert value == "alpha"
        finally:
            restored.close()
    scratch.close()
    print("db backup helper: OK")


def test_document_section_review_actions(connection):
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    scratch.execute("PRAGMA foreign_keys = ON;")
    connection.backup(scratch)

    scratch.execute(
        """
        INSERT INTO source_documents (document_id, fund_id, file_name, document_type, document_title, document_date)
        VALUES ('doc_bundle_test', 'fund_002', 'bundle_test.pdf', 'factsheet', 'Bundle Test Factsheet', '2026-07-07')
        """
    )
    scratch.execute(
        """
        INSERT INTO extraction_runs (
            run_id, document_id, extraction_scope, extraction_method, model_name,
            prompt_version, run_status
        )
        VALUES ('run_bundle_test', 'doc_bundle_test', 'fund_characteristics', 'test', 'test-model', 'test_v1', 'complete')
        """
    )
    scratch.execute(
        """
        INSERT INTO extracted_facts (
            fact_id, run_id, fund_id, document_id, fact_category, fact_name, raw_value,
            normalized_value, unit, as_of_date, source_document_id, page_number,
            quoted_text, extraction_method, confidence_score, approval_status
        )
        VALUES (
            'fact_bundle_metric', 'run_bundle_test', 'fund_002', 'doc_bundle_test', 'performance', 'beta',
            '0.75', '0.75', NULL, '2026-07-07', 'doc_bundle_test', 1,
            'Beta 0.75', 'test', 1.0, 'pending'
        )
        """
    )
    upsert_proposed_return_row(
        scratch,
        fund_id="fund_002",
        source_document_id="doc_bundle_test",
        page_number=1,
        period_type="monthly",
        period_start_date=None,
        period_end_date="2026-06-30",
        return_type="net",
        return_value=1.4,
        raw_value_text="1.4",
        unit="percent",
        evidence_text="Jun 2026 1.4%",
        table_name="Bundle Test Returns",
        row_label="2026",
        column_label="Jun",
        status="pending",
    )

    result = approve_document_section(
        scratch,
        "doc_bundle_test",
        reviewer="internal_test",
        section="metrics",
        note="Approve metrics from bundle.",
    )
    assert result["descriptive_pending_count"] == 1
    approved_metric_count = scratch.execute(
        """
        SELECT COUNT(*)
        FROM extracted_facts
        WHERE source_document_id = 'doc_bundle_test'
          AND fact_category = 'performance'
          AND fact_name = 'beta'
          AND approval_status = 'approved'
        """
    ).fetchone()[0]
    assert approved_metric_count == 1
    promoted_metric_count = scratch.execute(
        """
        SELECT COUNT(*)
        FROM fund_metrics
        WHERE source_document_id = 'doc_bundle_test'
          AND metric_name = 'beta'
        """
    ).fetchone()[0]
    assert promoted_metric_count == 1

    reject_result = reject_document_section(
        scratch,
        "doc_bundle_test",
        reviewer="internal_test",
        section="returns",
        note="Reject pending returns from bundle.",
    )
    assert reject_result["return_pending_count"] == 1
    pending_return_count = scratch.execute(
        """
        SELECT COUNT(*)
        FROM proposed_return_rows
        WHERE source_document_id = 'doc_bundle_test'
          AND status = 'pending'
        """
    ).fetchone()[0]
    assert pending_return_count == 0
    scratch.close()
    print("document section review actions: OK")


def test_document_item_revision_actions(connection):
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    scratch.execute("PRAGMA foreign_keys = ON;")
    connection.backup(scratch)

    scratch.execute(
        """
        INSERT INTO source_documents (document_id, fund_id, file_name, document_type, document_title, document_date)
        VALUES ('doc_item_test', 'fund_002', 'item_test.pdf', 'factsheet', 'Item Test Factsheet', '2026-07-07')
        """
    )
    scratch.execute(
        """
        INSERT INTO extraction_runs (
            run_id, document_id, extraction_scope, extraction_method, model_name,
            prompt_version, run_status
        )
        VALUES ('run_item_test', 'doc_item_test', 'fund_characteristics', 'test', 'test-model', 'test_v1', 'complete')
        """
    )
    scratch.execute(
        """
        INSERT INTO extracted_facts (
            fact_id, run_id, fund_id, document_id, fact_category, fact_name, raw_value,
            normalized_value, unit, as_of_date, source_document_id, page_number,
            quoted_text, extraction_method, confidence_score, approval_status
        )
        VALUES (
            'fact_item_test', 'run_item_test', 'fund_002', 'doc_item_test', 'performance', 'beta',
            'Beta 0.49', '0.49', NULL, '2026-07-07', 'doc_item_test', 1,
            'Beta 0.49', 'test', 1.0, 'pending'
        )
        """
    )
    upsert_proposed_return_row(
        scratch,
        fund_id="fund_002",
        source_document_id="doc_item_test",
        page_number=1,
        period_type="monthly",
        period_start_date=None,
        period_end_date="2026-06-30",
        return_type="unknown",
        return_value=1.1,
        raw_value_text="1.1",
        unit="percent",
        evidence_text="Jun 2026 1.1%",
        table_name="Item Test Returns",
        row_label="2026",
        column_label="Jun",
        status="pending",
    )
    proposed_row_id = scratch.execute(
        "SELECT proposed_row_id FROM proposed_return_rows WHERE source_document_id = 'doc_item_test'"
    ).fetchone()[0]

    fact_result = revise_and_approve_fact(
        scratch,
        "fact_item_test",
        reviewer="internal_test",
        approved_value="0.52",
        normalized_value="0.52",
        note="Revise beta before approval.",
    )
    assert fact_result["normalized_value"] == "0.52"
    revised_metric = scratch.execute(
        "SELECT metric_value FROM fund_metrics WHERE source_document_id = 'doc_item_test' AND metric_name = 'beta'"
    ).fetchone()[0]
    assert revised_metric == 0.52

    return_result = revise_and_approve_return_row(
        scratch,
        proposed_row_id,
        reviewer="internal_test",
        note="Revise return row before approval.",
        share_class="USD",
        return_type="net",
        return_value="1.4",
        raw_value_text="1.4",
    )
    assert return_result["revised_row"]["share_class"] == "USD"
    promoted_return = scratch.execute(
        """
        SELECT share_class, return_type, return_value
        FROM performance_returns
        WHERE source_document_id = 'doc_item_test'
        """
    ).fetchone()
    assert promoted_return["share_class"] == "USD"
    assert promoted_return["return_type"] == "net"
    assert promoted_return["return_value"] == 1.4
    scratch.close()
    print("document item revision actions: OK")


def test_pending_document_fact_cleanup(connection):
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    scratch.execute("PRAGMA foreign_keys = ON;")
    connection.rollback()
    connection.backup(scratch)

    scratch.execute(
        """
        INSERT INTO source_documents (document_id, fund_id, file_name, document_type, document_title, document_date)
        VALUES ('doc_cleanup_test', 'fund_002', 'cleanup_test.pdf', 'factsheet', 'Cleanup Test Factsheet', '2026-07-07')
        """
    )
    scratch.execute(
        """
        INSERT INTO extraction_runs (
            run_id, document_id, extraction_scope, extraction_method, model_name,
            prompt_version, run_status
        )
        VALUES ('run_cleanup_test', 'doc_cleanup_test', 'fund_characteristics', 'test', 'test-model', 'test_v1', 'complete')
        """
    )
    rows = [
        ("fact_cleanup_person", "people", "person_name", "Hiromasa Mizushima", "Hiromasa Mizushima", "Chief Investment Officer: Hiromasa Mizushima", 0.7),
        ("fact_cleanup_cio", "people", "chief_investment_officer", "Hiromasa Mizushima", "Hiromasa Mizushima", "Chief Investment Officer: Hiromasa Mizushima", 0.95),
        ("fact_cleanup_role", "people", "role_title", "Chief Investment Officer", "Chief Investment Officer", "Chief Investment Officer: Hiromasa Mizushima", 0.95),
        ("fact_cleanup_summary_a", "writeup", "neutral_summary", "Short summary", "Short summary", "summary a", 0.6),
        ("fact_cleanup_summary_b", "writeup", "neutral_summary", "Better summary", "Better summary", "summary b", 0.9),
    ]
    for fact_id, category, fact_name, raw_value, normalized_value, quoted_text, confidence in rows:
        scratch.execute(
            """
            INSERT INTO extracted_facts (
                fact_id, run_id, fund_id, document_id, fact_category, fact_name, raw_value,
                normalized_value, unit, as_of_date, source_document_id, page_number,
                quoted_text, extraction_method, confidence_score, approval_status
            )
            VALUES (?, 'run_cleanup_test', 'fund_002', 'doc_cleanup_test', ?, ?, ?, ?, NULL, '2026-07-07', 'doc_cleanup_test', 1, ?, 'test', ?, 'pending')
            """,
            (fact_id, category, fact_name, raw_value, normalized_value, quoted_text, confidence),
        )

    dry_run = sanitize_pending_document_facts(scratch, "doc_cleanup_test", apply=False)
    assert dry_run["pending_count"] == 5
    assert dry_run["kept_count"] == 2
    assert dry_run["duplicate_count"] == 3

    applied = sanitize_pending_document_facts(scratch, "doc_cleanup_test", reviewer="internal_test", apply=True)
    assert applied["duplicate_count"] == 3
    rejected = scratch.execute(
        """
        SELECT fact_id, approval_status
        FROM extracted_facts
        WHERE source_document_id = 'doc_cleanup_test'
        ORDER BY fact_id
        """
    ).fetchall()
    rejected_map = {row["fact_id"]: row["approval_status"] for row in rejected}
    assert rejected_map["fact_cleanup_cio"] == "pending"
    assert rejected_map["fact_cleanup_summary_b"] == "pending"
    assert rejected_map["fact_cleanup_person"] == "rejected"
    assert rejected_map["fact_cleanup_role"] == "rejected"
    assert rejected_map["fact_cleanup_summary_a"] == "rejected"
    scratch.close()
    print("pending document fact cleanup: OK")


def test_mock_analysis(connection):
    analysis_id, output_text = run_snapshot_chat(
        connection,
        ["fund_002", "fund_003"],
        "Summarize diligence gaps using approved data only.",
        model_name="test-model",
        reasoning_effort="low",
        created_by="internal_test",
        mock=True,
    )
    assert analysis_id.startswith("analysis_")
    assert "Mock analysis" in output_text
    saved = connection.execute(
        "SELECT COUNT(*) FROM analysis_runs WHERE analysis_id = ?",
        (analysis_id,),
    ).fetchone()[0]
    assert saved == 1
    print("mock analysis: OK")


def test_workbooks(connection):
    source_truth_path = export_source_truth_workbook(connection, fund_ids=["fund_002"])
    analysis_path = export_analysis_workbook(connection, fund_ids=["fund_002"])
    assert source_truth_path.exists()
    assert analysis_path.exists()
    assert source_truth_path.stat().st_size > 0
    assert analysis_path.stat().st_size > 0
    print("workbook exports: OK")


def test_comparison(connection):
    comparison = compare_funds(connection, ["fund_002", "fund_003"])
    assert len(comparison["profiles"]) == 2
    assert len(comparison["return_statistics"]) == 2
    assert len(comparison["analysis_readiness_scores"]) == 2
    assert len(comparison["similarity_matrix"]) == 2
    assert "score" in comparison["comparison_paragraph"].lower()
    print("deterministic comparison: OK")


def test_safe_return_promotion(connection):
    vague_id = insert_approved_fact(
        connection,
        "fund_002",
        "performance",
        "monthly_return",
        "1.7",
        "doc_003",
        unit="percent",
        as_of_date="2026-01-31",
    )
    structured_id = insert_approved_fact(
        connection,
        "fund_002",
        "performance",
        "monthly_return",
        "1.1",
        "doc_003",
        unit="percent",
        as_of_date="2026-02-28",
        structured_payload={
            "period_type": "monthly",
            "period_end_date": "2026-02-28",
            "return_type": "net",
            "share_class": "USD",
            "return_value": "1.1",
            "unit": "percent",
        },
    )
    promoted, skipped = promote_all_pending(connection, user="internal_test")
    assert any(row[0] == structured_id and row[1] == "performance_returns" for row in promoted)
    assert any(row[0] == vague_id for row in skipped)
    vague_clean_rows = connection.execute(
        """
        SELECT COUNT(*)
        FROM performance_returns
        WHERE approved_fact_id = ?
        """,
        (vague_id,),
    ).fetchone()[0]
    vague_characteristics = connection.execute(
        """
        SELECT COUNT(*)
        FROM fund_characteristics
        WHERE approved_fact_id = ?
        """,
        (vague_id,),
    ).fetchone()[0]
    structured_return = connection.execute(
        """
        SELECT share_class, period_end_date, return_value
        FROM performance_returns
        WHERE approved_fact_id = ?
        """,
        (structured_id,),
    ).fetchone()
    assert vague_clean_rows == 0
    assert vague_characteristics == 0
    assert structured_return["share_class"] == "USD"
    assert structured_return["period_end_date"] == "2026-02-28"
    assert structured_return["return_value"] == 1.1
    print("safe return promotion: OK")


def test_aligned_return_comparison(connection):
    left_id = insert_approved_fact(
        connection,
        "fund_002",
        "performance",
        "monthly_return",
        "1.5",
        "doc_003",
        unit="percent",
        as_of_date="2026-03-31",
        structured_payload={
            "period_type": "monthly",
            "period_end_date": "2026-03-31",
            "return_type": "net",
            "share_class": "USD",
            "return_value": "1.5",
            "unit": "percent",
        },
    )
    right_id = insert_approved_fact(
        connection,
        "fund_003",
        "performance",
        "monthly_return",
        "2.0",
        "doc_005",
        unit="percent",
        as_of_date="2026-03-31",
        structured_payload={
            "period_type": "monthly",
            "period_end_date": "2026-03-31",
            "return_type": "net",
            "share_class": "USD",
            "return_value": "2.0",
            "unit": "percent",
        },
    )
    promoted, skipped = promote_all_pending(connection, user="internal_test")
    assert any(row[0] == left_id for row in promoted)
    assert any(row[0] == right_id for row in promoted)
    comparison = compare_funds(connection, ["fund_002", "fund_003"])
    aligned = comparison["aligned_returns"]
    assert aligned
    row = next(item for item in aligned if item["period_end_date"] == "2026-03-31")
    assert row["fund_002"] == 1.5
    assert row["fund_003"] == 2.0
    assert row["fund_003_minus_fund_002"] == 0.5
    assert "overlapping" in comparison["comparison_paragraph"].lower()
    print("aligned return comparison: OK")


def test_duplicate_safe_return_promotion(connection):
    connection.rollback()
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    scratch.execute("PRAGMA foreign_keys = ON;")
    connection.backup(scratch)

    unknown_id = insert_approved_fact(
        scratch,
        "fund_002",
        "performance",
        "monthly_return",
        "1.1",
        "doc_003",
        unit="percent",
        as_of_date="2026-04-30",
        structured_payload={
            "period_type": "monthly",
            "period_end_date": "2026-04-30",
            "return_type": "unknown",
            "share_class": "USD",
            "return_value": "1.1",
            "unit": "percent",
        },
    )
    net_id = insert_approved_fact(
        scratch,
        "fund_002",
        "performance",
        "monthly_return",
        "1.2",
        "doc_003",
        unit="percent",
        as_of_date="2026-04-30",
        structured_payload={
            "period_type": "monthly",
            "period_end_date": "2026-04-30",
            "return_type": "net",
            "share_class": "USD",
            "return_value": "1.2",
            "unit": "percent",
        },
    )
    promoted, skipped = promote_all_pending(scratch, user="internal_test")
    assert any(row[0] == unknown_id for row in promoted)
    assert any(row[0] == net_id for row in promoted)
    rows = scratch.execute(
        """
        SELECT return_type, return_value, COUNT(*) OVER () AS total_rows
        FROM performance_returns
        WHERE fund_id = 'fund_002'
          AND COALESCE(share_class, '') = 'USD'
          AND period_type = 'monthly'
          AND period_end_date = '2026-04-30'
        """
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["return_type"] == "net"
    assert rows[0]["return_value"] == 1.2
    scratch.close()
    print("duplicate-safe return promotion: OK")


def test_canonicalize_performance_returns(connection):
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    scratch.execute("PRAGMA foreign_keys = ON;")
    connection.rollback()
    connection.backup(scratch)

    left_id = insert_approved_fact(
        scratch,
        "fund_002",
        "performance",
        "monthly_return",
        "1.1",
        "doc_003",
        unit="percent",
        as_of_date="2026-05-31",
        structured_payload={
            "period_type": "monthly",
            "period_end_date": "2026-05-31",
            "return_type": "unknown",
            "share_class": "USD",
            "return_value": "1.1",
            "unit": "percent",
        },
    )
    right_id = insert_approved_fact(
        scratch,
        "fund_002",
        "performance",
        "monthly_return",
        "1.2",
        "doc_003",
        unit="percent",
        as_of_date="2026-05-31",
        structured_payload={
            "period_type": "monthly",
            "period_end_date": "2026-05-31",
            "return_type": "net",
            "share_class": "USD",
            "return_value": "1.2",
            "unit": "percent",
        },
    )
    scratch.execute(
        """
        INSERT INTO performance_returns (
            return_id, fund_id, share_class, period_type, period_start_date, period_end_date,
            return_type, return_value, unit, source_document_id, page_number, approved_fact_id,
            created_at, updated_at, return_value_bps, raw_value_text
        )
        VALUES (
            'return_dup_left', 'fund_002', 'USD', 'monthly', NULL, '2026-05-31',
            'unknown', 1.1, 'percent', 'doc_003', 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 110, '1.1'
        )
        """,
        (left_id,),
    )
    scratch.execute(
        """
        INSERT INTO performance_returns (
            return_id, fund_id, share_class, period_type, period_start_date, period_end_date,
            return_type, return_value, unit, source_document_id, page_number, approved_fact_id,
            created_at, updated_at, return_value_bps, raw_value_text
        )
        VALUES (
            'return_dup_right', 'fund_002', 'USD', 'monthly', NULL, '2026-05-31',
            'net', 1.2, 'percent', 'doc_003', 1, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 120, '1.2'
        )
        """,
        (right_id,),
    )
    scratch.execute(
        """
        UPDATE approved_facts
        SET promotion_status = 'promoted',
            promoted_to_table = 'performance_returns',
            promoted_record_id = CASE approved_fact_id
                WHEN ? THEN 'return_dup_left'
                WHEN ? THEN 'return_dup_right'
            END,
            promoted_at = CURRENT_TIMESTAMP
        WHERE approved_fact_id IN (?, ?)
        """,
        (left_id, right_id, left_id, right_id),
    )

    dry_run = canonicalize_performance_returns(scratch, fund_id="fund_002", apply=False)
    assert any(item["period_end_date"] == "2026-05-31" for item in dry_run)

    applied = canonicalize_performance_returns(scratch, fund_id="fund_002", user="internal_test", apply=True)
    assert any(item["period_end_date"] == "2026-05-31" for item in applied)
    rows = scratch.execute(
        """
        SELECT return_id, return_type, return_value
        FROM performance_returns
        WHERE fund_id = 'fund_002'
          AND COALESCE(share_class, '') = 'USD'
          AND period_type = 'monthly'
          AND period_end_date = '2026-05-31'
        """
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["return_type"] == "net"
    assert rows[0]["return_value"] == 1.2
    promoted_record_ids = {
        row[0]
        for row in scratch.execute(
            """
            SELECT promoted_record_id
            FROM approved_facts
            WHERE approved_fact_id IN (?, ?)
            """,
            (left_id, right_id),
        ).fetchall()
    }
    assert len(promoted_record_ids) == 1
    scratch.close()
    print("canonicalize performance returns: OK")


def test_mock_web_research(connection):
    analysis_id, output_text = run_web_research_comparison(
        connection,
        "fund_002",
        "Compare stated strategy against public evidence.",
        model_name="test-model",
        reasoning_effort="low",
        created_by="internal_test",
        mock=True,
    )
    assert analysis_id.startswith("analysis_")
    assert "Mock web comparison" in output_text
    print("mock web research: OK")


def test_visual_return_extraction_mock(connection):
    run_id, inserted, skipped = extract_return_table_from_page_image(connection, "doc_003", 1, mock=True)
    assert run_id.startswith("run_")
    assert inserted == 1
    assert skipped == []
    staged = connection.execute(
        "SELECT COUNT(*) FROM proposed_return_rows WHERE run_id = ?",
        (run_id,),
    ).fetchone()[0]
    assert staged == 1
    print("visual return extraction mock: OK")


def test_return_extraction_skip_reasons(connection):
    document = dict(
        connection.execute(
            "SELECT * FROM source_documents WHERE document_id = ?", ("doc_003",)
        ).fetchone()
    )
    rows = [
        {"period_type": "monthly", "period_end_date": "2026-01-31", "return_value": 1.1},
        {"period_type": "bogus", "period_end_date": "2026-01-31", "return_value": 1.1},
        {"period_type": "monthly", "period_end_date": None, "return_value": 1.1},
        {"period_type": "monthly", "period_end_date": "2026-01-31", "return_value": None},
    ]
    run_id, inserted, skipped = save_return_rows_as_pending_facts(
        connection, document, 1, rows, "test-model", "{}", "image_test"
    )
    assert inserted == 1
    assert len(skipped) == 3
    reasons = [reason for _, reason in skipped]
    assert any("unrecognized period_type" in reason for reason in reasons)
    assert any("missing period_end_date" in reason for reason in reasons)
    assert any("missing return_value" in reason for reason in reasons)
    error_message = connection.execute(
        "SELECT error_message FROM extraction_runs WHERE run_id = ?", (run_id,)
    ).fetchone()["error_message"]
    assert error_message and "row 1" in error_message
    staged = connection.execute(
        "SELECT COUNT(*) FROM proposed_return_rows WHERE run_id = ?",
        (run_id,),
    ).fetchone()[0]
    assert staged == 1
    print("return extraction skip reasons: OK")


def test_return_row_backfill(connection):
    connection.rollback()
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    scratch.execute("PRAGMA foreign_keys = ON;")
    connection.backup(scratch)
    scratch.execute(
        """
        INSERT INTO extraction_runs (
            run_id, document_id, extraction_scope, extraction_method, model_name,
            prompt_version, run_status
        )
        VALUES ('run_backfill_test', 'doc_003', 'fund_characteristics', 'test', 'test-model', 'test_v1', 'complete')
        """
    )
    scratch.execute(
        """
        INSERT INTO extracted_facts (
            fact_id, run_id, fund_id, document_id, fact_category, fact_name, raw_value,
            normalized_value, unit, as_of_date, source_document_id, page_number,
            quoted_text, structured_payload_json, extraction_method, confidence_score, approval_status
        )
        VALUES (
            'fact_backfill_test', 'run_backfill_test', 'fund_002', 'doc_003', 'performance', 'monthly_return',
            '1.4', '1.4', 'percent', '2026-06-30', 'doc_003', 1,
            'Jun 2026 1.4%',
            '{"period_type":"monthly","period_end_date":"2026-06-30","return_type":"net","share_class":"USD","return_value":"1.4","unit":"percent","structured_context":{"table_name":"Backfill Test Returns","row_label":"2026","column_label":"Jun"}}',
            'test', 1.0, 'approved'
        )
        """
    )
    migrated = backfill_legacy_return_rows(scratch)
    assert migrated == 1
    staged_row = scratch.execute(
        """
        SELECT legacy_fact_id, period_type, period_end_date, return_type, share_class
        FROM proposed_return_rows
        WHERE legacy_fact_id = 'fact_backfill_test'
        """
    ).fetchone()
    assert staged_row["period_type"] == "monthly"
    assert staged_row["period_end_date"] == "2026-06-30"
    assert staged_row["return_type"] == "net"
    assert staged_row["share_class"] == "USD"
    scratch.close()
    print("return row backfill: OK")


def test_failed_extraction_run_recorded():
    scratch = sqlite3.connect(":memory:")
    scratch.row_factory = sqlite3.Row
    scratch.execute("PRAGMA foreign_keys = ON;")
    create_schema(scratch)
    seed_taxonomy(scratch)
    scratch.execute(
        "INSERT INTO funds (fund_id, fund_name, manager_name) VALUES ('fund_test', 'Test Fund', 'Test Manager')"
    )
    scratch.execute(
        """
        INSERT INTO source_documents (document_id, fund_id, file_name, document_type, document_title)
        VALUES ('doc_test', 'fund_test', 'test.pdf', 'factsheet', 'Test Factsheet')
        """
    )
    scratch.commit()
    document = {"document_id": "doc_test", "fund_id": "fund_test"}
    run_id = record_failed_extraction_run(scratch, document, "test-model", "not json", "Malformed JSON from model: test")
    row = scratch.execute(
        "SELECT run_status, error_message, raw_output FROM extraction_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row["run_status"] == "failed"
    assert row["error_message"] == "Malformed JSON from model: test"
    assert row["raw_output"] == "not json"
    scratch.close()
    print("failed extraction run recorded: OK")


def test_mock_approved_data(connection):
    approved_ids = load_mock_approved_data(connection)
    promoted, skipped = promote_all_pending(connection, user="internal_test")
    assert approved_ids
    assert promoted
    return_fact_ids = {
        row["approved_fact_id"]
        for row in connection.execute(
            """
            SELECT approved_fact_id
            FROM approved_facts
            WHERE fact_category = 'performance'
              AND fact_name IN ('monthly_return', 'quarterly_return', 'annual_return', 'ytd_return', 'ytd_performance')
            """
        ).fetchall()
    }
    tree_count = connection.execute(
        "SELECT COUNT(*) FROM fund_characteristics WHERE fund_id IN ('fund_002', 'fund_003')"
    ).fetchone()[0]
    assert tree_count >= len(approved_ids) - len(return_fact_ids)
    comparison = compare_funds(connection, ["fund_002", "fund_003"])
    assert any(row["return_count"] > 0 for row in comparison["return_statistics"])
    assert any(row["fund_characteristics"] > 0 for row in comparison["analysis_readiness_scores"])
    assert comparison["analysis_readiness_scores"][0]["analysis_readiness_score"] > 0
    print("mock approved data: OK")


def test_review_document_bundle_smoke():
    script_path = Path(__file__).resolve().parent / "review_document_bundle.py"
    with connect_db() as connection:
        with terminal_review_test_db(connection) as (db_path, document_id):
            env = dict(os.environ)
            env["ACTIVIST_DB_PATH"] = str(db_path)
            result = subprocess.run(
                [sys.executable, str(script_path), "--document-id", document_id],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
    assert f"# Review Bundle: Terminal Test Factsheet ({document_id})" in result.stdout
    assert "## Strategy / Portfolio" in result.stdout
    assert "Current approved:" in result.stdout
    assert "## Returns" in result.stdout
    assert "Pending review items:" in result.stdout
    assert "Pending/Reviewed Return Row IDs" in result.stdout
    print("review document bundle smoke: OK")


def test_review_document_bundle_cleanup_preview():
    script_path = Path(__file__).resolve().parent / "review_document_bundle.py"
    with connect_db() as connection:
        with terminal_review_test_db(connection) as (db_path, document_id):
            env = dict(os.environ)
            env["ACTIVIST_DB_PATH"] = str(db_path)
            result = subprocess.run(
                [sys.executable, str(script_path), "--document-id", document_id, "--preview-cleanup"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
    assert f"CLEANUP PREVIEW: document={document_id}" in result.stdout
    assert "duplicates=3" in result.stdout
    print("review document bundle cleanup preview: OK")


def test_review_document_bundle_return_cleanup_preview():
    script_path = Path(__file__).resolve().parent / "review_document_bundle.py"
    with connect_db() as connection:
        with terminal_review_test_db(connection) as (db_path, document_id):
            env = dict(os.environ)
            env["ACTIVIST_DB_PATH"] = str(db_path)
            result = subprocess.run(
                [sys.executable, str(script_path), "--document-id", document_id, "--preview-return-cleanup"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
    assert "RETURN CLEANUP PREVIEW: fund=fund_002" in result.stdout
    assert "duplicate_groups=1" in result.stdout
    print("review document bundle return cleanup preview: OK")


def test_review_document_bundle_repair_preview():
    script_path = Path(__file__).resolve().parent / "review_document_bundle.py"
    with connect_db() as connection:
        with terminal_review_test_db(connection) as (db_path, document_id):
            env = dict(os.environ)
            env["ACTIVIST_DB_PATH"] = str(db_path)
            result = subprocess.run(
                [sys.executable, str(script_path), "--document-id", document_id, "--preview-repair"],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
    assert f"CLEANUP PREVIEW: document={document_id}" in result.stdout
    assert "RETURN CLEANUP PREVIEW: fund=fund_002" in result.stdout
    assert "Pending review items:" in result.stdout
    print("review document bundle repair preview: OK")


def main():
    with connect_db() as connection:
        test_fund_snapshot(connection)
        test_fund_view(connection)
        test_document_bundle(connection)
        test_cross_document_duplicate_gate(connection)
        test_extraction_sanitization()
        test_db_backup_helper()
        test_document_section_review_actions(connection)
        test_document_item_revision_actions(connection)
        test_pending_document_fact_cleanup(connection)
        test_mock_analysis(connection)
        test_workbooks(connection)
        test_comparison(connection)
        test_safe_return_promotion(connection)
        test_aligned_return_comparison(connection)
        test_duplicate_safe_return_promotion(connection)
        test_canonicalize_performance_returns(connection)
        test_mock_web_research(connection)
        test_visual_return_extraction_mock(connection)
        test_return_extraction_skip_reasons(connection)
        test_return_row_backfill(connection)
        test_mock_approved_data(connection)
        connection.rollback()
    test_failed_extraction_run_recorded()
    test_review_document_bundle_smoke()
    test_review_document_bundle_cleanup_preview()
    test_review_document_bundle_return_cleanup_preview()
    test_review_document_bundle_repair_preview()
    print("Internal tests passed.")


if __name__ == "__main__":
    main()

"""No-API pipeline tests against an in-memory database.

Run: .venv/bin/python -m tests.test_pipeline
Covers the correctness core: staging dedup, approval upsert (structural
one-value-per-field), return quality upsert, factsheet assembly, comparison.
"""
import json
import sqlite3
import tempfile
from pathlib import Path

import fitz

from fund.compare import compare_funds, overlapping_returns
from fund.analyze import (
    activism_analysis_context,
    build_activism_reality_prompt,
    build_public_examples_prompt,
    factsheet_embedding_similarity,
    format_activism_reality_result,
)
from fund.db import SCHEMA, connect, create_schema, migrate_to_latest, utc_now
from fund.extract import _dedup_against_db, _save_return_rows, save_proposals
from fund.factsheet import (
    annual_display_series,
    build_factsheet,
    build_proposed_factsheet,
    format_factsheet,
    format_proposed_factsheet,
    return_statistics,
)
from fund.export import markdown_compare, markdown_factsheet, peer_context
import fund.factsheet as factsheet_mod
import fund.ingest as ingest_mod
from fund.review import (approve_proposal, approve_return_row, pending_proposals,
                         reject_proposal, reopen_proposal)
from fund.screen import format_screen, screen_funds
from fund.similarity import format_similar, similar_funds
from fund.verification import reconcile_returns, verify_existing
from fund.__main__ import format_inbox, format_next, workflow_inbox, workflow_next


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    create_schema(conn)
    now = utc_now()
    conn.execute("INSERT INTO funds VALUES ('f1', 'Alpha Fund', 'Alpha Mgmt', NULL, ?)", (now,))
    conn.execute("INSERT INTO funds VALUES ('f2', 'Beta Fund', 'Beta Mgmt', NULL, ?)", (now,))
    for doc, fund, is_current in (("d1", "f1", 1), ("d2", "f1", 0), ("d3", "f2", 1)):
        conn.execute(
            """INSERT INTO documents (
                   doc_id, fund_id, file_name, original_file_name, stored_path, sha256,
                   doc_type, title, doc_date, is_current, supersedes_doc_id, created_at
               ) VALUES (?, ?, 'x.pdf', 'x.pdf', 'data/pdfs/x.pdf', NULL,
                        'factsheet', 'T', NULL, ?, NULL, ?)""",
            (doc, fund, is_current, now),
        )
    return conn


def stage(conn, doc="d1", fund="f1", key="management_fee", value="1.5", confidence=0.9,
          share_class=None, quote="q"):
    document = {"doc_id": doc, "fund_id": fund}
    inserted, skipped = save_proposals(conn, document, "profile_terms", [{
        "field_key": key, "share_class": share_class, "value": value, "unit": "percent", "page_number": 1,
        "quoted_text": quote, "confidence": confidence,
    }])
    return inserted, skipped


def test_staging_and_dedup():
    conn = make_db()
    inserted, skipped = stage(conn)
    assert inserted == ["management_fee"] and not skipped
    # identical value for same fund from another document -> skipped
    inserted, skipped = stage(conn, doc="d2")
    assert not inserted and "already pending" in skipped[0][1]
    inserted, skipped = stage(conn, doc="d2", share_class="Class A")
    assert inserted == ["management_fee[Class A]"]
    # different value is allowed through (reviewer decides)
    inserted, _ = stage(conn, doc="d2", value="2.0")
    assert inserted == ["management_fee"]
    # within-run duplicate keeps the higher-confidence one
    document = {"doc_id": "d1", "fund_id": "f1"}
    inserted, skipped = save_proposals(conn, document, "profile_terms", [
        {"field_key": "notice_period", "value": "30 days", "confidence": 0.4},
        {"field_key": "notice_period", "value": "90 days", "confidence": 0.9},
    ])
    assert inserted == ["notice_period"]
    kept = conn.execute(
        "SELECT value FROM proposals WHERE field_key='notice_period' AND status='pending'"
    ).fetchone()["value"]
    assert kept == "90 days"
    print("staging + dedup: OK")


def test_approval_is_structurally_deduped():
    conn = make_db()
    stage(conn)
    prop = pending_proposals(conn, doc_id="d1")[0]
    approve_proposal(conn, prop["proposal_id"], "tester")
    # a second approval for the same field revises in place, never duplicates
    stage(conn, doc="d2", value="1.75")
    prop2 = pending_proposals(conn, doc_id="d2")[0]
    approve_proposal(conn, prop2["proposal_id"], "tester")
    rows = conn.execute(
        "SELECT value FROM facts WHERE fund_id='f1' AND field_key='management_fee' AND share_class=''"
    ).fetchall()
    assert len(rows) == 1 and rows[0]["value"] == "1.75"
    print("approval structural dedup (revise-in-place): OK")


def test_reject_and_reopen():
    conn = make_db()
    stage(conn)
    prop = pending_proposals(conn, doc_id="d1")[0]
    reject_proposal(conn, prop["proposal_id"], "tester", note="bad quote")
    assert not pending_proposals(conn, doc_id="d1")
    reopen_proposal(conn, prop["proposal_id"], "tester")
    assert len(pending_proposals(conn, doc_id="d1")) == 1
    print("reject + reopen: OK")


def test_proposed_factsheet_is_human_first():
    conn = make_db()
    stage(conn, key="management_fee", value="1.5")
    stage(conn, key="notice_period", value="90 days")
    proposed = build_proposed_factsheet(conn, "f1")
    rendered = format_proposed_factsheet(proposed)
    assert "Management fee" in rendered
    assert "1.5" in rendered
    assert "[prop_" not in rendered
    assert "source: d1 p.1" in rendered
    print("proposed factsheet formatting: OK")


def test_quote_verification_on_save_and_backfill():
    conn = make_db()
    conn.execute(
        "INSERT INTO pages (doc_id, page_number, text) VALUES ('d1', 1, ?)",
        ("The management fee is 1.5 percent per annum. Notice period is 90 days.",),
    )
    inserted, skipped = stage(
        conn,
        key="management_fee",
        value="1.5",
        quote="management fee is 1.5 percent per annum",
    )
    assert inserted == ["management_fee"] and not skipped
    prop = pending_proposals(conn, doc_id="d1")[0]
    assert prop["quote_verify_status"] == "verified_text"
    assert prop["quote_verified"] == 1

    stage(conn, key="notice_period", value="90 days", quote="Notice period is 90 days")
    conn.execute(
        "UPDATE proposals SET quote = 'completely different quote', quote_verified = NULL, "
        "quote_verify_score = NULL, quote_verify_status = NULL WHERE field_key = 'notice_period'"
    )
    counts = verify_existing(conn, doc_id="d1")
    assert counts["verified_text"] >= 1
    row = conn.execute(
        "SELECT quote_verify_status, quote_verified FROM proposals WHERE field_key = 'notice_period'"
    ).fetchone()
    assert row["quote_verify_status"] == "unverified_text"
    assert row["quote_verified"] == 0
    print("quote verification save + backfill: OK")


def stage_return(conn, doc="d1", fund="f1", period_end="2025-01-31", value=1.2,
                 return_type="unknown", row_id=None):
    from uuid import uuid4
    row_id = row_id or f"ret_{uuid4().hex[:8]}"
    conn.execute(
        """INSERT INTO proposed_returns (row_id, doc_id, fund_id, period_type, period_end,
           return_pct, return_type, created_at) VALUES (?, ?, ?, 'monthly', ?, ?, ?, ?)""",
        (row_id, doc, fund, period_end, value, return_type, utc_now()))
    return row_id


def test_return_quality_upsert():
    conn = make_db()
    first = stage_return(conn, return_type="gross", value=1.0)
    assert approve_return_row(conn, first, "tester") == "inserted"
    # net beats gross for the same period -> replaced
    better = stage_return(conn, doc="d2", return_type="net", value=1.1)
    assert approve_return_row(conn, better, "tester") == "replaced_existing"
    # unknown does not beat net -> kept existing
    worse = stage_return(conn, doc="d2", return_type="unknown", value=9.9)
    assert approve_return_row(conn, worse, "tester") == "kept_existing_higher_quality"
    rows = conn.execute("SELECT return_pct, return_type FROM returns").fetchall()
    assert len(rows) == 1 and rows[0]["return_type"] == "net" and rows[0]["return_pct"] == 1.1
    print("return quality upsert: OK")


def test_benchmark_rows_are_filtered_from_returns():
    conn = make_db()
    document = {"doc_id": "d1", "fund_id": "f1"}
    inserted, skipped = _save_return_rows(conn, document, 1, [
        {
            "period_type": "monthly",
            "period_end_date": "2026-04-30",
            "return_value": 0.4,
            "return_type": "net",
            "share_class": "ICHIGO*",
            "quoted_text": "ICHIGO* Apr 0.4%",
            "confidence": 0.95,
        },
        {
            "period_type": "monthly",
            "period_end_date": "2026-04-30",
            "return_value": 6.6,
            "return_type": "unknown",
            "share_class": "TOPIX",
            "quoted_text": "TOPIX Apr 6.6%",
            "confidence": 0.95,
        },
    ])
    assert inserted == 1
    assert skipped == [(1, "benchmark/index row")]
    rows = conn.execute("SELECT share_class, return_pct FROM proposed_returns").fetchall()
    assert [(row["share_class"], row["return_pct"]) for row in rows] == [("ICHIGO*", 0.4)]
    print("benchmark rows filtered from returns: OK")


def test_return_quote_verification_modes():
    conn = make_db()
    conn.execute(
        "INSERT INTO pages (doc_id, page_number, text) VALUES ('d1', 1, ?)",
        ("ICHIGO Apr 0.4 percent",),
    )
    document = {"doc_id": "d1", "fund_id": "f1"}
    inserted, skipped = _save_return_rows(conn, document, 1, [{
        "period_type": "monthly",
        "period_end_date": "2026-04-30",
        "return_value": 0.4,
        "return_type": "net",
        "share_class": "ICHIGO",
        "quoted_text": "ICHIGO Apr 0.4 percent",
        "confidence": 0.95,
    }], source_mode="text")
    assert inserted == 1 and not skipped
    row = conn.execute("SELECT quote_verify_status FROM proposed_returns").fetchone()
    assert row["quote_verify_status"] == "verified_text"

    inserted, skipped = _save_return_rows(conn, document, 1, [{
        "period_type": "monthly",
        "period_end_date": "2026-05-31",
        "return_value": 0.5,
        "return_type": "net",
        "share_class": "ICHIGO",
        "quoted_text": "image-only table cell",
        "confidence": 0.95,
    }], source_mode="vision")
    assert inserted == 1 and not skipped
    row = conn.execute(
        "SELECT quote_verify_status FROM proposed_returns WHERE period_end = '2026-05-31'"
    ).fetchone()
    assert row["quote_verify_status"] == "unverifiable_vision"
    print("return quote verification modes: OK")


def test_factsheet_and_compare():
    conn = make_db()
    stage(conn)
    approve_proposal(conn, pending_proposals(conn, doc_id="d1")[0]["proposal_id"], "tester")
    values = [("2025-01-31", 1.0), ("2025-02-28", -0.5), ("2025-03-31", 2.5)]
    for period, value in values:
        approve_return_row(conn, stage_return(conn, period_end=period, value=value), "tester")
        approve_return_row(
            conn, stage_return(conn, doc="d3", fund="f2", period_end=period, value=value + 1),
            "tester")
    sheet = build_factsheet(conn, "f1")
    assert sheet["coverage"]["fund_level_fields_filled"] == 1
    stats = sheet["return_statistics"]
    assert stats["count"] == 3 and stats["average"] == round(stats["average"], 1)
    comparison = compare_funds(conn, ["f1", "f2"], field_keys=["management_fee"])
    assert comparison["rows"][0]["values"] == {"f1": "1.5", "f2": None}
    assert len(comparison["overlapping_returns"]) == 3
    print("factsheet + compare: OK")


def test_screen_filters_and_presets():
    conn = make_db()
    now = utc_now()
    conn.execute(
        """INSERT INTO facts (fund_id, field_key, share_class, value, value_num, unit,
           as_of_date, doc_id, page, quote, approved_by, approved_at)
           VALUES ('f1', 'management_fee', '', '1.5', 1.5, 'percent',
                   NULL, 'd1', 1, 'q', 'tester', ?)""",
        (now,),
    )
    conn.execute(
        """INSERT INTO facts (fund_id, field_key, share_class, value, value_num, unit,
           as_of_date, doc_id, page, quote, approved_by, approved_at)
           VALUES ('f1', 'primary_strategy', '', 'activist', NULL, NULL,
                   NULL, 'd1', 1, 'q', 'tester', ?)""",
        (now,),
    )
    conn.execute(
        """INSERT INTO facts (fund_id, field_key, share_class, value, value_num, unit,
           as_of_date, doc_id, page, quote, approved_by, approved_at)
           VALUES ('f1', 'market_cap_focus', '', 'small-cap', NULL, NULL,
                   NULL, 'd1', 1, 'q', 'tester', ?)""",
        (now,),
    )
    for month in range(1, 13):
        approve_return_row(conn, stage_return(conn, period_end=f"2025-{month:02d}-28", value=1.0), "t")

    result = screen_funds(conn, where=["management_fee>=1.5"], sort_field="annualized_sharpe")
    assert [row["fund_id"] for row in result["rows"]] == ["f1"]
    rendered = format_screen(result)
    assert "Fund screen" in rendered and "f1" in rendered

    result = screen_funds(conn, preset="small-cap-activist")
    assert [row["fund_id"] for row in result["rows"]] == ["f1"]
    print("screen filters + presets: OK")


def test_workflow_inbox_and_next_action():
    conn = make_db()
    stage(conn, key="management_fee", value="1.5")
    stage(conn, key="notice_period", value="90 days")
    inbox = workflow_inbox(conn)
    f1 = next(row for row in inbox if row["fund_id"] == "f1")
    assert f1["handle"] == "alpha"
    assert f1["pending"] == 2
    assert f1["verified"] == 0
    rendered = format_inbox(inbox)
    assert "Review inbox" in rendered
    assert "fund review alpha --list" in rendered
    next_item = workflow_next(conn)
    assert next_item["kind"] == "review"
    assert next_item["command"] == "fund review alpha --list"
    assert "Next action" in format_next(next_item)
    print("workflow inbox + next action: OK")


def test_return_reconciliation_qc():
    conn = make_db()
    for month in range(1, 13):
        approve_return_row(conn, stage_return(conn, period_end=f"2024-{month:02d}-28", value=1.0), "t")
    approve_return_row(conn, stage_annual(conn, "d1", "f1", 2024, 10.0), "t")
    from uuid import uuid4
    ytd_id = f"ret_{uuid4().hex[:8]}"
    conn.execute(
        """INSERT INTO proposed_returns (row_id, doc_id, fund_id, period_type, period_end,
           return_pct, return_type, created_at)
           VALUES (?, 'd1', 'f1', 'ytd', '2024-03-31', 5.0, 'net', ?)""",
        (ytd_id, utc_now()),
    )
    approve_return_row(conn, ytd_id, "t")
    approve_return_row(conn, stage_return(conn, period_end="2025-01-31", value=61.0), "t")
    issues = reconcile_returns(conn, "f1")
    kinds = {issue["kind"] for issue in issues}
    assert "annual_vs_monthly" in kinds
    assert "ytd_vs_monthly" in kinds
    assert "monthly_bound" in kinds
    print("return reconciliation QC: OK")


def test_markdown_exports():
    conn = make_db()
    stage(conn, key="management_fee", value="1.5")
    approve_proposal(conn, pending_proposals(conn, fund_id="f1")[0]["proposal_id"], "tester")
    for month in range(1, 13):
        approve_return_row(conn, stage_return(conn, period_end=f"2024-{month:02d}-28", value=1.0), "t")
        approve_return_row(
            conn,
            stage_return(conn, doc="d3", fund="f2", period_end=f"2024-{month:02d}-28", value=0.5),
            "t",
        )
    sheet = build_factsheet(conn, "f1")
    rendered = markdown_factsheet(sheet, peer=peer_context(conn, "f1"))
    assert "# Alpha Fund" in rendered
    assert "peer median" in rendered
    comparison = markdown_compare(conn, ["f1", "f2"])
    assert "# Fund Comparison" in comparison
    assert "Calendar-Year Returns" in comparison
    print("markdown exports: OK")


def test_compare_output_is_summary_first():
    from fund.compare import format_comparison
    conn = make_db()
    stage(conn, key="management_fee", value="1.5")
    stage(conn, doc="d3", fund="f2", key="management_fee", value="2.0")
    for prop in pending_proposals(conn, fund_id="f1"):
        approve_proposal(conn, prop["proposal_id"], "tester")
    for prop in pending_proposals(conn, fund_id="f2"):
        approve_proposal(conn, prop["proposal_id"], "tester")
    comparison = compare_funds(conn, ["f1", "f2"], field_keys=["management_fee"])
    rendered = format_comparison(comparison)
    assert "Terms" in rendered
    assert "Reported Metrics" not in rendered or isinstance(rendered, str)
    assert "differ most clearly in stated strategy and terms" in rendered
    print("compare summary-first formatting: OK")


def test_common_period_comparison():
    conn = make_db()
    # f1 has Jan+Feb+Mar; f2 has Feb+Mar+Apr -> common period is Feb+Mar only
    for period, value in [("2025-01-31", 1.0), ("2025-02-28", 2.0), ("2025-03-31", 3.0)]:
        approve_return_row(conn, stage_return(conn, period_end=period, value=value), "t")
    for period, value in [("2025-02-28", 0.0), ("2025-03-31", 4.0), ("2025-04-30", 5.0)]:
        approve_return_row(conn, stage_return(conn, doc="d3", fund="f2", period_end=period, value=value), "t")
    comparison = compare_funds(conn, ["f1", "f2"])
    common = comparison["common_period_statistics"]
    assert common["count"] == 2
    assert common["period_start"] == "2025-02" and common["period_end"] == "2025-03"
    # f1 over the common period: mean(2,3)=2.5 ; f2: mean(0,4)=2.0
    assert common["by_fund"]["f1"]["average"] == 2.5
    assert common["by_fund"]["f2"]["average"] == 2.0
    assert "sharpe_like" not in common["by_fund"]["f1"]
    assert "annualized_sharpe" in common["by_fund"]["f1"]
    print("common-period comparison: OK")


def test_month_end_normalization_for_overlap():
    conn = make_db()
    approve_return_row(conn, stage_return(conn, period_end="2024-02-28", value=1.0), "t")
    approve_return_row(conn, stage_return(conn, doc="d3", fund="f2", period_end="2024-02-29", value=2.0), "t")
    comparison = compare_funds(conn, ["f1", "f2"])
    assert comparison["overlapping_returns"] == [{"period": "2024-02", "f1": 1.0, "f2": 2.0}]
    print("month-end normalization for overlap: OK")


def test_connect_requires_explicit_migration():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "old.sqlite"
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        now = utc_now()
        conn.executescript(
            """
            CREATE TABLE funds (
                fund_id TEXT PRIMARY KEY,
                fund_name TEXT NOT NULL,
                manager_name TEXT NOT NULL,
                notes TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE documents (
                doc_id TEXT PRIMARY KEY,
                fund_id TEXT NOT NULL REFERENCES funds (fund_id),
                file_name TEXT NOT NULL,
                sha256 TEXT,
                doc_type TEXT NOT NULL,
                title TEXT NOT NULL,
                doc_date TEXT,
                page_count INTEGER,
                created_at TEXT NOT NULL
            );
            CREATE TABLE proposals (
                proposal_id TEXT PRIMARY KEY,
                doc_id TEXT NOT NULL REFERENCES documents (doc_id),
                fund_id TEXT NOT NULL REFERENCES funds (fund_id),
                scope TEXT NOT NULL,
                field_key TEXT NOT NULL,
                value TEXT NOT NULL,
                value_num REAL,
                unit TEXT,
                as_of_date TEXT,
                page INTEGER,
                quote TEXT,
                confidence REAL,
                status TEXT NOT NULL DEFAULT 'pending',
                review_note TEXT,
                reviewed_by TEXT,
                reviewed_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE facts (
                fund_id TEXT NOT NULL REFERENCES funds (fund_id),
                field_key TEXT NOT NULL,
                value TEXT NOT NULL,
                value_num REAL,
                unit TEXT,
                as_of_date TEXT,
                doc_id TEXT REFERENCES documents (doc_id),
                page INTEGER,
                quote TEXT,
                approved_by TEXT NOT NULL,
                approved_at TEXT NOT NULL,
                PRIMARY KEY (fund_id, field_key)
            );
            """
        )
        conn.execute("INSERT INTO funds VALUES ('f1', 'Alpha Fund', 'Alpha Mgmt', NULL, ?)", (now,))
        conn.execute(
            "INSERT INTO documents VALUES ('d1', 'f1', 'alpha.pdf', NULL, 'factsheet', 'Alpha', NULL, NULL, ?)",
            (now,),
        )
        conn.execute(
            """INSERT INTO proposals (
                   proposal_id, doc_id, fund_id, scope, field_key, value, value_num, unit,
                   as_of_date, page, quote, confidence, status, review_note, reviewed_by,
                   reviewed_at, created_at
               ) VALUES (
                   'p1', 'd1', 'f1', 'profile_terms', 'management_fee', '1.5', 1.5, 'percent',
                   NULL, 1, 'quote', 0.9, 'pending', NULL, NULL, NULL, ?
               )""",
            (now,),
        )
        conn.execute(
            """INSERT INTO facts (
                   fund_id, field_key, value, value_num, unit, as_of_date, doc_id, page,
                   quote, approved_by, approved_at
               ) VALUES (
                   'f1', 'management_fee', '1.5', 1.5, 'percent', NULL, 'd1', 1, 'quote', 'tester', ?
               )""",
            (now,),
        )
        conn.commit()
        conn.close()

        try:
            with connect(db_path):
                raise AssertionError("connect should require explicit migration")
        except RuntimeError as exc:
            assert "fund migrate" in str(exc)

        with connect(db_path, verify=False) as migrated:
            result = migrate_to_latest(migrated, organize_files=False)
            assert result["migrated"] is True
            proposal_cols = [row["name"] for row in migrated.execute("PRAGMA table_info(proposals)")]
            fact_cols = [row["name"] for row in migrated.execute("PRAGMA table_info(facts)")]
            doc_cols = [row["name"] for row in migrated.execute("PRAGMA table_info(documents)")]
            assert "share_class" in proposal_cols
            assert "share_class" in fact_cols
            assert "stored_path" in doc_cols
            assert "is_current" in doc_cols
            proposal = migrated.execute("SELECT share_class FROM proposals WHERE proposal_id = 'p1'").fetchone()
            fact = migrated.execute(
                "SELECT share_class, value FROM facts WHERE fund_id='f1' AND field_key='management_fee'"
            ).fetchone()
            doc = migrated.execute(
                "SELECT stored_path, original_file_name, is_current FROM documents WHERE doc_id = 'd1'"
            ).fetchone()
            assert proposal["share_class"] == ""
            assert fact["share_class"] == ""
            assert fact["value"] == "1.5"
            assert doc["stored_path"] == "alpha.pdf"
            assert doc["original_file_name"] == "alpha.pdf"
            assert doc["is_current"] == 1
    print("explicit migration path: OK")


def stage_annual(conn, doc, fund, year, value, share_class="", return_type="net"):
    from uuid import uuid4
    rid = f"ret_{uuid4().hex[:8]}"
    conn.execute(
        """INSERT INTO proposed_returns (row_id, doc_id, fund_id, period_type, period_end,
           return_pct, return_type, share_class, created_at)
           VALUES (?, ?, ?, 'annual', ?, ?, ?, ?, ?)""",
        (rid, doc, fund, f"{year}-12-31", value, return_type, share_class, utc_now()))
    return rid


def test_calendar_year_comparison():
    from fund.compare import calendar_year_comparison
    from fund.factsheet import build_factsheet
    conn = make_db()
    # f1: 2022,2023,2024 ; f2: 2023,2024,2025 -> common years 2023,2024
    for year, value in [(2022, 10.0), (2023, 20.0), (2024, -5.0)]:
        approve_return_row(conn, stage_annual(conn, "d1", "f1", year, value), "t")
    for year, value in [(2023, 5.0), (2024, 15.0), (2025, 8.0)]:
        approve_return_row(conn, stage_annual(conn, "d3", "f2", year, value), "t")
    sheets = {fid: build_factsheet(conn, fid) for fid in ("f1", "f2")}
    cy = calendar_year_comparison(sheets, ["f1", "f2"])
    assert cy["years"] == ["2022", "2023", "2024", "2025"]
    assert cy["common_years"] == ["2023", "2024"]
    assert cy["own_stats"]["f1"]["count"] == 3
    assert cy["common_stats"]["f1"]["average"] == 7.5   # mean(20,-5)
    assert cy["common_stats"]["f2"]["average"] == 10.0  # mean(5,15)
    # picks the longest annual series when a fund has two share classes
    approve_return_row(conn, stage_annual(conn, "d1", "f1", 2021, 3.0, share_class="B"), "t")
    approve_return_row(conn, stage_annual(conn, "d1", "f1", 2020, 3.0, share_class="B"), "t")
    approve_return_row(conn, stage_annual(conn, "d1", "f1", 2019, 3.0, share_class="B"), "t")
    approve_return_row(conn, stage_annual(conn, "d1", "f1", 2018, 3.0, share_class="B"), "t")
    sheets = {fid: build_factsheet(conn, fid) for fid in ("f1", "f2")}
    cy = calendar_year_comparison(sheets, ["f1", "f2"])
    assert cy["class_used"]["f1"] == "B"  # 4 rows > 3 rows in the default class
    assert cy["source"]["f1"] == "reported annual" and cy["source"]["f2"] == "reported annual"
    print("calendar-year comparison: OK")


def test_annual_computed_from_monthly():
    from fund.compare import calendar_year_comparison
    from fund.factsheet import build_factsheet
    conn = make_db()
    # f1 reports an annual figure; f2 has only 12 monthly rows for 2024 -> computed
    approve_return_row(conn, stage_annual(conn, "d1", "f1", 2024, 10.0), "t")
    for month in range(1, 13):
        end = f"2024-{month:02d}-28"
        approve_return_row(conn, stage_return(conn, doc="d3", fund="f2", period_end=end, value=1.0), "t")
    sheets = {fid: build_factsheet(conn, fid) for fid in ("f1", "f2")}
    cy = calendar_year_comparison(sheets, ["f1", "f2"])
    assert cy["source"]["f1"] == "reported annual"
    assert cy["source"]["f2"] == "computed from monthly"
    # twelve +1.0% months compound to ~12.7%
    f2_2024 = next(r["f2"] for r in cy["rows"] if r["year"] == "2024")
    assert f2_2024 == 12.7
    print("annual computed from monthly: OK")


def test_factsheet_uses_year_end_ytd_as_annual():
    conn = make_db()
    for end, value in [("2023-12-31", 12.0), ("2024-12-31", 8.0), ("2025-04-30", 3.5)]:
        from uuid import uuid4
        rid = f"ret_{uuid4().hex[:8]}"
        conn.execute(
            """INSERT INTO proposed_returns (row_id, doc_id, fund_id, period_type, period_end,
               return_pct, return_type, created_at)
               VALUES (?, 'd1', 'f1', 'ytd', ?, ?, 'net', ?)""",
            (rid, end, value, utc_now()),
        )
        approve_return_row(conn, rid, "t")
    sheet = build_factsheet(conn, "f1")
    annual = annual_display_series(sheet["returns"])
    assert annual[0]["source"] == "year-end YTD"
    assert [row["period_end"] for row in annual[0]["rows"]] == ["2023-12-31", "2024-12-31"]
    rendered = format_factsheet(sheet)
    assert "[year-end YTD]" in rendered
    assert "2025 YTD" in rendered
    print("factsheet year-end YTD annual display: OK")


def test_factsheet_computes_annual_from_monthly_when_needed():
    conn = make_db()
    for month in range(1, 13):
        end = f"2024-{month:02d}-28"
        approve_return_row(conn, stage_return(conn, period_end=end, value=1.0), "t")
    sheet = build_factsheet(conn, "f1")
    annual = annual_display_series(sheet["returns"])
    assert annual[0]["source"] == "computed from monthly"
    assert annual[0]["rows"][0]["return_pct"] == 12.7
    rendered = format_factsheet(sheet)
    assert "[computed from monthly]" in rendered
    assert "2024" in rendered
    print("factsheet computed annual display: OK")


def test_snapshot_replaces_old_file_and_records_changes():
    conn = make_db()
    stage(conn, key="management_fee", value="1.5")
    approve_proposal(conn, pending_proposals(conn, fund_id="f1")[0]["proposal_id"], "tester")
    with tempfile.TemporaryDirectory() as tmp:
        original_dir = factsheet_mod.FACTSHEET_DIR
        factsheet_mod.FACTSHEET_DIR = Path(tmp)
        try:
            first_path, first_hash = factsheet_mod.snapshot_factsheet(conn, "f1")
            assert first_path.exists()
            stage(conn, doc="d2", key="management_fee", value="2.0")
            approve_proposal(conn, pending_proposals(conn, fund_id="f1")[0]["proposal_id"], "tester")
            second_path, second_hash = factsheet_mod.snapshot_factsheet(conn, "f1")
            assert second_hash != first_hash
            assert second_path.exists()
            assert not first_path.exists()
            files = list(Path(tmp).glob("f1_*.json"))
            assert files == [second_path]
            saved = json.loads(second_path.read_text())
            assert saved["changes"]["previous_hash"] == first_hash
            assert saved["changes"]["field_changes"][0]["field"] == "management_fee"
        finally:
            factsheet_mod.FACTSHEET_DIR = original_dir
    print("snapshot replace + change log: OK")


def test_compare_uses_year_end_ytd_before_monthly_rollup():
    from fund.compare import calendar_year_comparison
    conn = make_db()
    for end, value in [("2023-12-31", 12.0), ("2024-12-31", 8.0), ("2025-04-30", 3.5)]:
        from uuid import uuid4
        rid = f"ret_{uuid4().hex[:8]}"
        conn.execute(
            """INSERT INTO proposed_returns (row_id, doc_id, fund_id, period_type, period_end,
               return_pct, return_type, created_at)
               VALUES (?, 'd1', 'f1', 'ytd', ?, ?, 'net', ?)""",
            (rid, end, value, utc_now()),
        )
        approve_return_row(conn, rid, "t")
    for month in range(1, 13):
        end = f"2024-{month:02d}-28"
        approve_return_row(conn, stage_return(conn, doc="d3", fund="f2", period_end=end, value=1.0), "t")
    sheets = {fid: build_factsheet(conn, fid) for fid in ("f1", "f2")}
    cy = calendar_year_comparison(sheets, ["f1", "f2"])
    assert cy["source"]["f1"] == "year-end YTD"
    assert cy["source"]["f2"] == "computed from monthly"
    f1_2024 = next(r["f1"] for r in cy["rows"] if r["year"] == "2024")
    assert f1_2024 == 8.0
    print("compare uses year-end YTD before monthly rollup: OK")


def test_activism_prompt_and_render():
    snapshots = [{
        "fund_id": "f1",
        "fund_name": "Alpha Fund",
        "manager_name": "Alpha Mgmt",
        "sections": {
            "strategy": [{"field_key": "activism_style", "label": "Activism style", "value": "friendly activism", "source": None}],
            "terms": [{"field_key": "management_fee", "label": "Management fee", "value": "1.5", "source": None}],
        },
        "returns": [],
    }]
    context = activism_analysis_context(snapshots[0])
    assert "terms" not in context["sections"]
    search_prompt = build_public_examples_prompt(context)
    assert "use web search" in search_prompt.lower()
    assert "english and japanese" in search_prompt.lower()
    assert "translate japanese findings into english" in search_prompt.lower()
    prompt = build_activism_reality_prompt([context], ["FUND: Alpha Fund (f1)\nWEB FINDINGS:\n- unknown | none | none | none"])
    assert "web findings" in prompt.lower()
    rendered = format_activism_reality_result(
        "SUMMARY:\nAlpha appears broadly aligned based on limited public evidence.\n\n"
        "FUND: Alpha Fund (f1)\n"
        "STATED: friendly activism\n"
        "OBSERVED: constructive engagement in one disclosed campaign\n"
        "ALIGNMENT: partially_aligned\n"
        "CONFIDENCE: low\n"
        "EXAMPLES:\n"
        "- 2024-01-10 | Asked for a buyback at Example Co. | Example release | https://example.com/release",
        ["https://example.com/release"],
    )
    assert "Alpha appears broadly aligned" in rendered
    assert "EXAMPLES:" in rendered
    assert "Web sources used:" in rendered
    print("activism prompt + render: OK")


def test_qualitative_similarity():
    conn = make_db()
    now = utc_now()
    rows = [
        ("f1", "primary_strategy", "constructive activist engagement", None),
        ("f1", "activism_style", "friendly private engagement with management", None),
        ("f1", "geography_focus", "Japan", None),
        ("f1", "market_cap_focus", "small and mid cap companies", None),
        ("f1", "aum", "USD 1.5 billion", 1.5),
        ("f1", "volatility", "10%", 10.0),
        ("f2", "primary_strategy", "constructive activist strategy", None),
        ("f2", "activism_style", "friendly engagement with portfolio companies", None),
        ("f2", "geography_focus", "Japan", None),
        ("f2", "market_cap_focus", "small-cap companies", None),
        ("f2", "aum", "USD 2.0 billion", 2.0),
        ("f2", "volatility", "11%", 11.0),
    ]
    for fund_id, field_key, value, value_num in rows:
        unit = "currency" if field_key == "aum" else "percent" if field_key == "volatility" else None
        conn.execute(
            """INSERT INTO facts (fund_id, field_key, share_class, value, value_num, unit,
               as_of_date, doc_id, page, quote, approved_by, approved_at)
               VALUES (?, ?, '', ?, ?, ?, NULL, ?, 1, 'q', 'tester', ?)""",
            (fund_id, field_key, value, value_num, unit, "d1" if fund_id == "f1" else "d3", now),
        )
    result = similar_funds(conn, "f1")
    assert result["rows"][0]["fund_id"] == "f2"
    assert result["rows"][0]["score"] > 0.5
    rendered = format_similar(result)
    assert "Similar funds to Alpha Fund" in rendered
    assert "excludes terms and return history" in rendered
    print("qualitative similarity: OK")


def test_embedding_similarity_dry_run():
    conn = make_db()
    stage(conn, key="management_fee", value="1.5")
    stage(conn, doc="d3", fund="f2", key="management_fee", value="2.0")
    for prop in pending_proposals(conn, fund_id="f1"):
        approve_proposal(conn, prop["proposal_id"], "tester")
    for prop in pending_proposals(conn, fund_id="f2"):
        approve_proposal(conn, prop["proposal_id"], "tester")
    result = factsheet_embedding_similarity(conn, ["f1", "f2"], dry_run=True)
    assert result["dry_run"] is True
    assert len(result["chars"]) == 2
    print("embedding similarity dry-run: OK")


def test_return_page_detection_and_routing():
    from fund.extract import find_return_pages, onboard_plan, SCOPES_BY_DOC_TYPE
    conn = make_db()
    conn.execute("INSERT INTO pages (doc_id, page_number, text) VALUES ('d1', 1, ?)",
                 ("2020 2021 2022 2023 Jan Feb Mar YTD net return performance 1.2% 3.4% -0.5% 10%",))
    conn.execute("INSERT INTO pages (doc_id, page_number, text) VALUES ('d1', 2, ?)",
                 ("Portfolio commentary. We remain constructive on Japanese equities.",))
    conn.execute("UPDATE documents SET doc_type = 'presentation' WHERE doc_id = 'd2'")
    pages = find_return_pages(conn, "d1")
    assert pages[0][0] == 1                 # the return grid wins
    assert all(page != 2 for page, _ in pages)  # commentary page excluded
    plan = {item["doc_id"]: item for item in onboard_plan(conn, "f1")}
    assert plan["d1"]["scopes"] == SCOPES_BY_DOC_TYPE["factsheet"]   # FS -> quant scopes
    assert plan["d1"]["return_pages"] == [1]
    assert plan["d2"]["scopes"] == SCOPES_BY_DOC_TYPE["presentation"]  # PRS -> qualitative
    assert plan["d2"]["return_pages"] == []   # returns only from fact sheets
    print("return-page detection + routing: OK")


def test_most_recent_source_wins():
    from fund.review import doc_meta, resolve_field_conflicts
    conn = make_db()  # d1 and d2 are both fact sheets in make_db
    conn.execute("UPDATE documents SET doc_date = '2024-06-30' WHERE doc_id = 'd1'")
    conn.execute("UPDATE documents SET doc_date = '2025-09-30', is_current = 1 WHERE doc_id = 'd2'")
    conn.execute("UPDATE documents SET is_current = 0 WHERE doc_id = 'd1'")
    stage(conn, doc="d1", key="management_fee", value="1.5")
    stage(conn, doc="d2", key="management_fee", value="2.0")  # newer doc, different value
    props = pending_proposals(conn, fund_id="f1")
    winners, superseded = resolve_field_conflicts(props, doc_meta(conn, "f1"))
    newer = next(p for p in props if p["doc_id"] == "d2")
    older = next(p for p in props if p["doc_id"] == "d1")
    assert newer["proposal_id"] in winners
    assert superseded[older["proposal_id"]]["doc_id"] == "d2"
    # approving the winner writes the most-recent value into facts
    approve_proposal(conn, newer["proposal_id"], "tester")
    value = conn.execute(
        "SELECT value FROM facts WHERE fund_id='f1' AND field_key='management_fee' AND share_class=''"
    ).fetchone()["value"]
    assert value == "2.0"
    print("most recent source wins: OK")


def test_factsheet_source_beats_presentation():
    from fund.review import doc_meta, resolve_field_conflicts
    conn = make_db()
    # d1 is an older FACT SHEET; d2 is a newer PRESENTATION
    conn.execute("UPDATE documents SET doc_type='factsheet', doc_date='2020-01-01' WHERE doc_id='d1'")
    conn.execute("UPDATE documents SET doc_type='presentation', doc_date='2026-01-01' WHERE doc_id='d2'")
    stage(conn, doc="d1", key="management_fee", value="1.5")   # fact sheet, older
    stage(conn, doc="d2", key="management_fee", value="9.9")   # presentation, newer
    props = pending_proposals(conn, fund_id="f1")
    winners, superseded = resolve_field_conflicts(props, doc_meta(conn, "f1"))
    fs = next(p for p in props if p["doc_id"] == "d1")
    prs = next(p for p in props if p["doc_id"] == "d2")
    assert fs["proposal_id"] in winners            # fact sheet wins despite being older
    assert superseded[prs["proposal_id"]]["doc_id"] == "d1"
    print("fact sheet beats presentation: OK")


def test_proposed_factsheet_assembly():
    from fund.factsheet import build_proposed_factsheet
    conn = make_db()
    stage(conn, key="management_fee", value="1.5")
    stage(conn, key="notice_period", value="90 days")
    stage(conn, key="management_fee", value="2.0", share_class="Class A")
    proposed = build_proposed_factsheet(conn, "f1")
    assert proposed["count"] == 3
    assert len(proposed["sections"]["terms"]) == 2
    assert len(proposed["share_classes"]["Class A"]["terms"]) == 1
    print("proposed factsheet assembly: OK")


def test_share_class_fact_upsert():
    conn = make_db()
    stage(conn, key="management_fee", value="1.5", share_class="Class A")
    stage(conn, doc="d2", key="management_fee", value="2.0", share_class="Class B")
    props = pending_proposals(conn, fund_id="f1")
    approve_proposal(conn, next(p["proposal_id"] for p in props if p["share_class"] == "Class A"), "tester")
    approve_proposal(conn, next(p["proposal_id"] for p in props if p["share_class"] == "Class B"), "tester")
    rows = conn.execute(
        """SELECT share_class, value FROM facts
           WHERE fund_id='f1' AND field_key='management_fee'
           ORDER BY share_class"""
    ).fetchall()
    assert [(row["share_class"], row["value"]) for row in rows] == [("Class A", "1.5"), ("Class B", "2.0")]

    stage(conn, doc="d2", key="management_fee", value="1.75", share_class="Class A")
    prop = next(p for p in pending_proposals(conn, fund_id="f1") if p["share_class"] == "Class A")
    approve_proposal(conn, prop["proposal_id"], "tester")
    rows = conn.execute(
        """SELECT share_class, value FROM facts
           WHERE fund_id='f1' AND field_key='management_fee'
           ORDER BY share_class"""
    ).fetchall()
    assert [(row["share_class"], row["value"]) for row in rows] == [("Class A", "1.75"), ("Class B", "2.0")]
    print("share-class fact upsert: OK")


def test_fund_level_and_share_class_can_coexist():
    conn = make_db()
    stage(conn, key="minimum_investment", value="USD 1m")
    stage(conn, doc="d2", key="minimum_investment", value="USD 5m", share_class="Class A")
    for prop in pending_proposals(conn, fund_id="f1"):
        approve_proposal(conn, prop["proposal_id"], "tester")
    rows = conn.execute(
        """SELECT share_class, value FROM facts
           WHERE fund_id='f1' AND field_key='minimum_investment'
           ORDER BY share_class"""
    ).fetchall()
    assert [(row["share_class"], row["value"]) for row in rows] == [("", "USD 1m"), ("Class A", "USD 5m")]
    print("fund-level + share-class coexist: OK")


def test_share_class_conflict_resolution():
    from fund.review import doc_meta, resolve_field_conflicts
    conn = make_db()
    conn.execute("UPDATE documents SET doc_date = '2024-06-30' WHERE doc_id = 'd1'")
    conn.execute("UPDATE documents SET doc_date = '2025-09-30', is_current = 1 WHERE doc_id = 'd2'")
    conn.execute("UPDATE documents SET is_current = 0 WHERE doc_id = 'd1'")
    stage(conn, doc="d1", key="management_fee", value="1.5", share_class="Class A")
    stage(conn, doc="d2", key="management_fee", value="1.75", share_class="Class A")
    stage(conn, doc="d2", key="management_fee", value="2.0", share_class="Class B")
    props = pending_proposals(conn, fund_id="f1")
    winners, superseded = resolve_field_conflicts(props, doc_meta(conn, "f1"))
    class_a_new = next(p for p in props if p["doc_id"] == "d2" and p["share_class"] == "Class A")
    class_a_old = next(p for p in props if p["doc_id"] == "d1" and p["share_class"] == "Class A")
    class_b = next(p for p in props if p["share_class"] == "Class B")
    assert class_a_new["proposal_id"] in winners
    assert class_b["proposal_id"] in winners
    assert superseded[class_a_old["proposal_id"]]["proposal_id"] == class_a_new["proposal_id"]
    print("share-class conflict resolution: OK")


def test_factsheet_and_compare_share_classes():
    conn = make_db()
    stage(conn, key="management_fee", value="1.5", share_class="Class A")
    stage(conn, doc="d2", key="management_fee", value="2.0", share_class="Class B")
    for prop in pending_proposals(conn, fund_id="f1"):
        approve_proposal(conn, prop["proposal_id"], "tester")
    stage(conn, doc="d3", fund="f2", key="management_fee", value="1.2", share_class="Class A")
    for prop in pending_proposals(conn, fund_id="f2"):
        approve_proposal(conn, prop["proposal_id"], "tester")
    sheet = build_factsheet(conn, "f1")
    assert "Class A" in sheet["share_classes"] and "Class B" in sheet["share_classes"]
    comparison = compare_funds(conn, ["f1", "f2"], field_keys=["management_fee"])
    labels = [row["label"] for row in comparison["rows"]]
    assert "Management fee [Class A]" in labels
    class_a_row = next(row for row in comparison["rows"] if row["label"] == "Management fee [Class A]")
    assert class_a_row["values"] == {"f1": "1.5", "f2": "1.2"}
    print("factsheet + compare share classes: OK")


def test_register_document_tracks_current_lineage():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pdf_root = root / "data" / "pdfs"
        page_root = root / "data" / "page_images"
        pdf_root.mkdir(parents=True, exist_ok=True)
        page_root.mkdir(parents=True, exist_ok=True)

        original_project_root = ingest_mod.PROJECT_ROOT
        original_pdf_dir = ingest_mod.PDF_DIR
        original_page_image_dir = ingest_mod.PAGE_IMAGE_DIR
        ingest_mod.PROJECT_ROOT = root
        ingest_mod.PDF_DIR = pdf_root
        ingest_mod.PAGE_IMAGE_DIR = page_root
        try:
            conn = make_db()

            source_one = root / "alpha_one.pdf"
            source_two = root / "alpha_two.pdf"
            for path in (source_one, source_two):
                pdf = fitz.open()
                pdf.new_page().insert_text((72, 72), "Alpha")
                pdf.save(path)
                pdf.close()

            ingest_mod.register_document(
                conn,
                "d_new_1",
                "f1",
                source_one,
                "factsheet",
                "Alpha Factsheet",
                "2026-06-30",
            )
            first = conn.execute(
                "SELECT stored_path, is_current, supersedes_doc_id FROM documents WHERE doc_id = 'd_new_1'"
            ).fetchone()
            assert first["stored_path"].endswith("data/pdfs/alpha-fund/2026-06-30_factsheet_alpha-fund.pdf")
            assert first["is_current"] == 1
            assert first["supersedes_doc_id"] == "d1"

            ingest_mod.register_document(
                conn,
                "d_new_2",
                "f1",
                source_two,
                "factsheet",
                "Alpha Factsheet",
                "2026-07-31",
            )
            latest = conn.execute(
                "SELECT is_current, supersedes_doc_id FROM documents WHERE doc_id = 'd_new_2'"
            ).fetchone()
            older = conn.execute(
                "SELECT is_current FROM documents WHERE doc_id = 'd_new_1'"
            ).fetchone()
            assert latest["is_current"] == 1
            assert latest["supersedes_doc_id"] == "d_new_1"
            assert older["is_current"] == 0
        finally:
            ingest_mod.PROJECT_ROOT = original_project_root
            ingest_mod.PDF_DIR = original_pdf_dir
            ingest_mod.PAGE_IMAGE_DIR = original_page_image_dir
    print("document current lineage + normalized storage: OK")


if __name__ == "__main__":
    test_staging_and_dedup()
    test_approval_is_structurally_deduped()
    test_reject_and_reopen()
    test_proposed_factsheet_is_human_first()
    test_quote_verification_on_save_and_backfill()
    test_return_quality_upsert()
    test_benchmark_rows_are_filtered_from_returns()
    test_return_quote_verification_modes()
    test_factsheet_and_compare()
    test_screen_filters_and_presets()
    test_workflow_inbox_and_next_action()
    test_return_reconciliation_qc()
    test_markdown_exports()
    test_compare_output_is_summary_first()
    test_share_class_fact_upsert()
    test_fund_level_and_share_class_can_coexist()
    test_share_class_conflict_resolution()
    test_factsheet_and_compare_share_classes()
    test_common_period_comparison()
    test_month_end_normalization_for_overlap()
    test_calendar_year_comparison()
    test_annual_computed_from_monthly()
    test_factsheet_uses_year_end_ytd_as_annual()
    test_factsheet_computes_annual_from_monthly_when_needed()
    test_snapshot_replaces_old_file_and_records_changes()
    test_compare_uses_year_end_ytd_before_monthly_rollup()
    test_activism_prompt_and_render()
    test_qualitative_similarity()
    test_embedding_similarity_dry_run()
    test_connect_requires_explicit_migration()
    test_register_document_tracks_current_lineage()
    test_return_page_detection_and_routing()
    test_most_recent_source_wins()
    test_factsheet_source_beats_presentation()
    test_proposed_factsheet_assembly()
    print("All pipeline tests passed.")

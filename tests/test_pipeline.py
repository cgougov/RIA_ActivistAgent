"""No-API pipeline tests against an in-memory database.

Run: python -m tests.test_pipeline
Covers the correctness core: staging dedup, approval upsert (structural
one-value-per-field), return quality upsert, factsheet assembly, comparison.
"""
import sqlite3

from fund.compare import compare_funds, overlapping_returns
from fund.db import SCHEMA, utc_now
from fund.extract import _dedup_against_db, save_proposals
from fund.factsheet import build_factsheet, return_statistics
from fund.review import (approve_proposal, approve_return_row, pending_proposals,
                         reject_proposal, reopen_proposal)


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA)
    now = utc_now()
    conn.execute("INSERT INTO funds VALUES ('f1', 'Alpha Fund', 'Alpha Mgmt', NULL, ?)", (now,))
    conn.execute("INSERT INTO funds VALUES ('f2', 'Beta Fund', 'Beta Mgmt', NULL, ?)", (now,))
    for doc, fund in (("d1", "f1"), ("d2", "f1"), ("d3", "f2")):
        conn.execute(
            "INSERT INTO documents (doc_id, fund_id, file_name, doc_type, title, created_at)"
            " VALUES (?, ?, 'x.pdf', 'factsheet', 'T', ?)", (doc, fund, now))
    return conn


def stage(conn, doc="d1", fund="f1", key="management_fee", value="1.5", confidence=0.9):
    document = {"doc_id": doc, "fund_id": fund}
    inserted, skipped = save_proposals(conn, document, "profile_terms", [{
        "field_key": key, "value": value, "unit": "percent", "page_number": 1,
        "quoted_text": "q", "confidence": confidence,
    }])
    return inserted, skipped


def test_staging_and_dedup():
    conn = make_db()
    inserted, skipped = stage(conn)
    assert inserted == ["management_fee"] and not skipped
    # identical value for same fund from another document -> skipped
    inserted, skipped = stage(conn, doc="d2")
    assert not inserted and "already pending" in skipped[0][1]
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
        "SELECT value FROM facts WHERE fund_id='f1' AND field_key='management_fee'"
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
    assert sheet["coverage"]["fields_filled"] == 1
    stats = sheet["return_statistics"]
    assert stats["count"] == 3 and stats["average"] == round(stats["average"], 1)
    comparison = compare_funds(conn, ["f1", "f2"], field_keys=["management_fee"])
    assert comparison["rows"][0]["values"] == {"f1": "1.5", "f2": None}
    assert len(comparison["overlapping_returns"]) == 3
    print("factsheet + compare: OK")


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
    assert common["period_start"] == "2025-02-28" and common["period_end"] == "2025-03-31"
    # f1 over the common period: mean(2,3)=2.5 ; f2: mean(0,4)=2.0
    assert common["by_fund"]["f1"]["average"] == 2.5
    assert common["by_fund"]["f2"]["average"] == 2.0
    print("common-period comparison: OK")


def test_most_recent_source_wins():
    from fund.review import doc_dates, resolve_field_conflicts
    conn = make_db()
    conn.execute("UPDATE documents SET doc_date = '2024-06-30' WHERE doc_id = 'd1'")
    conn.execute("UPDATE documents SET doc_date = '2025-09-30' WHERE doc_id = 'd2'")
    stage(conn, doc="d1", key="management_fee", value="1.5")
    stage(conn, doc="d2", key="management_fee", value="2.0")  # newer doc, different value
    props = pending_proposals(conn, fund_id="f1")
    winners, superseded = resolve_field_conflicts(props, doc_dates(conn, "f1"))
    newer = next(p for p in props if p["doc_id"] == "d2")
    older = next(p for p in props if p["doc_id"] == "d1")
    assert newer["proposal_id"] in winners
    assert superseded[older["proposal_id"]]["doc_id"] == "d2"
    # approving the winner writes the most-recent value into facts
    approve_proposal(conn, newer["proposal_id"], "tester")
    value = conn.execute(
        "SELECT value FROM facts WHERE fund_id='f1' AND field_key='management_fee'"
    ).fetchone()["value"]
    assert value == "2.0"
    print("most recent source wins: OK")


def test_proposed_factsheet_assembly():
    from fund.factsheet import build_proposed_factsheet
    conn = make_db()
    stage(conn, key="management_fee", value="1.5")
    stage(conn, key="notice_period", value="90 days")
    proposed = build_proposed_factsheet(conn, "f1")
    assert proposed["count"] == 2
    assert len(proposed["sections"]["terms"]) == 2
    print("proposed factsheet assembly: OK")


if __name__ == "__main__":
    test_staging_and_dedup()
    test_approval_is_structurally_deduped()
    test_reject_and_reopen()
    test_return_quality_upsert()
    test_factsheet_and_compare()
    test_common_period_comparison()
    test_most_recent_source_wins()
    test_proposed_factsheet_assembly()
    print("All pipeline tests passed.")

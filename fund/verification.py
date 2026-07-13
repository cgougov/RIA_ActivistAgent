"""Quote verification for proposed facts and return rows.

This is a deterministic guardrail. It does not prove a fact is true; it checks
whether the proposed evidence quote appears to be supported by the cited page
text. Vision-only return cells can be legitimate but unverifiable against text,
so they get a distinct status instead of being treated as failed.
"""
import re

from fund.analytics import cumulative_return, group_by_class

VERIFY_THRESHOLD = 0.62


def _tokens(text):
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def _score_quote_against_page(quote, page_text):
    quote_tokens = _tokens(quote)
    if not quote_tokens:
        return None
    page_tokens = set(_tokens(page_text))
    if not page_tokens:
        return None
    hits = sum(1 for token in quote_tokens if token in page_tokens)
    return round(hits / len(quote_tokens), 3)


def _page_text(connection, doc_id, page):
    if page is None:
        return None
    row = connection.execute(
        "SELECT text FROM pages WHERE doc_id = ? AND page_number = ?",
        (doc_id, page),
    ).fetchone()
    if row is None:
        return None
    return row["text"] or ""


def verify_quote(connection, *, doc_id, page, quote, source_mode="text"):
    """Return {quote_verified, quote_verify_score, quote_verify_status}.

    Status values:
    - verified_text: quote overlaps sufficiently with extracted page text
    - unverified_text: quote had page text but did not match enough
    - unverifiable_vision: expected for image/table cells that are not in text
    - missing_quote: no quote supplied
    - missing_page: no cited page / no stored page
    """
    if not str(quote or "").strip():
        return {
            "quote_verified": 0,
            "quote_verify_score": None,
            "quote_verify_status": "missing_quote",
        }
    text = _page_text(connection, doc_id, page)
    if text is None:
        return {
            "quote_verified": 0,
            "quote_verify_score": None,
            "quote_verify_status": "missing_page",
        }
    score = _score_quote_against_page(quote, text)
    if score is not None and score >= VERIFY_THRESHOLD:
        return {
            "quote_verified": 1,
            "quote_verify_score": score,
            "quote_verify_status": "verified_text",
        }
    if source_mode == "vision":
        return {
            "quote_verified": None,
            "quote_verify_score": score,
            "quote_verify_status": "unverifiable_vision",
        }
    return {
        "quote_verified": 0,
        "quote_verify_score": score,
        "quote_verify_status": "unverified_text",
    }


def verification_label(row):
    status = row.get("quote_verify_status")
    score = row.get("quote_verify_score")
    if status == "verified_text":
        return f"verified text ({score:.2f})" if score is not None else "verified text"
    if status == "unverifiable_vision":
        return "unverifiable vision"
    if status == "unverified_text":
        return f"unverified text ({score:.2f})" if score is not None else "unverified text"
    return status or "not checked"


def verify_existing(connection, doc_id=None):
    """Backfill/re-run quote verification for staged proposals and returns."""
    proposal_where, proposal_params = "", []
    return_where, return_params = "", []
    if doc_id:
        proposal_where = "WHERE doc_id = ?"
        proposal_params.append(doc_id)
        return_where = "WHERE doc_id = ?"
        return_params.append(doc_id)

    counts = {}
    proposals = connection.execute(
        f"SELECT proposal_id, doc_id, page, quote FROM proposals {proposal_where}",
        proposal_params,
    ).fetchall()
    for row in proposals:
        result = verify_quote(
            connection,
            doc_id=row["doc_id"],
            page=row["page"],
            quote=row["quote"],
            source_mode="text",
        )
        connection.execute(
            """
            UPDATE proposals
            SET quote_verified = ?, quote_verify_score = ?, quote_verify_status = ?
            WHERE proposal_id = ?
            """,
            (
                result["quote_verified"],
                result["quote_verify_score"],
                result["quote_verify_status"],
                row["proposal_id"],
            ),
        )
        counts[result["quote_verify_status"]] = counts.get(result["quote_verify_status"], 0) + 1

    returns = connection.execute(
        f"SELECT row_id, doc_id, page, quote FROM proposed_returns {return_where}",
        return_params,
    ).fetchall()
    for row in returns:
        result = verify_quote(
            connection,
            doc_id=row["doc_id"],
            page=row["page"],
            quote=row["quote"],
            source_mode="vision",
        )
        connection.execute(
            """
            UPDATE proposed_returns
            SET quote_verified = ?, quote_verify_score = ?, quote_verify_status = ?
            WHERE row_id = ?
            """,
            (
                result["quote_verified"],
                result["quote_verify_score"],
                result["quote_verify_status"],
                row["row_id"],
            ),
        )
        key = f"returns:{result['quote_verify_status']}"
        counts[key] = counts.get(key, 0) + 1
    connection.commit()
    return counts


def _year(date_text):
    return str(date_text or "")[:4]


def _month_number(date_text):
    try:
        return int(str(date_text)[5:7])
    except (TypeError, ValueError):
        return None


def _label_class(share_class):
    return share_class or "(unspecified class)"


def reconcile_returns(connection, fund_id, threshold_pp=0.5, monthly_bound=60.0):
    """Deterministic return-number QC over approved return rows.

    Checks:
    - reported annual rows vs complete compounded monthly history
    - YTD rows vs compounded months through the YTD month
    - monthly rows outside a broad sanity bound
    """
    rows = [dict(row) for row in connection.execute(
        """SELECT share_class, period_type, period_end, return_pct, return_type, doc_id, page
           FROM returns WHERE fund_id = ?
           ORDER BY share_class, period_type, period_end""",
        (fund_id,),
    )]
    issues = []
    for share_class, class_rows in group_by_class(rows):
        monthly = [row for row in class_rows if row["period_type"] == "monthly"]
        by_year = {}
        for row in monthly:
            by_year.setdefault(_year(row["period_end"]), []).append(row)
            if abs(row["return_pct"]) > monthly_bound:
                issues.append({
                    "kind": "monthly_bound",
                    "severity": "warning",
                    "fund_id": fund_id,
                    "share_class": share_class,
                    "period": row["period_end"],
                    "message": (
                        f"{fund_id} {_label_class(share_class)} {row['period_end']}: "
                        f"monthly return {row['return_pct']:+.1f}% is outside +/-{monthly_bound:.0f}%"
                    ),
                    "row": row,
                })

        for row in (r for r in class_rows if r["period_type"] == "annual"):
            year = _year(row["period_end"])
            year_rows = sorted(by_year.get(year, []), key=lambda r: r["period_end"])
            if len({_month_number(r["period_end"]) for r in year_rows}) != 12:
                continue
            computed = cumulative_return([r["return_pct"] for r in year_rows])
            delta = round(row["return_pct"] - computed, 1)
            if abs(delta) > threshold_pp:
                issues.append({
                    "kind": "annual_vs_monthly",
                    "severity": "warning",
                    "fund_id": fund_id,
                    "share_class": share_class,
                    "period": year,
                    "reported": row["return_pct"],
                    "computed": computed,
                    "delta": delta,
                    "message": (
                        f"{fund_id} {_label_class(share_class)} {year}: reported "
                        f"{row['return_pct']:+.1f}% vs compounded {computed:+.1f}% "
                        f"(delta {abs(delta):.1f}pp) -- check a monthly cell."
                    ),
                    "row": row,
                })

        for row in (r for r in class_rows if r["period_type"] == "ytd"):
            year = _year(row["period_end"])
            month = _month_number(row["period_end"])
            if not month:
                continue
            ytd_rows = sorted(
                (
                    r for r in by_year.get(year, [])
                    if (_month_number(r["period_end"]) or 99) <= month
                ),
                key=lambda r: r["period_end"],
            )
            if len({_month_number(r["period_end"]) for r in ytd_rows}) != month:
                continue
            computed = cumulative_return([r["return_pct"] for r in ytd_rows])
            delta = round(row["return_pct"] - computed, 1)
            if abs(delta) > threshold_pp:
                issues.append({
                    "kind": "ytd_vs_monthly",
                    "severity": "warning",
                    "fund_id": fund_id,
                    "share_class": share_class,
                    "period": row["period_end"],
                    "reported": row["return_pct"],
                    "computed": computed,
                    "delta": delta,
                    "message": (
                        f"{fund_id} {_label_class(share_class)} {row['period_end']}: YTD "
                        f"{row['return_pct']:+.1f}% vs compounded {computed:+.1f}% "
                        f"(delta {abs(delta):.1f}pp) -- check monthly cells."
                    ),
                    "row": row,
                })
    return issues


def format_reconciliation(issues, fund_label):
    lines = [f"Return reconciliation for {fund_label}", "--------------------------"]
    if not issues:
        lines.append("  No deterministic return-number issues found.")
        return "\n".join(lines)
    for issue in issues:
        lines.append(f"  {issue['message']}")
    return "\n".join(lines)

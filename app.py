import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from core.analysis import run_snapshot_chat, run_web_research_comparison
from core.approval import approve_extracted_fact, reject_extracted_fact, reopen_rejected_fact
from core.comparison import compare_funds
from core.config import DATA_DIR, DEFAULT_ANALYSIS_MODEL, DEFAULT_REASONING_EFFORT, DB_PATH, PDF_ROOT
from core.db import connect_db
from core.fund_brief import NOT_APPROVED
from core.fund_views import build_fund_view, build_return_table_previews, format_return_cell
from core.documents import ingest_document_pages, render_document_page_image, render_document_page_images
from core.intake import register_pdf_bytes
from core.promotion import promote_all_pending
from core.return_rows import (
    approve_proposed_return_rows,
    backfill_legacy_return_rows,
    reject_proposed_return_rows,
)
from core.return_table_extraction import extract_return_table_from_page_image
from core.workbooks import export_analysis_workbook, export_source_truth_workbook


st.set_page_config(page_title="Japan Activist Funds", layout="wide")

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1400px;}
    div[data-testid="stMetric"] {background: #f8fafc; border: 1px solid #e5e7eb; padding: 0.75rem; border-radius: 8px;}
    div[data-testid="stExpander"] {border-radius: 8px;}
    .stTabs [data-baseweb="tab-list"] {gap: 0.25rem;}
    .stTabs [data-baseweb="tab"] {height: 2.5rem; padding: 0 0.85rem;}
    textarea {font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;}
    </style>
    """,
    unsafe_allow_html=True,
)


def query(sql, params=()):
    with connect_db() as connection:
        return pd.read_sql_query(sql, connection, params=params)


def rows(sql, params=()):
    with connect_db() as connection:
        return [dict(row) for row in connection.execute(sql, params).fetchall()]


@st.cache_resource
def _version_watcher():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def data_version():
    return _version_watcher().execute("PRAGMA data_version").fetchone()[0]


@st.cache_data(show_spinner=False)
def ensure_return_row_backfill(_version):
    with connect_db() as connection:
        migrated = backfill_legacy_return_rows(connection)
        connection.commit()
    return migrated


@st.cache_data(show_spinner=False)
def cached_fund_view(fund_id, version):
    with connect_db() as connection:
        return build_fund_view(connection, fund_id)


@st.cache_data(show_spinner=False)
def cached_compare_funds(fund_ids, version):
    with connect_db() as connection:
        return compare_funds(connection, list(fund_ids))


def table_view(table_name):
    st.dataframe(query(f"SELECT * FROM {table_name}"), use_container_width=True)


def fund_options():
    return rows("SELECT fund_id, fund_name, manager_name, status FROM funds ORDER BY fund_name")


def select_fund(label="Fund"):
    funds = fund_options()
    selected = st.selectbox(
        label,
        options=[fund["fund_id"] for fund in funds],
        format_func=lambda fid: next(f"{fund['fund_name']} ({fund['fund_id']})" for fund in funds if fund["fund_id"] == fid),
    )
    return selected


def page_image_path(image_path):
    relative_path = Path(image_path)
    if relative_path.parts and relative_path.parts[0] == "data":
        relative_path = Path(*relative_path.parts[1:])
    return DATA_DIR / relative_path


def evidence_context_text(page_text, quoted_text, radius=1200):
    if not page_text:
        return ""
    if quoted_text:
        needle = quoted_text.strip()
        location = page_text.lower().find(needle.lower())
        if location >= 0:
            start = max(location - radius, 0)
            end = min(location + len(needle) + radius, len(page_text))
            prefix = "..." if start else ""
            suffix = "..." if end < len(page_text) else ""
            return f"{prefix}{page_text[start:end]}{suffix}"
    return page_text[: radius * 2]


def render_evidence_panel(fact):
    st.markdown("**Source Evidence**")
    st.text_area(
        "Quoted text",
        value=fact["quoted_text"] or "",
        height=110,
        disabled=True,
        key=f"quote_{fact['fact_id']}",
    )
    if fact.get("page_number"):
        page_rows = rows(
            """
            SELECT page_text
            FROM document_pages
            WHERE document_id = ?
              AND page_number = ?
            """,
            [fact["source_document_id"], fact["page_number"]],
        )
        page_text = page_rows[0]["page_text"] if page_rows else ""
        st.text_area(
            "Page context",
            value=evidence_context_text(page_text, fact["quoted_text"]),
            height=180,
            disabled=True,
            key=f"context_{fact['fact_id']}",
        )
        try:
            with connect_db() as connection:
                image = render_document_page_image(
                    connection,
                    fact["source_document_id"],
                    int(fact["page_number"]),
                    dpi=140,
                )
                connection.commit()
            image_path = page_image_path(image["image_path"])
            if image_path.exists():
                st.image(str(image_path), caption=f"Page {fact['page_number']} source image", use_container_width=True)
        except Exception as exc:
            st.caption(f"Page image unavailable: {exc}")

    pdf_path = PDF_ROOT / Path(fact["file_name"]).name
    if pdf_path.exists():
        st.link_button("Open full PDF", str(pdf_path))


def render_overview():
    st.subheader("Research Workspace")
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    counts = {}
    for table in ["funds", "source_documents", "document_pages", "extracted_facts", "approved_facts", "fund_characteristics", "fund_flags"]:
        counts[table] = query(f"SELECT COUNT(*) AS n FROM {table}")["n"].iloc[0]
    pending_facts = query("SELECT COUNT(*) AS n FROM extracted_facts WHERE approval_status = 'pending'")["n"].iloc[0]
    pending_returns = query("SELECT COUNT(*) AS n FROM proposed_return_rows WHERE status = 'pending'")["n"].iloc[0]
    pending_total = pending_facts + pending_returns
    c1.metric("Funds", counts["funds"])
    c2.metric("Documents", counts["source_documents"])
    c3.metric("Pages", counts["document_pages"])
    c4.metric("Approved facts", counts["approved_facts"])
    c5.metric("Tree facts", counts["fund_characteristics"])
    c6.metric("Pending review", pending_total)

    st.markdown(
        """
        **Pipeline:** PDFs → page text → LLM proposed facts → human approval → approved fact ledger → clean tables → fund snapshots / analysis.
        """
    )

    st.subheader("Fund-Characteristics Tree")
    tree = query(
        """
        SELECT
            cg.display_name AS group_name,
            cd.fact_category,
            cd.fact_name,
            cd.display_name,
            cd.value_type,
            cd.clean_table,
            cd.clean_field,
            cd.source_priority
        FROM characteristic_definitions cd
        JOIN characteristic_groups cg
            ON cg.group_id = cd.group_id
        ORDER BY cg.display_name, cd.fact_category, cd.fact_name
        """
    )
    st.dataframe(tree, use_container_width=True)


def render_brief_pairs(pairs):
    st.dataframe(
        pd.DataFrame([{"Field": label, "Value": value} for label, value in pairs]),
        use_container_width=True,
        hide_index=True,
    )


def render_performance_metrics(fund_view):
    st.subheader("Performance & Risk")
    st.dataframe(
        pd.DataFrame(
            [
                {"Metric": row["label"], "Value": row["value"], "Source": row["source"]}
                for row in fund_view["performance_rows"]
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )


def render_fund_brief(brief):
    st.subheader("Approved Fund Brief")

    overview_col, strategy_col = st.columns(2)
    with overview_col:
        st.markdown("**1. Overview**")
        render_brief_pairs(brief["overview"])
    with strategy_col:
        st.markdown("**2. Strategy**")
        render_brief_pairs(brief["strategy"])

    people_col, terms_col = st.columns(2)
    with people_col:
        st.markdown("**3. People**")
        people = brief["people"] or [NOT_APPROVED]
        st.dataframe(
            pd.DataFrame([{"Person / Role": value} for value in people]),
            use_container_width=True,
            hide_index=True,
        )
    with terms_col:
        st.markdown("**4. Terms**")
        render_brief_pairs(brief["terms"])

    st.markdown("**5. Source-Backed Notes**")
    notes = brief["notes"]
    st.text_area("Neutral summary", value=notes["neutral_summary"], height=120, disabled=True, key="fund_brief_neutral_summary")
    st.text_area("Differentiating edge", value=notes["differentiating_edge"], height=120, disabled=True, key="fund_brief_differentiating_edge")
    flags = notes["flags"] or [NOT_APPROVED]
    st.dataframe(
        pd.DataFrame([{"Flags / Missing disclosures": value} for value in flags]),
        use_container_width=True,
        hide_index=True,
    )


def render_approved_returns_section(fund_view):
    st.subheader("Approved Returns")
    snapshot = fund_view["snapshot"]
    returns_df = pd.DataFrame(snapshot["returns"])
    if returns_df.empty:
        st.info("No approved structured return rows yet. Use the Returns tab to extract rows from factsheet page images, then approve and promote them.")
        return

    previews = fund_view["return_table_previews"]
    if previews:
        for preview in previews:
            heading = preview.get("display_name") or preview["table_name"]
            st.caption(heading)
            preview_df = pd.DataFrame(index=preview["row_order"], columns=preview["column_order"])
            for (row_label, column_label), value in preview["values"].items():
                preview_df.loc[row_label, column_label] = format_return_cell(value)
            preview_df.index.name = "Row"
            st.dataframe(preview_df, use_container_width=True)

    display_columns = [
        "share_class",
        "period_type",
        "period_start_date",
        "period_end_date",
        "return_type",
        "return_value",
        "return_value_bps",
        "unit",
        "evidence_text",
        "source_document_id",
        "page_number",
    ]
    st.dataframe(returns_df[display_columns], use_container_width=True, hide_index=True)

    values = pd.to_numeric(returns_df["return_value"], errors="coerce").dropna()
    if len(values) >= 2:
        stat_cols = st.columns(4)
        stats = fund_view["calculated_return_statistics"]
        stat_cols[0].metric("Return rows", stats["return_count"])
        stat_cols[1].metric("Average return", f"{stats['average_return']:.1f}%" if stats["average_return"] is not None else "No approved value")
        stat_cols[2].metric("Volatility", fund_view["performance_rows"][3]["value"])
        stat_cols[3].metric(
            "Best / worst",
            f"{stats['best_return']:.1f}% / {stats['worst_return']:.1f}%"
            if stats["best_return"] is not None and stats["worst_return"] is not None
            else "No approved value",
        )
    else:
        st.caption("At least two approved return rows are needed for volatility and basic return statistics.")


def fund_name_lookup(funds):
    return {fund["fund_id"]: fund["fund_name"] for fund in funds}


def with_fund_names(df, name_by_id):
    if df.empty:
        return df
    renamed = df.copy()
    if "fund_id" in renamed.columns:
        renamed["fund_id"] = renamed["fund_id"].map(lambda value: name_by_id.get(value, value))
        renamed = renamed.rename(columns={"fund_id": "Fund"})
    renamed = renamed.rename(columns={key: value for key, value in name_by_id.items() if key in renamed.columns})
    renamed = renamed.rename(
        columns={
            column: column.replace("_", " ")
            for column in renamed.columns
            if "_minus_" not in column
        }
    )
    renamed = renamed.rename(
        columns={
            column: " minus ".join(name_by_id.get(part, part) for part in column.split("_minus_"))
            for column in renamed.columns
            if "_minus_" in column
        }
    )
    return renamed


def render_fund_explorer():
    st.subheader("Fund Explorer")
    funds_df = query("SELECT fund_id, fund_name, manager_name, base_currency, inception_date, primary_strategy FROM funds ORDER BY fund_name")
    if funds_df.empty:
        st.info("No funds available.")
        return

    pending_df = query(
        """
        SELECT fund_id, SUM(pending_count) AS pending_review
        FROM (
            SELECT fund_id, COUNT(*) AS pending_count
            FROM extracted_facts
            WHERE approval_status = 'pending'
            GROUP BY fund_id
            UNION ALL
            SELECT fund_id, COUNT(*) AS pending_count
            FROM proposed_return_rows
            WHERE status = 'pending'
            GROUP BY fund_id
        )
        GROUP BY fund_id
        """
    )
    funds_df = funds_df.merge(pending_df, on="fund_id", how="left")
    funds_df["pending_review"] = funds_df["pending_review"].fillna(0).astype(int)

    selected = st.selectbox(
        "Open fund",
        options=funds_df["fund_id"].tolist(),
        format_func=lambda fid: funds_df.loc[funds_df["fund_id"] == fid, "fund_name"].iloc[0],
        key="funds_open_fund",
    )

    fund_view = cached_fund_view(selected, data_version())
    snapshot = fund_view["snapshot"]

    fund = snapshot["fund"]
    brief = fund_view["brief"]
    st.header(fund["fund_name"])
    pending_for_fund = int(funds_df.loc[funds_df["fund_id"] == selected, "pending_review"].iloc[0])
    if pending_for_fund:
        st.warning(f"Needs review: {pending_for_fund} pending fact(s) awaiting approval.")
    else:
        st.caption("No pending facts awaiting approval for this fund.")

    render_fund_brief(brief)
    render_performance_metrics(fund_view)
    render_approved_returns_section(fund_view)

    st.divider()
    with st.expander("Browse / Filter Funds", expanded=False):
        search = st.text_input("Filter funds", placeholder="Type fund, manager, or strategy", key="funds_filter")
        browse_df = funds_df
        if search:
            mask = browse_df.astype(str).apply(lambda col: col.str.contains(search, case=False, na=False)).any(axis=1)
            browse_df = browse_df[mask]
        st.dataframe(browse_df.drop(columns=["fund_id"]), use_container_width=True, hide_index=True)

    st.subheader("Approved Raw Tables")
    with st.expander("Approved Characteristics Tree", expanded=False):
        st.dataframe(pd.DataFrame(snapshot["characteristics"]), use_container_width=True)
    with st.expander("Neutral Writeups", expanded=False):
        st.dataframe(pd.DataFrame(snapshot["writeups"]), use_container_width=True)
    with st.expander("Flags", expanded=False):
        st.dataframe(pd.DataFrame(snapshot["flags"]), use_container_width=True)
    with st.expander("Profile Attributes", expanded=False):
        st.dataframe(pd.DataFrame(snapshot["profile_attributes"]), use_container_width=True)
    with st.expander("Terms", expanded=False):
        st.dataframe(pd.DataFrame(snapshot["terms"]), use_container_width=True)
    with st.expander("Strategy / Exposure", expanded=False):
        st.dataframe(pd.DataFrame(snapshot["strategy"]), use_container_width=True)
        st.dataframe(pd.DataFrame(snapshot["exposures"]), use_container_width=True)
    with st.expander("Returns And Metrics", expanded=False):
        st.dataframe(pd.DataFrame(snapshot["returns"]), use_container_width=True)
        st.dataframe(pd.DataFrame(snapshot["metrics"]), use_container_width=True)
    with st.expander("Documents", expanded=False):
        st.dataframe(pd.DataFrame(snapshot["documents"]), use_container_width=True)
    with st.expander("Raw FundSnapshot JSON", expanded=False):
        st.json(snapshot, expanded=False)

def _return_table_review_rows(document_id):
    return rows(
        """
        SELECT pr.*, er.model_name
        FROM proposed_return_rows pr
        LEFT JOIN extraction_runs er
            ON er.run_id = pr.run_id
        WHERE pr.source_document_id = ?
          AND pr.status = 'pending'
        ORDER BY pr.period_end_date, pr.share_class, pr.table_name, pr.row_label, pr.column_label
        """,
        [document_id],
    )


def _build_return_review_dataframe(pending_rows):
    display_rows = []
    for row in pending_rows:
        display_rows.append(
            {
                "proposed_row_id": row["proposed_row_id"],
                "share_class": row["share_class"],
                "period_type": row["period_type"],
                "period_start_date": row["period_start_date"],
                "period_end_date": row["period_end_date"],
                "return_type": row["return_type"],
                "return_value": row["return_value"],
                "return_value_bps": row["return_value_bps"],
                "unit": row["unit"],
                "table_name": row["table_name"],
                "row_label": row["row_label"],
                "column_label": row["column_label"],
                "evidence_text": row["evidence_text"],
                "status": row["status"],
                "model_name": row["model_name"],
                "created_at": row["created_at"],
            }
        )
    return pd.DataFrame(display_rows)

def _run_return_table_extraction(doc_id, page_number, dpi, mock):
    try:
        with connect_db() as connection:
            run_id, inserted, skipped = extract_return_table_from_page_image(
                connection,
                doc_id,
                int(page_number),
                dpi=int(dpi),
                mock=mock,
            )
            connection.commit()
        label = "Mock created" if mock else "Created"
        st.success(f"{label} {inserted} proposed return row(s). Review run: {run_id}")
        if skipped:
            st.warning(f"{len(skipped)} row(s) from the model were skipped — review reasons below.")
            st.dataframe(
                pd.DataFrame(skipped, columns=["row_index", "reason"]),
                use_container_width=True,
                hide_index=True,
            )
    except Exception as exc:
        st.error(f"Return extraction failed: {exc}")


def render_return_extraction_controls(doc_id, page_number, dpi, key_prefix, disabled=False):
    cols = st.columns(2)
    if cols[0].button("Extract return table from page image", key=f"{key_prefix}_extract_return_table", disabled=disabled):
        _run_return_table_extraction(doc_id, page_number, dpi, mock=False)
    if cols[1].button("Mock visual return extraction (no API call)", key=f"{key_prefix}_mock_visual_return_extraction", disabled=disabled):
        _run_return_table_extraction(doc_id, page_number, dpi, mock=True)


def render_returns_workspace():
    st.subheader("Returns")
    st.caption(
        "Primary workflow: extract structured return rows from factsheet page images. "
        "High-confidence structured rows can be auto-approved here, and any leftovers stay in this table-first review workspace."
    )

    funds = fund_options()
    if not funds:
        st.info("No funds available.")
        return

    selected_fund = st.selectbox(
        "Fund",
        options=[fund["fund_id"] for fund in funds],
        format_func=lambda fid: next(f"{fund['fund_name']} ({fid})" for fund in funds if fund["fund_id"] == fid),
        key="returns_fund",
    )
    documents = rows(
        """
        SELECT document_id, document_title, document_type, document_date, page_count
        FROM source_documents
        WHERE fund_id = ?
        ORDER BY document_date DESC, document_id
        """,
        [selected_fund],
    )
    if not documents:
        st.info("No source documents are registered for this fund.")
        return

    selected_doc = st.selectbox(
        "Source document",
        options=[document["document_id"] for document in documents],
        format_func=lambda doc_id: next(
            f"{document['document_title']} ({doc_id}, {document['document_date'] or 'undated'})"
            for document in documents
            if document["document_id"] == doc_id
        ),
        key="returns_document",
    )
    document = next(document for document in documents if document["document_id"] == selected_doc)
    max_page = int(document["page_count"] or 1)
    controls = st.columns([1, 1, 1])
    page_number = controls[0].number_input("Page number", min_value=1, max_value=max_page, value=1, step=1, key="returns_page_number")
    dpi = controls[1].number_input("Render DPI", min_value=100, max_value=300, value=180, step=20, key="returns_render_dpi")
    image = None
    if controls[2].button("Render page image", key="returns_render_page_image"):
        with connect_db() as connection:
            image = render_document_page_image(connection, selected_doc, int(page_number), dpi=int(dpi))
            connection.commit()
        st.success(f"Rendered page {int(page_number)} for {selected_doc}.")
    else:
        existing = rows(
            """
            SELECT *
            FROM document_page_images
            WHERE document_id = ?
              AND page_number = ?
              AND render_dpi = ?
            """,
            [selected_doc, int(page_number), int(dpi)],
        )
        image = existing[0] if existing else None

    if image:
        image_path = page_image_path(image["image_path"])
        if image_path.exists():
            st.image(str(image_path), caption=f"{selected_doc} page {int(page_number)}", use_container_width=True)
    else:
        st.info("Render the page image before extracting return rows.")

    st.markdown("**Extract Structured Return Rows**")
    st.caption("Returns stay in this workflow and do not go through the generic one-by-one fund-facts queue.")
    render_return_extraction_controls(selected_doc, page_number, dpi, "returns", disabled=image is None)

    pending_rows = _return_table_review_rows(selected_doc)
    pending = _build_return_review_dataframe(pending_rows)
    st.markdown("**Pending Return Rows For Review**")
    if pending.empty:
        st.info("No pending return rows for this document.")
    else:
        reviewer = st.text_input("Return reviewer", value="christian", key="returns_reviewer")
        previews = build_return_table_previews(pending_rows)
        if previews:
            st.markdown("**Extracted Table Preview**")
            for preview in previews:
                heading = preview.get("display_name") or preview["table_name"]
                st.caption(heading)
                preview_df = pd.DataFrame(index=preview["row_order"], columns=preview["column_order"])
                for (row_label, column_label), value in preview["values"].items():
                    preview_df.loc[row_label, column_label] = format_return_cell(value)
                preview_df.index.name = "Row"
                st.dataframe(preview_df, use_container_width=True)

        action_cols = st.columns(3)
        if action_cols[0].button("Approve all pending rows for this document", key="returns_approve_all_pending"):
            with connect_db() as connection:
                approved_fact_ids, promoted, promotion_skips = approve_proposed_return_rows(
                    connection,
                    [row["proposed_row_id"] for row in pending_rows],
                    reviewer=reviewer,
                    note=f"Bulk-approved from Returns workspace for document {selected_doc}.",
                )
                connection.commit()
            st.success(
                f"Approved {len(approved_fact_ids)} row(s) and promoted {len(promoted)} row(s) into approved return history."
            )
            if promotion_skips:
                st.warning(f"{len(promotion_skips)} approved row(s) could not be promoted.")
                st.dataframe(
                    pd.DataFrame(promotion_skips, columns=["approved_fact_id", "fact_category", "fact_name", "reason"]),
                    use_container_width=True,
                    hide_index=True,
                )
            st.rerun()
        if action_cols[1].button("Reject all pending rows for this document", key="returns_reject_all_pending"):
            with connect_db() as connection:
                reject_proposed_return_rows(
                    connection,
                    [row["proposed_row_id"] for row in pending_rows],
                    reviewer=reviewer,
                    note=f"Bulk-rejected from Returns workspace for document {selected_doc}.",
                )
                connection.commit()
            st.success(f"Rejected {len(pending_rows)} pending row(s) for this document.")
            st.rerun()
        action_cols[2].caption("Rows stay ordered by period end date so review format remains stable before and after approval.")
        st.dataframe(pending, use_container_width=True, hide_index=True)

    approved = query(
        """
        SELECT share_class, period_type, period_start_date, period_end_date,
               return_type, return_value, return_value_bps, unit, evidence_text, source_document_id, page_number
        FROM performance_returns
        WHERE fund_id = ?
        ORDER BY period_end_date, period_type, share_class
        """,
        [selected_fund],
    )
    st.markdown("**Approved Promoted Return History**")
    approved_rows = rows(
        """
        SELECT share_class, period_type, period_start_date, period_end_date, return_type,
               return_value, return_value_bps, unit, evidence_text, table_name, row_label, column_label
        FROM proposed_return_rows
        WHERE fund_id = ?
          AND status = 'approved'
        ORDER BY period_end_date, period_type, share_class
        """,
        [selected_fund],
    )
    approved_previews = build_return_table_previews(approved_rows)
    if approved_previews:
        for preview in approved_previews:
            heading = preview.get("display_name") or preview["table_name"]
            st.caption(heading)
            preview_df = pd.DataFrame(index=preview["row_order"], columns=preview["column_order"])
            for (row_label, column_label), value in preview["values"].items():
                preview_df.loc[row_label, column_label] = format_return_cell(value)
            preview_df.index.name = "Row"
            st.dataframe(preview_df, use_container_width=True)
    st.dataframe(approved, use_container_width=True, hide_index=True)


def render_document_intake():
    st.subheader("Document Intake")
    funds = rows("SELECT fund_id, fund_name FROM funds ORDER BY fund_id")
    fund_options = [""] + [fund["fund_id"] for fund in funds]
    fund_labels = {"": "Unassigned / public source"}
    fund_labels.update({fund["fund_id"]: f"{fund['fund_id']} - {fund['fund_name']}" for fund in funds})

    uploaded = st.file_uploader("Upload PDF", type=["pdf"], key="intake_upload_pdf")
    col1, col2 = st.columns(2)
    with col1:
        fund_id = st.selectbox("Fund", fund_options, format_func=lambda fid: fund_labels[fid], key="intake_fund")
        document_type = st.selectbox(
            "Document type",
            ["factsheet", "presentation", "other"],
            key="intake_document_type",
        )
        document_date = st.text_input("Document date", placeholder="YYYY-MM-DD if known", key="intake_document_date")
    with col2:
        document_title = st.text_input("Document title", key="intake_document_title")
        confidentiality_level = st.selectbox("Confidentiality", ["internal", "restricted", "public"], key="intake_confidentiality")
        uploaded_by = st.text_input("Uploaded by", value="christian", key="intake_uploaded_by")

    notes = st.text_area("Notes", key="intake_notes")
    ingest_now = st.checkbox("Ingest pages immediately", value=True, key="intake_ingest_now")

    if st.button("Register PDF", disabled=uploaded is None or not document_title, key="intake_register_pdf"):
        with connect_db() as connection:
            result = register_pdf_bytes(
                connection,
                uploaded.name,
                uploaded.getvalue(),
                fund_id=fund_id or None,
                document_type=document_type,
                document_title=document_title,
                document_date=document_date or None,
                confidentiality_level=confidentiality_level,
                uploaded_by=uploaded_by,
                notes=notes,
            )
            if result["status"] == "registered" and ingest_now:
                ingest_result = ingest_document_pages(connection, result["document_id"])
                result["page_ingestion"] = ingest_result
            connection.commit()
        st.success(result)
        st.rerun()

    st.subheader("Metadata Suggestions / Review")
    table_view("document_classification_suggestions")


def render_approval_queue():
    pending = rows(
        """
        SELECT
            ef.*,
            f.fund_name,
            sd.document_title,
            sd.file_name
        FROM extracted_facts ef
        JOIN funds f ON f.fund_id = ef.fund_id
        JOIN source_documents sd ON sd.document_id = ef.source_document_id
        WHERE ef.approval_status = 'pending'
          AND NOT (
              ef.fact_category = 'performance'
              AND ef.fact_name IN ('monthly_return', 'quarterly_return', 'annual_return', 'ytd_return')
          )
        ORDER BY ef.created_at, ef.fact_category, ef.fact_name
        """
    )
    st.subheader(f"Pending Facts ({len(pending)})")
    st.caption("Structured return rows are reviewed in the Returns tab as a table-first workflow.")
    reviewer = st.text_input("Reviewer", value="christian", key="approval_queue_reviewer")

    if not pending:
        st.info("No pending facts.")
        return

    for fact in pending:
        with st.container(border=True):
            st.markdown(f"**{fact['fund_name']}** · `{fact['fact_category']}.{fact['fact_name']}`")
            st.caption(f"{fact['document_title']} · page {fact['page_number'] or 'unknown'}")
            cols = st.columns([1, 1])
            with cols[0]:
                approved_value = st.text_input(
                    "Approved value",
                    value=fact["raw_value"],
                    key=f"value_{fact['fact_id']}",
                )
                normalized_value = st.text_input(
                    "Normalized value",
                    value=fact["normalized_value"] or fact["raw_value"],
                    key=f"norm_{fact['fact_id']}",
                )
                note = st.text_input("Review note", key=f"note_{fact['fact_id']}")
                a, r = st.columns(2)
                if a.button("Approve", key=f"approve_{fact['fact_id']}"):
                    with connect_db() as connection:
                        approve_extracted_fact(
                            connection,
                            fact["fact_id"],
                            approved_value,
                            normalized_value,
                            reviewer,
                            note,
                        )
                        connection.commit()
                    st.rerun()
                if r.button("Reject", key=f"reject_{fact['fact_id']}"):
                    with connect_db() as connection:
                        reject_extracted_fact(connection, fact["fact_id"], reviewer, note)
                        connection.commit()
                    st.rerun()
            with cols[1]:
                render_evidence_panel(fact)


def render_promotion():
    st.subheader("Approved Fact Promotion")
    last_skips = st.session_state.pop("last_promotion_skips", None)
    if last_skips:
        st.warning(f"{len(last_skips)} approved fact(s) were skipped during the last promotion run.")
        st.dataframe(
            pd.DataFrame(last_skips, columns=["approved_fact_id", "fact_category", "fact_name", "reason"]),
            use_container_width=True,
            hide_index=True,
        )
    approved = query(
        """
        SELECT approved_fact_id, fund_id, fact_category, fact_name, approved_value,
               promotion_status, promoted_to_table, promoted_at
        FROM approved_facts
        ORDER BY approved_at DESC
        """
    )
    st.dataframe(approved, use_container_width=True)
    if st.button("Promote pending approved facts", key="promotion_promote_pending"):
        with connect_db() as connection:
            promoted, skipped = promote_all_pending(connection, user="streamlit")
            connection.commit()
        st.session_state["last_promotion_skips"] = skipped
        st.success(f"Promoted {len(promoted)} facts; skipped {len(skipped)}.")
        st.rerun()


def render_clean_tables():
    tables = [
        "funds",
        "fund_characteristics",
        "fund_profile_attributes",
        "fund_people",
        "fund_terms",
        "fund_strategy",
        "proposed_return_rows",
        "performance_returns",
        "benchmark_returns",
        "fund_metrics",
        "fund_exposures",
        "fund_writeups",
        "fund_flags",
        "data_imports",
        "public_campaigns",
        "campaign_events",
    ]
    selected = st.selectbox("Clean table", tables, key="admin_clean_table")
    table_view(selected)


def render_documents():
    st.subheader("Documents")
    table_view("source_documents")
    st.subheader("Pages")
    doc_id = st.text_input("Document ID", value="doc_003", key="documents_doc_id")
    if doc_id:
        page_controls = st.columns([1, 1, 1])
        page_number = page_controls[0].number_input("Page", min_value=1, value=1, step=1, key="documents_page_number")
        dpi = page_controls[1].number_input("Render DPI", min_value=100, max_value=300, value=180, step=20, key="documents_render_dpi")
        if page_controls[2].button("Render page image", key="documents_render_page_image"):
            with connect_db() as connection:
                images = render_document_page_images(connection, doc_id, pages=[int(page_number)], dpi=int(dpi))
                connection.commit()
            st.success(f"Rendered {len(images)} page image(s).")

        render_return_extraction_controls(doc_id, page_number, dpi, "documents")

        pages = query(
            "SELECT page_number, extraction_status, substr(page_text, 1, 1000) AS page_text_preview FROM document_pages WHERE document_id = ? ORDER BY page_number",
            [doc_id],
        )
        st.dataframe(pages, use_container_width=True)
        images_df = query(
            "SELECT page_number, image_path, image_width, image_height, render_dpi, created_at FROM document_page_images WHERE document_id = ? ORDER BY page_number, render_dpi",
            [doc_id],
        )
        st.subheader("Rendered Page Images")
        st.dataframe(images_df, use_container_width=True)


def render_conflicts():
    st.subheader("Fact Conflicts")
    conflicts = query(
        """
        SELECT *
        FROM fact_conflicts
        ORDER BY created_at DESC
        """
    )
    st.dataframe(conflicts, use_container_width=True)


def render_fact_admin():
    st.subheader("Rejected Fact Admin")
    st.caption("Reopen a rejected proposed fact for another pass through the normal approval queue.")

    doc_filter = st.text_input("Optional document ID filter", value="", placeholder="doc_003", key="fact_admin_doc_filter")
    params = []
    where = "WHERE ef.approval_status = 'rejected'"
    if doc_filter.strip():
        where += " AND ef.source_document_id = ?"
        params.append(doc_filter.strip())

    rejected = rows(
        f"""
        SELECT
            ef.*,
            f.fund_name,
            sd.document_title,
            sd.file_name
        FROM extracted_facts ef
        JOIN funds f ON f.fund_id = ef.fund_id
        JOIN source_documents sd ON sd.document_id = ef.source_document_id
        {where}
        ORDER BY ef.reviewed_at DESC, ef.created_at DESC
        """,
        params,
    )
    st.caption(f"Rejected facts found: {len(rejected)}")
    if not rejected:
        st.info("No rejected facts match the current filter.")
        return

    selected_fact_id = st.selectbox(
        "Rejected fact",
        options=[fact["fact_id"] for fact in rejected],
        format_func=lambda fact_id: next(
            (
                f"{fact['fund_name']} · {fact['fact_category']}.{fact['fact_name']} · "
                f"{fact['document_title']} · page {fact['page_number'] or 'unknown'}"
            )
            for fact in rejected
            if fact["fact_id"] == fact_id
        ),
        key="fact_admin_rejected_fact",
    )
    fact = next(fact for fact in rejected if fact["fact_id"] == selected_fact_id)

    cols = st.columns([1, 1])
    with cols[0]:
        reviewer = st.text_input("Reviewer", value="christian", key=f"reopen_reviewer_{fact['fact_id']}")
        raw_value = st.text_area(
            "Revised raw value",
            value=fact["raw_value"],
            height=120,
            key=f"reopen_raw_{fact['fact_id']}",
        )
        normalized_value = st.text_input(
            "Revised normalized value",
            value=fact["normalized_value"] or fact["raw_value"],
            key=f"reopen_norm_{fact['fact_id']}",
        )
        note = st.text_input(
            "Reopen note",
            value=f"Reopened rejected fact {fact['fact_id']} for review.",
            key=f"reopen_note_{fact['fact_id']}",
        )
        if st.button("Reopen as pending", key=f"reopen_{fact['fact_id']}"):
            with connect_db() as connection:
                reopen_rejected_fact(
                    connection,
                    fact["fact_id"],
                    raw_value,
                    normalized_value,
                    reviewer,
                    note,
                )
                connection.commit()
            st.success("Fact reopened. It will appear in the Approval Queue.")
            st.rerun()
    with cols[1]:
        st.markdown("**Last rejection**")
        st.write(
            {
                "reviewed_by": fact["reviewed_by"],
                "reviewed_at": fact["reviewed_at"],
                "review_note": fact["review_note"],
            }
        )
        render_evidence_panel(fact)


def render_analysis_runs():
    st.subheader("AI Analyst")
    st.caption("Uses approved FundSnapshot data only. Raw pending LLM extractions are not included.")
    all_funds = fund_options()
    fund_ids = st.multiselect(
        "Funds to include",
        options=[fund["fund_id"] for fund in all_funds],
        default=[all_funds[0]["fund_id"]] if all_funds else [],
        format_func=lambda fid: next(fund["fund_name"] for fund in all_funds if fund["fund_id"] == fid),
    )
    left, right = st.columns([2, 1])
    with left:
        question = st.text_area(
            "Ask about approved source-of-truth data",
            height=160,
            placeholder="Example: Identify the main diligence gaps, unusual characteristics, and data limitations for these funds.",
        )
    with right:
        model_name = st.text_input("Model", value=DEFAULT_ANALYSIS_MODEL, key="analysis_model_name")
        reasoning_effort = st.selectbox(
            "Reasoning effort",
            ["low", "medium", "high"],
            index=["low", "medium", "high"].index(DEFAULT_REASONING_EFFORT)
            if DEFAULT_REASONING_EFFORT in {"low", "medium", "high"}
            else 1,
            key="analysis_reasoning_effort",
        )
        created_by = st.text_input("Created by", value="christian", key="analysis_created_by")

    action_col1, action_col2 = st.columns([1, 1])
    if action_col1.button("Run analysis", disabled=not fund_ids or not question):
        try:
            with connect_db() as connection:
                analysis_id, output_text = run_snapshot_chat(
                    connection,
                    fund_ids,
                    question,
                    model_name=model_name,
                    reasoning_effort=reasoning_effort,
                    created_by=created_by,
                )
                connection.commit()
            st.success(f"Saved analysis: {analysis_id}")
            st.markdown(output_text)
        except Exception as exc:
            st.error(str(exc))

    if action_col2.button("Generate analysis workbook", disabled=not fund_ids):
        with connect_db() as connection:
            output_path = export_analysis_workbook(connection, fund_ids=fund_ids)
        st.success(f"Workbook created: {output_path}")
        st.download_button(
            "Download workbook",
            data=Path(output_path).read_bytes(),
            file_name=Path(output_path).name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.subheader("Saved Analysis Runs")
    table_view("analysis_runs")


def render_comparison():
    st.subheader("Fund Comparison")
    all_funds = fund_options()
    name_by_id = fund_name_lookup(all_funds)
    selected = st.multiselect(
        "Funds to compare",
        options=[fund["fund_id"] for fund in all_funds],
        default=[fund["fund_id"] for fund in all_funds[:2]],
        format_func=lambda fid: next(fund["fund_name"] for fund in all_funds if fund["fund_id"] == fid),
        key="comparison_selected_funds",
    )
    if not selected:
        st.info("Select at least one fund.")
        return

    comparison = cached_compare_funds(tuple(selected), data_version())

    st.markdown(comparison["comparison_paragraph"])
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Return Statistics**")
        st.dataframe(
            with_fund_names(pd.DataFrame(comparison["return_statistics"]), name_by_id),
            use_container_width=True,
            hide_index=True,
        )
    with c2:
        st.markdown("**Analysis Readiness Scores**")
        st.dataframe(
            with_fund_names(pd.DataFrame(comparison["analysis_readiness_scores"]), name_by_id),
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("**Characteristic Similarity Matrix**")
    st.dataframe(
        with_fund_names(pd.DataFrame(comparison["similarity_matrix"]), name_by_id),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**Aligned Return Comparison**")
    aligned_returns = pd.DataFrame(comparison.get("aligned_returns", []))
    if aligned_returns.empty:
        st.info("No overlapping approved return periods found for the selected funds.")
    else:
        st.dataframe(with_fund_names(aligned_returns, name_by_id), use_container_width=True, hide_index=True)

    if st.button("Generate comparison workbook", key="comparison_generate_workbook"):
        from core.config import EXPORT_DIR

        output_path = EXPORT_DIR / "fund_comparison.xlsx"
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            with_fund_names(pd.DataFrame(comparison["profiles"]), name_by_id).to_excel(writer, sheet_name="profiles", index=False)
            with_fund_names(pd.DataFrame(comparison["return_statistics"]), name_by_id).to_excel(writer, sheet_name="return_stats", index=False)
            with_fund_names(pd.DataFrame(comparison.get("aligned_returns", [])), name_by_id).to_excel(writer, sheet_name="aligned_returns", index=False)
            with_fund_names(pd.DataFrame(comparison["analysis_readiness_scores"]), name_by_id).to_excel(writer, sheet_name="readiness_scores", index=False)
            with_fund_names(pd.DataFrame(comparison["similarity_matrix"]), name_by_id).to_excel(writer, sheet_name="similarity", index=False)
            pd.DataFrame([{"paragraph": comparison["comparison_paragraph"]}]).to_excel(writer, sheet_name="summary", index=False)
        st.success(f"Workbook created: {output_path}")
        st.download_button(
            "Download comparison workbook",
            data=Path(output_path).read_bytes(),
            file_name=Path(output_path).name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    st.divider()
    st.subheader("Web Research Overlay")
    st.caption("Optional. Uses web search as public context and saves the result as analysis, not source-of-truth.")
    web_fund = st.selectbox(
        "Fund for public web comparison",
        options=selected,
        format_func=lambda fid: next(fund["fund_name"] for fund in all_funds if fund["fund_id"] == fid),
        key="comparison_web_fund",
    )
    web_question = st.text_area(
        "Web comparison question",
        value="Compare the fund's stated strategy and characteristics against public evidence of activist behavior.",
        key="comparison_web_question",
    )
    if st.button("Run web research comparison", key="comparison_run_web_research"):
        try:
            with connect_db() as connection:
                analysis_id, output_text = run_web_research_comparison(
                    connection,
                    web_fund,
                    web_question,
                    created_by="streamlit",
                )
                connection.commit()
            st.success(f"Saved web research analysis: {analysis_id}")
            st.markdown(output_text)
        except Exception as exc:
            st.error(str(exc))


def render_review_center():
    review_tabs = st.tabs(["Approval Queue", "Promotion", "Conflicts"])
    with review_tabs[0]:
        render_approval_queue()
    with review_tabs[1]:
        render_promotion()
    with review_tabs[2]:
        render_conflicts()


def render_data_exports():
    st.subheader("Data & Exports")
    col1, col2 = st.columns(2)
    if col1.button("Create source-of-truth workbook", key="exports_create_source_truth_workbook"):
        with connect_db() as connection:
            output_path = export_source_truth_workbook(connection)
        st.success(f"Workbook created: {output_path}")
        st.download_button(
            "Download source-of-truth workbook",
            data=Path(output_path).read_bytes(),
            file_name=Path(output_path).name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    if col2.button("Create analysis workbook", key="exports_create_analysis_workbook"):
        with connect_db() as connection:
            output_path = export_analysis_workbook(connection)
        st.success(f"Workbook created: {output_path}")
        st.download_button(
            "Download analysis workbook",
            data=Path(output_path).read_bytes(),
            file_name=Path(output_path).name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    table_choice = st.selectbox(
        "Preview table",
        [
            "approved_facts",
            "fund_characteristics",
            "fund_writeups",
            "fund_flags",
            "proposed_return_rows",
            "performance_returns",
            "fund_metrics",
            "analysis_runs",
        ],
        key="exports_preview_table",
    )
    table_view(table_choice)


def render_admin():
    admin_tabs = st.tabs(["Clean Tables", "Raw Documents", "System Tree", "Fact Admin"])
    with admin_tabs[0]:
        render_clean_tables()
    with admin_tabs[1]:
        render_documents()
    with admin_tabs[2]:
        render_overview()
    with admin_tabs[3]:
        render_fact_admin()


def main():
    migrated_rows = ensure_return_row_backfill(data_version())
    st.title("Japan Activist Funds Research Platform")
    st.caption(f"Database: {DB_PATH}")
    if migrated_rows:
        st.caption(f"Return staging foundation refreshed: {migrated_rows} legacy return row(s) synced into proposed return storage.")

    tabs = st.tabs(
        [
            "Home",
            "Funds",
            "Returns",
            "Comparison",
            "AI Analyst",
            "Review",
            "Documents",
            "Data & Exports",
            "Admin",
        ]
    )
    with tabs[0]:
        render_overview()
    with tabs[1]:
        render_fund_explorer()
    with tabs[2]:
        render_returns_workspace()
    with tabs[3]:
        render_comparison()
    with tabs[4]:
        render_analysis_runs()
    with tabs[5]:
        render_review_center()
    with tabs[6]:
        render_document_intake()
    with tabs[7]:
        render_data_exports()
    with tabs[8]:
        render_admin()


if __name__ == "__main__":
    main()

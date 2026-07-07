import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from core.db_backup import create_sqlite_backup
from core.db import connect_db
from core.document_bundle import build_document_bundle
from core.document_review import (
    REVIEWABLE_SECTIONS,
    approve_document_section,
    reject_document_fact,
    reject_document_return_row,
    reject_document_section,
    revise_and_approve_fact,
    revise_and_approve_return_row,
)
from core.factsheet_schema import SECTION_TITLES
from core.fund_views import build_return_table_previews, format_return_cell
from core.proposal_cleanup import sanitize_pending_document_facts
from core.return_canonicalization import canonicalize_performance_returns


def _format_fact(row, show_ids=False):
    label = row.get("display_name") or row.get("fact_name")
    value = row.get("normalized_value") or row.get("raw_value")
    status = row.get("approval_status")
    page = row.get("page_number") or "?"
    quote = row.get("quoted_text") or ""
    if len(quote) > 140:
        quote = quote[:137] + "..."
    identifier = f"{row.get('fact_id')} " if show_ids and row.get("fact_id") else ""
    return f"- {identifier}{label}: {value} [{status}] (p.{page}) {quote}"


def _status_text(counts):
    parts = []
    for status in ("pending", "approved", "rejected"):
        count = counts.get(status, 0)
        if count:
            parts.append(f"{status}={count}")
    return ", ".join(parts) if parts else "no rows"


def _print_section(title, rows, counts, show_ids=False):
    print(f"\n## {title} [{_status_text(counts)}]")
    if not rows:
        print("No rows.")
        return
    for row in rows:
        print(_format_fact(row, show_ids=show_ids))


def _print_standardized_entry(entry, show_ids=False):
    print(f"- {entry['label']} [{entry['status_summary']}]")
    print(f"  {entry['display_value'] or entry['empty_text']}")
    if entry.get("approved_display_value"):
        print(f"  Current approved: {entry['approved_display_value']} ({entry.get('approved_source') or 'approved value'})")
    if show_ids and entry["rows"]:
        for row in entry["rows"]:
            print(f"  {_format_fact(row, show_ids=True)}")


def _print_standardized_section(title, entries, counts, show_ids=False):
    print(f"\n## {title} [{_status_text(counts)}]")
    if not entries:
        print("No fields.")
        return
    for entry in entries:
        _print_standardized_entry(entry, show_ids=show_ids)


def _render_return_tables(return_rows, show_ids=False):
    previews = build_return_table_previews(return_rows)
    print(f"\n## Returns [{_status_text({'pending': sum(1 for row in return_rows if row.get('status') == 'pending'), 'approved': sum(1 for row in return_rows if row.get('status') == 'approved'), 'rejected': sum(1 for row in return_rows if row.get('status') == 'rejected')})}]")
    if not previews:
        print("No proposed return rows.")
        return
    for preview in previews:
        heading = preview.get("display_name") or preview["table_name"]
        print(f"\n### {heading}")
        preview_df = pd.DataFrame(index=preview["row_order"], columns=preview["column_order"])
        for (row_label, column_label), value in preview["values"].items():
            preview_df.loc[row_label, column_label] = format_return_cell(value)
        print(preview_df.fillna("").to_string())
    if show_ids:
        detail_columns = [
            "proposed_row_id",
            "share_class",
            "period_type",
            "period_end_date",
            "return_type",
            "return_value",
            "status",
        ]
        detail_rows = [
            {column: row.get(column) for column in detail_columns}
            for row in return_rows
        ]
        print("\nPending/Reviewed Return Row IDs")
        print(pd.DataFrame(detail_rows).fillna("").to_string(index=False))


def _print_bundle(bundle, section=None, show_ids=False):
    document = bundle["document"]
    print(f"# Review Bundle: {document['document_title']} ({document['document_id']})")
    print(f"Fund: {document.get('fund_name') or document.get('fund_id') or 'Unassigned'}")
    print(f"Manager: {document.get('manager_name') or 'Unknown'}")
    print(f"Document type: {document.get('document_type')}")
    print(f"Document date: {document.get('document_date') or 'Undated'}")
    print(f"Pages: {document.get('page_count') or 'Unknown'}")
    print(
        "Pending review items: "
        f"{sum(len(bundle['pending_fact_ids_by_section'][name]) for name in bundle['section_order'])} descriptive, "
        f"{len(bundle['pending_return_row_ids'])} return rows"
    )

    if section == "returns":
        _render_return_tables(bundle["returns"], show_ids=show_ids)
        return

    if section:
        sections = [section]
    else:
        sections = ["overview", "manager", "strategy", "terms", "metrics", "notes_flags"]
        if bundle.get("pending_only"):
            sections = [
                section_name
                for section_name in sections
                if bundle["section_status_counts"][section_name].get("pending", 0) > 0
            ]
    for section_name in sections:
        entries = bundle["standardized_sections"][section_name]
        if bundle.get("pending_only"):
            entries = [entry for entry in entries if entry.get("candidate_count", 0) > 0]
        _print_standardized_section(
            SECTION_TITLES[section_name],
            entries,
            bundle["section_status_counts"][section_name],
            show_ids=show_ids,
        )

    if not section:
        _render_return_tables(bundle["returns"], show_ids=show_ids)


def _print_action_summary(action, result):
    print(
        f"{action}: section={result['section']} "
        f"descriptive_pending={result['descriptive_pending_count']} "
        f"return_pending={result['return_pending_count']}"
    )
    if "approved_fact_ids" in result:
        print(
            f"Approved descriptive facts: {len(result['approved_fact_ids'])} | "
            f"Promoted descriptive records: {len(result['promoted'])}"
        )
        print(
            f"Approved return rows: {len(result['approved_return_fact_ids'])} | "
            f"Promoted return records: {len(result['return_promoted'])}"
        )
        if result["promotion_skips"]:
            print(f"Descriptive promotion skips: {len(result['promotion_skips'])}")
            for approved_fact_id, fact_category, fact_name, reason in result["promotion_skips"]:
                print(f"- {approved_fact_id} {fact_category}.{fact_name}: {reason}")
        if result["return_promotion_skips"]:
            print(f"Return promotion skips: {len(result['return_promotion_skips'])}")
            for approved_fact_id, fact_category, fact_name, reason in result["return_promotion_skips"]:
                print(f"- {approved_fact_id} {fact_category}.{fact_name}: {reason}")
    else:
        print(
            f"Rejected descriptive facts: {len(result['rejected_fact_ids'])} | "
            f"Rejected return rows: {len(result['rejected_return_row_ids'])}"
        )


def _print_specific_action_result(action, result):
    print(f"{action}: {result}")


def _print_cleanup_summary(action, summary):
    print(
        f"{action}: document={summary['document_id']} "
        f"pending={summary['pending_count']} keep={summary['kept_count']} "
        f"duplicates={summary['duplicate_count']}"
    )
    for row in summary["duplicate_rows"]:
        value = row.get("normalized_value") or row.get("raw_value")
        print(
            f"- {row['fact_id']} | {row['fact_category']}.{row['fact_name']} | "
            f"page {row.get('page_number') or '?'} | {value}"
        )


def _print_return_cleanup_summary(action, fund_id, summary):
    print(f"{action}: fund={fund_id} duplicate_groups={len(summary)}")
    for item in summary:
        print(
            f"- {item['fund_id']} | {item['share_class'] or '(blank share class)'} | "
            f"{item['period_type']} | {item['period_end_date']} | "
            f"keep={item['canonical_return_id']} ({item['canonical_return_type']}) | "
            f"drop={len(item['duplicate_return_ids'])}"
        )
        for duplicate_return_id, duplicate_return_type in zip(item["duplicate_return_ids"], item["duplicate_return_types"]):
            print(f"  - duplicate {duplicate_return_id} ({duplicate_return_type})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", required=True)
    parser.add_argument("--section", choices=["overview", "manager", "strategy", "terms", "metrics", "notes_flags", "returns"])
    parser.add_argument("--pending-only", action="store_true")
    parser.add_argument("--include-reviewed", action="store_true")
    parser.add_argument("--show-ids", action="store_true")
    parser.add_argument("--reviewer", default="christian")
    parser.add_argument("--note", default="")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument("--approved-value")
    parser.add_argument("--normalized-value")
    parser.add_argument("--return-value")
    parser.add_argument("--raw-value-text")
    parser.add_argument("--share-class")
    parser.add_argument("--period-type")
    parser.add_argument("--period-start-date")
    parser.add_argument("--period-end-date")
    parser.add_argument("--return-type")
    parser.add_argument("--unit")
    parser.add_argument("--currency")
    parser.add_argument("--benchmark-name")
    parser.add_argument("--evidence-text")
    parser.add_argument("--table-name")
    parser.add_argument("--row-label")
    parser.add_argument("--column-label")
    action_group = parser.add_mutually_exclusive_group()
    action_group.add_argument("--approve-section", choices=REVIEWABLE_SECTIONS)
    action_group.add_argument("--reject-section", choices=REVIEWABLE_SECTIONS)
    action_group.add_argument("--approve-all-pending", action="store_true")
    action_group.add_argument("--reject-all-pending", action="store_true")
    action_group.add_argument("--approve-fact-id")
    action_group.add_argument("--reject-fact-id")
    action_group.add_argument("--approve-return-row-id")
    action_group.add_argument("--reject-return-row-id")
    action_group.add_argument("--preview-cleanup", action="store_true")
    action_group.add_argument("--apply-cleanup", action="store_true")
    action_group.add_argument("--preview-return-cleanup", action="store_true")
    action_group.add_argument("--apply-return-cleanup", action="store_true")
    action_group.add_argument("--preview-repair", action="store_true")
    action_group.add_argument("--apply-repair", action="store_true")
    args = parser.parse_args()
    args.pending_only = args.pending_only or not args.include_reviewed

    with connect_db() as connection:
        document = connection.execute(
            "SELECT * FROM source_documents WHERE document_id = ?",
            (args.document_id,),
        ).fetchone()
        if document is None:
            raise ValueError(f"Unknown document_id: {args.document_id}")
        document = dict(document)
        fund_id = document.get("fund_id")
        backup_path = None

        def ensure_backup(label):
            nonlocal backup_path
            if args.no_backup:
                return None
            if backup_path is None:
                backup_path = create_sqlite_backup(connection, label=label)
                print(f"BACKUP CREATED: {backup_path}")
            return backup_path

        if args.preview_repair:
            cleanup_summary = sanitize_pending_document_facts(
                connection,
                args.document_id,
                reviewer=args.reviewer,
                apply=False,
            )
            return_summary = canonicalize_performance_returns(
                connection,
                fund_id=fund_id,
                user=args.reviewer,
                apply=False,
            )
            _print_cleanup_summary("CLEANUP PREVIEW", cleanup_summary)
            _print_return_cleanup_summary("RETURN CLEANUP PREVIEW", fund_id, return_summary)
            args.pending_only = True
        elif args.apply_repair:
            ensure_backup(f"repair_{fund_id or args.document_id}")
            cleanup_summary = sanitize_pending_document_facts(
                connection,
                args.document_id,
                reviewer=args.reviewer,
                apply=True,
            )
            return_summary = canonicalize_performance_returns(
                connection,
                fund_id=fund_id,
                user=args.reviewer,
                apply=True,
            )
            connection.commit()
            _print_cleanup_summary("CLEANUP APPLIED", cleanup_summary)
            _print_return_cleanup_summary("RETURN CLEANUP APPLIED", fund_id, return_summary)
            args.pending_only = True
        elif args.preview_cleanup:
            summary = sanitize_pending_document_facts(
                connection,
                args.document_id,
                reviewer=args.reviewer,
                apply=False,
            )
            _print_cleanup_summary("CLEANUP PREVIEW", summary)
        elif args.apply_cleanup:
            ensure_backup(f"pending_cleanup_{args.document_id}")
            summary = sanitize_pending_document_facts(
                connection,
                args.document_id,
                reviewer=args.reviewer,
                apply=True,
            )
            connection.commit()
            _print_cleanup_summary("CLEANUP APPLIED", summary)
        elif args.preview_return_cleanup:
            summary = canonicalize_performance_returns(
                connection,
                fund_id=fund_id,
                user=args.reviewer,
                apply=False,
            )
            _print_return_cleanup_summary("RETURN CLEANUP PREVIEW", fund_id, summary)
        elif args.apply_return_cleanup:
            ensure_backup(f"return_cleanup_{fund_id or args.document_id}")
            summary = canonicalize_performance_returns(
                connection,
                fund_id=fund_id,
                user=args.reviewer,
                apply=True,
            )
            connection.commit()
            _print_return_cleanup_summary("RETURN CLEANUP APPLIED", fund_id, summary)
        elif args.approve_section:
            result = approve_document_section(
                connection,
                args.document_id,
                reviewer=args.reviewer,
                section=args.approve_section,
                note=args.note or f"Approved pending {args.approve_section} items from document bundle.",
            )
            connection.commit()
            _print_action_summary("APPROVED", result)
        elif args.reject_section:
            result = reject_document_section(
                connection,
                args.document_id,
                reviewer=args.reviewer,
                section=args.reject_section,
                note=args.note or f"Rejected pending {args.reject_section} items from document bundle.",
            )
            connection.commit()
            _print_action_summary("REJECTED", result)
        elif args.approve_all_pending:
            result = approve_document_section(
                connection,
                args.document_id,
                reviewer=args.reviewer,
                section=None,
                note=args.note or "Approved all pending items from document bundle.",
            )
            connection.commit()
            _print_action_summary("APPROVED", result)
        elif args.reject_all_pending:
            result = reject_document_section(
                connection,
                args.document_id,
                reviewer=args.reviewer,
                section=None,
                note=args.note or "Rejected all pending items from document bundle.",
            )
            connection.commit()
            _print_action_summary("REJECTED", result)
        elif args.approve_fact_id:
            result = revise_and_approve_fact(
                connection,
                args.approve_fact_id,
                reviewer=args.reviewer,
                approved_value=args.approved_value,
                normalized_value=args.normalized_value,
                note=args.note,
            )
            connection.commit()
            _print_specific_action_result("APPROVED FACT", result)
        elif args.reject_fact_id:
            result = reject_document_fact(
                connection,
                args.reject_fact_id,
                reviewer=args.reviewer,
                note=args.note,
            )
            connection.commit()
            _print_specific_action_result("REJECTED FACT", result)
        elif args.approve_return_row_id:
            result = revise_and_approve_return_row(
                connection,
                args.approve_return_row_id,
                reviewer=args.reviewer,
                note=args.note,
                share_class=args.share_class,
                period_type=args.period_type,
                period_start_date=args.period_start_date,
                period_end_date=args.period_end_date,
                return_type=args.return_type,
                return_value=args.return_value,
                raw_value_text=args.raw_value_text,
                unit=args.unit,
                currency=args.currency,
                benchmark_name=args.benchmark_name,
                evidence_text=args.evidence_text,
                table_name=args.table_name,
                row_label=args.row_label,
                column_label=args.column_label,
            )
            connection.commit()
            _print_specific_action_result("APPROVED RETURN ROW", result)
        elif args.reject_return_row_id:
            result = reject_document_return_row(
                connection,
                args.reject_return_row_id,
                reviewer=args.reviewer,
                note=args.note,
            )
            connection.commit()
            _print_specific_action_result("REJECTED RETURN ROW", result)

        bundle = build_document_bundle(connection, args.document_id, pending_only=args.pending_only)
    _print_bundle(bundle, section=args.section, show_ids=args.show_ids or args.pending_only)


if __name__ == "__main__":
    main()

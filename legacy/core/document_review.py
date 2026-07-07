from core.approval import (
    approve_extracted_fact,
    approve_pending_facts,
    reject_pending_facts,
    reopen_rejected_fact,
    revise_pending_fact,
)
from core.document_bundle import SECTION_ORDER, build_document_bundle
from core.promotion import promote_approved_fact_ids
from core.return_rows import approve_proposed_return_rows, reject_proposed_return_rows, revise_proposed_return_row


REVIEWABLE_SECTIONS = SECTION_ORDER + ["returns"]


def pending_review_items(bundle, section=None):
    if section and section not in REVIEWABLE_SECTIONS:
        raise ValueError(f"Unknown review section: {section}")

    if section == "returns":
        return [], list(bundle["pending_return_row_ids"])

    if section:
        return list(bundle["pending_fact_ids_by_section"].get(section, [])), []

    fact_ids = []
    for section_name in SECTION_ORDER:
        fact_ids.extend(bundle["pending_fact_ids_by_section"].get(section_name, []))
    return fact_ids, list(bundle["pending_return_row_ids"])


def approve_document_section(connection, document_id, reviewer, section=None, note=""):
    bundle = build_document_bundle(connection, document_id)
    fact_ids, return_row_ids = pending_review_items(bundle, section=section)

    approved_fact_ids = approve_pending_facts(connection, fact_ids, reviewer=reviewer, note=note)
    promoted, promotion_skips = promote_approved_fact_ids(connection, approved_fact_ids, user=reviewer)

    return_approved_fact_ids, return_promoted, return_promotion_skips = approve_proposed_return_rows(
        connection,
        return_row_ids,
        reviewer=reviewer,
        note=note,
    )

    return {
        "document_id": document_id,
        "section": section or "all",
        "approved_fact_ids": approved_fact_ids,
        "promoted": promoted,
        "promotion_skips": promotion_skips,
        "approved_return_fact_ids": return_approved_fact_ids,
        "return_promoted": return_promoted,
        "return_promotion_skips": return_promotion_skips,
        "descriptive_pending_count": len(fact_ids),
        "return_pending_count": len(return_row_ids),
    }


def reject_document_section(connection, document_id, reviewer, section=None, note=""):
    bundle = build_document_bundle(connection, document_id)
    fact_ids, return_row_ids = pending_review_items(bundle, section=section)

    reject_pending_facts(connection, fact_ids, reviewer=reviewer, note=note)
    reject_proposed_return_rows(connection, return_row_ids, reviewer=reviewer, note=note)

    return {
        "document_id": document_id,
        "section": section or "all",
        "rejected_fact_ids": fact_ids,
        "rejected_return_row_ids": return_row_ids,
        "descriptive_pending_count": len(fact_ids),
        "return_pending_count": len(return_row_ids),
    }


def revise_and_approve_fact(
    connection,
    fact_id,
    *,
    reviewer,
    approved_value=None,
    normalized_value=None,
    note="",
):
    revised = revise_pending_fact(
        connection,
        fact_id,
        raw_value=approved_value,
        normalized_value=normalized_value,
        reviewer=reviewer,
        note=note or f"Revised pending fact {fact_id} before approval.",
    )
    approved_fact_id = approve_extracted_fact(
        connection,
        fact_id,
        revised["raw_value"],
        revised["normalized_value"],
        reviewer=reviewer,
        note=note or f"Approved revised fact {fact_id} from document bundle.",
    )
    promoted, promotion_skips = promote_approved_fact_ids(connection, [approved_fact_id], user=reviewer)
    return {
        "fact_id": fact_id,
        "approved_fact_id": approved_fact_id,
        "promoted": promoted,
        "promotion_skips": promotion_skips,
        "approved_value": revised["raw_value"],
        "normalized_value": revised["normalized_value"],
    }


def revise_and_approve_return_row(connection, proposed_row_id, *, reviewer, note="", **updates):
    revised = revise_proposed_return_row(
        connection,
        proposed_row_id,
        reviewer=reviewer,
        note=note or f"Revised pending return row {proposed_row_id} before approval.",
        **updates,
    )
    approved_fact_ids, promoted, promotion_skips = approve_proposed_return_rows(
        connection,
        [proposed_row_id],
        reviewer=reviewer,
        note=note or f"Approved revised return row {proposed_row_id} from document bundle.",
    )
    return {
        "proposed_row_id": proposed_row_id,
        "approved_fact_ids": approved_fact_ids,
        "promoted": promoted,
        "promotion_skips": promotion_skips,
        "revised_row": revised,
    }


def reject_document_fact(connection, fact_id, *, reviewer, note=""):
    reject_pending_facts(connection, [fact_id], reviewer=reviewer, note=note or f"Rejected pending fact {fact_id} from document bundle.")
    return {"fact_id": fact_id}


def reject_document_return_row(connection, proposed_row_id, *, reviewer, note=""):
    reject_proposed_return_rows(
        connection,
        [proposed_row_id],
        reviewer=reviewer,
        note=note or f"Rejected pending return row {proposed_row_id} from document bundle.",
    )
    return {"proposed_row_id": proposed_row_id}


def revise_document_fact(connection, fact_id, *, reviewer, raw_value=None, normalized_value=None, note=""):
    return revise_pending_fact(
        connection,
        fact_id,
        raw_value=raw_value,
        normalized_value=normalized_value,
        reviewer=reviewer,
        note=note or f"Revised pending fact {fact_id} from document bundle.",
    )


def reopen_document_fact(connection, fact_id, *, reviewer, raw_value, normalized_value, note=""):
    reopen_rejected_fact(
        connection,
        fact_id,
        raw_value,
        normalized_value,
        reviewer,
        note or f"Reopened rejected fact {fact_id} from document bundle.",
    )
    return {
        "fact_id": fact_id,
        "raw_value": raw_value,
        "normalized_value": normalized_value,
    }

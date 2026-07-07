import argparse
from pathlib import Path
import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import DEFAULT_MODEL
from core.db import connect_db
from core.documents import render_document_page_image
from core.extraction import build_prompt, extract_with_openai, preflight_for_extraction
from core.return_table_extraction import build_return_table_prompt, extract_return_table_from_page_image
from core.taxonomy import extraction_scope_names


DEFAULT_FACTSHEET_SCOPES = ["profile_terms", "strategy_people", "performance", "writeups_flags"]


def _default_return_pages(document):
    page_count = int(document.get("page_count") or 0)
    document_type = (document.get("document_type") or "").strip().lower()
    if document_type == "factsheet" and page_count:
        return list(range(1, min(page_count, 3) + 1))
    return []


def _selected_return_pages(document, args):
    if args.skip_returns:
        return []
    if args.return_pages:
        return sorted(set(args.return_pages))
    return _default_return_pages(document)


def _existing_return_rows(connection, document_id, page_number):
    return connection.execute(
        """
        SELECT COUNT(*)
        FROM proposed_return_rows
        WHERE source_document_id = ?
          AND page_number = ?
          AND status IN ('pending', 'approved')
        """,
        (document_id, page_number),
    ).fetchone()[0]


def main():
    parser = argparse.ArgumentParser(
        description="Build a proposed standardized factsheet package for one source document."
    )
    parser.add_argument("document_id")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--return-model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--scope", action="append", choices=extraction_scope_names())
    parser.add_argument("--skip-returns", action="store_true")
    parser.add_argument("--return-page", dest="return_pages", action="append", type=int)
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    scopes = args.scope or DEFAULT_FACTSHEET_SCOPES

    with connect_db() as connection:
        document, pages = preflight_for_extraction(
            connection,
            args.document_id,
            require_api_key=not args.dry_run,
        )
        return_pages = _selected_return_pages(document, args)

        print("Proposed factsheet build")
        print(f"Document: {document['document_id']} / {document['document_title']}")
        print(f"Fund: {document.get('fund_id')}")
        print(f"Document type: {document.get('document_type')}")
        print(f"Pages with text: {len(pages)}")
        print(f"Text scopes: {', '.join(scopes)}")
        print(f"Return pages: {', '.join(str(page) for page in return_pages) if return_pages else 'none'}")
        print(f"Text model: {args.model}")
        print(f"Return model: {args.return_model}")

        if args.dry_run:
            print()
            print("Text extraction preflight")
            for scope in scopes:
                prompt = build_prompt(document, pages, scope=scope)
                print(f"- {scope}: prompt characters={len(prompt)}")

            print()
            print("Return extraction preflight")
            for page_number in return_pages:
                image = render_document_page_image(connection, args.document_id, page_number, dpi=args.dpi)
                prompt = build_return_table_prompt(document, page_number)
                print(
                    f"- page {page_number}: render_dpi={args.dpi} image_id={image['image_id']} "
                    f"prompt_characters={len(prompt)} existing_rows={_existing_return_rows(connection, args.document_id, page_number)}"
                )
            print("Dry run only. No OpenAI call made.")
            connection.commit()
            return

        print()
        print("Running text extraction")
        for scope in scopes:
            run_id, fact_count = extract_with_openai(
                connection,
                args.document_id,
                model_name=args.model,
                force=args.force,
                scope=scope,
            )
            print(f"- {scope}: run_id={run_id} proposed_facts={fact_count}")

        if return_pages:
            print()
            print("Running return extraction")
        for page_number in return_pages:
            existing_rows = _existing_return_rows(connection, args.document_id, page_number)
            if existing_rows and not args.force:
                raise ValueError(
                    f"{existing_rows} reviewable return row(s) already exist for document {args.document_id} page {page_number}. "
                    "Use --force to rerun."
                )
            run_id, inserted, skipped = extract_return_table_from_page_image(
                connection,
                args.document_id,
                page_number,
                model_name=args.return_model,
                dpi=args.dpi,
                mock=False,
            )
            print(f"- page {page_number}: run_id={run_id} inserted={inserted} skipped={len(skipped)}")
            if skipped:
                for index, reason in skipped:
                    print(f"  row {index}: {reason}")

        connection.commit()
        print()
        print(f"Done. Review next with: python scripts/review_document_bundle.py --document-id {args.document_id}")


if __name__ == "__main__":
    main()

import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from core.config import DEFAULT_MODEL
from core.db import connect_db
from core.extraction import build_prompt, extract_with_openai, preflight_for_extraction
from core.taxonomy import extraction_scope_names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("document_id")
    parser.add_argument("--model", default=os.getenv("OPENAI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--scope", choices=extraction_scope_names(), default="profile_terms")
    parser.add_argument("--all-scopes", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if load_dotenv:
        load_dotenv()

    with connect_db() as connection:
        scopes = extraction_scope_names() if args.all_scopes else [args.scope]

        document, pages = preflight_for_extraction(
            connection,
            args.document_id,
            require_api_key=not args.dry_run,
        )

        print("Preflight passed")
        print(f"Document: {document['document_id']} / {document['document_title']}")
        print(f"Pages: {len(pages)}")
        print(f"Model: {args.model}")
        print(f"Scopes: {', '.join(scopes)}")

        if args.dry_run:
            for scope in scopes:
                prompt = build_prompt(document, pages, scope=scope)
                print(f"{scope}: prompt characters {len(prompt)}")
            print("Dry run only. No OpenAI call made.")
            return

        for scope in scopes:
            run_id, fact_count = extract_with_openai(
                connection,
                args.document_id,
                model_name=args.model,
                force=args.force,
                scope=scope,
            )
            print(f"{scope}: extraction run saved {run_id}; proposed facts saved {fact_count}")
        connection.commit()


if __name__ == "__main__":
    main()

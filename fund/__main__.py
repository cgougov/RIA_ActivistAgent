"""The terminal. One entrypoint for the whole pipeline:

  fund init                      create the database
  fund migrate                   apply schema/data migrations explicitly
  fund status                    pipeline state at a glance
  fund list                      every fund, its typeable name, and review state
  fund inbox                     pending review queue with suggested commands
  fund next                      the next useful command for the current DB state
  fund doctor                    environment and DB sanity check
  fund ingest                    absorb PDFs (funds/docs from data/seed)
  fund add-doc --fund simplex --type factsheet --date 2026-06-30 path.pdf
                                 register a new current source document
  fund extract DOC [...]         LLM: propose facts / return rows
  fund verify [--doc DOC]        deterministic quote verification backfill
  fund reconcile NAME            deterministic return-number QC
  fund review NAME               review a fund's whole proposed factsheet at once
  fund factsheet NAME            the standardized factsheet (returns at the bottom)
  fund returns NAME              approved return history
  fund screen                    deterministic approved-data screener
  fund compare NAME NAME [...]   deterministic side-by-side, common period only
  fund export NAME               Markdown factsheet export
  fund analyze --funds a,b -q Q  LLM analysis on approved truth
  fund analyze-activism --funds a,b  policy-dependent public activism check
  fund analyses / show / log     saved analyses and the LLM call log

Funds can be named by a handle (simplex, begonia) instead of fund_002.
"""
import argparse
import json
import os
import sys
from importlib import metadata
from pathlib import Path

from fund import analyze as analyze_mod
from fund import compare as compare_mod
from fund import export as export_mod
from fund import extract as extract_mod
from fund import factsheet as factsheet_mod
from fund import ingest as ingest_mod
from fund import review as review_mod
from fund import screen as screen_mod
from fund import similarity as similarity_mod
from fund import universe as universe_mod
from fund import verification as verification_mod
from fund.config import DB_PATH, PAGE_IMAGE_DIR, PDF_DIR, SOURCE_DOC_ROOT
from fund.db import (
    CURRENT_SCHEMA_VERSION,
    backup_database,
    connect,
    create_schema,
    current_schema_version,
    migrate_to_latest,
)


def resolve_fund(conn, token):
    """Turn a user token into a fund_id. Accepts the exact id ('fund_002') or a
    case-insensitive substring of the fund, manager, or reviewed alias."""
    exact = conn.execute("SELECT fund_id FROM funds WHERE fund_id = ?", (token,)).fetchone()
    if exact:
        return exact["fund_id"]
    like = f"%{token}%"
    matches = conn.execute(
        """
        SELECT DISTINCT f.fund_id, f.fund_name
        FROM funds f LEFT JOIN fund_aliases a ON a.fund_id = f.fund_id
        WHERE f.fund_name LIKE ? OR f.manager_name LIKE ? OR a.alias LIKE ?
        ORDER BY f.fund_id
        """, (like, like, like),
    ).fetchall()
    if not matches:
        raise ValueError(f"No fund matches '{token}'. Try: fund list")
    if len(matches) > 1:
        names = ", ".join(f"{m['fund_id']} ({m['fund_name']})" for m in matches)
        raise ValueError(f"'{token}' is ambiguous: {names}")
    return matches[0]["fund_id"]


def _fund_handle(fund_name):
    return compare_mod.short_name(fund_name)


def _pending_conflicts(conn, fund_id):
    proposals = review_mod.pending_proposals(conn, fund_id=fund_id)
    if not proposals:
        return 0, 0
    meta = review_mod.doc_meta(conn, fund_id)
    _, superseded = review_mod.resolve_field_conflicts(proposals, meta)
    groups = {}
    for row in proposals:
        groups.setdefault((row["field_key"], row.get("share_class") or ""), []).append(row)
    conflict_rows = sum(len(rows) for rows in groups.values() if len(rows) > 1)
    return conflict_rows, len(superseded)


def workflow_inbox(conn):
    """Current work queue, grouped by fund, with enough state to choose next action."""
    rows = conn.execute(
        """SELECT f.fund_id, f.fund_name, f.manager_name,
                  (SELECT COUNT(*) FROM facts WHERE fund_id = f.fund_id) AS facts,
                  (SELECT COUNT(*) FROM returns WHERE fund_id = f.fund_id) AS returns_count,
                  (SELECT COUNT(*) FROM proposals WHERE fund_id = f.fund_id AND status='pending') AS pending,
                  (SELECT COUNT(*) FROM proposals
                   WHERE fund_id = f.fund_id AND status='pending' AND quote_verified = 1) AS verified,
                  (SELECT COUNT(*) FROM proposals
                   WHERE fund_id = f.fund_id AND status='pending'
                     AND COALESCE(quote_verified, 0) != 1) AS unverified,
                  (SELECT COUNT(*) FROM proposed_returns
                   WHERE fund_id = f.fund_id AND status='pending') AS pending_returns,
                  (SELECT COUNT(*) FROM documents WHERE fund_id = f.fund_id) AS docs
           FROM funds f ORDER BY f.fund_id"""
    ).fetchall()
    inbox = []
    for row in rows:
        item = dict(row)
        item["handle"] = _fund_handle(row["fund_name"])
        item["conflict_rows"], item["superseded"] = _pending_conflicts(conn, row["fund_id"])
        scopes = conn.execute(
            """SELECT scope, COUNT(*) AS count
               FROM proposals
               WHERE fund_id = ? AND status='pending'
               GROUP BY scope ORDER BY scope""",
            (row["fund_id"],),
        ).fetchall()
        item["scopes"] = {scope["scope"]: scope["count"] for scope in scopes}
        inbox.append(item)
    return inbox


def format_inbox(inbox):
    lines = [
        "Review inbox",
        "------------",
        f"  {'handle':<12} {'fund_id':<10} {'facts':>5} {'returns':>7} {'pending':>8} "
        f"{'verified':>8} {'conflict':>8}  next",
    ]
    active = False
    for row in inbox:
        pending_total = row["pending"] + row["pending_returns"]
        if not pending_total:
            continue
        active = True
        if row["pending"]:
            next_cmd = f"fund review {row['handle']} --list"
        else:
            next_cmd = f"fund review {row['handle']}"
        lines.append(
            f"  {row['handle']:<12} {row['fund_id']:<10} {row['facts']:>5} "
            f"{row['returns_count']:>7} {pending_total:>8} {row['verified']:>8} "
            f"{row['conflict_rows']:>8}  {next_cmd}"
        )
        if row["scopes"]:
            scope_text = ", ".join(f"{scope}:{count}" for scope, count in row["scopes"].items())
            lines.append(f"  {'':<12} {'':<10} {'':>5} {'':>7} {'':>8} {'':>8} {'':>8}  {scope_text}")
    if not active:
        lines.append("  Nothing pending.")
    return "\n".join(lines)


def workflow_next(conn):
    version = current_schema_version(conn)
    if version != CURRENT_SCHEMA_VERSION:
        return {
            "kind": "migrate",
            "message": f"Schema is v{version}; current is v{CURRENT_SCHEMA_VERSION}.",
            "command": "fund migrate",
        }
    inbox = workflow_inbox(conn)
    pending = [row for row in inbox if row["pending"] or row["pending_returns"]]
    if pending:
        pending.sort(key=lambda r: (r["unverified"] > 0, r["conflict_rows"], -r["verified"], r["fund_id"]))
        row = pending[0]
        return {
            "kind": "review",
            "fund_id": row["fund_id"],
            "handle": row["handle"],
            "message": (
                f"{row['fund_name']} has {row['pending']} pending facts and "
                f"{row['pending_returns']} pending return rows."
            ),
            "command": f"fund review {row['handle']} --list",
        }
    unapproved = [row for row in inbox if row["facts"] == 0 and row["docs"]]
    if unapproved:
        row = unapproved[0]
        return {
            "kind": "onboard",
            "fund_id": row["fund_id"],
            "handle": row["handle"],
            "message": f"{row['fund_name']} has documents but no approved facts.",
            "command": f"fund onboard {row['handle']} --dry-run",
        }
    return {
        "kind": "ready",
        "message": "No pending review queue. Approved data is ready for factsheets, screens, comparisons, or analysis.",
        "command": "fund screen --sort \"annualized_sharpe desc\"",
    }


def format_next(next_item):
    return f"Next action\n-----------\n{next_item['message']}\n\nRun: {next_item['command']}"


def cmd_init(args):
    with connect(verify=False) as conn:
        table_count = conn.execute(
            """SELECT COUNT(*) FROM sqlite_master
               WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                 AND name NOT GLOB 'page_search_*'"""
        ).fetchone()[0]
        version = current_schema_version(conn)
        if table_count == 0:
            create_schema(conn)
            print(f"Database ready: {DB_PATH}")
            return
        if version == CURRENT_SCHEMA_VERSION:
            print(f"Database already initialized: {DB_PATH} (schema v{version})")
            return
        raise RuntimeError(
            "Database exists but is not on the current schema. Run: fund migrate"
        )


def cmd_migrate(args):
    backup_path = backup_database()
    with connect(verify=False) as conn:
        result = migrate_to_latest(conn)
    if backup_path:
        print(f"Backup: {backup_path}")
    if result["created"]:
        print(f"Initialized database at schema v{result['version']}")
    elif result["migrated"]:
        print(f"Migrated database to schema v{result['version']}")
    else:
        print(f"Schema already current (v{result['version']})")


def _dir_stats(root, suffix):
    if not root.exists():
        return {"count": 0, "bytes": 0}
    paths = [path for path in root.rglob(suffix) if path.is_file()]
    return {
        "count": len(paths),
        "bytes": sum(path.stat().st_size for path in paths),
    }


def cmd_status(args):
    with connect() as conn:
        version = current_schema_version(conn)
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("funds", "documents", "pages", "proposals", "proposed_returns",
                          "facts", "returns", "llm_calls", "analyses")
        }
        pending = conn.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0]
        pending_ret = conn.execute("SELECT COUNT(*) FROM proposed_returns WHERE status='pending'").fetchone()[0]
        verified_pending = conn.execute(
            "SELECT COUNT(*) FROM proposals WHERE status='pending' AND quote_verified = 1"
        ).fetchone()[0]
        unverified_pending = conn.execute(
            """SELECT COUNT(*) FROM proposals
               WHERE status='pending' AND COALESCE(quote_verified, 0) != 1"""
        ).fetchone()[0]
        current_docs = conn.execute(
            "SELECT doc_type, COUNT(*) AS count FROM documents WHERE is_current = 1 GROUP BY doc_type"
        ).fetchall()
        current_by_type = {row["doc_type"]: row["count"] for row in current_docs}
        pdf_stats = _dir_stats(PDF_DIR, "*.pdf")
        image_stats = _dir_stats(PAGE_IMAGE_DIR, "*.png")
        print(f"Database: {DB_PATH}")
        print(f"Schema version: {version}")
        for table, count in counts.items():
            print(f"  {table:<18} {count}")
        print(f"\nPending review: {pending} proposals, {pending_ret} return rows")
        print(f"Quote verification: {verified_pending} verified pending facts, "
              f"{unverified_pending} not verified/not checked")
        print(
            f"\nStorage: {pdf_stats['count']} PDFs ({pdf_stats['bytes'] / 1e6:.1f} MB), "
            f"{image_stats['count']} cached page images ({image_stats['bytes'] / 1e6:.1f} MB)"
        )
        print(
            "Current documents: "
            f"factsheets={current_by_type.get('factsheet', 0)}, "
            f"presentations={current_by_type.get('presentation', 0)}"
        )
        rows = conn.execute(
            """SELECT f.fund_id, f.fund_name,
                      (SELECT COUNT(*) FROM facts WHERE fund_id = f.fund_id) AS facts,
                      (SELECT COUNT(*) FROM returns WHERE fund_id = f.fund_id) AS returns,
                      (SELECT COUNT(*) FROM proposals WHERE fund_id = f.fund_id AND status='pending') AS pending
               FROM funds f ORDER BY f.fund_id"""
        ).fetchall()
        if rows:
            print(f"\n  {'fund':<10} {'facts':>6} {'returns':>8} {'pending':>8}  name")
            for row in rows:
                print(f"  {row['fund_id']:<10} {row['facts']:>6} {row['returns']:>8} "
                      f"{row['pending']:>8}  {row['fund_name']}")


def cmd_list(args):
    with connect() as conn:
        rows = conn.execute(
            """SELECT f.fund_id, f.fund_name, f.manager_name,
                      (SELECT COUNT(*) FROM facts WHERE fund_id = f.fund_id) AS facts,
                      (SELECT COUNT(*) FROM returns WHERE fund_id = f.fund_id) AS returns,
                      (SELECT COUNT(*) FROM proposals WHERE fund_id = f.fund_id AND status='pending') AS pending,
                      (SELECT COUNT(*) FROM proposed_returns WHERE fund_id = f.fund_id AND status='pending') AS pending_ret
               FROM funds f ORDER BY f.fund_id"""
        ).fetchall()
    print(f"  {'type this':<12} {'fund_id':<10} {'facts':>5} {'returns':>7} {'pending':>8}  name")
    for row in rows:
        state = "approved" if row["facts"] else "unreviewed"
        pending = row["pending"] + row["pending_ret"]
        print(f"  {compare_mod.short_name(row['fund_name']):<12} {row['fund_id']:<10} {row['facts']:>5} "
              f"{row['returns']:>7} {pending:>8}  {row['fund_name']}  [{state}]")


def cmd_inbox(args):
    with connect() as conn:
        inbox = workflow_inbox(conn)
    if args.json:
        print(json.dumps(inbox, indent=2))
    else:
        print(format_inbox(inbox))


def cmd_next(args):
    with connect() as conn:
        next_item = workflow_next(conn)
    if args.json:
        print(json.dumps(next_item, indent=2))
    else:
        print(format_next(next_item))


def cmd_doctor(args):
    with connect(verify=False) as conn:
        version = current_schema_version(conn)
        table_count = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        pending = 0
        pending_returns = 0
        if version is not None:
            pending = conn.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0]
            pending_returns = conn.execute(
                "SELECT COUNT(*) FROM proposed_returns WHERE status='pending'"
            ).fetchone()[0]
    try:
        package_version = metadata.version("fund")
    except metadata.PackageNotFoundError:
        package_version = "editable/local"
    print("Doctor")
    print("------")
    print(f"DB path: {DB_PATH}")
    print(f"Schema: {version or 'not initialized'} / current {CURRENT_SCHEMA_VERSION}")
    print(f"SQLite tables: {table_count} (includes local search-index internals when present)")
    print(f"Python: {sys.executable}")
    print(f"Package: fund {package_version}")
    print(f"OPENAI_API_KEY: {'set' if os.getenv('OPENAI_API_KEY') else 'missing'}")
    print(f"Pending review: {pending} facts, {pending_returns} return rows")
    if table_count == 0:
        print("\nRun: fund init")
    elif version != CURRENT_SCHEMA_VERSION:
        print("\nRun: fund migrate")
    elif pending or pending_returns:
        print("\nRun: fund inbox")
    else:
        print("\nLooks ready. Run: fund next")


def cmd_prune(args):
    with connect() as conn:
        doc_id = args.doc
        if doc_id and not conn.execute(
                "SELECT 1 FROM documents WHERE doc_id = ?", (doc_id,)).fetchone():
            sys.exit(f"error: no document {doc_id}")
        result = ingest_mod.prune_page_images(conn, doc_id=doc_id, dry_run=args.dry_run)
    prefix = "[dry-run] would remove" if args.dry_run else "removed"
    scope = f" for {doc_id}" if doc_id else ""
    print(f"{prefix} {result['images']} cached page images{scope}, "
          f"{result['bytes'] / 1e6:.1f} MB.")
    print("They regenerate automatically the next time a vision extraction needs them.")


def cmd_ingest(args):
    with connect() as conn:
        results = ingest_mod.ingest_from_seeds(conn)
    for result in results:
        print(f"  {result['doc_id']}: {result['status']} ({result['pages']} pages)")


def cmd_add_doc(args):
    with connect() as conn:
        fund_id = resolve_fund(conn, args.fund)
        result = ingest_mod.add_document(
            conn,
            fund_id,
            args.type,
            args.date,
            Path(args.path),
            title=args.title,
            external=args.external,
        )
    print(f"Added {result['doc_type']} for {fund_id}: {result['doc_id']}")
    print(f"  source ({result['source_kind']}): {result['source_path']}")
    print(f"  pages: {result['pages']}")
    if result["supersedes_doc_id"]:
        print(f"  current lineage: supersedes {result['supersedes_doc_id']}")
    print(f"Next: fund onboard {args.fund} --dry-run")


def cmd_discover_docs(args):
    root = args.root or SOURCE_DOC_ROOT
    if not root:
        raise ValueError("Pass --root PATH or set FUND_SOURCE_DOC_ROOT.")
    with connect() as conn:
        rows = ingest_mod.discover_source_documents(conn, root)
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    print(f"Discovered {len(rows)} PDFs under {root} (read-only; no files or DB rows changed).")
    for row in rows:
        duplicate = ", ".join(row["duplicate_doc_ids"]) or "new"
        inferred = f"{row['inferred_type']}, {row['inferred_date'] or 'date unknown'}"
        print(f"  [{duplicate}] {inferred:<28} {row['path']}")


def cmd_alias(args):
    with connect() as conn:
        fund_id = resolve_fund(conn, args.fund) if args.fund else None
        if args.alias:
            if not fund_id:
                raise ValueError("Pass --fund when adding an alias.")
            ingest_mod.register_alias(conn, fund_id, args.alias)
            print(f"Alias added: {args.alias} -> {fund_id}")
            return
        rows = ingest_mod.list_aliases(conn, fund_id=fund_id)
    if not rows:
        print("No reviewed aliases.")
        return
    for row in rows:
        print(f"{row['fund_id']:<12} {row['alias']:<42} {row['alias_type']}")


def cmd_crosswalk(args):
    root = args.root or SOURCE_DOC_ROOT
    if not root:
        raise ValueError("Pass --root PATH or set FUND_SOURCE_DOC_ROOT.")
    with connect() as conn:
        rows = ingest_mod.crosswalk_folders(conn, root)
    if not args.all:
        rows = [row for row in rows if row["status"] != "unmatched"]
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    print(f"Folder crosswalk under {root} (read-only; review likely matches before using them).")
    if not args.all:
        print("  Showing matched/ambiguous folders only; use --all for the full source tree.")
    for row in rows:
        targets = ", ".join(row["fund_ids"]) or "-"
        terms = ", ".join(row["matched_terms"]) or "-"
        print(f"  {row['status']:<10} {row['folder']:<42} -> {targets} [{terms}]")


def cmd_refresh(args):
    root = args.root or SOURCE_DOC_ROOT
    if not root:
        raise ValueError("Pass --root PATH or set FUND_SOURCE_DOC_ROOT.")
    with connect() as conn:
        rows = ingest_mod.refresh_preflight(conn, root, include_unmatched=args.all)
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    print(f"Refresh preflight under {root} (read-only; no files, database rows, or API calls changed).")
    if not args.all:
        print("  Showing matched/ambiguous folders only; use --all for the full source tree.")
    for row in rows:
        newest = row.get("newest_factsheet") or {}
        document = newest.get("path") or "-"
        date = newest.get("inferred_date") or "date unknown"
        print(f"  {row['status']:<10} {row['folder']:<38} PDFs={row['pdf_count']:<3} "
              f"newest={date:<12} {row['next_action']}")
        if document != "-":
            print(f"    {document}")


def cmd_search(args):
    with connect() as conn:
        fund_id = resolve_fund(conn, args.fund) if args.fund else None
        rows = ingest_mod.search_source_pages(
            conn, args.query, fund_id=fund_id, doc_id=args.doc, limit=args.limit,
        )
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print(f"No source-page matches for: {args.query!r}")
        return
    print(f"Local evidence matches for: {args.query!r} (keyword retrieval, not a model answer)")
    print("Use the document/page and excerpt below to inspect the original source.")
    for row in rows:
        date = row["doc_date"] or "undated"
        print(f"\n[{row['fund_id']} {row['doc_id']} p.{row['page_number']} | {row['title']} | {row['doc_type']} {date}]")
        print(f"  {row['excerpt']}")
        print(f"  {row['source_path']}")


def cmd_classify(args):
    if not args.activity and not args.activist:
        raise ValueError("Pass --activity and/or --activist.")
    with connect() as conn:
        fund_id = resolve_fund(conn, args.fund)
        results = []
        for field_key, value in (
            ("activity_status", args.activity),
            ("activist_universe_status", args.activist),
        ):
            if value:
                evidence = review_mod.approve_sourced_fact(
                    conn, fund_id=fund_id, field_key=field_key, value=value,
                    doc_id=args.doc, page=args.page, quote=args.quote,
                    reviewer=args.reviewer, as_of_date=args.as_of,
                )
                results.append((field_key, value, evidence["quote_verify_status"]))
    for field_key, value, verification in results:
        print(f"Approved {field_key}: {value} ({verification})")


def cmd_universe(args):
    with connect() as conn:
        rows = universe_mod.classifications(conn)
    print("Activist universe")
    print("-----------------")
    print(f"  {'fund_id':<12} {'universe':<20} {'activity':<12} evidence")
    for row in rows:
        universe = row["activist_universe_status"] or "unclassified"
        activity = row["activity_status"] or "unclassified"
        evidence = []
        if row["universe_doc_id"]:
            evidence.append(f"universe {row['universe_doc_id']} p.{row['universe_page']}")
        if row["activity_doc_id"]:
            evidence.append(f"activity {row['activity_doc_id']} p.{row['activity_page']}")
        print(f"  {row['fund_id']:<12} {universe:<20} {activity:<12} {'; '.join(evidence) or '-'}")


def cmd_verify(args):
    with connect() as conn:
        doc_id = args.doc
        if doc_id and not conn.execute(
            "SELECT 1 FROM documents WHERE doc_id = ?",
            (doc_id,),
        ).fetchone():
            raise ValueError(f"No document {doc_id}")
        counts = verification_mod.verify_existing(conn, doc_id=doc_id)
    print("Quote verification complete.")
    if not counts:
        print("  No staged proposals or return rows found.")
        return
    for key in sorted(counts):
        print(f"  {key:<24} {counts[key]}")


def cmd_reconcile(args):
    with connect() as conn:
        fund_id = resolve_fund(conn, args.fund)
        fund = conn.execute("SELECT fund_name FROM funds WHERE fund_id = ?", (fund_id,)).fetchone()
        issues = verification_mod.reconcile_returns(
            conn,
            fund_id,
            threshold_pp=args.threshold,
            monthly_bound=args.monthly_bound,
        )
    label = f"{compare_mod.short_name(fund['fund_name'])} ({fund_id})"
    print(verification_mod.format_reconciliation(issues, label))


def cmd_extract(args):
    with connect() as conn:
        if args.returns:
            pages = [args.page] if args.page else [
                p for p, _ in extract_mod.find_return_pages(conn, args.doc_id)]
            if not pages:
                sys.exit(f"No return-table page detected in {args.doc_id}; pass --page N.")
            if args.from_text:
                extractor = extract_mod.extract_returns_from_text
            else:
                extractor = extract_mod.extract_returns_auto  # vision, auto text fallback
            result = [extractor(conn, args.doc_id, page,
                                force=args.force, dry_run=args.dry_run) for page in pages]
            result = result[0] if len(result) == 1 else result
        else:
            scopes = list(extract_mod.SCOPE_GUIDANCE) if args.all_scopes else [args.scope]
            if scopes == [None]:
                sys.exit("Pass --scope NAME, --all-scopes, or --returns --page N")
            result = [extract_mod.extract_scope(conn, args.doc_id, scope,
                                                force=args.force, dry_run=args.dry_run)
                      for scope in scopes]
    print(json.dumps(result, indent=2, default=str))


def cmd_onboard(args):
    with connect() as conn:
        fund_id = resolve_fund(conn, args.fund)
        if args.dry_run:
            print(f"Onboarding plan for {fund_id} (no LLM calls):")
            for item in extract_mod.onboard_plan(conn, fund_id):
                pages = ", ".join(f"p{p}" for p in item["return_pages"]) or "none detected"
                print(f"  {item['doc_id']} [{item['doc_type']}] {item['title']}")
                print(f"      scopes: {', '.join(item['scopes']) or '(none)'}")
                if item["doc_type"] == "factsheet":
                    print(f"      return pages: {pages}")
            return
        results = extract_mod.onboard_fund(conn, fund_id, force=args.force)
        handle = compare_mod.short_name(
            conn.execute("SELECT fund_name FROM funds WHERE fund_id = ?", (fund_id,)).fetchone()[0])
    for item in results:
        print(f"\n{item['doc_id']} [{item['doc_type']}] {item['title']}")
        for res in item["scope_results"]:
            if "inserted" in res:  # scope actually ran
                extra = f", {len(res['skipped'])} dup/skip" if res["skipped"] else ""
                print(f"  scope {res['scope']}: {len(res['inserted'])} proposed{extra}")
            else:                  # guarded/errored before running
                print(f"  scope {res['scope']}: skipped ({res['skipped']})")
        for res in item["return_results"]:
            if "inserted" in res:  # extraction ran
                tag = " (text fallback)" if res.get("fell_back_from_vision") else ""
                print(f"  returns p{res['page']}: {res['inserted']} rows{tag}")
            else:
                print(f"  returns p{res['page']}: skipped ({res['skipped']})")
    print(f"\nReview when ready:  fund review {handle}")


def _approved_fact_for_row(conn, row):
    return conn.execute(
        """SELECT value, doc_id, page, approved_at FROM facts
           WHERE fund_id = ? AND field_key = ? AND share_class = ?""",
        (row["fund_id"], row["field_key"], row.get("share_class") or ""),
    ).fetchone()


def _print_proposal(row, index=None, total=None, approved=None):
    head = f"[{index}/{total}] " if index else ""
    qualifier = f" [{row['share_class']}]" if row.get("share_class") else ""
    print(f"\n{head}{row['field_key']}{qualifier}  ({row['scope']}, {row['doc_id']} p.{row['page']})")
    print(f"  value: {row['value']}")
    if approved:
        print(f"  current: {approved['value']}  ({approved['doc_id']} p.{approved['page']})")
    if row["unit"]:
        print(f"  unit: {row['unit']}")
    if row["as_of_date"]:
        print(f"  as of: {row['as_of_date']}")
    if row["quote"]:
        print(f"  quote: {row['quote'][:200]}")
    print(f"  quote check: {verification_mod.verification_label(row)}")
    print(f"  id: {row['proposal_id']}")


def _approve_verified(conn, proposals, reviewer):
    meta = review_mod.doc_meta(conn, proposals[0]["fund_id"]) if proposals else {}
    _, superseded = review_mod.resolve_field_conflicts(proposals, meta)
    groups = {}
    for row in proposals:
        groups.setdefault((row["field_key"], row.get("share_class") or ""), []).append(row)
    approved, skipped = 0, []
    for row in proposals:
        group = groups[(row["field_key"], row.get("share_class") or "")]
        if row["proposal_id"] in superseded or len(group) > 1:
            skipped.append((row["proposal_id"], "conflict"))
            continue
        if row.get("quote_verified") != 1:
            skipped.append((row["proposal_id"], "quote not verified"))
            continue
        review_mod.approve_proposal(conn, row["proposal_id"], reviewer)
        approved += 1
    return approved, skipped


def cmd_review(args):
    with connect() as conn:
        if args.approve_id:
            key = review_mod.approve_proposal(conn, args.approve_id, args.reviewer,
                                              value=args.value, note=args.note)
            conn.commit()
            print(f"approved -> facts.{key}")
            return
        if args.reject_id:
            review_mod.reject_proposal(conn, args.reject_id, args.reviewer, note=args.note)
            conn.commit()
            print("rejected")
            return
        if args.reopen_id:
            review_mod.reopen_proposal(conn, args.reopen_id, args.reviewer, note=args.note)
            conn.commit()
            print("reopened as pending")
            return
        if args.approve_return_id:
            outcome = review_mod.approve_return_row(conn, args.approve_return_id, args.reviewer,
                                                    note=args.note)
            conn.commit()
            print(f"approved return row ({outcome})")
            return
        if args.reject_return_id:
            review_mod.reject_return_row(conn, args.reject_return_id, args.reviewer, note=args.note)
            conn.commit()
            print("rejected return row")
            return

        # Resolve the target: a document id, or a fund name/id (whole factsheet).
        doc_id, fund_id = None, None
        if args.target:
            if conn.execute("SELECT 1 FROM documents WHERE doc_id = ?", (args.target,)).fetchone():
                doc_id = args.target
            else:
                fund_id = resolve_fund(conn, args.target)

        proposals = review_mod.pending_proposals(conn, doc_id=doc_id, fund_id=fund_id)
        returns = review_mod.pending_returns(conn, doc_id=doc_id, fund_id=fund_id)
        if not proposals and not returns:
            print("Nothing pending.")
            return

        if args.summary:
            if fund_id:
                print(format_inbox([row for row in workflow_inbox(conn) if row["fund_id"] == fund_id]))
            else:
                print(f"Pending facts: {len(proposals)}")
                print(f"Pending return rows: {len(returns)}")
            return

        if args.approve_verified:
            if not fund_id:
                raise ValueError("--approve-verified requires a fund target")
            approved, skipped = _approve_verified(conn, proposals, args.reviewer)
            conn.commit()
            print(f"Approved {approved} verified, non-conflicting proposals.")
            if skipped:
                print(f"Skipped {len(skipped)} proposals needing manual review.")
            return

        # Show the whole proposed factsheet at once (grouped by section, incl. returns).
        if fund_id:
            print(factsheet_mod.format_proposed_factsheet(
                factsheet_mod.build_proposed_factsheet(conn, fund_id)))
        else:
            for row in proposals:
                _print_proposal(row, approved=_approved_fact_for_row(conn, row))
            if returns:
                print(f"\nPending return rows ({len(returns)}):")
                for row in returns:
                    print(f"  [{row['row_id']}] {row['period_type']:<10} {row['period_end']}  "
                          f"{row['return_pct']:>7.2f}%  {row['share_class'] or '-'}")

        if args.list:
            return

        # Authoritative source wins on conflict: fact sheet over presentation, then recency.
        conflict_fund = fund_id or (proposals[0]["fund_id"] if proposals else None)
        meta = review_mod.doc_meta(conn, conflict_fund) if conflict_fund else {}
        _, superseded = review_mod.resolve_field_conflicts(proposals, meta)

        def approve_all(reject_ids=frozenset()):
            approved, stale = 0, 0
            for row in proposals:
                winner = superseded.get(row["proposal_id"])
                reject_tokens = {row["proposal_id"], row["field_key"]}
                if row.get("share_class"):
                    reject_tokens.add(f"{row['field_key']}[{row['share_class']}]")
                if reject_ids & reject_tokens:
                    review_mod.reject_proposal(conn, row["proposal_id"], args.reviewer)
                elif winner:
                    review_mod.reject_proposal(
                        conn, row["proposal_id"], args.reviewer,
                        note=f"superseded by {winner['doc_id']} "
                             f"({(meta.get(winner['doc_id']) or {}).get('date') or 'undated'})")
                    stale += 1
                else:
                    review_mod.approve_proposal(conn, row["proposal_id"], args.reviewer)
                    approved += 1
            for row in returns:
                review_mod.approve_return_row(conn, row["row_id"], args.reviewer)
            conn.commit()
            rejected = len(proposals) - approved - stale
            tail = f", superseded {stale} older" if stale else ""
            print(f"\nApproved {approved} fields and {len(returns)} return rows; "
                  f"rejected {rejected}{tail}.")

        if args.approve_all:
            approve_all()
            return

        choice = input(
            "\n  [a]pprove whole factsheet / [r]eject some then approve rest / "
            "[f]ield-by-field / [q]uit > "
        ).strip().lower()
        if choice == "a":
            approve_all()
        elif choice == "r":
            raw = input("  ids or field names to reject (space-separated) > ").strip().split()
            approve_all(reject_ids=frozenset(raw))
        elif choice == "f":
            for index, row in enumerate(proposals, start=1):
                _print_proposal(row, index, len(proposals), approved=_approved_fact_for_row(conn, row))
                pick = input("  [a]pprove / [e]dit+approve / [r]eject / [s]kip / [q]uit > ").strip().lower()
                if pick == "q":
                    break
                if pick == "a":
                    review_mod.approve_proposal(conn, row["proposal_id"], args.reviewer)
                    conn.commit()
                elif pick == "e":
                    review_mod.approve_proposal(conn, row["proposal_id"], args.reviewer,
                                                value=input("  new value > ").strip())
                    conn.commit()
                elif pick == "r":
                    review_mod.reject_proposal(conn, row["proposal_id"], args.reviewer,
                                               note=input("  reject note (optional) > ").strip())
                    conn.commit()
            if returns and input(f"\n  approve {len(returns)} return rows? [a]ll / [n]one > ").strip().lower() == "a":
                for row in returns:
                    review_mod.approve_return_row(conn, row["row_id"], args.reviewer)
                conn.commit()
                print(f"Approved {len(returns)} return rows.")


def cmd_pending(args):
    args.list = True
    args.approve_id = None
    args.reject_id = None
    args.reopen_id = None
    args.approve_return_id = None
    args.reject_return_id = None
    args.approve_all = False
    args.approve_verified = False
    args.summary = False
    args.value = None
    args.note = ""
    cmd_review(args)


def cmd_approve(args):
    with connect() as conn:
        try:
            key = review_mod.approve_proposal(
                conn,
                args.proposal_id,
                args.reviewer,
                value=args.value,
                note=args.note,
            )
            conn.commit()
            print(f"approved -> facts.{key}")
        except ValueError:
            outcome = review_mod.approve_return_row(
                conn,
                args.proposal_id,
                args.reviewer,
                note=args.note,
            )
            conn.commit()
            print(f"approved return row ({outcome})")


def cmd_reject(args):
    with connect() as conn:
        try:
            review_mod.reject_proposal(conn, args.proposal_id, args.reviewer, note=args.note)
            conn.commit()
            print("rejected")
        except ValueError:
            review_mod.reject_return_row(conn, args.proposal_id, args.reviewer, note=args.note)
            conn.commit()
            print("rejected return row")


def cmd_factsheet(args):
    with connect() as conn:
        fund_id = resolve_fund(conn, args.fund)
        sheet = factsheet_mod.build_factsheet(conn, fund_id)
        print(factsheet_mod.format_factsheet(sheet, show_sources=args.sources))
        if args.save:
            path, digest = factsheet_mod.snapshot_factsheet(conn, fund_id)
            print(f"Snapshot saved: {path} (hash {digest})")


def cmd_returns(args):
    with connect() as conn:
        fund_id = resolve_fund(conn, args.fund)
        sheet = factsheet_mod.build_factsheet(conn, fund_id)
    print(factsheet_mod.format_returns_table(sheet["returns"]))


def cmd_screen(args):
    sort_field, descending = None, True
    if args.sort:
        parts = args.sort.split()
        sort_field = parts[0]
        if len(parts) > 1:
            descending = parts[1].lower() != "asc"
    with connect() as conn:
        result = screen_mod.screen_funds(
            conn,
            where=args.where,
            preset=args.preset,
            min_history_years=args.min_history,
            sort_field=sort_field,
            descending=descending,
            activist_only=args.activist_only,
            include_uncertain=args.include_uncertain,
            include_candidates=args.include_candidates,
        )
    print(screen_mod.format_screen(result))


def cmd_similar(args):
    with connect() as conn:
        if args.all:
            result = similarity_mod.all_pairwise_similarity(
                conn, activist_only=args.activist_only,
                include_uncertain=args.include_uncertain,
                include_candidates=args.include_candidates,
            )
            print(similarity_mod.format_pairwise(result, limit=args.limit))
            return
        if not args.fund:
            raise ValueError("Usage: fund similar FUND or fund similar --all")
        fund_id = resolve_fund(conn, args.fund)
        result = similarity_mod.similar_funds(
            conn, fund_id, limit=args.limit, activist_only=args.activist_only,
            include_uncertain=args.include_uncertain,
            include_candidates=args.include_candidates,
        )
    print(similarity_mod.format_similar(result))


def cmd_compare(args):
    fields = args.fields.split(",") if args.fields else None
    with connect() as conn:
        fund_ids = [resolve_fund(conn, token) for token in args.funds]
        comparison = compare_mod.compare_funds(conn, fund_ids, field_keys=fields)
        web_result = None
        if args.web_reality_check:
            web_result = analyze_mod.run_activism_reality_analysis(
                conn, fund_ids, dry_run=args.dry_run,
            )
    print(compare_mod.format_comparison(comparison))
    if web_result is not None:
        if args.dry_run:
            print("\nWeb reality-check preflight (no API call):")
            print(json.dumps(web_result, indent=2, ensure_ascii=False))
        else:
            print("\nPublic-web activism reality check:")
            print(web_result["output_text"])


def cmd_export(args):
    with connect() as conn:
        if args.target[0] == "compare":
            if len(args.target) < 3:
                raise ValueError("Usage: fund export compare FUND FUND [FUND ...]")
            fund_ids = [resolve_fund(conn, token) for token in args.target[1:]]
            print(export_mod.markdown_compare(conn, fund_ids), end="")
            return
        if len(args.target) != 1:
            raise ValueError("Usage: fund export FUND or fund export compare FUND FUND [FUND ...]")
        fund_id = resolve_fund(conn, args.target[0])
        sheet = factsheet_mod.build_factsheet(conn, fund_id)
        peers = export_mod.peer_context(conn, fund_id) if args.peer_context else {}
    print(export_mod.markdown_factsheet(sheet, peer=peers), end="")


def cmd_analyze(args):
    with connect() as conn:
        fund_ids = [resolve_fund(conn, token) for token in args.funds.split(",")]
        result = analyze_mod.run_analysis(conn, fund_ids, args.question, dry_run=args.dry_run)
    if args.dry_run:
        print(json.dumps(result, indent=2))
    else:
        print(f"[{result['analysis_id']}] snapshots: {', '.join(result['snapshot_hashes'])}\n")
        print(result["output_text"])


def cmd_analyze_activism(args):
    with connect() as conn:
        fund_ids = [resolve_fund(conn, token) for token in args.funds.split(",")]
        result = analyze_mod.run_activism_reality_analysis(conn, fund_ids, dry_run=args.dry_run)
    if args.dry_run:
        print(json.dumps(result, indent=2))
    else:
        print(f"[{result['analysis_id']}] snapshots: {', '.join(result['snapshot_hashes'])}\n")
        print(result["output_text"])


def cmd_analyses(args):
    with connect() as conn:
        rows = conn.execute(
            "SELECT analysis_id, created_at, fund_ids, question FROM analyses ORDER BY created_at DESC"
        ).fetchall()
    for row in rows:
        print(f"{row['analysis_id']}  {row['created_at']}  [{row['fund_ids']}]  {row['question'][:70]}")


def cmd_show(args):
    with connect() as conn:
        row = conn.execute("SELECT * FROM analyses WHERE analysis_id = ?", (args.analysis_id,)).fetchone()
    if row is None:
        sys.exit(f"No analysis {args.analysis_id}")
    print(f"Question: {row['question']}\nFunds: {row['fund_ids']}  Snapshots: {row['snapshot_hashes']}")
    print(f"Model: {row['model']}  At: {row['created_at']}\n\n{row['output_text']}")


def cmd_log(args):
    with connect() as conn:
        rows = conn.execute(
            """SELECT call_id, created_at, call_type, model, doc_id, fund_id, input_tokens,
                      output_tokens, status FROM llm_calls ORDER BY created_at DESC LIMIT ?""",
            (args.limit,),
        ).fetchall()
    print(f"{'call_id':<18} {'when':<20} {'type':<18} {'status':<7} {'in_tok':>7} {'out_tok':>7}  target")
    for row in rows:
        print(f"{row['call_id']:<18} {row['created_at']:<20} {row['call_type']:<18} "
              f"{row['status']:<7} {row['input_tokens'] or '-':>7} {row['output_tokens'] or '-':>7}  "
              f"{row['doc_id'] or row['fund_id'] or '-'}")


def main():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            reconfigure(encoding="utf-8", errors="backslashreplace")
    parser = argparse.ArgumentParser(prog="fund", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("migrate").set_defaults(func=cmd_migrate)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("list").set_defaults(func=cmd_list)
    p = sub.add_parser("inbox", help="pending review queue grouped by fund")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_inbox)

    p = sub.add_parser("next", help="suggest the next useful workflow action")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_next)

    sub.add_parser("doctor", help="environment and database sanity check").set_defaults(func=cmd_doctor)
    sub.add_parser("ingest").set_defaults(func=cmd_ingest)

    p = sub.add_parser("add-doc", help="register a factsheet, presentation, or supporting evidence PDF")
    p.add_argument("--fund", required=True, help="fund name (e.g. simplex) or id")
    p.add_argument("--type", required=True, choices=("factsheet", "presentation", "evidence"))
    p.add_argument("--date", required=True, help="document date as YYYY-MM-DD when known")
    p.add_argument("--title", help="optional document title override")
    source_mode = p.add_mutually_exclusive_group()
    source_mode.add_argument("--external", dest="external", action="store_const", const=True,
                             help="keep the PDF at its source path")
    source_mode.add_argument("--copy", dest="external", action="store_const", const=False,
                             help="copy the PDF into project storage (overrides source-root default)")
    p.set_defaults(external=None)
    p.add_argument("path", help="path to the PDF to register")
    p.set_defaults(func=cmd_add_doc)

    p = sub.add_parser("discover-docs", help="inventory external PDFs and detect known hashes (read-only)")
    p.add_argument("--root", help="source root; defaults to FUND_SOURCE_DOC_ROOT")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_discover_docs)

    p = sub.add_parser("alias", help="add or list reviewed fund aliases for T-drive matching")
    p.add_argument("alias", nargs="?", help="alias to add")
    p.add_argument("--fund", help="fund name or id; required when adding an alias")
    p.set_defaults(func=cmd_alias)

    p = sub.add_parser("crosswalk", help="map immediate source folders to reviewed fund identities (read-only)")
    p.add_argument("--root", help="source root; defaults to FUND_SOURCE_DOC_ROOT")
    p.add_argument("--all", action="store_true", help="include unmatched folders")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_crosswalk)

    p = sub.add_parser("refresh", help="one read-only source refresh preflight")
    p.add_argument("--root", help="source root; defaults to FUND_SOURCE_DOC_ROOT")
    p.add_argument("--all", action="store_true", help="include unmatched folders")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_refresh)

    p = sub.add_parser("search", help="search extracted source pages locally with document/page citations")
    p.add_argument("query")
    p.add_argument("--fund", help="limit to one fund name or id")
    p.add_argument("--doc", help="limit to one document id")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("classify", help="approve sourced activist/activity status facts")
    p.add_argument("fund", help="fund name or id")
    p.add_argument("--activity", choices=("active", "uncertain", "inactive"))
    p.add_argument("--activist", choices=("candidate", "verified_activist", "excluded"))
    p.add_argument("--doc", required=True, help="source document id")
    p.add_argument("--page", required=True, type=int, help="source page")
    p.add_argument("--quote", required=True, help="supporting quote from that page")
    p.add_argument("--as-of", help="optional YYYY-MM-DD status date; defaults to source document date")
    p.add_argument("--reviewer", default="christian")
    p.set_defaults(func=cmd_classify)

    sub.add_parser("universe", help="show reviewed activist/activity classifications").set_defaults(func=cmd_universe)

    p = sub.add_parser("verify", help="backfill quote verification for staged proposals/returns")
    p.add_argument("--doc", help="limit to one document id")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("reconcile", help="deterministic return-number QC for one fund")
    p.add_argument("fund", help="fund name (e.g. simplex) or id")
    p.add_argument("--threshold", type=float, default=0.5,
                   help="flag annual/YTD deltas above this many percentage points")
    p.add_argument("--monthly-bound", type=float, default=60.0,
                   help="flag monthly returns outside +/- this percent")
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("prune", help="delete cached page images (regenerated on demand)")
    p.add_argument("--doc", help="limit to one document id")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("extract")
    p.add_argument("doc_id")
    p.add_argument("--scope", choices=list(extract_mod.SCOPE_GUIDANCE))
    p.add_argument("--all-scopes", action="store_true")
    p.add_argument("--returns", action="store_true")
    p.add_argument("--from-text", dest="from_text", action="store_true",
                   help="extract returns from page text instead of the page image "
                        "(for tables the vision model won't read)")
    p.add_argument("--page", type=int, help="return page (auto-detected if omitted)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("onboard", help="run a fund's whole extraction plan (doc-type routed)")
    p.add_argument("fund", help="fund name (e.g. simplex) or id")
    p.add_argument("--force", action="store_true", help="re-extract scopes/pages already done")
    p.add_argument("--dry-run", action="store_true", help="show the plan, no LLM calls")
    p.set_defaults(func=cmd_onboard)

    p = sub.add_parser("review", help="review a whole fund's proposed factsheet (or one doc)")
    p.add_argument("target", nargs="?", help="fund name (e.g. simplex) or a doc id")
    p.add_argument("--reviewer", default="christian")
    p.add_argument("--list", action="store_true")
    p.add_argument("--approve-id")
    p.add_argument("--reject-id")
    p.add_argument("--reopen-id")
    p.add_argument("--approve-return-id")
    p.add_argument("--reject-return-id")
    p.add_argument("--approve-all", action="store_true",
                   help="approve everything pending for the target (review the list first)")
    p.add_argument("--approve-verified", action="store_true",
                   help="approve verified proposals with no field conflicts")
    p.add_argument("--summary", action="store_true",
                   help="show counts for this review target")
    p.add_argument("--value")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("pending", help="friendlier alias for: fund review TARGET --list")
    p.add_argument("target", help="fund name (e.g. simplex) or a doc id")
    p.add_argument("--reviewer", default="christian")
    p.set_defaults(func=cmd_pending)

    p = sub.add_parser("approve", help="approve one proposal or return row id")
    p.add_argument("proposal_id")
    p.add_argument("--reviewer", default="christian")
    p.add_argument("--value")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("reject", help="reject one proposal or return row id")
    p.add_argument("proposal_id")
    p.add_argument("--reviewer", default="christian")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_reject)

    p = sub.add_parser("factsheet")
    p.add_argument("fund", help="fund name (e.g. simplex) or id")
    p.add_argument("--sources", action="store_true")
    p.add_argument("--save", action="store_true")
    p.set_defaults(func=cmd_factsheet)

    p = sub.add_parser("returns")
    p.add_argument("fund", help="fund name (e.g. simplex) or id")
    p.set_defaults(func=cmd_returns)

    p = sub.add_parser("screen", help="screen funds using approved facts and computed analytics")
    p.add_argument("--preset", choices=sorted(screen_mod.PRESETS))
    p.add_argument("--where", action="append",
                   help="numeric filter, e.g. management_fee<1.5 or annualized_sharpe>=1")
    p.add_argument("--min-history", type=float, help="minimum monthly return history in years")
    p.add_argument("--sort", help='sort expression, e.g. "annualized_sharpe desc"')
    p.add_argument("--activist-only", action="store_true",
                   help="include only verified activists with active status")
    p.add_argument("--include-uncertain", action="store_true",
                   help="with --activist-only, include uncertain activity status")
    p.add_argument("--include-candidates", action="store_true",
                   help="with --activist-only, include candidate activist funds")
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("similar", help="rank qualitative similarity using approved facts")
    p.add_argument("fund", nargs="?", help="fund name (e.g. simplex) or id")
    p.add_argument("--all", action="store_true", help="rank all fund pairs")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--activist-only", action="store_true",
                   help="include only verified activists with active status")
    p.add_argument("--include-uncertain", action="store_true",
                   help="with --activist-only, include uncertain activity status")
    p.add_argument("--include-candidates", action="store_true",
                   help="with --activist-only, include candidate activist funds")
    p.set_defaults(func=cmd_similar)

    p = sub.add_parser("compare")
    p.add_argument("funds", nargs="+", help="fund names (e.g. simplex begonia) or ids")
    p.add_argument("--fields")
    p.add_argument("--web-reality-check", action="store_true",
                   help="also run the policy-dependent bilingual public-web activism check")
    p.add_argument("--dry-run", action="store_true",
                   help="with --web-reality-check, show planned bilingual queries without an API call")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("export", help="export approved factsheet or comparison as Markdown")
    p.add_argument("target", nargs="+",
                   help="FUND, or: compare FUND FUND [FUND ...]")
    p.add_argument("--no-peer-context", dest="peer_context", action="store_false",
                   help="omit peer-relative computed-stat context for single-fund exports")
    p.set_defaults(func=cmd_export, peer_context=True)

    p = sub.add_parser("analyze")
    p.add_argument("--funds", required=True, help="comma-separated fund names or ids")
    p.add_argument("-q", "--question", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("analyze-activism")
    p.add_argument("--funds", required=True, help="comma-separated fund names or ids")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_analyze_activism)

    sub.add_parser("analyses").set_defaults(func=cmd_analyses)

    p = sub.add_parser("show")
    p.add_argument("analysis_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("log")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_log)

    args = parser.parse_args()
    try:
        args.func(args)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()

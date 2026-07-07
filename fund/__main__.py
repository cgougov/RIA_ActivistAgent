"""The terminal. One entrypoint for the whole pipeline:

  fund init                      create the database
  fund status                    pipeline state at a glance
  fund ingest                    absorb PDFs (funds/docs from data/seed)
  fund extract DOC [...]         LLM: propose facts / return rows
  fund review DOC [...]          approve / reject / revise proposals
  fund factsheet FUND            the standardized factsheet
  fund returns FUND              approved return history
  fund compare FUND FUND [...]   deterministic side-by-side
  fund analyze --funds a,b -q Q  LLM analysis on approved truth
  fund analyses / show / log     saved analyses and the LLM call log
"""
import argparse
import json
import sys

from fund import analyze as analyze_mod
from fund import compare as compare_mod
from fund import extract as extract_mod
from fund import factsheet as factsheet_mod
from fund import ingest as ingest_mod
from fund import review as review_mod
from fund.config import DB_PATH
from fund.db import connect, create_schema


def cmd_init(args):
    with connect() as conn:
        create_schema(conn)
    print(f"Database ready: {DB_PATH}")


def cmd_status(args):
    with connect() as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("funds", "documents", "pages", "proposals", "proposed_returns",
                          "facts", "returns", "llm_calls", "analyses")
        }
        pending = conn.execute("SELECT COUNT(*) FROM proposals WHERE status='pending'").fetchone()[0]
        pending_ret = conn.execute("SELECT COUNT(*) FROM proposed_returns WHERE status='pending'").fetchone()[0]
        print(f"Database: {DB_PATH}")
        for table, count in counts.items():
            print(f"  {table:<18} {count}")
        print(f"\nPending review: {pending} proposals, {pending_ret} return rows")
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


def cmd_ingest(args):
    with connect() as conn:
        create_schema(conn)
        results = ingest_mod.ingest_from_seeds(conn)
    for result in results:
        print(f"  {result['doc_id']}: {result['status']} ({result['pages']} pages)")


def cmd_extract(args):
    with connect() as conn:
        if args.returns:
            if not args.page:
                sys.exit("--returns requires --page N")
            result = extract_mod.extract_returns(conn, args.doc_id, args.page,
                                                 force=args.force, dry_run=args.dry_run)
        else:
            scopes = list(extract_mod.SCOPE_GUIDANCE) if args.all_scopes else [args.scope]
            if scopes == [None]:
                sys.exit("Pass --scope NAME, --all-scopes, or --returns --page N")
            result = [extract_mod.extract_scope(conn, args.doc_id, scope,
                                                force=args.force, dry_run=args.dry_run)
                      for scope in scopes]
    print(json.dumps(result, indent=2, default=str))


def _print_proposal(row, index=None, total=None):
    head = f"[{index}/{total}] " if index else ""
    print(f"\n{head}{row['field_key']}  ({row['scope']}, {row['doc_id']} p.{row['page']})")
    print(f"  value: {row['value']}")
    if row["unit"]:
        print(f"  unit: {row['unit']}")
    if row["as_of_date"]:
        print(f"  as of: {row['as_of_date']}")
    if row["quote"]:
        print(f"  quote: {row['quote'][:200]}")
    print(f"  id: {row['proposal_id']}")


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

        proposals = review_mod.pending_proposals(conn, doc_id=args.doc_id)
        returns = review_mod.pending_returns(conn, doc_id=args.doc_id)
        if not proposals and not returns:
            print("Nothing pending.")
            return

        if args.list:
            for row in proposals:
                _print_proposal(row)
            if returns:
                print(f"\nPending return rows ({len(returns)}):")
                for row in returns:
                    print(f"  {row['row_id']}  {row['period_type']:<10} {row['period_end']}  "
                          f"{row['return_pct']:>7.2f}%  {row['share_class'] or '-'}")
            return

        if args.approve_all_returns:
            for row in returns:
                outcome = review_mod.approve_return_row(conn, row["row_id"], args.reviewer)
                print(f"  {row['period_type']} {row['period_end']}: {outcome}")
            conn.commit()
            print(f"Approved {len(returns)} return rows.")
            return

        # Interactive review
        for index, row in enumerate(proposals, start=1):
            _print_proposal(row, index, len(proposals))
            choice = input("  [a]pprove / [e]dit+approve / [r]eject / [s]kip / [q]uit > ").strip().lower()
            if choice == "q":
                break
            if choice == "a":
                review_mod.approve_proposal(conn, row["proposal_id"], args.reviewer)
                conn.commit()
            elif choice == "e":
                new_value = input("  new value > ").strip()
                review_mod.approve_proposal(conn, row["proposal_id"], args.reviewer, value=new_value)
                conn.commit()
            elif choice == "r":
                note = input("  reject note (optional) > ").strip()
                review_mod.reject_proposal(conn, row["proposal_id"], args.reviewer, note=note)
                conn.commit()
        if returns:
            print(f"\nPending return rows ({len(returns)}):")
            for row in returns:
                print(f"  {row['period_type']:<10} {row['period_end']}  {row['return_pct']:>7.2f}%  "
                      f"{row['share_class'] or '-'}  ({row['return_type']})")
            choice = input("  approve [a]ll / [n]one > ").strip().lower()
            if choice == "a":
                for row in returns:
                    review_mod.approve_return_row(conn, row["row_id"], args.reviewer)
                conn.commit()
                print(f"Approved {len(returns)} return rows.")


def cmd_factsheet(args):
    with connect() as conn:
        sheet = factsheet_mod.build_factsheet(conn, args.fund_id)
        print(factsheet_mod.format_factsheet(sheet, show_sources=args.sources))
        if args.save:
            path, digest = factsheet_mod.snapshot_factsheet(conn, args.fund_id)
            print(f"Snapshot saved: {path} (hash {digest})")


def cmd_returns(args):
    with connect() as conn:
        sheet = factsheet_mod.build_factsheet(conn, args.fund_id)
    print(factsheet_mod.format_returns_table(sheet["returns"]))


def cmd_compare(args):
    fields = args.fields.split(",") if args.fields else None
    with connect() as conn:
        comparison = compare_mod.compare_funds(conn, args.fund_ids, field_keys=fields)
    print(compare_mod.format_comparison(comparison))


def cmd_analyze(args):
    fund_ids = args.funds.split(",")
    with connect() as conn:
        result = analyze_mod.run_analysis(conn, fund_ids, args.question, dry_run=args.dry_run)
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
    parser = argparse.ArgumentParser(prog="fund", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("status").set_defaults(func=cmd_status)
    sub.add_parser("ingest").set_defaults(func=cmd_ingest)

    p = sub.add_parser("extract")
    p.add_argument("doc_id")
    p.add_argument("--scope", choices=list(extract_mod.SCOPE_GUIDANCE))
    p.add_argument("--all-scopes", action="store_true")
    p.add_argument("--returns", action="store_true")
    p.add_argument("--page", type=int)
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("review")
    p.add_argument("doc_id", nargs="?")
    p.add_argument("--reviewer", default="christian")
    p.add_argument("--list", action="store_true")
    p.add_argument("--approve-id")
    p.add_argument("--reject-id")
    p.add_argument("--reopen-id")
    p.add_argument("--approve-return-id")
    p.add_argument("--reject-return-id")
    p.add_argument("--approve-all-returns", action="store_true")
    p.add_argument("--value")
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("factsheet")
    p.add_argument("fund_id")
    p.add_argument("--sources", action="store_true")
    p.add_argument("--save", action="store_true")
    p.set_defaults(func=cmd_factsheet)

    p = sub.add_parser("returns")
    p.add_argument("fund_id")
    p.set_defaults(func=cmd_returns)

    p = sub.add_parser("compare")
    p.add_argument("fund_ids", nargs="+")
    p.add_argument("--fields")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("analyze")
    p.add_argument("--funds", required=True, help="comma-separated fund ids")
    p.add_argument("-q", "--question", required=True)
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_analyze)

    sub.add_parser("analyses").set_defaults(func=cmd_analyses)

    p = sub.add_parser("show")
    p.add_argument("analysis_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("log")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_log)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

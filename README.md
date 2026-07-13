# Japan Activist Funds Research Platform

A lean, terminal-first research platform for diligencing Japan activist funds.

```
absorb PDFs → extract proposed facts (LLM) → human approval → standardized factsheet
→ deterministic comparison → LLM analysis on approved truth only
```

## Principles

- Do not invent data. LLM output is proposal-only; a human approves every fact.
- Every approved fact keeps its source document, page, and quote.
- One value per field per fund or per share class, enforced by the database itself. Approving again = revising.
- Document type drives everything: fact sheets are the authoritative source for returns,
  terms, and stats; presentations supply the qualitative story (thesis, example engagements,
  what makes the fund unique). `fund onboard` routes extraction by type automatically.
- When documents disagree on a field, the authoritative one wins — fact sheet over
  presentation, then most recent — and approving the whole factsheet supersedes the rest
  (shown before you approve). Stats say where they came from ("from fact sheet").
- Return extraction is high-recall by design — it captures the full multi-year history and
  includes uncertain cells (flagged), because a human approves every row.
- Reported metrics and computed analytics are kept separate. PDF-reported values
  such as beta, volatility, and Sharpe remain sourced facts; return-derived
  analytics are labeled internal and show annualization assumptions.
- The LLM is called at exactly two points — extraction and analysis — and every call is logged.
- If it can be computed from stored data, it is never sent to the model.
- The factsheet is assembled on demand from approved data; analyses save one current
  hashed snapshot per fund with a `changes` block versus the previous snapshot.

## Setup

macOS / Linux:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # add OPENAI_API_KEY
```

Windows PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env   # add OPENAI_API_KEY
```

Do not copy `.venv/` between machines. It contains platform-specific binaries;
recreate it on the target machine with the commands above. `.env` is gitignored,
so move the OpenAI key deliberately rather than relying on a folder copy. The live
SQLite database under `data/db/` is also gitignored; transfer it separately only
if you intentionally want the VM to start from the current local approved-data state.

`pyproject.toml` is the source of truth for dependencies. `requirements.txt` is
kept only as a convenience mirror.

## The pipeline, in order

Funds can be named by a handle (`simplex`, `begonia`) instead of `fund_002` — a
case-insensitive match on the fund or manager name. `fund list` shows the handle
for each fund.

```bash
fund init                      # create an empty current-schema database
fund migrate                   # apply schema/data migrations explicitly
fund ingest                    # register seed funds/docs and absorb PDF pages
fund status                    # pipeline state at a glance
fund list                      # every fund, its typeable handle, and review state
fund inbox                     # pending review queue, grouped by fund
fund next                      # suggested next command for the current DB state
fund doctor                    # DB/env sanity check

fund add-doc --fund simplex --type factsheet --date 2026-06-30 path/to/file.pdf
                               # add a new current factsheet or presentation
                               # old current docs remain for audit, but are demoted

fund onboard simplex           # run the whole plan: doc-type-routed scopes + returns
fund onboard simplex --dry-run # show what it would extract (pages, scopes), no API

fund extract doc_003 --scope profile_terms --dry-run   # preflight, no API
fund extract doc_003 --all-scopes                      # LLM: propose descriptive facts
fund extract doc_003 --returns                          # auto-detect return page; vision, text fallback
fund extract doc_003 --returns --page 1                 # or name the page explicitly
fund extract doc_005 --returns --from-text              # force text extraction (vision-hostile tables)

fund verify                    # backfill deterministic quote checks for staged facts/returns
fund verify --doc doc_003       # limit quote verification to one document
fund reconcile simplex          # deterministic return-number QC

fund review simplex --list     # the whole proposed factsheet at once, with evidence
fund pending simplex           # friendlier alias for review --list
fund review simplex            # [a]pprove all / [r]eject some then approve / [f]ield-by-field
fund review simplex --approve-all          # scripted bulk approve (review --list first)
fund review simplex --approve-verified     # approve verified proposals with no conflicts
fund approve prop_xxx           # approve one proposal or return row
fund reject prop_xxx            # reject one proposal or return row
fund review doc_003            # or scope the review to a single document

fund factsheet simplex --sources           # the standardized factsheet (returns at the bottom)
fund factsheet simplex --save              # save current hashed snapshot + changes, prune older fund snapshots
fund returns simplex                       # approved return history
fund screen --sort "annualized_sharpe desc" # deterministic approved-data screener
fund screen --preset small-cap-activist --min-history 3
fund similar simplex --limit 5              # deterministic qualitative similarity ranking
fund similar --all --limit 20               # rank all fund pairs by similarity
fund compare simplex begonia               # synthesis paragraph + focused terms/metrics/returns tables
fund compare simplex begonia --embedding-similarity
fund compare simplex begonia --fields management_fee,sharpe_ratio
fund export simplex                         # Markdown factsheet with sources + peer context
fund export compare simplex begonia         # Markdown comparison

fund analyze --funds simplex,begonia -q "..."   # LLM analysis, cited, approved data only
fund analyze-activism --funds simplex,begonia   # English+Japanese web-backed activism check
fund analyses                  # saved analyses
fund show an_xxx               # one analysis in full
fund log                       # every LLM call: tokens, status, target
fund prune                     # delete cached page images (regenerated on demand)
```

## Layout

```
fund/           the whole system (terminal modules, readable top to bottom)
  schema.py     the standardized field vocabulary — single source of truth,
                including which fields may be share-class-qualified
  db.py         explicit schema + migrations + indexes + snapshot embedding cache
  llm.py        the only file that talks to OpenAI; logs every call
  ingest.py     PDFs -> normalized per-fund storage -> pages (+ page images for vision)
  extract.py    LLM touchpoint #1: documents -> proposals (dedup at entry)
  verification.py deterministic quote/page-text checks for staged evidence
                and return-number reconciliation over approved rows
  review.py     approval -> facts/returns (dedup enforced by primary keys)
  analytics.py  deterministic return analytics over approved return rows only
  screen.py     deterministic approved-data fund screener
  similarity.py deterministic qualitative fund similarity
  factsheet.py  assembled factsheet + hashed snapshots
  compare.py    synthesis-first comparison + focused tables
  export.py     Markdown factsheet/comparison exports, no new stored state
  analyze.py    LLM touchpoint #2: snapshots -> cited narrative / activism reality checks
tests/          no-API pipeline tests: .venv/bin/python -m tests.test_pipeline
data/           pdfs, db, page_images, current factsheet snapshots
```

## Storage Notes

- PDFs stay in the system after ingestion. They are the audit source and are used for re-rendering and re-extraction.
- New fund docs are stored under normalized per-fund paths such as `data/pdfs/simplex/2026-06-30_factsheet_simplex.pdf`.
- `documents.is_current` marks the active factsheet or presentation for a fund; older docs remain linked through `supersedes_doc_id`.
- Factsheet snapshots are local, regenerable artifacts and are gitignored. Older
  snapshots for the same fund are removed when a new current snapshot is saved.
- Embeddings are cached by factsheet snapshot hash in `snapshot_embeddings`, not mixed into the facts table.
- Web-backed fund research should search English and Japanese sources by default,
  translate Japanese evidence into English, and keep source URLs attached.

## Practical Guide

For the actual day-to-day terminal workflow, see [TERMINAL_WORKFLOW.md](TERMINAL_WORKFLOW.md).

For the Windows VM handoff and post-migration build checklist, see
[VM_MIGRATION_PLAN.md](VM_MIGRATION_PLAN.md).

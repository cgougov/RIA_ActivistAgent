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

## Setup (Windows PowerShell)

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env   # add OPENAI_API_KEY
```

Do not copy `.venv/` between machines. `.env` is gitignored, so add the API key
and optional `FUND_SOURCE_DOC_ROOT` deliberately. The live SQLite database under
`data/db/` is also gitignored.

`pyproject.toml` is the source of truth for dependencies. `requirements.txt` is
kept only as a convenience mirror.

## The pipeline, in order

Funds can be named by a handle (`simplex`, `begonia`) instead of `fund_002` — a
case-insensitive match on the fund or manager name. `fund list` shows the handle
for each fund.

```bash
fund init                      # create an empty current-schema database
fund migrate                   # apply schema/data migrations explicitly
fund ingest                    # development/demo bootstrap only; not normal research intake
fund status                    # pipeline state at a glance
fund list                      # every fund, its typeable handle, and review state
fund inbox                     # pending review queue, grouped by fund
fund next                      # suggested next command for the current DB state
fund doctor                    # DB/env sanity check
fund discover-docs --root "T:\\Research\\General\\20 Japan-Focused Hedge Funds"
                               # read-only external-PDF inventory + SHA-256 duplicate check
fund alias --fund simplex "Simplex Long Short / Value Up"  # reviewed folder/name alias
fund crosswalk --root "T:\\Research\\General\\20 Japan-Focused Hedge Funds"
                               # read-only folder -> database-fund mapping
fund refresh --root "T:\\Research\\General\\20 Japan-Focused Hedge Funds"
                               # one read-only current-factsheet and next-action preflight

fund add-doc --fund simplex --type factsheet --date 2026-06-30 "T:\\...\\factsheet.pdf"
                               # when FUND_SOURCE_DOC_ROOT is configured, keeps shared-drive PDFs external
                               # and ingests metadata/page text; use --copy only when a local copy is intended

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
fund search "shareholder proposal" --fund simplex  # local source-page search with citations
fund classify simplex --activist verified_activist --activity active --doc doc_003 --page 1 --quote "..."
fund universe                  # reviewed activist/activity status and evidence pointers
fund factsheet simplex --save              # save current hashed snapshot + changes, prune older fund snapshots
fund returns simplex                       # approved return history
fund screen --sort "annualized_sharpe desc" # deterministic approved-data screener
fund screen --preset small-cap-activist --min-history 3 --activist-only
fund similar simplex --limit 5 --activist-only # deterministic qualitative similarity ranking
fund similar --all --limit 20               # rank all fund pairs by similarity
fund compare simplex begonia               # synthesis paragraph + focused terms/metrics/returns tables
fund compare simplex begonia --web-reality-check --dry-run
                               # show planned English/Japanese public-web checks; no API call
fund compare simplex begonia --fields management_fee,sharpe_ratio
fund export simplex                         # Markdown factsheet with sources + peer context
fund export compare simplex begonia         # Markdown comparison

fund analyze --funds simplex,begonia -q "..."   # LLM analysis, cited, approved data only
fund analyze-activism --funds simplex,begonia --dry-run # preflight policy-dependent web check
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
  db.py         explicit schema + migrations + indexes
  llm.py        the only file that talks to OpenAI; logs every call
  ingest.py     source PDFs (managed copy or external path) -> pages (+ page images for vision)
                plus local cited page search and reviewed folder/alias crosswalks
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
tests/          no-API pipeline tests: .venv\Scripts\python -m tests.test_pipeline
data/           pdfs, db, page_images, current factsheet snapshots
```

## Storage Notes

- Documents may be managed copies or external source files. When `FUND_SOURCE_DOC_ROOT`
  is configured, `fund add-doc` automatically keeps files below that root external: PDFs remain on the source drive while the project
  stores the source path, metadata, extracted page text, and approved evidence.
- Capture public web research as a dated PDF in the approved T-drive source root,
  then register it with `fund add-doc --type evidence`. Supporting evidence never
  becomes a current factsheet and cannot outrank a factsheet during review.
- `fund discover-docs` is read-only. It inventories external PDFs, infers only
  filename candidates, and flags SHA-256 duplicates already registered in the database.
- `fund refresh` combines discovery, the reviewed folder crosswalk, and newest
  filename-classified factsheet candidates into one read-only preflight.
- `fund search` uses a local SQLite full-text index over extracted page text and
  always returns the source document, page, excerpt, and source path. It is the
  evidence-retrieval layer; no generic RAG/vector store is used.
- `fund classify` writes only source-backed `activity_status` and
  `activist_universe_status` facts. `--activist-only` screen/similarity results
  require `verified_activist` and `active` unless explicit inclusion flags are used.
- Locally managed copies, when explicitly requested with `--copy`, are stored under
  normalized per-fund paths such as `data/pdfs/simplex/2026-06-30_factsheet_simplex.pdf`.
- `documents.is_current` marks the active factsheet or presentation for a fund; older docs remain linked through `supersedes_doc_id`.
- Factsheet snapshots are local, regenerable artifacts and are gitignored. Older
  snapshots for the same fund are removed when a new current snapshot is saved.
- Web-backed fund research should search English and Japanese sources by default,
  translate Japanese evidence into English, and keep source URLs attached.
- A managed environment can block live web analysis when it would transmit
  workspace-derived context externally. That policy boundary does not affect
  local document intake, evidence review, deterministic comparison, or dry-run.

## Practical Guide

For a new intern's shortest setup and monthly-work checklist, see
[INTERN_QUICKSTART.md](INTERN_QUICKSTART.md).

For the actual day-to-day terminal workflow, see [TERMINAL_WORKFLOW.md](TERMINAL_WORKFLOW.md).

For the shared-drive document lifecycle, see [T_DRIVE_SYSTEM.md](T_DRIVE_SYSTEM.md).

For the project purpose, current capabilities, server setup, and limitations,
see [INTERN_PROJECT_BRIEF.md](INTERN_PROJECT_BRIEF.md).

For the Windows VM handoff and post-migration build checklist, see
[VM_MIGRATION_PLAN.md](VM_MIGRATION_PLAN.md).

# Terminal Workflow

This repo is meant to be used from the terminal first.

The active pipeline is:

`PDFs -> proposed facts / proposed return rows -> human approval -> approved factsheet -> comparison -> analysis`

The schema is now explicit:

- normal commands read the current schema
- `fund migrate` is the only command that should change schema structure
- new factsheets/presentations should come in through `fund add-doc`

## Start Here

From the repo root:

macOS / Linux:

```bash
source .venv/bin/activate
unset FUND_DB_PATH
export FUND_DB_PATH="$PWD/data/db/fund.db"
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
Remove-Item Env:FUND_DB_PATH -ErrorAction SilentlyContinue
$env:FUND_DB_PATH = (Resolve-Path .\data\db\fund.db)
```

If this is a fresh Windows VM checkout:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
```

Do not copy the macOS `.venv/` folder into the Windows VM. Rebuild it on the VM.
Also treat `.env` deliberately: Git will not transfer it, and folder-copying it
would silently move the API key.

Sanity check:

```bash
which fund
python3 -c "from fund.config import DB_PATH; print(DB_PATH)"
fund doctor
```

Windows PowerShell equivalent:

```powershell
Get-Command fund
py -c "from fund.config import DB_PATH; print(DB_PATH)"
fund doctor
```

If `which fund` is blank or points somewhere unexpected, use the explicit venv
command. On macOS / Linux:

```bash
.venv/bin/fund status
.venv/bin/fund migrate
```

On Windows PowerShell:

```powershell
.\.venv\Scripts\fund status
.\.venv\Scripts\fund migrate
```

If any command says `Run: fund migrate`, run:

macOS / Linux:

```bash
fund migrate
fund status
```

Windows PowerShell:

```powershell
fund migrate
fund status
```

## Command Map

Use this as the quick index. Commands are grouped by how you use them day to day.

### Overview

These commands tell you what exists and what to do next.

`fund status`
- overall database state
- how many proposals, approved facts, and return rows exist
- schema version, current-doc counts, PDF count, and cached image footprint

`fund list`
- all funds
- the short handle to type, like `simplex` or `ichigo`

`fund inbox`
- the pending review queue grouped by fund
- shows verified counts, conflicts, and the next review command

`fund next`
- reads the current database state and suggests one useful next command
- start here when you are unsure what to do

### Add / Extract / Review

These commands move documents into approved truth.

`fund add-doc --fund <fund> --type factsheet --date YYYY-MM-DD <path>`
- copies the PDF into normalized per-fund storage
- marks it as the new current factsheet/presentation
- keeps the old document for audit
- ingests pages immediately

`fund onboard <fund>`
- runs the full extraction plan for that fund
- factsheets route to terms / metrics / returns extraction
- presentations route to qualitative strategy / people extraction

`fund review <fund> --list`
- shows the pending proposed factsheet
- this is the approval inbox, not the final output
- shows quote verification status next to each proposed fact

`fund pending <fund>`
- friendlier alias for `fund review <fund> --list`

`fund review <fund>`
- interactive approval flow
- approve, edit, reject, or skip pending proposals

`fund review <fund> --approve-verified`
- approves verified proposals only when they have no field conflict
- leaves anything ambiguous for manual review

`fund approve <proposal_id>` / `fund reject <proposal_id>`
- approve or reject a single proposal or proposed return row

### Approved Data Views

These commands show approved source-of-truth data.

`fund factsheet <fund> --sources`
- shows the approved source-of-truth factsheet
- this is the clean profile to trust for later comparison
- reported metrics are shown as sourced facts
- computed return analytics are shown separately as internal, annualized calculations

`fund factsheet <fund> --save`
- saves one current hashed JSON snapshot for that fund
- removes older saved snapshots for the same fund
- writes a `changes` section explaining what changed versus the previous snapshot

`fund returns <fund>`
- shows approved structured return rows only

### Analytical And Functional Tools

These commands compare, screen, rank, export, or analyze approved data.

`fund screen`
- screens funds using approved facts plus computed internal analytics
- no LLM calls
- examples:
  - `fund screen --sort "annualized_sharpe desc"`
  - `fund screen --preset small-cap-activist --min-history 3`
  - `fund screen --where "management_fee<=1.5" --sort "annualized_sharpe desc"`

`fund similar <fund>`
- ranks other funds by deterministic qualitative similarity
- uses approved strategy, activism style, geography, market-cap focus, AUM, and reported metrics
- intentionally excludes terms and return history
- examples:
  - `fund similar simplex --limit 5`
  - `fund similar --all --limit 20`

`fund compare <fund1> <fund2>`
- compares approved data only
- does not use pending facts
- leads with a synthesis paragraph
- tables are focused on terms, reported metrics, and returns
- return-derived risk stats are labeled computed/internal and annualized

`fund export <fund>`
- writes a Markdown factsheet to stdout
- includes sections, provenance, presentation highlights, annual returns,
  computed analytics, drawdown, peer-relative context, and a sources appendix
- use `--no-peer-context` to omit peer medians/ranks

`fund export compare <fund1> <fund2> [fund3...]`
- writes a Markdown side-by-side comparison to stdout
- useful when you want something greppable, diffable, or shareable

`fund analyze --funds a,b -q "..."`
- runs LLM analysis on approved snapshots only
- this is a separate reasoning step after facts are approved
- use it after `factsheet`, `returns`, `similar`, and `compare`, not instead of them

`fund analyze-activism --funds a,b`
- runs a web-backed reality check using English and Japanese web search by default
- compares what the funds say in approved materials against public activism examples
- translates Japanese findings into English and keeps the output English-only
- keeps the conclusion to one paragraph, then lists the public examples used
- requires internet/API access; if it fails with a connection error, the database
  may still be fine, but the live web-backed OpenAI call did not complete

Any future web-backed fund search should follow the same rule: search both English
and Japanese sources, translate Japanese evidence into English, and keep source URLs
attached. Raw Japanese should not be shown by default except for proper names.

### Operational Checks

These commands keep the system clean and sane.

`fund doctor`
- checks DB path, schema version, Python executable, API key presence, and pending queue

`fund migrate`
- applies schema and storage migrations explicitly
- use this after code updates that change the database layout

`fund verify`
- runs deterministic quote checks over staged proposals and return rows
- no LLM calls
- use `fund verify --doc doc_003` to scope it to one document
- statuses include `verified_text`, `unverified_text`, and `unverifiable_vision`

`fund reconcile <fund>`
- runs deterministic QC over approved return rows
- compares reported annual rows against compounded monthly rows
- compares YTD rows against compounded monthly rows for the same year
- flags monthly rows outside broad sanity bounds
- no LLM calls

`fund prune`
- deletes cached page images only
- page images are regenerable cache, not source data

## The Important Difference: Review vs Factsheet

`review` and `factsheet` are not the same thing.

`fund review simplex --list`
- proposed / pending values
- used to decide what to approve

`fund factsheet simplex --sources`
- approved values only
- used as source-of-truth

So the intended sequence is:

1. `fund onboard ichigo`
2. `fund review ichigo --list`
3. `fund review ichigo`
4. `fund factsheet ichigo --sources`
5. `fund returns ichigo`
6. `fund similar ichigo`
7. `fund compare ichigo simplex`
8. `fund analyze --funds ichigo,simplex -q "..."`

## Recommended Daily Flow

For a new fund:

```bash
fund next
fund onboard ichigo
fund review ichigo --list
fund review ichigo
fund factsheet ichigo --sources
fund returns ichigo
```

For a refreshed factsheet:

```bash
fund add-doc --fund simplex --type factsheet --date 2026-06-30 ~/Downloads/simplex_june.pdf
fund onboard simplex --dry-run
fund onboard simplex
fund review simplex --list
fund review simplex
fund factsheet simplex --sources
fund factsheet simplex --save
```

For already-approved funds:

```bash
fund inbox
fund factsheet simplex --sources
fund returns simplex
fund reconcile simplex
fund similar simplex --limit 5
fund similar --all --limit 20
fund screen --sort "annualized_sharpe desc"
fund screen --preset small-cap-activist --min-history 3
fund compare simplex begonia
fund compare simplex begonia --embedding-similarity
fund export simplex
fund export compare simplex begonia
fund analyze --funds simplex,begonia -q "What are the key differences in strategy, terms, and approved return history?"
fund analyze-activism --funds simplex,begonia
```

## Reported vs Computed

Reported values:

- come from approved PDF facts
- keep their source document/page/quote
- include fields like AUM, beta, volatility, Sharpe ratio, and information ratio

Computed values:

- come only from approved structured return rows
- are labeled internal
- use `FUND_RISK_FREE_RATE` and `FUND_PERIODS_PER_YEAR`
- use the shared analytics spine in `fund/analytics.py`
- include cumulative return, annualized volatility, annualized Sharpe, Sortino,
  positive-period percentage, rolling return history, and max drawdown

The system should never make an internal calculation look like a manager-reported metric.

## Compare vs Analyze

`fund compare ...`
- deterministic
- approved data only
- best for clean table-based comparison

`fund similar ...`
- deterministic
- approved data only
- best for qualitative peer ranking and future clustering
- does not use terms or return history in the similarity score

`fund analyze ...`
- LLM reasoning on approved snapshots only
- best for synthesis, tradeoffs, and written comparison paragraphs
- should be treated as an interpretation layer, not source-of-truth

`fund analyze-activism ...`
- LLM reasoning plus English and Japanese web search
- best for the specific question: "what do they say they do, and what do public events suggest they actually do?"
- Japanese findings are translated into English before display
- still separate from source-of-truth; it interprets approved internal data plus public evidence

## Snapshots

`fund factsheet <fund> --save` and analysis commands save one current snapshot per fund.

- the file name is still content-hashed
- older saved snapshots for the same fund are removed automatically
- factsheet snapshots are local artifacts and are gitignored
- the retained file includes a `changes` block showing what changed versus the previous snapshot
- the approved database remains the source of truth; snapshots are reproducibility/artifact files

## Moving To A Windows VM

Recommended route: push the cleaned repo to GitHub, clone it on the Microsoft
desktop, then rebuild the environment there.

Do:

```powershell
git clone <repo-url>
cd <repo>
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
fund doctor
```

Do not:

- copy `.venv/` from macOS; it contains macOS binaries and old unused packages
- rely on Git to transfer `.env`; add the OpenAI key deliberately on the VM
- move legacy databases or page-image caches; `data/db/fund.db` is the live DB,
  while page images and factsheet snapshots are regenerable

## Troubleshooting

If a fund unexpectedly looks empty:

```bash
echo $FUND_DB_PATH
python3 -c "from fund.config import DB_PATH; import sys; print(sys.executable); print(DB_PATH)"
```

Most likely causes:

- wrong virtualenv
- wrong `FUND_DB_PATH`
- looking at a pending queue when you meant to inspect approved data

## What Travels To The VM

Commit and transfer:

- `fund/`
- `tests/`
- `data/pdfs/`
- `README.md`, `TERMINAL_WORKFLOW.md`, `AGENTS.md`
- packaging files such as `pyproject.toml`

Keep local or recreate:

- `.venv/` (recreate on the VM)
- `.env` (add the key deliberately on the VM)
- `data/db/fund.db` unless you intentionally choose a DB transfer path
- `data/page_images/`
- `data/factsheets/`

Removed pre-migration ballast:

- old `legacy/` Streamlit/script reference tree
- old legacy database/backups
- generated `fund.egg-info/`
- tracked factsheet snapshots

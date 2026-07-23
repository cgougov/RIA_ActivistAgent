# Japan Activist Funds Research Platform: Intern Brief

## Purpose

This project is a lean, terminal-first research system for Japan-focused
activist and engagement funds. Its job is to turn source documents into an
auditable fund universe and comparison set.

The intended questions are:

- Which funds belong in the Japan activist universe?
- Are they active, uncertain, or inactive based on current evidence?
- What do they say their strategy is, and what source documents support that?
- How do their terms, positioning, and comparable return histories differ?
- Does public observed activism align with a fund's stated strategy?

The system is deliberately designed to avoid plausible but unsupported answers.

## What has been implemented

### Source and evidence workflow

- T-drive PDFs remain on the T-drive; the project records paths, hashes,
  document metadata, extracted page text, and approved evidence.
- Reviewed aliases map T-drive folders to database funds.
- `fund crosswalk` checks folder-to-fund mappings without changing anything.
- `fund refresh` gives a fast, read-only view of the latest likely factsheet in
  each mapped folder.
- `fund discover-docs` performs the slower SHA-256 duplicate check on a chosen
  folder.
- `fund search` searches extracted page text locally and returns document/page
  citations. It is evidence retrieval, not a chatbot answer.

### Research data workflow

- PDFs are ingested into pages, then extraction creates proposed facts and
  proposed returns.
- Every approved fact and return retains its source document, page, and quote.
- Facts use one approved value per fund/field/share class; approving a newer
  sourced value revises the prior one rather than creating duplicates.
- Factsheets are assembled from approved data, not manually maintained files.
- Return analytics, common-period comparisons, and return correlations are
  computed locally and clearly labeled as internal calculations.

### Activist-universe workflow

- `activity_status`: `active`, `uncertain`, or `inactive`.
- `activist_universe_status`: `candidate`, `verified_activist`, or `excluded`.
- `fund classify` records either status only with a supplied document, page, and
  quote.
- `fund universe` shows the current classification and evidence pointer.
- `fund screen --activist-only` and `fund similar --activist-only` use the
  strict `verified_activist + active` rule.

### Comparison and similarity

- `fund compare` shows a deterministic comparison of approved facts and returns.
- Common-period statistics avoid comparing mismatched return windows.
- Return correlation is reported only over the shared monthly window and is not
  treated as proof of strategy similarity.
- `fund similar` starts with the target fund's approved strategy context and
  explains each peer's specific difference.

## How the system is intentionally lean

The project does not use a web of scripts, separate topic tables, a web UI, or
a generic vector database.

| Design choice | Why it is efficient and reliable |
| --- | --- |
| One `fund` command-line interface | One place to learn and operate the workflow. |
| SQLite database | Portable, inspectable, and sufficient for the bounded fund universe. |
| One `facts` table plus `returns` | Prevents per-topic schema sprawl and duplicate values. |
| Local SQLite full-text page search | Returns exact pages and excerpts without vector-search ambiguity. |
| T-drive external documents | Avoids duplicate PDFs and keeps the source of record where the team already works. |
| Deterministic comparison/analytics | No model call is needed for calculations that code can verify. |
| Two model touchpoints | Model use is limited to extraction proposals and analysis; all calls are logged. |

This design favors traceability over flashy automation. A model can propose or
interpret; it cannot silently become the record of truth.

## What the project relies on

### Required on a user or server machine

- Windows PowerShell and Python 3.10 or later.
- A fresh project virtual environment installed from `pyproject.toml`.
- Access to the shared T-drive for source PDF refreshes.
- A local `.env` file containing the permitted OpenAI key and, normally,
  `FUND_SOURCE_DOC_ROOT`.
- A local SQLite database at `data/db/fund.db` if the user needs the current
  approved research state.

### Not stored in Git

Git intentionally does **not** contain:

- `.env` or any API key;
- `data/db/fund.db` or its backups;
- T-drive source PDFs;
- generated page-image caches and factsheet snapshots.

This means a new intern can clone the code and documentation from Git, but must
receive the current database through the approved secure internal channel. They
must also have the T-drive mounted and create their own `.env` locally.

## New-server setup

After cloning the repository on the Microsoft server:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
```

Then:

1. Add the approved API key and `FUND_SOURCE_DOC_ROOT` to `.env`.
2. Receive `fund.db` through the secure internal transfer process and place it
   at `data\db\fund.db`.
3. Confirm the T-drive is mounted and readable.
4. Run:

```powershell
fund migrate
fund doctor
fund refresh --root "T:\Research\General\20 Japan-Focused Hedge Funds"
.\.venv\Scripts\python.exe -m tests.test_pipeline
```

`fund doctor` should show the current schema. `fund refresh` should show the
mapped folders. The tests should pass without an API call.

For detail, use [T_DRIVE_SYSTEM.md](T_DRIVE_SYSTEM.md) and
[TERMINAL_WORKFLOW.md](TERMINAL_WORKFLOW.md).

## Important limitations

### Live web search in this managed environment

The code supports bilingual English/Japanese public-web activism research.
`fund compare ... --web-reality-check --dry-run` shows its planned searches,
including Japanese queries.

In the managed Codex environment used to build this project, live web-enabled
model calls are blocked when they would send workspace-derived context to an
external service. This is a tenant/runtime data-egress rule, not a code bug,
missing API key, or user-permission issue.

The feature can operate in a company-approved deployment where that data flow is
permitted. Until then, capture a public source as a dated PDF on the approved
T-drive and add it with `fund add-doc --type evidence`; it will follow the same
page/quote review process as all other sources.

### Current universe coverage

The initial mapped database universe contains seven funds. Additional priority
funds, candidate activist funds, and performance-workbook mappings still need
reviewed onboarding. An unmatched T-drive folder is a lead, not a database fund
or an activist classification.

### Freshness is a review decision

`fund refresh` uses filename date patterns as a fast candidate selector. The
filename date can be a publication date rather than the report's as-of date.
Always inspect the PDF and use its stated as-of date when registering it.

### Classification is intentionally not automatic

The system does not infer that a fund is an activist or active merely from a
folder name, historic performance, or similarity. A reviewer must record the
status with document, page, and quote evidence.

## Practical extension roadmap

1. Create reviewed identity mappings for the remaining priority funds and
   activist candidates.
2. Classify the mapped funds using current factsheets, official sources, and
   supporting public evidence.
3. Build a controlled crosswalk for the manager performance workbook before any
   return import.
4. Enable the live bilingual web-analysis adapter only in an approved deployment
   with the required data-egress permission.
5. Add sources for candidate funds, then include only `verified_activist +
   active` funds in the default research universe.

## First-day intern checklist

```powershell
fund doctor
fund status
fund refresh --root "T:\Research\General\20 Japan-Focused Hedge Funds"
fund universe
fund search "shareholder activism" --fund edwall
fund compare simplex begonia
fund similar simplex --limit 5
```

These commands are read-only except where explicitly documented otherwise.
Before any live extraction call, run its `--dry-run` version first.

# Terminal Workflow

This is the Windows, terminal-first operating guide for the Japan activist-fund
research platform. The product is intentionally small: maintain a reliable
activist universe, retain document-level evidence, approve facts, then compare
only approved information.

## The operating model

```text
T-drive source PDF (stays on T:) -> page text + metadata -> proposed facts
-> evidence check -> approval -> factsheet / screen / compare
```

The SQLite database is the approved record. PDFs are not copied from the
configured T-drive source root; the database holds their source paths, hashes,
metadata, page text, and approved evidence. A fact is usable only when it has a
document, page, and supporting quote.

No generic RAG or vector database is needed for the normal workflow. The
database already provides the useful retrieval boundary: exact fund, field,
document, page, quote, and approved status. Use `fund factsheet --sources` or
`fund export` whenever you need to inspect the evidence behind an output.

## Start a session

From the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
$env:FUND_DB_PATH = (Resolve-Path .\data\db\fund.db)
fund doctor
fund status
fund next
```

For a new Windows machine:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
```

Add the OpenAI key and T-drive root deliberately to `.env`:

```dotenv
OPENAI_API_KEY=...
FUND_SOURCE_DOC_ROOT=T:\Research\General
```

`.env`, the live database, page-image cache, and generated snapshots are not
versioned. Rebuild `.venv` on each machine; never copy it between machines.

If the command is unavailable, use the virtual-environment executable directly:

```powershell
.\.venv\Scripts\fund.exe status
```

Run `fund migrate` only when `fund doctor` or a release note explicitly asks
for it. Migration changes schema metadata; it is not a daily command.

## Daily workflow

### 1. See what needs attention

```powershell
fund status
fund inbox
fund next
fund list
```

`fund inbox` is the review queue. `fund next` is the quickest way to resume
after an interruption.

### 2. Find a current source document without changing anything

```powershell
fund discover-docs --root "T:\Research\General\20 Japan-Focused Hedge Funds"
fund crosswalk --root "T:\Research\General\20 Japan-Focused Hedge Funds"
fund refresh --root "T:\Research\General\20 Japan-Focused Hedge Funds"
```

This is read-only. It inventories PDFs, infers only filename candidates, hashes
each file, and identifies documents already known to the database. It does not
copy files, create documents, call an API, or alter the database.

`fund crosswalk` maps immediate T-drive folders to reviewed fund identities.
Add a reviewed alias when a known folder uses a different label:

```powershell
fund alias --fund simplex "Simplex Long Short / Value Up"
```

`fund refresh` is the one-command preflight: it combines the folder mapping,
document inventory, newest filename-classified factsheet, and the next action.
It skips file hashing to stay fast; run `fund discover-docs` on a chosen folder
before registration when you need SHA-256 duplicate confirmation.

Choose the most recent factsheet by the document's stated as-of date, not just
by Windows modified time. A presentation is supporting evidence; a factsheet is
the normal source for terms, AUM, and returns.

### 3. Register one selected source

```powershell
fund add-doc --fund simplex --type factsheet --date 2026-06-30 \
  "T:\Research\General\20 Japan-Focused Hedge Funds\Simplex\factsheet.pdf"
```

When the path is inside `FUND_SOURCE_DOC_ROOT`, the PDF stays external by
default. The command records its source path and hash and ingests page text.
Use `--external` to make that choice explicit. Use `--copy` only for a document
that is meant to be managed locally.

Do not use `fund ingest` for normal research. It is the sample/seed bootstrap
command, retained for tests and a fresh demonstration database.

### 4. Preflight extraction, then extract

```powershell
fund onboard simplex --dry-run
fund onboard simplex
fund verify
fund reconcile simplex
```

The dry run is required before a live extraction call. `fund onboard` routes a
factsheet to terms, metrics, and returns; it routes a presentation to strategy
and people. It creates proposals only—never approved facts.

For narrow work, use the explicit command instead:

```powershell
fund extract doc_003 --scope profile_terms --dry-run
fund extract doc_003 --scope profile_terms
fund extract doc_003 --returns --page 1
```

Every model call goes through the logged extraction path. Check `fund log` if a
call fails or costs need to be reviewed.

### 5. Review evidence and approve only what is supported

```powershell
fund review simplex --list
fund review simplex --approve-verified
fund review simplex
fund factsheet simplex --sources
```

`--approve-verified` is appropriate only for proposals whose deterministic
quote check passes and which have no conflict. It is a convenience, not a truth
guarantee: confirm that the quote, page, document date, and field meaning match
before using the result. Use interactive review for numbers in tables,
ambiguous wording, and conflicts.

## Produce an answer from approved data

```powershell
fund factsheet simplex --sources
fund search "shareholder proposal" --fund simplex
fund returns simplex
fund screen --preset small-cap-activist --min-history 3 --activist-only
fund similar simplex --limit 5 --activist-only
fund compare simplex begonia
fund compare simplex begonia --web-reality-check --dry-run
fund export simplex
fund export compare simplex begonia
```

These are deterministic, local operations over approved data. They do not call
an LLM. `similar` uses the explicit qualitative fields; it is a research lead,
not proof that a peer is an activist. `screen` and `similar` should be used only
after the universe has been classified with approved activism and activity facts.

`fund search` is the local evidence retrieval tool. It searches extracted page
text and returns the fund, document, page, excerpt, and original source path.
It is deliberately citation-first rather than a generic RAG/vector search.

`fund compare` is deterministic by default. Add `--web-reality-check` only
after reviewing the deterministic comparison; its dry run prints the planned
English and Japanese public-search queries without making an API call.

Use these commands before any narrative analysis. They make the evidence and
the calculations inspectable instead of asking a model to rediscover them.

## Analysis and public web validation

```powershell
fund analyze --funds simplex,begonia -q "Compare the approved strategies."
fund analyze --funds simplex,begonia -q "..." --dry-run
fund analyze-activism --funds simplex,begonia --dry-run
```

`fund analyze` sends approved factsheet snapshots to the configured model and
saves the resulting analysis. Always dry-run first.

`fund analyze-activism` is a separate, policy-dependent convenience for public
English- and Japanese-language web research. It must never auto-approve a fact.
In this managed environment, policy blocks sending workspace-derived snapshots
to an external web-enabled model. That is a tenant data-egress control, not a
missing API key or a lack of user permission. The dry run remains useful because
it shows the exact planned search context without transmitting it.

When that policy applies, keep web validation outside this command: collect the
public source URL, document date, quoted passage, and relevant page locally;
save a dated PDF in the approved T-drive source root, then register it as
supporting evidence and approve only after review:

```powershell
fund add-doc --fund simplex --type evidence --date 2026-07-17 `
  "T:\Research\...\public_source.pdf"
```

Supporting evidence never becomes the current factsheet and cannot outrank a
factsheet during conflict resolution. A local
deployment whose policy permits the call can use the live command, but its
output is still research material until source-backed facts are reviewed.

## Activist-universe decision rule

Maintain these two sourced facts for every fund under consideration:

| Field | Allowed values | Meaning |
| --- | --- | --- |
| `activist_universe_status` | `candidate`, `verified_activist`, `excluded` | Whether the fund belongs in the activist universe. |
| `activity_status` | `active`, `uncertain`, `inactive` | Whether current evidence supports treating the fund as operating. |

Use the most recent reliable manager factsheet, official manager site or filing,
and (when relevant) a public engagement/ownership record. Apply these rules:

- **active**: current evidence (normally within 12 months) shows the fund or
  strategy is offered, reporting, investing, or actively managed.
- **uncertain**: evidence is stale, contradictory, or only proves the historic
  strategy; retain the fund but flag it for refresh.
- **inactive**: a reliable source states liquidation, closure, merger, or that
  the strategy has stopped accepting/managing capital.
- **verified_activist**: the manager or a high-quality public source explicitly
  describes engagement, shareholder proposals, governance/value-up action, or
  an equivalent activist mandate.
- **candidate**: plausible activist lead, not yet proven. Do not include it in
  activist comparisons by default.

Each status requires its own document/page/quote evidence. Never infer active
or activist from a folder name, performance history, or a similar fund.

Record a human-reviewed classification directly from its evidence:

```powershell
fund classify simplex --activist verified_activist --activity active `
  --doc doc_003 --page 1 --quote "Exact supporting source text"
fund universe
```

`fund screen --activist-only` and `fund similar --activist-only` apply the
strict rule. Add `--include-uncertain` or `--include-candidates` only when that
broader universe is intentional.

## Advanced and maintenance commands

```powershell
fund verify --doc doc_003       # re-run deterministic evidence checks
fund reconcile simplex          # compare approved monthly/annual/YTD returns
fund factsheet simplex --save   # write one local, regenerable snapshot
fund analyses                   # list saved narrative analyses
fund show an_xxx                # display one saved analysis
fund log                        # model-call audit log
fund prune                      # remove only regenerable page-image cache
```

`fund factsheet --save` retains one current snapshot per fund with a change
summary. Snapshots are convenience artifacts; the database remains the source
of truth.

## Before handing off a result

```powershell
.\.venv\Scripts\python.exe -m tests.test_pipeline
fund doctor
fund status
```

For a fund-level conclusion, also run:

```powershell
fund factsheet <fund> --sources
fund reconcile <fund>
```

State any missing, stale, uncertain, or unverified evidence plainly. The system
is designed to make those gaps visible, not to fill them with plausible prose.

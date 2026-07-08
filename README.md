# Japan Activist Funds Research Platform

A lean, terminal-first research platform for diligencing Japan activist funds.

```
absorb PDFs → extract proposed facts (LLM) → human approval → standardized factsheet
→ deterministic comparison → LLM analysis on approved truth only
```

## Principles

- Do not invent data. LLM output is proposal-only; a human approves every fact.
- Every approved fact keeps its source document, page, and quote.
- One value per field per fund, enforced by the database itself. Approving again = revising.
- When documents disagree on a field, the most recent source document wins; approving the
  whole factsheet keeps that value and supersedes the older ones (shown before you approve).
- Return extraction is high-recall by design — it captures the full multi-year history and
  includes uncertain cells (flagged), because a human approves every row.
- The LLM is called at exactly two points — extraction and analysis — and every call is logged.
- If it can be computed from stored data, it is never sent to the model.
- The factsheet is assembled on demand from approved data; analyses freeze a hashed snapshot.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # add OPENAI_API_KEY
```

## The pipeline, in order

Funds can be named by a handle (`simplex`, `begonia`) instead of `fund_002` — a
case-insensitive match on the fund or manager name. `fund list` shows the handle
for each fund.

```bash
fund init                      # create the database
fund ingest                    # register funds/documents (data/seed) and absorb PDF pages
fund status                    # pipeline state at a glance
fund list                      # every fund, its typeable handle, and review state

fund extract doc_003 --scope profile_terms --dry-run   # preflight, no API
fund extract doc_003 --all-scopes                      # LLM: propose descriptive facts
fund extract doc_003 --returns --page 1                # LLM vision: propose return rows
fund extract doc_005 --returns --page 1 --from-text    # returns from page text (vision-hostile tables)

fund review simplex --list     # the whole proposed factsheet at once, with evidence
fund review simplex            # [a]pprove all / [r]eject some then approve / [f]ield-by-field
fund review simplex --approve-all          # scripted bulk approve (review --list first)
fund review doc_003            # or scope the review to a single document

fund factsheet simplex --sources           # the standardized factsheet (returns at the bottom)
fund factsheet simplex --save              # freeze a hashed snapshot
fund returns simplex                       # approved return history
fund compare simplex begonia               # side-by-side; calendar-year + common-period returns
fund compare simplex begonia --fields management_fee,sharpe_ratio

fund analyze --funds simplex,begonia -q "..."   # LLM analysis, cited, approved data only
fund analyses                  # saved analyses
fund show an_xxx               # one analysis in full
fund log                       # every LLM call: tokens, status, target
```

## Layout

```
fund/           the whole system (~10 modules, readable top to bottom)
  schema.py     the standardized field vocabulary — single source of truth
  db.py         9 tables: funds, documents, pages, proposals, proposed_returns,
                facts, returns, llm_calls, analyses
  llm.py        the only file that talks to OpenAI; logs every call
  ingest.py     PDFs -> pages (+ page images for vision)
  extract.py    LLM touchpoint #1: documents -> proposals (dedup at entry)
  review.py     approval -> facts/returns (dedup enforced by primary keys)
  factsheet.py  assembled factsheet + hashed snapshots
  compare.py    deterministic comparison, one decimal place
  analyze.py    LLM touchpoint #2: snapshots -> cited narrative
tests/          no-API pipeline tests: python -m tests.test_pipeline
data/           pdfs, db, page_images, factsheet snapshots
legacy/         the pre-rebuild system, frozen as a reference parts bin
```

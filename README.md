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

```bash
fund init                      # create the database
fund ingest                    # register funds/documents (data/seed) and absorb PDF pages
fund status                    # pipeline state at a glance

fund extract doc_003 --scope profile_terms --dry-run   # preflight, no API
fund extract doc_003 --all-scopes                      # LLM: propose descriptive facts
fund extract doc_003 --returns --page 1                # LLM vision: propose return rows

fund review doc_003 --list     # see the pending queue with evidence
fund review doc_003            # interactive approve / edit / reject
fund review doc_003 --approve-all          # bulk approve (review the list first)

fund factsheet fund_002 --sources          # the standardized factsheet
fund factsheet fund_002 --save             # freeze a hashed snapshot
fund returns fund_002                      # approved return history
fund compare fund_002 fund_006             # deterministic side-by-side
fund compare fund_002 fund_006 --fields management_fee,sharpe_ratio

fund analyze --funds fund_002,fund_006 -q "..."   # LLM analysis, cited, approved data only
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

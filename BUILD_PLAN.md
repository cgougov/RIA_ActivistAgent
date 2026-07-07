# Ground-Up Rebuild Scope — Lean, Terminal-First

This replaces `FOUNDATION_REBUILD_PLAN.md` and `REBUILD_IMPLEMENTATION_SPEC.md` (both should be deleted — they're from an abandoned direction).

## What "done" means

One clear pipeline, following the project goal exactly, all driven from the terminal:

```
absorb PDF → extract proposed facts (incl. returns) → approve → standardized factsheet
→ deterministic comparison → LLM analysis on approved truth only
```

No Streamlit until this is flawless in the terminal. Lean enough that any file can be read top-to-bottom and understood.

## The core structural insight

The old system stores "a fact about a fund" in **9 different tables** (`fund_characteristics`, `fund_profile_attributes`, `fund_people`, `fund_terms`, `fund_strategy`, `fund_metrics`, `fund_exposures`, `fund_writeups`, `fund_flags`). This is the single biggest source of duplication, drift, and "which table does this go in" confusion.

**New model: one `facts` table**, keyed by a controlled field vocabulary. A fact is: `fund_id, field_key, value_text, value_num, unit, as_of_date, source_doc_id, page, quote, confidence, method, updated_at`. The field vocabulary (`domicile`, `management_fee`, `sharpe_ratio`, `cio_name`, …) comes from the ported taxonomy. Returns stay separate (they're time-series, not single-value): one `returns` table.

Comparison then becomes trivial and is exactly what you asked for ("compare characteristics specifically"): `SELECT fund_id, field_key, value FROM facts WHERE field_key IN (...) AND fund_id IN (...)`, pivot, print.

The **factsheet is not stored redundantly** — it is *assembled on demand* from `facts` + `returns`, shaped by the schema definition in code. When you run an analysis (or "lock" a fund), we snapshot the assembled factsheet to a JSON file + content hash, so the analysis is reproducible and auditable. That's the "intentional about how the factsheet is saved" answer: the truth lives in `facts`; a factsheet is a deterministic view of it; a snapshot is a frozen, hashed copy tied to an analysis.

## Target data model (~8 tables, down from 31)

| Table | Purpose |
|---|---|
| `funds` | identity (id, name, manager) |
| `documents` | source PDFs + metadata + extraction status |
| `pages` | page text + page-image path per document |
| `proposals` | proposed descriptive facts, pre-approval (status: pending/approved/rejected) |
| `proposed_returns` | proposed return rows, pre-approval |
| `facts` | **approved descriptive facts — the one fact table**, keyed by field vocabulary |
| `returns` | approved return rows (time-series) |
| `llm_calls` | log of every LLM call (type, model, tokens, cost, refs, raw output) |
| `analyses` | saved LLM analysis outputs + the factsheet-snapshot hash they used |

Everything the old schema had beyond this (`citations`, `benchmark_returns`, `external_sources`, `public_campaigns`, `campaign_events`, `fact_conflicts`, `document_classification_suggestions`, `change_history`, the 9 clean tables…) is either dropped or folded in. Anything genuinely needed later, we add back deliberately — not carry as ambient complexity now.

## Target file structure (~10 modules + 1 CLI, down from 54 files)

```
afp/
  config.py       # env, paths, model ids
  db.py           # connection + schema (one create_schema)
  schema.py       # the standardized field vocabulary (ported taxonomy + factsheet_schema, merged)
  llm.py          # THE single OpenAI wrapper — every LLM call goes through here, and is logged
  ingest.py       # PDF -> pages + page images
  extract.py      # LLM: pages -> proposals (descriptive + returns). LLM touchpoint #1
  review.py       # approve / reject / revise proposals -> facts + returns (with dedup at write time)
  factsheet.py    # assemble one standardized factsheet from facts+returns; snapshot to JSON
  compare.py      # deterministic field-by-field + return-stats comparison. NO LLM
  analyze.py      # LLM: factsheet(s) -> narrative analysis on approved truth. LLM touchpoint #2
cli.py            # ONE entrypoint: `afp ingest|extract|review|factsheet|compare|analyze`
data/             # db, pdfs, page_images, factsheets/ (snapshots)
tests/            # one test module per stage
```

The 27 scripts collapse into **one `afp` command with subcommands** — the Bloomberg-terminal feel. `afp compare fund_002 fund_006 --fields management_fee,sharpe_ratio`. `afp analyze fund_002 fund_006 --question "..."`.

## The two hard rules you asked for

**1. LLM is called at exactly two points, nowhere else.** (1) extraction (PDF→proposals), (2) analysis (factsheet→narrative). Assembling a factsheet, comparing fields, computing return stats — all pure Python on stored data. If it can be computed from what we have, it is never sent to the model.

**2. Every LLM call is logged.** `llm.py` is the only place that talks to OpenAI. Every call writes an `llm_calls` row: timestamp, call-type, model, input refs, raw output, token counts, cost estimate, linked doc/fund. Extraction won't re-run on an already-extracted document without `--force`. Analyses are saved and re-viewable without re-calling. This is the "minimize uncertainty / smart about LLM vs stored" contract, enforced structurally.

## What gets ported wholesale (the parts bin)

- `core/taxonomy.py` — 78 field definitions, tuned. Merge with `factsheet_schema.py` into the new `afp/schema.py`.
- `core/extraction.py` — the scoped prompts + `SCOPE_GUIDANCE` (the actual extraction intelligence).
- `core/return_table_extraction.py` — vision return-table extraction (bug-fixed this session).
- `core/documents.py` — PDF→page-text and page-image rendering.
- `promote_return`'s upsert-on-conflict pattern (`core/promotion.py`) — the ONE dedup pattern done right; it becomes the model for how all facts are written.
- Return-row pivoting / preview shaping (from `fund_views.py` / `return_rows.py`).

## What gets rebuilt fresh (don't port)

- The schema (31→8 tables).
- The review flow (unified terminal approve/reject/revise, with dedup enforced at write time — the root-cause fix, not cleanup scripts).
- The presentation layer (one `factsheet.py`, not `fund_snapshot` + `fund_brief` + `fund_views` + dead `comparison` code).
- The CLI surface (27 scripts → 1).

## What gets dropped

Streamlit (for now), the second/terminal-bundle review subsystem, all cleanup scripts (dedup moves to write-time so there's nothing to clean up after), the empty future-facing tables, `change_history`/`fact_conflicts` (add back only if needed).

## Build sequence (each stage tested in the terminal with real data before the next)

0. Move current project to `legacy/` (untouched reference). New project builds in a clean tree beside it.
1. `config` + `db` + `schema` + `llm`-logging foundation. Test: create DB, print schema.
2. `ingest` — load the existing PDFs. Test: `afp ingest`, verify pages+images.
3. `extract` — LLM proposals for one document. **Test with real API**, inspect proposals.
4. `review` — approve/reject/revise one document's proposals → `facts`/`returns`, dedup enforced. Test end-to-end on one fund.
5. `factsheet` — assemble + snapshot one standardized factsheet. Test: print it, verify provenance on every field.
6. `compare` — deterministic comparison of two funds. Test: `afp compare`, verify field-by-field + return stats.
7. `analyze` — LLM narrative over approved factsheets. **Test with real API**, verify it only sees approved truth.
8. Only then: a thin read-only Streamlit viewer, if still wanted.

## Locked decisions

1. **Folders**: move all current code into `legacy/` (untouched reference/parts bin). New lean project builds at repo root.
2. **Data model**: one field-keyed `facts` table for all descriptive facts; `returns` stays a separate time-series table. Confirmed.
3. **CLI**: the command is `fund` — `fund ingest`, `fund extract`, `fund review`, `fund factsheet`, `fund compare fund_002 fund_006`, `fund analyze ...`.

The package folder is therefore `fund/` (importable as `fund`), CLI entry `fund` (via `python -m fund` or a console-script shim).

## Stage 0 — the one destructive step (needs explicit go)

Move current `app.py`, `core/`, `scripts/`, and the old plan docs into `legacy/`. Keep `data/` shared (the PDFs and existing DB stay usable). Git safety commit already exists, so this is reversible. Nothing else is touched until this lands and you confirm the tree looks right.

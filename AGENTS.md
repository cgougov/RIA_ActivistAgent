# Agent Instructions

Terminal-first research platform for Japan activist funds. Read README.md for the pipeline.

## Product scope

- Keep the research universe bounded to Japan activist and engagement funds.
  Treat `candidate`, `verified_activist`, and `excluded` as sourced universe
  classifications—not conclusions inferred from a folder name or performance.
- Treat `active`, `uncertain`, and `inactive` as sourced operating-status facts.
  Strict activist screening requires `verified_activist` and `active` unless a
  user deliberately asks for a broader universe.
- T-drive PDFs are the source-document store. When `FUND_SOURCE_DOC_ROOT` is
  configured, retain those PDFs externally and store only source paths, hashes,
  metadata, extracted page text, and approved evidence in the project.
- Use `fund refresh` for fast read-only source selection and `fund discover-docs`
  for SHA-256 duplicate confirmation on a chosen folder. The filename date is a
  candidate only; use the document's stated as-of date when registering it.
- Public-web analysis must search English and Japanese when policy permits. In a
  managed environment that blocks workspace-derived context from external web
  models, do not bypass the control: use dry-run and capture public sources as
  dated `evidence` PDFs for the normal source/page/quote workflow.
- Keep `.env`, the live database, database backups, T-drive PDFs, and generated
  caches out of Git. A new server receives code from Git and the live database
  through the approved secure internal channel.

## Non-negotiable rules

- Do not invent data. LLM output is proposal-only; humans approve every fact.
- Every fact keeps source document, page, and quote.
- Do not reset or delete the database unless explicitly asked.
- Dry-run (`--dry-run`) before any OpenAI call. Every LLM call goes through `fund/llm.py` and is logged.
- Do not hardcode fund/document ids outside test commands.

## Architecture rules (these prevent the sprawl that forced the 2026-07 rebuild)

- **One fact table.** All descriptive facts live in `facts`, keyed by the vocabulary in
  `fund/schema.py`. Do not add per-topic fact tables. Returns are the only time-series
  and live in `returns`.
- **One value per field per fund or per share class**, enforced by primary keys. Duplicate
  prevention happens at write time (extract.py entry gate, review.py upserts) — never as
  cleanup scripts.
- **The factsheet is assembled, not stored.** `factsheet.py` builds it from approved data;
  snapshots are local, gitignored artifacts with one current file per fund and a
  `changes` block for refreshes.
- **Two LLM touchpoints only** (extract, analyze). If it can be computed from stored data,
  compute it. New LLM features must route through `fund/llm.py` so they are logged.
- **One CLI** (`fund`, in `fund/__main__.py`). New workflows become subcommands, not new
  scripts. No Streamlit or other UI without explicit user request — terminal first.
- **Add, don't accrete.** Before adding a module/table/abstraction, check whether an
  existing one covers it. Do not recreate the removed legacy Streamlit/script tree.

## Verify changes with

```powershell
.\.venv\Scripts\python.exe -m tests.test_pipeline      # no-API pipeline tests
fund status                        # pipeline state sanity check
fund extract doc_003 --scope profile_terms --dry-run   # preflight still builds
```

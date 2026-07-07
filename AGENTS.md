# Agent Instructions

Terminal-first research platform for Japan activist funds. Read README.md for the pipeline.

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
- **One value per field per fund**, enforced by primary keys. Duplicate prevention happens
  at write time (extract.py entry gate, review.py upserts) — never as cleanup scripts.
- **The factsheet is assembled, not stored.** `factsheet.py` builds it from approved data;
  snapshots are frozen hashed JSON for analysis reproducibility.
- **Two LLM touchpoints only** (extract, analyze). If it can be computed from stored data,
  compute it. New LLM features must route through `fund/llm.py` so they are logged.
- **One CLI** (`fund`, in `fund/__main__.py`). New workflows become subcommands, not new
  scripts. No Streamlit or other UI without explicit user request — terminal first.
- **Add, don't accrete.** Before adding a module/table/abstraction, check whether an
  existing one covers it. `legacy/` is a frozen reference — never extend it, never import
  from it.

## Verify changes with

```bash
python -m tests.test_pipeline      # no-API pipeline tests
fund status                        # pipeline state sanity check
fund extract doc_003 --scope profile_terms --dry-run   # preflight still builds
```

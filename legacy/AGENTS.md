# Japan Activist Platform - Codex Instructions

This is a local Streamlit + SQLite research platform for Japan activist funds.

## Core Goal

PDF source documents -> page text/page images -> LLM proposed facts -> extracted_facts staging -> human approval -> approved_facts ledger -> fund_characteristics tree + clean analytic tables -> FundSnapshot -> analysis/comparison.

## Non-Negotiable Rules

- Do not invent data.
- Factsheets and presentations are source of truth for fund-level data.
- Public filings/releases/proposals/letters/news are source of truth for activism behavior.
- AI output is proposed only. Humans approve, revise, or reject before source-of-truth storage.
- Every fact should keep source document, page number, and quote/evidence when available.
- Do not hardcode one fund or document except in test commands.
- Scripts should operate by document_id or fund_id.
- Do not reset the database or run init_db.py unless explicitly asked.
- Before any OpenAI API call, run dry-run/preflight checks first.

## Architecture

- funds = main fund nodes.
- fund_characteristics = canonical approved characteristic tree under each fund.
- performance_returns, benchmark_returns, fund_metrics = numeric analysis tables.
- fund_writeups and fund_flags = approved qualitative source-backed notes.
- external_sources, public_campaigns, campaign_events = future public activism behavior layer.
- FundSnapshot is the LLM-readable object. Analysis should use approved snapshots, not raw pending facts.

## Commands

Use the virtual environment:

```bash
source .venv/bin/activate
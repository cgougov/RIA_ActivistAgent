# Japan Activist Funds Research Platform

This is a clean systems-level base for a local Streamlit + SQLite research platform focused on Japan activist funds.

The core workflow is:

```text
PDF source documents
→ page-level text extraction
→ LLM proposed facts
→ extracted_facts staging table
→ human approval queue
→ approved_facts ledger
→ fund_characteristics tree + calculation tables
→ FundSnapshot object
→ dashboard, comparison, and future LLM analysis
```

## Design Principles

- Do not invent data.
- Fund-level data comes from factsheets, presentations, DDQs, investor letters, and manager materials.
- Public activism behavior comes from filings, releases, proposals, letters, AGM results, credible news, and other public evidence.
- LLM output is proposed only.
- Humans approve, revise, or reject every fact before it becomes source-of-truth.
- Every approved fact keeps source document, page number, quote/evidence, and promotion status when available.
- The canonical approved memory is `fund_characteristics`, organized by the fund-characteristics taxonomy.
- Narrow calculation tables stay relational where they are useful, especially returns, metrics, benchmarks, flags, and campaigns.
- Analysis uses `FundSnapshot` objects assembled from the characteristic tree and supporting tables.

## Main Architecture

### Evidence Layer

- `source_documents`
- `document_pages`
- `citations`
- `external_sources`

### Fund-Characteristics Taxonomy

The canonical tree lives in:

- `core/taxonomy.py`
- `characteristic_groups`
- `characteristic_definitions`

The taxonomy controls:

- allowed LLM fact names
- approval queue fields
- promotion into the canonical `fund_characteristics` tree
- promotion into calculation-friendly tables when needed
- future dashboards and analysis

### Fact Workflow Layer

- `extraction_runs`
- `extracted_facts`
- `approved_facts`
- `change_history`

### Clean Research Tables

- `funds`
- `fund_characteristics`
- `fund_profile_attributes`
- `fund_people`
- `fund_terms`
- `fund_strategy`
- `performance_returns`
- `fund_metrics`
- `fund_exposures`
- `fund_writeups`
- `fund_flags`
- `benchmark_returns`
- `public_campaigns`
- `campaign_events`

The future-facing public activism layer is intentionally small: external/public sources can become campaigns and dated campaign events. Broader public behavior scoring should be produced as analysis, not stored as source-of-truth.

### Analysis Layer

- `core/fund_snapshot.py`
- `analysis_runs`
- `exports/`

`FundSnapshot` is the object layer for future LLM comparison and analysis. It collects relational data into one structured JSON-ready representation without flattening or losing source evidence.

## First Setup

From the project folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then edit `.env` and add your OpenAI API key.

Optional model settings:

```bash
OPENAI_MODEL=gpt-5
OPENAI_ANALYSIS_MODEL=gpt-5.2
OPENAI_REASONING_EFFORT=medium
```

`OPENAI_MODEL` is used for extraction. `OPENAI_ANALYSIS_MODEL` is used for the local AI analyst/chat workflow.

Create the fresh database:

```bash
python scripts/init_db.py --confirm-reset
```

For future non-destructive updates, use:

```bash
python scripts/migrate_schema.py
```

Ingest the current PDFs:

```bash
python scripts/ingest_pdfs.py --all
```

Run the system check:

```bash
python scripts/check_system.py --document-id doc_003
```

Run the smoke test:

```bash
python scripts/smoke_test.py
```

Run internal no-API tests:

```bash
python scripts/internal_tests.py
```

The internal tests use mock analysis and workbook generation. They do not call OpenAI.

Start the app:

```bash
streamlit run app.py
```

## LLM Extraction

V1 is focused on factsheets and presentations. Extraction runs are intentionally scoped so each prompt has a narrow job and does not lose context.

Scopes:

| Scope | Purpose |
|---|---|
| `profile_terms` | Fund identity, structure, providers, fees, liquidity, and terms |
| `strategy_people` | People, roles, strategy, activism style, geography, market-cap focus, exposures |
| `performance` | AUM, NAV, beta, volatility, Sharpe, and other point-in-time metrics; excludes periodic return observations |
| `writeups_flags` | Neutral one-paragraph summary, differentiating edge, and material review flags |

Normal text extraction intentionally does **not** propose periodic return facts such as `monthly_return`, `quarterly_return`, `annual_return`, or `ytd_return`. Handle fund return histories through the separate return-table image extraction workflow or CSV import workflow below, then review, approve, and promote those staged return facts into `performance_returns`.

Review philosophy: standard fund-fact extraction should produce a minimal, high-signal approval queue rather than an exhaustive inventory. The LLM should avoid duplicate or redundant facts, skip placeholder facts such as "exists" when a named field captures the same information, and extract each disclosed value once using the best page/quote evidence. Writeups should be limited to at most one `neutral_summary` and one `differentiating_edge` per document. Flags should be reserved for real diligence issues, important missing disclosures, or source inconsistencies; ordinary factsheet brevity should not generate a flag by itself.

Dry-run first. This performs preflight checks and does not call OpenAI:

```bash
python scripts/extract_facts_llm.py doc_003 --scope profile_terms --dry-run
python scripts/extract_facts_llm.py doc_003 --all-scopes --dry-run
```

Real extraction:

```bash
python scripts/extract_facts_llm.py doc_003 --scope profile_terms
python scripts/extract_facts_llm.py doc_003 --scope strategy_people
python scripts/extract_facts_llm.py doc_003 --scope performance
python scripts/extract_facts_llm.py doc_003 --scope writeups_flags
```

If reviewable facts already exist for a document and you intentionally want to rerun:

```bash
python scripts/extract_facts_llm.py doc_003 --force
```

The `writeups_flags` prompt asks for a scientific, logical, neutral writing style. It should not produce sales language. It should state what the document supports and flag only material diligence issues, important missing disclosures, or source inconsistencies.

## Document Intake

The base includes a simple intake path for new PDFs.

Command line:

```bash
python scripts/intake_pdf.py path/to/file.pdf \
  --fund-id fund_002 \
  --document-type factsheet \
  --document-title "Simplex Value Up Strategy Factsheet" \
  --document-date 2025-09-30 \
  --uploaded-by christian \
  --ingest-pages
```

Streamlit also has a `Document Intake` tab. It can:

- upload a PDF
- assign fund/document metadata
- detect duplicate files by SHA-256
- register the document
- ingest page text
- stage metadata suggestions for review

The AI can later help classify new documents, but document metadata should still be reviewable. The system should not silently reclassify source documents.

## Approval And Promotion

Use the Streamlit Approval Queue to approve, revise, or reject proposed facts.

For cleaner document-centered review, use the terminal bundle reviewer. It renders one standardized pending factsheet package per source document, including descriptive facts and structured return tables in the same review flow:

```bash
python scripts/review_document_bundle.py --document-id doc_003
python scripts/review_document_bundle.py --document-id doc_003 --show-ids
python scripts/review_document_bundle.py --document-id doc_003 --approve-section metrics --reviewer christian
python scripts/review_document_bundle.py --document-id doc_003 --approve-section returns --reviewer christian
python scripts/review_document_bundle.py --document-id doc_003 --approve-all-pending --reviewer christian
```

By default the terminal reviewer shows the pending-only standardized bundle. It also shows the current approved baseline next to pending fields when an approved value already exists for the fund. Add `--include-reviewed` if you intentionally want approved and rejected proposal rows in the same output.

## Terminal-First Workflow

The repo is now oriented around a terminal-first research workflow:

1. preserve source evidence
2. build one proposed standardized factsheet per document
3. review and approve that factsheet in terminal
4. compare funds using approved truth only
5. run analysis separately from source-of-truth storage

Reset back to an evidence-preserving baseline:

```bash
python scripts/reset_to_evidence_baseline.py
python scripts/reset_to_evidence_baseline.py --apply
```

Build a proposed factsheet package for one document:

```bash
python scripts/build_proposed_factsheet.py doc_003 --dry-run
python scripts/build_proposed_factsheet.py doc_003
```

This command runs the standard descriptive extraction scopes and the return-table extraction workflow from one entrypoint, then hands off to:

```bash
python scripts/review_document_bundle.py --document-id doc_003
```

List and filter funds in terminal:

```bash
python scripts/compare_funds_terminal.py --list
python scripts/compare_funds_terminal.py --list --filter activist
```

Compare approved fund data in terminal:

```bash
python scripts/compare_funds_terminal.py --fund "Simplex Value Up Strategy" --fund "Strategic Capital Japan-Up Investment Strategy"
```

Then promote approved facts into clean relational tables:

```bash
python scripts/promote_approved_facts.py --dry-run
python scripts/promote_approved_facts.py --user christian
```

Promoted approved facts record:

- `promotion_status`
- `promoted_to_table`
- `promoted_record_id`
- `promoted_at`

Descriptive approved facts are written into `fund_characteristics` and, where useful, narrower clean tables such as `fund_metrics`, `fund_flags`, or `fund_terms`. Structured return rows are different: they promote into `performance_returns` as time-series/table data, not into the descriptive characteristics tree.

## Performance Returns

Performance returns are first-class structured table/time-series data stored in SQLite. The primary workflow is to render factsheet pages as images, extract visible return-table rows, review the staged rows, and promote approved rows into `performance_returns`. CSV remains an optional import/export format, not the main workflow.

Returns are part of the same document review unit as the rest of the factsheet. The system stores approved returns as normalized rows in `performance_returns`, but reviewers should inspect them in reconstructed factsheet-like tables through the bundle reviewer or Streamlit return previews rather than as isolated raw rows.

Use this template:

```bash
data/seed/performance_returns_template.csv
```

Required CSV columns:

```text
period_type, period_end_date, return_value
```

Recommended columns:

```text
period_start_date, return_type, share_class, unit, page_number, quoted_text
```

Importing a CSV does **not** write directly to clean tables. It stages pending `extracted_facts` so the returns still go through human approval:

```bash
python scripts/import_performance_csv.py path/to/returns.csv --fund-id fund_002 --source-document-id doc_003 --imported-by christian
```

After approval, promote:

```bash
python scripts/promote_approved_facts.py --user christian
```

Export approved return history:

```bash
python scripts/export_performance_csv.py
python scripts/export_performance_csv.py --fund-id fund_002
```

### Visual Return Table Extraction

For factsheet return tables, the system can render a PDF page as an image and ask the LLM to extract visible table rows. Streamlit has a dedicated `Returns` tab for this workflow: select fund, source document, page number, render the page image, run extraction, then review the pending rows in the approval queue.

Render a page image:

```bash
python scripts/render_pdf_pages.py doc_003 --page 1
```

Extract a return table from the rendered page image:

```bash
python scripts/extract_return_table_vision.py doc_003 --page 1
```

Test without API usage:

```bash
python scripts/extract_return_table_vision.py doc_003 --page 1 --mock
```

Visual return extraction creates pending `extracted_facts`. It does not directly write to `performance_returns`; returns still require approval and promotion.

Approved return rows promote into `performance_returns` only when structured period data is present, including period type, period end date, return value, and source document/page. Vague text-extracted return facts are not treated as approved return history.

The mock command verifies PDF rendering, page-image storage, extraction-run creation, and pending-fact staging. It does not verify the paid model's visual table accuracy. After adding your API key, run the non-mock command on `doc_003` page 1 and review the staged return facts before approval.

## Mock Data For UI Testing

To test the Funds and Comparison tabs with populated approved data:

```bash
python scripts/load_mock_approved_data.py --confirm
```

This creates mock approved facts and promotes them into clean tables. Use this only for UI testing. To return to a clean seed state:

```bash
python scripts/init_db.py --confirm-reset
python scripts/ingest_pdfs.py --all
```

## Export A Fund Snapshot

```bash
python scripts/export_fund_snapshot.py fund_002
```

This writes an analysis-ready JSON snapshot into `exports/`.

## Export Source-Of-Truth Workbook

This exports only approved/clean source-of-truth tables, not raw LLM proposals:

```bash
python scripts/export_source_truth_workbook.py
```

The workbook is useful for Excel review, return analysis, and backup inspection.

## Conflict Detection

Run this after approving or importing facts:

```bash
python scripts/detect_conflicts.py
```

Conflicts are written to `fact_conflicts` when the same fund/field/date has multiple approved values.

## LLM Analysis

Dry-run first:

```bash
python scripts/run_fund_analysis_llm.py --fund-id fund_002 --compare-fund-id fund_003 --dry-run
```

Real analysis:

```bash
python scripts/run_fund_analysis_llm.py --fund-id fund_002 --compare-fund-id fund_003 --created-by christian
```

The analysis script consumes `FundSnapshot` data and saves outputs in `analysis_runs`. It is designed for approved, source-backed data rather than raw PDF guesses.

The Streamlit `AI Analyst` tab includes a basic live analysis box. It sends only approved `FundSnapshot` data to the LLM. The snapshot includes `characteristics_tree`, so the model reads the approved fund memory instead of re-reading raw PDFs.

The app is organized around the final workflow:

| Tab | Purpose |
|---|---|
| Home | System counts and fund-characteristics tree |
| Funds | Fund search/filter and clean fund profile sections |
| Returns | Page-image return table extraction, pending return-row review context, and approved return history |
| Comparison | Deterministic return stats, similarity matrix, readiness scores, and optional web research overlay |
| AI Analyst | Local GPT-style analyst over approved source-of-truth snapshots |
| Review | Approval queue, promotion, and conflicts |
| Documents | PDF intake and metadata registration |
| Data & Exports | Source-of-truth and analysis workbook exports |
| Admin | Raw clean tables and system inspection |

The AI Analyst can:

- answer questions using approved `FundSnapshot` data only
- use a larger reasoning model via `OPENAI_ANALYSIS_MODEL`
- save outputs to `analysis_runs`
- generate Excel workbooks from source-of-truth tables

It should not be used as the source of truth. It is an analysis layer over approved source-backed data.

## Comparison

The `Comparison` tab provides the simplest analyst baseline:

- approved return count, average return, volatility, and Sharpe-like ratio
- source-backed analysis-readiness score
- characteristic similarity matrix
- one neutral paragraph explaining the comparison
- optional web research overlay comparing manager-material claims with public evidence

The readiness score is a data-coverage score, not a fund-quality or investment score.

Command-line comparison workbook:

```bash
python scripts/export_comparison_workbook.py --fund-id fund_002 --fund-id fund_003
```

Web research comparison, using OpenAI web search:

```bash
python scripts/run_web_research_comparison.py --fund-id fund_002
```

Mock test without API/web call:

```bash
python scripts/run_web_research_comparison.py --fund-id fund_002 --mock
```

## Where To Build Next

Recommended next functional steps:

1. Process current factsheets/presentations through scoped extraction, approval, and promotion.
2. Import or visually extract return tables, then approve and promote them into `performance_returns`.
3. Add public activism extraction from web/manual sources into `external_sources`, `public_campaigns`, and `campaign_events`.
4. Improve UI polish after the core source-of-truth flow is populated.
5. Add richer LLM analysis prompts for diligence and campaign-vs-description comparison.

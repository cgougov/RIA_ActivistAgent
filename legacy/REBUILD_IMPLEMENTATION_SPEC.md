# Rebuild Implementation Spec

This document translates [FOUNDATION_REBUILD_PLAN.md](/Users/christiangougov/Desktop/japan-activist-platform-system-base-v8/FOUNDATION_REBUILD_PLAN.md) into a concrete implementation blueprint for the current repo.

It is designed to answer:

- what already exists
- what needs to change
- where the changes belong
- what success looks like
- whether terminal-first review is a better fit than the current tab model

## 1. Current State Snapshot

As of the latest audit:

- dedicated staged return rows exist in `proposed_return_rows`
- approved returns exist in `performance_returns`
- internal tests pass
- the review and fund views now share a return-table preview helper

### Live evidence snapshot

- `proposed_return_rows`: 72
- pending proposed return rows: 4
- approved proposed return rows: 68
- `performance_returns`: 68 approved rows
- `approved_facts`: 81
- pending non-return facts: 27

### Passed checks

- `.venv/bin/python scripts/internal_tests.py`
- `.venv/bin/python scripts/check_system.py --document-id doc_003`

### Current architectural problem

The repo has improved return-row storage but still lacks:

- a single document-centered review unit
- a unified approved presentation layer
- clear precedence for reported vs calculated metrics
- a standardized review flow that is cleaner than manual PDF review plus chat

## 1.1 Progress audit against the implementation phases

This is the current best evidence-based read of the repo.

| Area | Status | Evidence | What still blocks usefulness |
| --- | --- | --- | --- |
| `FundView` | Mostly in place | `core/fund_views.py` exists and is used by Fund and Comparison | It is still a thin assembler, not yet the single authoritative presentation contract for every consumer |
| `FundSnapshot` reduced to raw fetcher | In progress | `core/fund_snapshot.py` is now a relational fetch layer for approved tables | Some presentation logic still lives outside a disciplined view-model boundary |
| `DocumentBundle` | Mostly in place | `core/document_bundle.py` now builds standardized section slots plus returns | It still needs richer current-approved context for field-by-field review |
| terminal bundle review | Mostly in place | `scripts/review_document_bundle.py` now defaults to a pending-only standardized bundle and supports approve/reject/revise actions | The remaining gap is guided review ergonomics rather than core workflow coverage |
| return rows normalized | In place | `proposed_return_rows` and `performance_returns` are live and populated | Duplicate row promotion is still possible in current data |
| approved return history cleanup | Partially in place | `core/return_canonicalization.py` and `scripts/canonicalize_performance_returns.py` now exist | Live duplicate rows are still present until the cleanup utility is explicitly applied |
| shared return table preview | Partially in place | Fund tab, Returns tab, and bundle script share the same preview builder | Comparison and exports still do not consume one identical return table contract |
| reported metric precedence | Partially in place | `build_fund_view()` prefers reported metrics over calculated fallback | The DB still has few approved `fund_metrics`, so this value is not yet realized in practice |
| descriptive review cleanup | Not far enough | extraction prompts are narrower and periodic returns are excluded | Real proposed facts are still noisy and duplicative |
| terminal-first approval | Not started | no approval CLI exists | The user still has to rely on Streamlit review paths for actual decisions |

## 1.2 What the live review bundle proves right now

The current terminal bundle is useful evidence because it shows what the system does when forced to present one factsheet coherently.

What it already proves:

- the repo can assemble one document-centered object
- returns can be rendered in a factsheet-like table shape from normalized rows
- source-backed descriptive sections can be grouped in a more human way than raw tables

What it exposes as still broken:

- duplicate people facts still appear
- multiple versions of the same writeup still appear
- approved, pending, and rejected rows are mixed together in the same readout
- returns are normalized correctly, but duplicate approved rows still exist in current data
- the bundle is inspectable and partially actionable, but still lacks a more guided revise workflow

Latest evidence update:

- the bundle suppression pass reduced `doc_003` pending descriptive items from `27` to `20`
- dry-run return canonicalization reports `21` duplicate approved Simplex return groups in live history

## 2. What The System Should Optimize For

The system should optimize for:

1. source-of-truth integrity
2. low-friction review
3. easy correction and updating
4. clean structured storage
5. trustworthy comparison
6. being genuinely better than manual review plus a chat model

Anything that makes review slower or obscures source provenance is a regression, even if it is technically sophisticated.

## 3. Proposed End-State User Workflow

### Terminal-first review workflow

The primary review experience should become:

```bash
python scripts/review_document_bundle.py --document-id doc_003
```

The script now supports a first practical version and should continue evolving.

Current supported patterns:

```bash
python scripts/review_document_bundle.py --document-id doc_003
python scripts/review_document_bundle.py --document-id doc_003 --pending-only
python scripts/review_document_bundle.py --document-id doc_003 --approve-section metrics --reviewer christian
python scripts/review_document_bundle.py --document-id doc_003 --approve-section returns --reviewer christian
python scripts/review_document_bundle.py --document-id doc_003 --pending-only --show-ids
python scripts/review_document_bundle.py --document-id doc_003 --approve-fact-id fact_... --approved-value "0.52" --normalized-value "0.52" --reviewer christian
python scripts/review_document_bundle.py --document-id doc_003 --approve-return-row-id prop_return_... --share-class "USD" --return-type net --return-value "1.4" --reviewer christian
```

The fuller end-state should:

1. load one source document
2. render a standardized factsheet bundle
3. show section-by-section evidence
4. show the reconstructed returns table
5. allow approve / revise / reject by section or field
6. save approved source-of-truth rows

### Streamlit role after rebuild

Streamlit should remain useful for:

- browsing funds
- viewing approved source-of-truth
- comparison
- exports
- admin / audit inspection

Streamlit should not be the only or necessarily primary review environment.

### Revised recommendation after audit

Yes: terminal-first approval now looks easier than trying to force the whole review process through tabs.

Reason:

- the bundle object already exists
- the terminal render already works
- approval is inherently sequential and evidence-driven
- the current Streamlit split between Approval Queue and Returns tab is one of the biggest causes of friction

## 4. New Core Objects To Introduce

## 4.1 `FundView`

Purpose:

- one canonical approved presentation object for a `fund_id`

Consumers:

- Fund tab
- Comparison tab
- exports
- future analysis

Sections:

- overview
- manager
- strategy
- portfolio
- terms
- reported metrics
- calculated metrics
- returns table
- notes / flags

## 4.2 `ReviewedFundDocumentBundle`

Purpose:

- one canonical review object for a `document_id`

Consumers:

- terminal review script
- future document review UI

Sections:

- proposed overview fields
- proposed strategy fields
- proposed manager fields
- proposed terms
- proposed reported metrics
- proposed returns table
- proposed notes / flags
- evidence references

## 5. File-by-File Implementation Plan

## Phase 1: Unified approved presentation layer

### Add `core/fund_views.py`

Responsibilities:

- fetch approved data from relational tables
- normalize display-ready structures
- enforce metrics precedence
- assemble one clean approved fund view

Functions to add:

- `build_fund_view(connection, fund_id)`
- `build_overview_view(snapshot_like_data)`
- `build_manager_view(...)`
- `build_strategy_view(...)`
- `build_terms_view(...)`
- `build_reported_metrics_view(...)`
- `build_calculated_metrics_view(...)`
- `build_returns_view(...)`
- `build_notes_view(...)`

Rules:

- prefer reported Sharpe/beta/volatility if approved source values exist
- fallback to calculated values only if no reported values exist
- format summary stats with one decimal place max
- always preserve source document/page references in view objects

### Revise `core/fund_snapshot.py`

New role:

- raw approved data fetcher only

Keep:

- relational queries

Avoid:

- treating `FundSnapshot` as the final presentation object

### Revise `core/fund_brief.py`

New role:

- temporary adapter or formatter over `FundView`

Long-term:

- shrink or replace once `fund_views.py` becomes canonical

## Phase 2: Document-centered review bundle

### Add `core/document_bundle.py`

Responsibilities:

- build one standardized review bundle for a `document_id`
- combine proposed descriptive facts and proposed return rows
- group data into meaningful sections
- attach evidence and page references

Functions to add:

- `build_document_bundle(connection, document_id)`
- `bundle_overview(...)`
- `bundle_manager(...)`
- `bundle_strategy(...)`
- `bundle_terms(...)`
- `bundle_metrics(...)`
- `bundle_returns(...)`
- `bundle_notes_flags(...)`

Important:

- returns should be part of the same bundle, not a separate conceptual flow
- use `proposed_return_rows` for proposed return rows
- use `extracted_facts` for descriptive proposal rows until the descriptive workflow is rebuilt further

## Phase 3: Terminal-first review flow

### Add `scripts/review_document_bundle.py`

Responsibilities:

- render a standardized review bundle in terminal
- provide section review controls
- allow edits before approval

Suggested CLI:

```bash
python scripts/review_document_bundle.py --document-id doc_003
python scripts/review_document_bundle.py --document-id doc_003 --section returns
python scripts/review_document_bundle.py --document-id doc_003 --approve-section returns
```

Minimum first version:

- read-only render mode
- no curses/TUI complexity required
- plain structured text output is fine if it is coherent

### Add `scripts/apply_document_review.py`

Responsibilities:

- take reviewer choices and save approved rows

This can be a second script if keeping review and approval separate is cleaner.

## Phase 4: Shared return table rendering

### Keep / refine helpers in `app.py`

Current helper:

- `_build_return_table_previews(...)`

Move eventually to:

- `core/fund_views.py` or a small `core/return_views.py`

Required behavior:

- same table shape in review and approved display
- stable chronological ordering
- clear share-class grouping

## Phase 5: Reported vs calculated metrics policy

### Add policy helpers in `core/fund_views.py`

Functions:

- `preferred_metric(metric_name, reported_rows, calculated_rows)`
- `format_metric_value(value, unit=None, decimals=1)`

Rules:

- reported metric first
- calculated fallback
- label metric origin explicitly

### Revise `core/comparison.py`

Current problem:

- comparison computes return stats directly from `performance_returns`
- comparison does not yet clearly incorporate reported metrics precedence

New role:

- read from `FundView`
- use approved returns plus approved preferred metrics

## Phase 6: Fund and Comparison consumers

### Revise `app.py`

Fund tab should:

- read one `FundView`
- show returns in same shape as review
- show preferred reported metrics
- show calculated stats as secondary if used
- avoid raw-table feel at top of page

Comparison tab should:

- read multiple `FundView` objects
- show reported metrics if present
- show calculated fallback if needed
- round summary values to one decimal place
- continue to show overlapping approved return rows

### Potential UI simplification

Once terminal review exists, the separate Returns tab can be demoted or eventually removed as the primary review path.

## Phase 7: Descriptive proposal cleanup

### Revise `core/extraction.py`

Current role:

- scoped descriptive extraction

Needed changes:

- stronger duplicate suppression
- section-friendly output
- fewer vague or ontology-heavy facts

Target:

The descriptive proposal layer should already look like a draft standardized factsheet, not a bucket of extracted fragments.

### Future optional addition

Add a `proposed_profile_fields` table or similar only if `extracted_facts` becomes too awkward for descriptive document-bundle review.

Do not do this until the bundle flow is proven necessary.

## 6. Tables And Data Contracts

## Keep

- `source_documents`
- `document_pages`
- `document_page_images`
- `extracted_facts`
- `approved_facts`
- `proposed_return_rows`
- `performance_returns`
- `benchmark_returns`
- `fund_metrics`
- `fund_terms`
- `fund_strategy`
- `fund_people`
- `fund_writeups`
- `fund_flags`

## Add only if needed

- no new return blob table
- no opaque JSON source-of-truth for returns
- no second approved return abstraction

## Canonical returns rule

- proposed returns: `proposed_return_rows`
- approved returns: `performance_returns`
- fund/comparison read only approved normalized rows

## 7. Acceptance Criteria By Phase

## Phase 1 acceptance

- a `FundView` object exists
- Fund tab can read from it
- one-decimal formatting is enforced for summary stats
- reported metrics are preferred over calculated metrics

Current audit result:

- mostly met in code
- not yet proven useful on live data because approved reported metrics are sparse

## Phase 2 acceptance

- a `ReviewedFundDocumentBundle` exists for one `document_id`
- returns are included in the same review object
- each section includes source document/page evidence

Current audit result:

- met for read-only rendering
- not yet met as a clean review object because statuses are mixed and no review actions exist

## Phase 3 acceptance

- `python scripts/review_document_bundle.py --document-id doc_003` produces a readable standardized bundle
- reviewer can inspect the whole document in one flow

Current audit result:

- first line met
- second line now partially met for approval, though not yet for revision/editing

## Phase 4 acceptance

- return table shape is visually consistent between:
  - review
  - Fund tab
  - exports

Current audit result:

- review and Fund tab are aligned
- exports are not yet aligned

## Phase 5 acceptance

- Fund and Comparison show:
  - preferred reported Sharpe/beta/volatility if approved
  - calculated fallback only when reported values are absent
  - value origin labels

Current audit result:

- largely implemented in code
- still needs stronger test coverage and more live approved metric data

## Phase 6 acceptance

- separate Returns tab is no longer the only viable review path
- the user can review a standardized factsheet package without tab-hopping

Current audit result:

- not met

## 8. Test Plan

### Existing checks to keep running

- `.venv/bin/python scripts/internal_tests.py`
- `.venv/bin/python scripts/check_system.py --document-id doc_003`
- `.venv/bin/python -m py_compile app.py`

### New tests to add

#### `FundView` tests

- reported metrics win over calculated fallback
- one-decimal formatting policy is applied
- returns table structure is stable

#### `DocumentBundle` tests

- one `document_id` produces sections in consistent order
- returns are included in the same review bundle
- evidence references are preserved

#### terminal review smoke test

- `scripts/review_document_bundle.py --document-id doc_003` exits successfully

### Added during this audit

- `FundView` smoke coverage in `scripts/internal_tests.py`
- `DocumentBundle` smoke coverage in `scripts/internal_tests.py`
- terminal bundle render smoke coverage in `scripts/internal_tests.py`
- section-level bundle approval/rejection coverage in `scripts/internal_tests.py`

## 9. Suggested Build Order

1. enforce canonical review status rules inside `DocumentBundle`
2. add revise/edit actions to the terminal bundle flow
3. move Streamlit away from treating returns as a separate primary review surface
4. add duplicate suppression / canonicalization rules for promoted return rows
5. tighten descriptive extraction output so the bundle reads like a standardized factsheet draft
6. only then ingest/load more factsheets at scale

## 10. What Not To Do

- do not redesign unrelated analysis features first
- do not store approved returns only as JSON
- do not create another fragmented review surface
- do not bulk-load more factsheets before the review unit is coherent
- do not treat passing tests as proof that the workflow is actually good

## 11. Decision Recommendation

If the goal is true usefulness, the next implementation should prioritize:

- unified approved presentation
- document-bundle review
- terminal-first review script

That combination is the shortest path from “technically works” to “actually helps a human diligencing funds.”

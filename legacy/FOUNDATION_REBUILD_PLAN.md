# Foundation Rebuild Plan

This document is an evidence-based architecture and workflow plan for making the Japan activist funds research system genuinely more useful than pasting factsheets into a chat model.

It is intentionally focused on:

- source-of-truth integrity
- review efficiency
- structured storage
- easy updates and corrections
- better comparison readiness

It is not a UI polish plan.

## 1. Current Status Audit

### Verified status by rebuild phase

This section reflects the current codebase and checked commands as of `2026-07-07`, not prior intent.

| Phase | Status | What is true now | Main remaining gap |
| --- | --- | --- | --- |
| Phase 1: unified approved presentation layer | Mostly implemented | `core/fund_views.py` exists, Fund tab uses it, Comparison uses it, and approved return previews are shared. | The presentation layer is still not the only read-model, and some views still feel stitched together. |
| Phase 2: document bundle assembler | Mostly implemented | `core/document_bundle.py` now builds standardized review sections plus proposed return rows. | The standardized bundle still needs richer approved-vs-proposed comparison context for each field. |
| Phase 3: terminal review flow | Mostly implemented | `scripts/review_document_bundle.py` now defaults to a pending-only standardized bundle and supports revise/approve/reject actions, including returns, from terminal. | The next gap is guided bulk review ergonomics, not basic capability. |
| Phase 4: same return renderer everywhere | Partially implemented | Fund tab and bundle review both use the same preview helper. | Comparison/export paths do not yet use one consistent factsheet-like table view. |
| Phase 5: reported-vs-calculated metric policy | Partially implemented | `build_fund_view()` prefers approved reported metrics over calculated fallback in code. | The live DB still lacks enough approved `fund_metrics` for this to feel reliable in practice, and test coverage was thin before this audit. |
| Phase 6: descriptive proposal cleanup | Not implemented enough | Normal text extraction excludes periodic returns and is narrower than before. | Review output is still noisy and duplicative for real factsheets. |
| Phase 7: bulk-load more factsheets | Intentionally not started | Most source documents are present in the DB already. | The review contract is not clean enough to justify scaling extraction across the full library yet. |

### Verified commands

These commands were re-run successfully during this audit:

- `.venv/bin/python -m py_compile app.py core/fund_views.py core/document_bundle.py scripts/review_document_bundle.py core/fund_snapshot.py core/comparison.py`
- `.venv/bin/python scripts/internal_tests.py`
- `.venv/bin/python scripts/check_system.py --document-id doc_003`
- `.venv/bin/python scripts/review_document_bundle.py --document-id doc_003`

### Practical scorecard

- Fully landed enough to build on: normalized return-row storage, staged return review table, approved return promotion, `FundView`, document-bundle render, terminal read-only bundle script.
- Landed but still clunky: shared return preview rendering, metric precedence policy, fund/comparison read model.
- Still missing in the way that matters most to the user: one coherent review flow where a whole standardized factsheet bundle can be inspected and approved without tab-hopping.

### Practical conclusion

The return storage foundation has improved, but the overall product still behaves too much like:

`extract fragments -> review fragments -> display stitched fragments`

instead of:

`extract standardized fund document -> review coherent package -> approve source-backed fields -> compare clean approved views`

## 2. Why The System Still Feels Messy

The main issue is not just Streamlit.

The deeper issues are:

1. Too many representations of the same information.
2. Proposal, approval, and display layers still bleed into each other.
3. There is no single "review unit" that matches how a human diligences a fund.
4. Fund display still relies on several raw approved tables plus helper logic instead of one curated view model.
5. Returns are now stored better, but still conceptually reviewed separately from the standardized factsheet.

This is why the system can feel noisier than the source PDFs.

## 3. Product Principle

The system should be useful only if it does all of the following better than manual PDF review plus chat:

- save structured evidence once
- allow correction without data loss
- preserve source provenance
- keep approved source-of-truth separate from proposed extraction
- make comparison easy and trustworthy
- reduce reviewer burden instead of increasing it

## 4. Revised Architecture

The system should be rebuilt around four strict layers.

### A. Source Layer

Raw evidence only:

- `source_documents`
- `document_pages`
- `document_page_images`
- `citations`

### B. Proposal Layer

Reviewable, editable, not yet source-of-truth:

- proposed descriptive facts
- proposed return rows
- review status
- evidence and provenance

### C. Source-of-Truth Layer

Approved only:

- `approved_facts`
- `performance_returns`
- `benchmark_returns`
- approved reported metrics
- approved notes and flags

### D. Presentation Layer

Assembled views for humans:

- fund profile view
- comparison view
- export views
- review bundle views

Nothing in the presentation layer should need to guess how to stitch together conflicting storage shapes.

## 5. New Review Unit

The core missing object is:

`ReviewedFundDocumentBundle`

For each source document, the system should assemble one standardized bundle that includes:

- fund overview
- manager / people
- strategy
- portfolio / exposures
- terms
- reported metrics
- returns table
- source notes
- diligence flags
- source references

This bundle becomes the main review unit.

The reviewer should inspect the standardized factsheet package, not a random queue of isolated facts.

## 6. Returns Design

### Canonical rule

Approved returns should remain normalized one row per period in:

- `performance_returns`
- `benchmark_returns`

### Proposed rule

Proposed returns should be reviewed from:

- `proposed_return_rows`

### Important clarification

The hard part is not storing returns as rows.

The hard part is:

- reconstructing a factsheet-like table for review
- keeping review state aligned with approved rows
- preserving source evidence cleanly
- making the fund view and review view use the same table shape

### Required behavior

- the return table should render in the same shape during review and after approval
- rows should stay in stable chronological order
- share class grouping should remain clear
- approved fund pages and comparison should read only approved normalized return rows

## 7. Metrics Policy

Metrics need explicit precedence rules.

### Reported metrics

If the source document states:

- Sharpe
- beta
- volatility
- max drawdown
- correlation

then the approved reported value should be the primary value used for diligence and comparison.

### Calculated metrics

Calculated values from approved return rows should be used only as fallback or supplementary analytics.

### Display policy

Each metric should be labeled as one of:

- `reported`
- `calculated`
- `no approved value`

### Formatting policy

Display metrics and return stats with one decimal place max unless a field truly needs more precision.

## 8. Category Simplification

Reviewer-facing categories should be simplified to:

- `fund_profile`
- `manager`
- `strategy`
- `portfolio`
- `terms`
- `performance`
- `risk`
- `source_note`
- `diligence_flag`

Use specific field names for detail instead of proliferating category labels.

Examples:

- `terms.management_fee`
- `terms.redemption_terms`
- `strategy.engagement_style`
- `portfolio.number_of_positions`
- `risk.net_exposure_policy`
- `source_note.neutral_summary`
- `diligence_flag.missing_key_terms`

This can be introduced first at the review and presentation layer without immediately renaming every legacy DB category.

## 9. Terminal vs Streamlit

Terminal review now looks like the better primary workflow for approval work.

### Recommended split

- terminal-first for document review and approval
- Streamlit for browsing, audit, comparison, and exports

Why:

- review is sequential and stateful
- comparison is exploratory and visual
- the current Streamlit tabs still split one factsheet across multiple review surfaces
- the terminal bundle already proves the repo can assemble one document-centered object cleanly

This is a better fit than forcing the entire workflow through tabs.

## 10. Proposed Rebuild Sequence

### Phase 1: Build a unified presentation layer

Create a module such as:

- `core/fund_views.py`

It should produce a clean approved view model for:

- overview
- strategy
- manager
- terms
- reported metrics
- calculated metrics
- returns table
- notes / flags

Fund and Comparison should read from this, not from ad hoc table stitching.

### Phase 2: Build a document bundle assembler

Create:

- `core/document_bundle.py`

It should assemble one standardized review bundle for a `document_id`, combining:

- proposed descriptive facts
- proposed return rows
- existing approved data where relevant
- evidence references

### Phase 3: Build a terminal review flow

Add a script such as:

- `scripts/review_document_bundle.py --document-id doc_003`

The script should render a standardized factsheet bundle and allow:

- approve section
- revise field
- reject field
- approve returns table rows
- save changes cleanly

### Phase 4: Use the same return renderer everywhere

The review bundle, Fund tab, and comparison exports should use the same return table rendering rules.

### Phase 5: Enforce metrics precedence

Add a clear policy layer:

- reported metric first
- calculated fallback
- clear labels
- one decimal place max

### Phase 6: Simplify descriptive fact review

Reduce noise in the proposal layer by:

- stronger duplicate suppression
- section grouping
- bulk reject for obvious noise
- less ontology exposure to the reviewer

### Phase 7: Only then bulk-load more factsheets

Do not scale the system until the review object is cleaner than the source material.

## 11. Acceptance Criteria

The rebuild should only be considered successful if all of the following become true.

### Review

- one document can be reviewed as a standardized bundle
- returns are reviewed as a table inside the same review flow
- reviewer can correct and approve without digging through raw DB tables

### Source-of-truth

- every displayed fact retains source document, page, and evidence
- approved values are easy to revise later
- proposal and approved layers are clearly separated

### Fund page

- reads like a coherent manager profile
- uses the same return table shape as review
- shows reported metrics and calculated metrics clearly
- does not feel like a DB dump

### Comparison

- uses approved normalized rows and approved view-model fields
- clearly shows missing data
- clearly distinguishes reported vs calculated metrics
- is trustworthy enough for real diligence use

### Operational value

- reviewing the system is faster and cleaner than manual PDF review plus chat

If that last criterion is not met, the architecture still needs work.

## 12. Current Recommendation

The next highest-value move is:

1. build `core/fund_views.py`
2. build `core/document_bundle.py`
3. build `scripts/review_document_bundle.py`
4. make Fund and Comparison consume the same unified approved presentation layer
5. make returns part of the standardized review bundle, not a separate conceptual workflow

That is the path most likely to make the system genuinely useful.

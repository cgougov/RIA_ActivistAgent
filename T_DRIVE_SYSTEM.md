# T-Drive Source System

This guide explains how the platform uses the shared T-drive without copying or
changing its PDFs. It is the operating guide for keeping the Japan activist-fund
universe current.

## The simple model

```text
T-drive PDF stays on T:
        |
        v
source path + SHA-256 + metadata + extracted page text in this project
        |
        v
proposed facts -> evidence review -> approved facts / returns -> comparison
```

The T-drive is the original-document store. The project stores only the source
path, document metadata, file hash, extracted page text, and approved facts or
returns. It never modifies a T-drive PDF.

## Configuration

Set this in `.env` once:

```dotenv
FUND_SOURCE_DOC_ROOT=T:\Research\General
```

Any PDF registered from below that root is external by default: it stays on the
T-drive. Use `--copy` only when a local managed copy is deliberately wanted.

## The normal monthly workflow

### 1. Start with refresh

```powershell
fund refresh --root "T:\Research\General\20 Japan-Focused Hedge Funds"
```

This is read-only. It uses reviewed folder aliases to show only mapped funds,
counts their PDFs, and picks the newest *filename-dated factsheet candidate*.
It intentionally skips SHA-256 hashing so it remains quick.

The currently mapped folders are:

- Begonia -> `fund_006`
- Edwall -> `fund_005`
- Ichigo - same - Activist -> `fund_007`
- Misaki Capital -> `fund_004`
- Simplex - Value Up - Activist -> `fund_002`
- Strategic Capital- Japan Up -> `fund_003`
- UMJ - VPL-I - Long only -> `fund_001`

`fund refresh --all` includes every unmatched folder, but is not the normal
activist workflow.

### 2. Confirm the selected folder and hash

```powershell
fund discover-docs --root "T:\Research\General\20 Japan-Focused Hedge Funds\Begonia"
```

This is also read-only. It hashes each PDF in the selected folder and labels it:

- `[doc_xxx]`: exact SHA-256 match with a registered document.
- `[new]`: not yet registered under that exact file hash.

Run discovery on one chosen fund folder, not the full drive, when you are ready
to refresh that fund. Hashing the entire drive is intentionally slower.

### 3. Check the PDF's stated as-of date

The filename date is only a routing clue. It can be the publication date rather
than the reporting period. For example, a July 15 monthly can report June data.

Open the chosen PDF on the T-drive and use its stated as-of date when registering
it. Do not infer the final document date solely from the filename or modified
time.

### 4. Register one selected document

```powershell
fund add-doc --fund begonia --type factsheet --date 2026-06-30 `
  "T:\Research\General\20 Japan-Focused Hedge Funds\Begonia\2026 monthlies\chosen_file.pdf"
```

Because the path is under `FUND_SOURCE_DOC_ROOT`, this command:

- leaves the PDF on the T-drive;
- records its external source path and SHA-256 hash;
- extracts its page text into the project database;
- makes it the current factsheet for that fund; and
- retains the prior document for audit history.

It does not alter the source file.

For a public filing, manager webpage saved as PDF, or engagement record, use
`--type evidence` instead. Evidence is supporting material: it never becomes a
current factsheet and cannot outrank a factsheet during review.

### 5. Extract and review

```powershell
fund onboard begonia --dry-run
fund onboard begonia
fund verify
fund review begonia --list
```

The dry run is required before the model call. Extraction creates proposals;
facts and returns are not changed until reviewed and approved.

## Finding evidence after registration

```powershell
fund search "shareholder activism" --fund edwall
fund search governance --fund misaki
```

This searches locally extracted page text. It is keyword retrieval, not a model
answer: each result shows the fund, document, page, source excerpt, and T-drive
or managed source path so the original evidence can be opened immediately.

## Folder aliases and mapping

The T-drive folder name is often not the database fund name. Reviewed aliases
make this explicit rather than relying on fuzzy matching.

```powershell
fund alias --fund simplex "Simplex - Value Up - Activist"
fund alias                    # list all reviewed aliases
fund crosswalk --root "T:\Research\General\20 Japan-Focused Hedge Funds"
```

`crosswalk` is read-only. It reports `exact`, `likely`, `ambiguous`, or
`unmatched`. Review any `likely` or `ambiguous` result before adding an alias.

## What is safe to run

| Command | Changes T-drive? | Changes project database? | Calls an API? |
| --- | --- | --- | --- |
| `fund crosswalk` | No | No | No |
| `fund refresh` | No | No | No |
| `fund discover-docs` | No | No | No |
| `fund search` | No | No | No |
| `fund add-doc` | No | Yes: metadata/page text | No |
| `fund onboard --dry-run` | No | No | No |
| `fund onboard` | No | Yes: proposals/log | Yes |
| `fund review` / `fund classify` | No | Yes: approved facts | No |

## Troubleshooting

**“No fund matches”**

Run `fund alias --fund <database fund> "<exact folder name>"`, then run
`fund crosswalk` again.

**Refresh shows an old or date-unknown candidate**

Treat it as a cue to inspect the folder—not a conclusion. The source may use an
unusual filename, a month-name format, or contain no factsheet at all. Choose
the actual current PDF manually, confirm its as-of date inside the document, and
then use `fund add-doc`.

**A file is marked `[new]` but looks familiar**

It has a different SHA-256 hash from registered files. That can be a genuinely
new monthly, a corrected version, or a renamed/reissued PDF. Compare it to the
current document before registering it.

**The T-drive is unavailable**

The project can still show existing approved facts and extracted page text, but
it cannot ingest or verify a new external PDF until the drive is mounted again.

## Integrity rule

Discovery and refresh make source selection quick. They do not prove that a
fund is active, that a document is current, or that a proposed fact is true.
Those conclusions still require a dated document, page, quote, and review.

# Windows VM Migration Plan

This document is the handoff for moving the Japan activist funds platform from
the current macOS workspace into a Microsoft Windows virtual desktop.

## Current State

Completed before migration:

- Cleanup commit created: `64143bf Prepare platform for VM migration`.
- `legacy/`, `.Rhistory`, `BUILD_PLAN.md`, generated `fund.egg-info/`, tracked
  factsheet snapshots, old legacy DB/backups, and page-image cache were removed.
- `.gitignore` now excludes `.venv/`, `.env`, `data/db/`, `data/factsheets/`,
  `data/page_images/`, `exports/`, `.Rhistory`, and `fund.egg-info/`.
- PDF source documents are committed under normalized per-fund folders in
  `data/pdfs/`.
- Factsheet snapshots are local artifacts only. They can be regenerated.
- The live SQLite DB remains local at `data/db/fund.db` and is intentionally
  gitignored.
- The OpenAI key remains local in `.env` and is intentionally gitignored.

Verification already run on macOS:

```bash
.venv/bin/python -m tests.test_pipeline
.venv/bin/fund status
.venv/bin/fund extract doc_003 --scope profile_terms --dry-run --force
```

The plain dry-run without `--force` is expected to stop because existing
proposals already exist for `doc_003/profile_terms`.

## Recommended Transfer Strategy

Use GitHub for source code and committed PDFs. Do not folder-copy the project.

Transfer through GitHub:

- `fund/`
- `tests/`
- `data/pdfs/`
- docs
- packaging files such as `pyproject.toml`

Transfer separately, deliberately:

- `.env` values, especially `OPENAI_API_KEY`
- `data/db/fund.db` if the VM should start with the current approved database

Do not transfer:

- `.venv/`
- `data/page_images/`
- `data/factsheets/`
- generated `fund.egg-info/`
- old legacy databases/backups

## Mac: Publish The Clean Repo

If no remote exists yet:

```bash
git remote add origin <private-github-repo-url>
git push -u origin main
```

If a remote already exists:

```bash
git push
```

After pushing, confirm GitHub does not contain `.env`, `.venv/`, `data/db/`,
`data/factsheets/`, or `data/page_images/`.

## Windows VM: Clone And Build

PowerShell:

```powershell
git clone <private-github-repo-url>
cd <repo>
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
Copy-Item .env.example .env
```

Edit `.env` and add `OPENAI_API_KEY`.

If transferring the live DB:

```powershell
New-Item -ItemType Directory -Force data\db
# Copy fund.db into data\db\fund.db using your chosen secure transfer method.
```

## Windows VM: First Smoke Test

Run these in PowerShell from the repo root:

```powershell
.\.venv\Scripts\Activate.ps1
fund doctor
fund status
python -m tests.test_pipeline
fund list
fund factsheet simplex --sources
fund similar simplex --limit 5
fund extract doc_003 --scope profile_terms --dry-run --force
```

Expected results:

- `fund doctor` shows the Windows Python executable.
- `fund status` shows schema v2 if `data\db\fund.db` was copied.
- tests pass.
- factsheet, returns, similarity, and dry-run extraction commands work.

If `fund status` cannot find data, check whether `data\db\fund.db` was copied
or whether `FUND_DB_PATH` points somewhere unexpected.

## Windows VM: API / Web Search Test

Always dry-run before live API calls:

```powershell
fund analyze-activism --funds simplex --dry-run
```

Then run one live test:

```powershell
fund analyze-activism --funds simplex
```

This verifies:

- `.env` is loaded.
- OpenAI API access works from the VM.
- web search tools work.
- Japanese + English search prompting works.
- LLM calls are logged in `llm_calls`.

The output should be English-only. Japanese findings should be translated into
English with source URLs retained.

## Database Transfer Options

Short-term recommended path:

- Copy `data/db/fund.db` manually after GitHub clone.

Future improvement:

- Add `fund db-export` and `fund db-import` commands to create a portable,
  timestamped DB bundle.
- Optionally add `fund doctor --storage` to report whether the live DB, PDFs,
  cache, and factsheets are present and whether paths are Windows-compatible.

Do not commit `data/db/fund.db` unless you intentionally want SQLite DB blobs in
Git history.

## T-Drive Integration Backlog

Build this only after the Windows VM baseline passes smoke tests.

Goal:

- Let the platform discover and ingest new factsheets / presentations from the
  T-drive or another Microsoft-mounted shared drive.

Recommended design:

- Add storage roots through environment variables in `fund/config.py`, for
  example `FUND_SOURCE_DOC_ROOT`.
- Keep existing normalized storage under `data/pdfs/`.
- Add a CLI command such as:

```powershell
fund discover-docs --root "T:\path\to\fund docs"
fund import-docs --root "T:\path\to\fund docs" --dry-run
```

Rules:

- Never hardcode drive letters in core logic.
- Use `pathlib.Path` everywhere.
- Dry-run before copying files.
- Preserve source file path, original filename, doc type, and doc date.
- Do not call the LLM during discovery/import.
- Route extraction later through existing `fund onboard` / `fund extract`.

## Post-Migration Codex Build Tasks

Give Codex this checklist after opening the repo on the Windows VM:

1. Run the Windows smoke tests listed above and record results.
2. Confirm `fund analyze-activism --funds simplex --dry-run` builds a prompt.
3. Run one live API-backed activism check if approved.
4. Add `fund doctor --storage` to check DB, PDFs, factsheets, page cache, and
   source-doc root presence.
5. Add optional DB bundle commands:
   - `fund db-export path\to\bundle`
   - `fund db-import path\to\bundle --dry-run`
6. Design T-drive document discovery:
   - file matching
   - doc-date inference
   - doc-type inference
   - duplicate detection by SHA256
   - dry-run table before import
7. Add tests for Windows-style paths and T-drive discovery.

## Open Decisions

- How will `data/db/fund.db` be transferred to the VM?
- Will the T-drive be mounted as a drive letter, UNC path, or synced folder?
- Should the system keep one shared DB on the T-drive, or a local DB with explicit
  export/import?
- Should factsheet snapshots remain purely local, or should exports be saved to a
  shared reporting folder later?

# Intern Quick Start

Use this page for the first setup and normal monthly work. It is the short
version; follow the linked guides when a step needs more detail.

## 1. Get ready once

Ask your manager or IT for:

- access to this GitHub repository;
- read access to `T:\Research\General`;
- the current `data\db\fund.db` through the approved internal channel; and
- a local `.env` containing the approved OpenAI key.

On the server or your workstation:

```powershell
git clone https://github.com/cgougov/RIA_ActivistAgent.git
cd RIA_ActivistAgent
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
```

In `.env`, set:

```dotenv
FUND_SOURCE_DOC_ROOT=T:\Research\General\20 Japan-Focused Hedge Funds
```

Place the approved database at `data\db\fund.db`, then confirm the setup:

```powershell
fund migrate
fund doctor
fund crosswalk
fund refresh
.\.venv\Scripts\python.exe -m tests.test_pipeline
```

`fund crosswalk` should show seven `exact` mappings. If the T-drive commands
fail, check that `Test-Path "T:\Research\General\20 Japan-Focused Hedge Funds"`
returns `True`, then ask IT to map the T-drive and grant read access.

## 2. Start each research session

```powershell
fund status
fund refresh
fund inbox
```

These commands are safe and read-only. They show what is new on the T-drive
and what needs review in the database.

## 3. Process one new monthly factsheet

```powershell
fund discover-docs --root "T:\Research\General\20 Japan-Focused Hedge Funds\Begonia"
fund add-doc --fund begonia --type factsheet --date 2026-06-30 "T:\...\chosen_file.pdf"
fund onboard begonia --dry-run
fund onboard begonia
fund verify
fund review begonia --list
```

Before `add-doc`, open the PDF on the T-drive and use its stated as-of date;
the filename date is only a clue. `add-doc` stores metadata and extracted text
in the database but leaves the source PDF on the T-drive. `onboard` proposes
facts—it does not approve them. Approve only items with the correct document,
page, and quote.

## 4. Use the research tools

```powershell
fund search "shareholder activism" --fund edwall
fund factsheet simplex --sources
fund compare simplex begonia
fund similar simplex --limit 5
fund universe
```

These rely on approved, source-backed data. `fund search` returns the exact
source page and excerpt; use it before making a conclusion.

## 5. Important rules

- Never put `.env`, `fund.db`, T-drive PDFs, or generated artifacts in Git.
- Always run `--dry-run` before any command that calls a model.
- Do not label a fund active or activist without a source document, page, and
  quote.
- The project has no web server or browser application to run; use the `fund`
  commands in PowerShell.
- Live web analysis requires a company-approved environment that permits
  sending approved snapshots to the external web-analysis service. The dry-run
  works everywhere and shows the planned English/Japanese searches.

## Where to go next

- [T_DRIVE_SYSTEM.md](T_DRIVE_SYSTEM.md): monthly PDF discovery, T-drive
  troubleshooting, aliases, and source integrity.
- [TERMINAL_WORKFLOW.md](TERMINAL_WORKFLOW.md): every terminal workflow and
  review command.
- [INTERN_PROJECT_BRIEF.md](INTERN_PROJECT_BRIEF.md): project goals, current
  state, limitations, and the remaining research backlog.

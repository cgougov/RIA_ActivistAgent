"""Stage 6 of the pipeline: LLM analysis over approved truth.

This is LLM touchpoint #2 of exactly two. The model sees ONLY frozen factsheet
snapshots (approved facts + approved returns) — never raw PDFs, never pending
proposals. Each analysis records the snapshot hashes it used, so any answer can
be traced to the exact data state that produced it.
"""
import json
from uuid import uuid4

from fund.config import ANALYSIS_MODEL
from fund.db import utc_now
from fund.factsheet import snapshot_factsheet
from fund.llm import call


def build_analysis_prompt(snapshots, question):
    sheets_json = json.dumps(snapshots, indent=1, ensure_ascii=False)
    return f"""
You are a hedge-fund diligence analyst. Answer the question using ONLY the approved,
source-backed factsheet data below. Every fact carries its source document and page.

Rules:
- Do not use outside knowledge about these funds. If the data does not support an answer, say so.
- Distinguish what the sources state from what you infer, and keep inference minimal.
- Cite fields as (doc_id p.N) using the source references in the data.
- Numbers: one decimal place.
- Be concise and neutral. No sales language.

Question: {question}

Approved factsheets:
{sheets_json}
""".strip()


def run_analysis(connection, fund_ids, question, dry_run=False):
    snapshots, hashes = [], []
    for fund_id in fund_ids:
        path, digest = snapshot_factsheet(connection, fund_id)
        snapshots.append(json.loads(path.read_text()))
        hashes.append(digest)

    prompt = build_analysis_prompt(snapshots, question)
    if dry_run:
        return {"fund_ids": fund_ids, "snapshot_hashes": hashes,
                "prompt_chars": len(prompt), "dry_run": True}

    call_id, output = call(
        connection, call_type="analyze", model=ANALYSIS_MODEL, prompt_input=prompt,
        fund_id=",".join(fund_ids), json_output=False,
    )
    analysis_id = f"an_{uuid4().hex[:12]}"
    connection.execute(
        """
        INSERT INTO analyses (analysis_id, question, fund_ids, snapshot_hashes, model,
                              output_text, call_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (analysis_id, question, ",".join(fund_ids), ",".join(hashes), ANALYSIS_MODEL,
         output, call_id, utc_now()),
    )
    connection.commit()
    return {"analysis_id": analysis_id, "fund_ids": fund_ids,
            "snapshot_hashes": hashes, "output_text": output}

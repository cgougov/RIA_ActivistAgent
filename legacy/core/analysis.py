import json
import os
from uuid import uuid4

from core.config import DEFAULT_ANALYSIS_MODEL, DEFAULT_REASONING_EFFORT
from core.db import utc_now
from core.fund_snapshot import FundSnapshot


def build_chat_prompt(question, snapshots):
    return f"""
You are an internal research analyst using only approved source-of-truth fund data.

Rules:
- Use only the supplied fund snapshots.
- Treat the characteristics_tree and characteristics sections as the canonical approved memory for each fund.
- Do not invent facts or infer missing values as facts.
- Separate observations from data gaps.
- Write in a scientific, logical, neutral style.
- Prefer clear, compact sentences.
- If evidence is insufficient, say so.

User question:
{question}

Approved fund snapshots:
{json.dumps(snapshots, indent=2, ensure_ascii=False)}
""".strip()


def mock_analysis_response(question, snapshots):
    fund_count = len(snapshots)
    approved_sections = sorted({key for snapshot in snapshots.values() for key in snapshot.keys()})
    return (
        f"Mock analysis for internal testing. The question was: {question}\n\n"
        f"The request included {fund_count} fund snapshot(s). Available sections were: "
        f"{', '.join(approved_sections)}. No API call was made."
    )


def run_snapshot_chat(
    connection,
    fund_ids,
    question,
    model_name=DEFAULT_ANALYSIS_MODEL,
    reasoning_effort=DEFAULT_REASONING_EFFORT,
    created_by="system",
    mock=False,
):
    if not mock and not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not set.")

    snapshots = {fund_id: FundSnapshot(connection, fund_id).build() for fund_id in fund_ids}
    prompt = build_chat_prompt(question, snapshots)

    if mock:
        output_text = mock_analysis_response(question, snapshots)
    else:
        from openai import OpenAI

        client = OpenAI()
        response = client.responses.create(
            model=model_name,
            input=prompt,
            reasoning={"effort": reasoning_effort, "summary": "auto"},
        )
        output_text = response.output_text

    analysis_id = f"analysis_{uuid4().hex}"
    primary_fund_id = fund_ids[0] if fund_ids else None
    comparison_fund_ids = fund_ids[1:] if len(fund_ids) > 1 else []

    connection.execute(
        """
        INSERT INTO analysis_runs (
            analysis_id, analysis_type, fund_id, comparison_fund_ids, prompt_version,
            model_name, input_snapshot_json, output_text, run_status, created_by, created_at
        )
        VALUES (?, 'snapshot_chat', ?, ?, 'snapshot_chat_v1', ?, ?, ?, 'complete', ?, ?)
        """,
        (
            analysis_id,
            primary_fund_id,
            json.dumps(comparison_fund_ids),
            model_name,
            json.dumps(
                {
                    "question": question,
                    "snapshots": snapshots,
                    "reasoning_effort": reasoning_effort,
                    "mock": mock,
                },
                ensure_ascii=False,
            ),
            output_text,
            created_by,
            utc_now(),
        ),
    )
    return analysis_id, output_text


def build_web_comparison_prompt(fund_name, snapshot, question):
    return f"""
You are comparing manager-material claims against public web information.

Rules:
- Use the approved internal snapshot as the baseline.
- Treat the characteristics_tree and characteristics sections as the canonical approved internal record.
- Use web search only for public evidence.
- Separate internal source-backed facts from public web findings.
- Do not treat web claims as approved source-of-truth.
- Cite public web findings in the response.
- Use neutral, scientific, logical language.
- Focus on whether public evidence appears consistent with, silent on, or in tension with the fund's stated characteristics.

Question:
{question}

Fund name:
{fund_name}

Approved internal snapshot:
{json.dumps(snapshot, indent=2, ensure_ascii=False)}
""".strip()


def run_web_research_comparison(
    connection,
    fund_id,
    question,
    model_name=DEFAULT_ANALYSIS_MODEL,
    reasoning_effort=DEFAULT_REASONING_EFFORT,
    created_by="system",
    mock=False,
):
    fund_snapshot = FundSnapshot(connection, fund_id).build()
    fund_name = fund_snapshot["fund"]["fund_name"]
    prompt = build_web_comparison_prompt(fund_name, fund_snapshot, question)

    if mock:
        output_text = (
            "Mock web comparison for internal testing. No web search or API call was made. "
            f"The baseline fund was {fund_name}."
        )
    else:
        if not os.getenv("OPENAI_API_KEY"):
            raise ValueError("OPENAI_API_KEY is not set.")
        from openai import OpenAI

        client = OpenAI()
        response = client.responses.create(
            model=model_name,
            input=prompt,
            reasoning={"effort": reasoning_effort, "summary": "auto"},
            tools=[{"type": "web_search"}],
            tool_choice="auto",
            include=["web_search_call.action.sources"],
        )
        output_text = response.output_text

    analysis_id = f"analysis_{uuid4().hex}"
    connection.execute(
        """
        INSERT INTO analysis_runs (
            analysis_id, analysis_type, fund_id, comparison_fund_ids, prompt_version,
            model_name, input_snapshot_json, external_context_json, output_text,
            run_status, created_by, created_at
        )
        VALUES (?, 'web_research_comparison', ?, '[]', 'web_research_v1', ?, ?, ?, ?, 'complete', ?, ?)
        """,
        (
            analysis_id,
            fund_id,
            model_name,
            json.dumps({"question": question, "snapshot": fund_snapshot}, ensure_ascii=False),
            json.dumps({"web_search_enabled": not mock, "mock": mock}),
            output_text,
            created_by,
            utc_now(),
        ),
    )
    return analysis_id, output_text

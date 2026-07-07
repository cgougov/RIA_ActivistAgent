"""The single gateway to the LLM. Every call is logged to llm_calls.

Two call types exist in this system: extraction (document -> proposals) and
analysis (approved factsheets -> narrative). If a result can be computed from
stored data, it must not come through here.
"""
import json
from uuid import uuid4

from fund.config import REASONING_EFFORT, require_api_key
from fund.db import utc_now


def call(connection, *, call_type, model, prompt_input, doc_id=None, fund_id=None,
         json_output=True, reasoning_effort=REASONING_EFFORT):
    """Run one logged LLM call. prompt_input is a string or a responses-API input list.

    Returns (call_id, output_text). Raises RuntimeError on failure, after
    logging the failed call.
    """
    require_api_key()
    from openai import OpenAI

    if isinstance(prompt_input, str):
        prompt_chars = len(prompt_input)
        prompt_head = prompt_input[:400]
    else:
        texts = [
            part.get("text", "")
            for message in prompt_input
            for part in message.get("content", [])
            if isinstance(part, dict)
        ]
        joined = "\n".join(texts)
        prompt_chars = len(joined)
        prompt_head = joined[:400]

    call_id = f"call_{uuid4().hex[:12]}"
    kwargs = {"model": model, "input": prompt_input}
    if json_output:
        kwargs["text"] = {"format": {"type": "json_object"}}
    if reasoning_effort and model.startswith("gpt-5"):
        kwargs["reasoning"] = {"effort": reasoning_effort}

    try:
        client = OpenAI()
        response = client.responses.create(**kwargs)
        output_text = response.output_text
        usage = getattr(response, "usage", None)
        connection.execute(
            """
            INSERT INTO llm_calls (call_id, call_type, model, doc_id, fund_id, prompt_chars,
                                   prompt_head, raw_output, input_tokens, output_tokens,
                                   status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', ?)
            """,
            (
                call_id, call_type, model, doc_id, fund_id, prompt_chars, prompt_head,
                output_text,
                getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None),
                utc_now(),
            ),
        )
        connection.commit()
        return call_id, output_text
    except Exception as exc:
        connection.execute(
            """
            INSERT INTO llm_calls (call_id, call_type, model, doc_id, fund_id, prompt_chars,
                                   prompt_head, status, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'failed', ?, ?)
            """,
            (call_id, call_type, model, doc_id, fund_id, prompt_chars, prompt_head,
             str(exc), utc_now()),
        )
        connection.commit()
        raise RuntimeError(f"LLM call failed (logged as {call_id}): {exc}") from exc


def parse_json(call_id, output_text):
    try:
        return json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Model returned invalid JSON (raw output stored on call {call_id}): {exc}"
        ) from exc

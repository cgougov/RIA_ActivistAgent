import base64
import json
import os
from pathlib import Path
from uuid import uuid4

from core.config import DATA_DIR, DEFAULT_MODEL, PROMPT_VERSION
from core.db import utc_now
from core.documents import render_document_page_image
from core.return_rows import stage_return_rows


RETURN_TABLE_DEFINITIONS = """
Definitions:
- period_type: monthly, quarterly, annual, or ytd.
- period_start_date: first date in the return period, if the table makes it explicit.
- period_end_date: final date in the return period. This is required for every return row.
- return_value: numeric return, normally a percent. Store 1.23 for 1.23%, not 0.0123.
- return_type: net, gross, or unknown. Use net only if the table says net or the context clearly says net performance.
- share_class: share class/currency/class label if the table separates classes.
- unit: normally percent.
- quoted_text: the exact table cell/row text supporting the extracted value.
- table_name: visible table title, such as "Value Up Strategy USD with FX Hedge Class".
- row_label: visible row label, usually the year.
- column_label: visible column label, usually the month, quarter, YTD, or ITD.
""".strip()


def image_data_url(image_path):
    relative_path = Path(image_path)
    if relative_path.parts and relative_path.parts[0] == "data":
        relative_path = Path(*relative_path.parts[1:])
    path = DATA_DIR / relative_path
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def build_return_table_prompt(document, page_number):
    return f"""
Extract the performance return table from this PDF page image.

Document:
- document_id: {document['document_id']}
- fund_id: {document['fund_id']}
- title: {document['document_title']}
- document_date: {document['document_date']}
- page_number: {page_number}

{RETURN_TABLE_DEFINITIONS}

Rules:
- Do not invent rows.
- Extract only return observations visible in the image.
- Preserve row/column meaning. Treat each visible class table separately.
- Extract each monthly/quarterly/annual/YTD cell as a separate row when the date meaning is clear.
- Include the table title, visible row label, and visible column label in structured_context.
- If a date is shown as a month or quarter, normalize period_end_date to YYYY-MM-DD using the period end date.
- If the table is cumulative performance rather than periodic return, omit it unless the period meaning is clear.
- If no return table is visible, return an empty rows array.
- Return strict JSON with key rows.

Each row:
{{
  "period_type": "monthly|quarterly|annual|ytd",
  "period_start_date": "YYYY-MM-DD or null",
  "period_end_date": "YYYY-MM-DD",
  "return_value": 1.23,
  "return_type": "net|gross|unknown",
  "share_class": "text or null",
  "unit": "percent",
  "quoted_text": "source row/cell text",
  "structured_context": {{
    "table_name": "visible table title",
    "row_label": "visible row label",
    "column_label": "visible column label"
  }},
  "confidence_score": 0.0
}}
""".strip()


def save_return_rows_as_pending_facts(connection, document, page_number, rows, model_name, raw_output, image_id):
    return stage_return_rows(
        connection,
        document=document,
        page_number=page_number,
        rows=rows,
        model_name=model_name,
        raw_output=raw_output,
        image_id=image_id,
    )


def record_failed_extraction_run(connection, document, model_name, raw_output, error_message):
    run_id = f"run_{uuid4().hex}"
    now = utc_now()
    connection.execute(
        """
        INSERT INTO extraction_runs (
            run_id, document_id, extraction_scope, extraction_method, model_name,
            prompt_version, run_status, created_at, finished_at, error_message, raw_output
        )
        VALUES (?, ?, 'visual_return_table', 'llm_pdf_page_image', ?, ?, 'failed', ?, ?, ?, ?)
        """,
        (run_id, document["document_id"], model_name, PROMPT_VERSION, now, now, error_message, raw_output),
    )
    connection.commit()
    return run_id


def extract_return_table_from_page_image(connection, document_id, page_number, model_name=DEFAULT_MODEL, dpi=180, mock=False):
    document = connection.execute(
        "SELECT * FROM source_documents WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    if document is None:
        raise ValueError(f"Unknown document_id: {document_id}")
    document = dict(document)
    image = render_document_page_image(connection, document_id, page_number, dpi=dpi)

    if mock:
        rows = [
            {
                "period_type": "monthly",
                "period_start_date": None,
                "period_end_date": document.get("document_date") or "2025-09-30",
                "return_value": 1.23,
                "return_type": "unknown",
                "share_class": None,
                "unit": "percent",
                "quoted_text": "Mock return table row for internal test.",
                "confidence_score": 1.0,
            }
        ]
        raw_output = json.dumps({"rows": rows})
        return save_return_rows_as_pending_facts(connection, document, page_number, rows, "mock", raw_output, image["image_id"])

    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY is not set.")

    from openai import OpenAI

    prompt = build_return_table_prompt(document, page_number)
    client = OpenAI()
    try:
        response = client.responses.create(
            model=model_name,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": image_data_url(image["image_path"])},
                    ],
                }
            ],
            text={"format": {"type": "json_object"}},
        )
    except Exception as exc:
        run_id = record_failed_extraction_run(connection, document, model_name, None, f"OpenAI API call failed: {exc}")
        raise RuntimeError(f"OpenAI API call failed (run {run_id} recorded as failed): {exc}") from exc

    raw_output = response.output_text
    try:
        parsed = json.loads(raw_output)
    except (json.JSONDecodeError, TypeError) as exc:
        run_id = record_failed_extraction_run(connection, document, model_name, raw_output, f"Malformed JSON from model: {exc}")
        raise RuntimeError(f"Malformed JSON from model (run {run_id} recorded as failed): {exc}") from exc

    return save_return_rows_as_pending_facts(
        connection,
        document,
        page_number,
        parsed.get("rows", []),
        model_name,
        raw_output,
        image["image_id"],
    )

"""Stage 6 of the pipeline: analysis over approved truth.

There are two analysis modes:

- General analysis: approved factsheet snapshots only.
- Activism reality check: approved snapshots + web search for public activism
  events, compared back against the fund's stated style.

Analyses remain separate from the source-of-truth pipeline. They interpret
approved data; they never write facts.
"""
import json
import math
from uuid import uuid4

from fund.config import ANALYSIS_MODEL, EMBEDDING_MODEL, WEB_ANALYSIS_MODEL
from fund.db import utc_now
from fund.factsheet import annual_display_series, snapshot_factsheet
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
- When asked whether claimed activism style matches actual behavior, compare the approved
  strategy fields first (especially activism_style and strategy_description) against the
  approved presentation evidence such as value_creation_approach, example_engagements,
  and track_record_highlights. If the approved data is not enough to support a reality
  check, say that explicitly instead of filling gaps with outside knowledge.

Question: {question}

Approved factsheets:
{sheets_json}
""".strip()


def build_public_examples_prompt(context):
    context_lines = [f"Fund name: {context['fund_name']}", f"Fund id: {context['fund_id']}"]
    if context.get("manager_name"):
        context_lines.append(f"Manager: {context['manager_name']}")
    for section_entries in context.get("sections", {}).values():
        for entry in section_entries:
            context_lines.append(f"{entry['label']}: {entry['value']}")
    compact_context = "\n".join(context_lines[:14])
    return f"""
You are a hedge-fund diligence analyst. Use web search in BOTH English and Japanese
to find concrete public examples of activism, engagement, or governance intervention
involving this fund manager or strategy in Japan.

Fund context:
{compact_context}

Search guidance:
- Prioritize concrete public events: shareholder proposals, letters, stake disclosures,
  board actions, buyback pushes, MBO involvement, public engagement campaigns, or
  clearly described company-level interventions.
- Prefer primary sources where possible; use reputable secondary reporting only to
  fill context.
- Ignore generic marketing pages unless they point to a concrete event.
- Search Japanese sources directly as well as English sources. Use Japanese query
  variants for the fund name, manager name, activism, engagement, shareholder
  proposals, governance, buybacks, tender offers, and large-shareholding reports.
- Translate Japanese findings into English. Do not output raw Japanese unless it is
  part of a proper name that has no English rendering.

Output rules:
- Output plain English text only.
- Use exactly this format:

FUND: <fund name> (<fund_id>)
WEB FINDINGS:
- <date> | <English event summary; note if source was Japanese> | <source name in English or romanized form> | <url>
- <date> | <event> | <source name> | <url>

- Use at most 3 examples.
- If you cannot find concrete public examples, say:
  - unknown | No concrete public activism example found | none | none
- Do not invent dates or URLs.
""".strip()


def build_public_examples_retry_prompt(context):
    return f"""
Use web search in English and Japanese. Find up to 3 concrete public activism or
engagement examples for:
- fund: {context['fund_name']}
- manager: {context.get('manager_name') or 'unknown'}

Prefer company releases, filings, proposals, stake disclosures, or reputable reporting.
Translate Japanese findings into English and do not output raw Japanese except proper names.

Output only:
FUND: {context['fund_name']} ({context['fund_id']})
WEB FINDINGS:
- <date> | <English event summary; note if source was Japanese> | <source name> | <url>

If nothing concrete is found, output:
- unknown | No concrete public activism example found | none | none
""".strip()


def build_activism_reality_prompt(contexts, web_findings):
    sheets_json = json.dumps(contexts, indent=1, ensure_ascii=False)
    findings_text = "\n\n".join(web_findings)
    return f"""
You are a hedge-fund diligence analyst performing a reality check on activist funds.

Use the approved factsheet contexts below as the internal stated-position record.
Then use the web findings below as the observed-public-behavior record.

Comparison objective:
- First summarize the fund's stated activism style from the approved snapshots only.
- Then summarize the observed public behavior from the web findings only.
- Decide whether the public evidence appears aligned, partially aligned, unclear, or in tension with the stated style.
- If evidence is sparse, say that clearly instead of forcing a conclusion.

Output rules:
- Output plain text only.
- Keep the conclusion to one paragraph maximum.
- Use exactly this structure:

SUMMARY:
<one paragraph max>

FUND: <fund name> (<fund_id>)
STATED: <short phrase from approved materials>
OBSERVED: <short phrase from public evidence>
ALIGNMENT: aligned | partially_aligned | unclear | in_tension
CONFIDENCE: high | medium | low
EXAMPLES:
- <date> | <event> | <source name> | <url>
- <date> | <event> | <source name> | <url>

- Use at most 3 public examples per fund.
- Do not invent dates or URLs.

Approved factsheets:
{sheets_json}

Web findings:
{findings_text}
""".strip()


def activism_analysis_context(snapshot):
    wanted = {
        "primary_strategy",
        "strategy_description",
        "activism_style",
        "value_creation_approach",
        "example_engagements",
        "track_record_highlights",
        "investment_thesis",
        "competitive_edge",
        "differentiating_edge",
        "geography_focus",
        "market_cap_focus",
    }
    sections = {}
    for section_name, entries in snapshot.get("sections", {}).items():
        picked = []
        for entry in entries:
            if entry.get("field_key") not in wanted or entry.get("value") is None:
                continue
            picked.append({
                "field_key": entry["field_key"],
                "label": entry["label"],
                "value": entry["value"],
                "source": entry.get("source"),
            })
        if picked:
            sections[section_name] = picked
    return {
        "fund_id": snapshot.get("fund_id"),
        "fund_name": snapshot.get("fund_name"),
        "manager_name": snapshot.get("manager_name"),
        "sections": sections,
    }


def _load_snapshots(connection, fund_ids):
    snapshots, hashes = [], []
    for fund_id in fund_ids:
        path, digest = snapshot_factsheet(connection, fund_id)
        snapshots.append(json.loads(path.read_text()))
        hashes.append(digest)
    return snapshots, hashes


def _analysis_record(connection, *, analysis_id, question, fund_ids, hashes, model, output_text, call_id):
    connection.execute(
        """
        INSERT INTO analyses (analysis_id, question, fund_ids, snapshot_hashes, model,
                              output_text, call_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (analysis_id, question, ",".join(fund_ids), ",".join(hashes), model,
         output_text, call_id, utc_now()),
    )
    connection.commit()


def _as_dict(item):
    if item is None:
        return {}
    if isinstance(item, dict):
        return item
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        return dump()
    return dict(getattr(item, "__dict__", {}) or {})


def extract_web_sources(response):
    sources = []
    for item in getattr(response, "output", []) or []:
        payload = _as_dict(item)
        if payload.get("type") != "web_search_call":
            continue
        action = payload.get("action") or {}
        for source in action.get("sources") or []:
            src = _as_dict(source)
            url = src.get("url")
            if url and url not in sources:
                sources.append(url)
    return sources


def run_analysis(connection, fund_ids, question, dry_run=False):
    snapshots, hashes = _load_snapshots(connection, fund_ids)
    prompt = build_analysis_prompt(snapshots, question)
    if dry_run:
        return {"fund_ids": fund_ids, "snapshot_hashes": hashes,
                "prompt_chars": len(prompt), "dry_run": True}

    call_id, output = call(
        connection, call_type="analyze", model=ANALYSIS_MODEL, prompt_input=prompt,
        fund_id=",".join(fund_ids), json_output=False,
    )
    analysis_id = f"an_{uuid4().hex[:12]}"
    _analysis_record(
        connection,
        analysis_id=analysis_id,
        question=question,
        fund_ids=fund_ids,
        hashes=hashes,
        model=ANALYSIS_MODEL,
        output_text=output,
        call_id=call_id,
    )
    return {"analysis_id": analysis_id, "fund_ids": fund_ids,
            "snapshot_hashes": hashes, "output_text": output}


def run_activism_reality_analysis(connection, fund_ids, dry_run=False):
    snapshots, hashes = _load_snapshots(connection, fund_ids)
    contexts = [activism_analysis_context(snapshot) for snapshot in snapshots]
    search_prompts = [build_public_examples_prompt(context) for context in contexts]
    synthesis_prompt = build_activism_reality_prompt(
        contexts,
        [f"FUND: {context['fund_name']} ({context['fund_id']})\nWEB FINDINGS:\n- pending live web search"
         for context in contexts],
    )
    if dry_run:
        return {
            "fund_ids": fund_ids,
            "snapshot_hashes": hashes,
            "search_prompt_chars": [len(prompt) for prompt in search_prompts],
            "synthesis_prompt_chars": len(synthesis_prompt),
            "dry_run": True,
            "tools": ["web_search_preview"],
        }

    findings = []
    web_sources = []
    for fund_id, prompt in zip(fund_ids, search_prompts):
        _, output, response = call(
            connection,
            call_type="analyze:activism_web",
            model=WEB_ANALYSIS_MODEL,
            prompt_input=prompt,
            fund_id=fund_id,
            json_output=False,
            tools=[{
                "type": "web_search_preview",
                "search_context_size": "high",
                "user_location": {"type": "approximate", "country": "US"},
            }],
            include=["web_search_call.action.sources"],
            max_output_tokens=900,
            return_response=True,
        )
        if not output.strip():
            _, output, response = call(
                connection,
                call_type="analyze:activism_web",
                model="gpt-4.1",
                prompt_input=build_public_examples_retry_prompt(
                    next(context for context in contexts if context["fund_id"] == fund_id)
                ),
                fund_id=fund_id,
                json_output=False,
                tools=[{
                    "type": "web_search_preview",
                    "search_context_size": "medium",
                    "user_location": {"type": "approximate", "country": "US"},
                }],
                include=["web_search_call.action.sources"],
                max_output_tokens=700,
                return_response=True,
            )
        findings.append(output.strip())
        for url in extract_web_sources(response):
            if url not in web_sources:
                web_sources.append(url)

    synthesis_prompt = build_activism_reality_prompt(contexts, findings)
    call_id, output = call(
        connection,
        call_type="analyze:activism_reality",
        model=ANALYSIS_MODEL,
        prompt_input=synthesis_prompt,
        fund_id=",".join(fund_ids),
        json_output=False,
        max_output_tokens=1500,
    )
    analysis_id = f"an_{uuid4().hex[:12]}"
    rendered = format_activism_reality_result(output, web_sources)
    _analysis_record(
        connection,
        analysis_id=analysis_id,
        question="Automatic activism reality check: stated style vs observed public behavior",
        fund_ids=fund_ids,
        hashes=hashes,
        model=WEB_ANALYSIS_MODEL,
        output_text=rendered,
        call_id=call_id,
    )
    return {
        "analysis_id": analysis_id,
        "fund_ids": fund_ids,
        "snapshot_hashes": hashes,
        "web_sources": web_sources,
        "output_text": rendered,
    }


def format_activism_reality_result(output_text, web_sources=None):
    lines = [output_text.strip()]
    web_sources = web_sources or []
    if web_sources:
        lines.append("")
        lines.append("Web sources used:")
        for url in web_sources[:12]:
            lines.append(f"  - {url}")
    return "\n".join(lines)


def snapshot_text(snapshot):
    lines = [
        f"Fund: {snapshot.get('fund_name')}",
        f"Manager: {snapshot.get('manager_name')}",
    ]
    for section_name, entries in snapshot.get("sections", {}).items():
        values = [entry for entry in entries if entry.get("value") is not None]
        if not values:
            continue
        lines.append(section_name.upper())
        for entry in values:
            lines.append(f"{entry['label']}: {entry['value']}")
    for share_class, payload in (snapshot.get("share_classes") or {}).items():
        lines.append(f"SHARE CLASS: {share_class}")
        for entries in payload.get("sections", {}).values():
            for entry in entries:
                if entry.get("value") is not None:
                    lines.append(f"{entry['label']}: {entry['value']}")
    annual = annual_display_series(snapshot.get("returns") or [])
    if annual:
        lines.append("ANNUAL RETURNS")
        for block in annual:
            cls = block["share_class"] or "(unspecified)"
            lines.append(f"{cls} [{block['source']}]")
            for row in block["rows"]:
                lines.append(f"{row['period_end'][:4]}: {row['return_pct']}")
    for row in (snapshot.get("returns") or []):
        if row.get("period_type") == "ytd":
            lines.append(
                f"YTD {row.get('share_class') or '(unspecified)'} {row['period_end']}: {row['return_pct']}"
            )
    return "\n".join(lines)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _norm(v):
    return math.sqrt(_dot(v, v))


def cosine_similarity(a, b):
    denom = _norm(a) * _norm(b)
    if not denom:
        return None
    return round(_dot(a, b) / denom, 3)


def _load_cached_embedding(connection, snapshot_hash):
    row = connection.execute(
        """
        SELECT embedding_vector
        FROM snapshot_embeddings
        WHERE snapshot_hash = ? AND embedding_model = ?
        """,
        (snapshot_hash, EMBEDDING_MODEL),
    ).fetchone()
    if row is None:
        return None
    return json.loads(row["embedding_vector"])


def _store_cached_embedding(connection, snapshot_hash, fund_id, vector):
    connection.execute(
        """
        INSERT OR REPLACE INTO snapshot_embeddings (
            snapshot_hash, fund_id, embedding_model, embedding_vector, created_at
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (snapshot_hash, fund_id, EMBEDDING_MODEL, json.dumps(vector), utc_now()),
    )


def factsheet_embedding_similarity(connection, fund_ids, dry_run=False):
    snapshots, hashes = _load_snapshots(connection, fund_ids)
    texts = [snapshot_text(snapshot) for snapshot in snapshots]
    if dry_run:
        return {
            "fund_ids": fund_ids,
            "snapshot_hashes": hashes,
            "chars": [len(text) for text in texts],
            "dry_run": True,
        }

    from openai import OpenAI
    vectors = []
    missing = []
    missing_positions = []
    for index, snapshot_hash in enumerate(hashes):
        cached = _load_cached_embedding(connection, snapshot_hash)
        if cached is None:
            missing.append(texts[index])
            missing_positions.append(index)
            vectors.append(None)
        else:
            vectors.append(cached)
    if missing:
        client = OpenAI()
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=missing)
        for position, item in zip(missing_positions, response.data):
            vector = item.embedding
            vectors[position] = vector
            _store_cached_embedding(connection, hashes[position], fund_ids[position], vector)
        connection.commit()
    pairs = []
    for i, left in enumerate(fund_ids):
        for j in range(i + 1, len(fund_ids)):
            right = fund_ids[j]
            pairs.append({
                "left": left,
                "right": right,
                "cosine_similarity": cosine_similarity(vectors[i], vectors[j]),
            })
    return {
        "fund_ids": fund_ids,
        "snapshot_hashes": hashes,
        "model": EMBEDDING_MODEL,
        "pairs": pairs,
    }

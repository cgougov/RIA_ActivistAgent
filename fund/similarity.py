"""Deterministic qualitative fund similarity over approved facts.

The score is intentionally not a return ranking and not a terms comparison.
It is built for allocator-style grouping: stated strategy, activism style,
geography, market-cap focus, AUM scale, and reported risk profile.
"""
import math
import re

from fund.factsheet import build_factsheet
from fund.universe import classifications, is_eligible


TEXT_FIELDS = {
    "primary_strategy": 1.4,
    "strategy_description": 1.2,
    "activism_style": 1.6,
    "geography_focus": 1.2,
    "market_cap_focus": 1.2,
    "investment_thesis": 1.1,
    "value_creation_approach": 1.4,
    "example_engagements": 0.8,
    "competitive_edge": 0.9,
    "differentiating_edge": 0.9,
}

NUMERIC_FIELDS = {
    "aum": 0.9,
    "volatility": 0.5,
    "beta": 0.45,
    "sharpe_ratio": 0.35,
    "information_ratio": 0.25,
    "benchmark_correlation": 0.25,
    "max_drawdown": 0.35,
}

STOPWORDS = {
    "a", "an", "and", "are", "as", "by", "for", "from", "in", "into",
    "is", "it", "of", "on", "or", "the", "their", "to", "up", "using",
    "with", "while",
}

SYNONYMS = {
    "activism": "activist",
    "activist": "activist",
    "engagement": "engagement",
    "engage": "engagement",
    "friendly": "constructive",
    "constructive": "constructive",
    "governance": "governance",
    "small": "smallcap",
    "smallcap": "smallcap",
    "mid": "midcap",
    "midcap": "midcap",
    "japan": "japan",
    "japanese": "japan",
}


def similar_funds(connection, target_fund_id, limit=None, activist_only=False,
                  include_uncertain=False, include_candidates=False):
    fund_ids = [
        row["fund_id"] for row in connection.execute(
            "SELECT fund_id FROM funds ORDER BY fund_id"
        ).fetchall()
    ]
    if target_fund_id not in fund_ids:
        raise ValueError(f"Unknown fund_id: {target_fund_id}")
    classes = {row["fund_id"]: row for row in classifications(connection, fund_ids)}
    if activist_only and not is_eligible(
            classes[target_fund_id], include_uncertain=include_uncertain,
            include_candidates=include_candidates):
        raise ValueError("Target is not an eligible activist under the selected universe rule.")
    sheets = {fund_id: build_factsheet(connection, fund_id) for fund_id in fund_ids}
    target = _profile(sheets[target_fund_id])
    rows = []
    for fund_id in fund_ids:
        if fund_id == target_fund_id:
            continue
        if activist_only and not is_eligible(
                classes[fund_id], include_uncertain=include_uncertain,
                include_candidates=include_candidates):
            continue
        score = _similarity_score(target, _profile(sheets[fund_id]))
        rows.append({
            "fund_id": fund_id,
            "fund_name": sheets[fund_id]["fund_name"],
            "manager_name": sheets[fund_id]["manager_name"],
            "difference": _key_difference(sheets[target_fund_id], sheets[fund_id]),
            **score,
        })
    rows.sort(key=lambda row: (-row["score"], row["fund_id"]))
    if limit:
        rows = rows[:limit]
    return {
        "target": {
            "fund_id": target_fund_id,
            "fund_name": sheets[target_fund_id]["fund_name"],
            "manager_name": sheets[target_fund_id]["manager_name"],
            "context": _fund_context(sheets[target_fund_id]),
        },
        "basis": "strategy, AUM, and reported metrics; excludes terms and return history",
        "activist_only": activist_only,
        "rows": rows,
    }


def all_pairwise_similarity(connection, activist_only=False, include_uncertain=False,
                            include_candidates=False):
    fund_ids = [
        row["fund_id"] for row in connection.execute(
            "SELECT fund_id FROM funds ORDER BY fund_id"
        ).fetchall()
    ]
    classes = {row["fund_id"]: row for row in classifications(connection, fund_ids)}
    if activist_only:
        fund_ids = [fund_id for fund_id in fund_ids if is_eligible(
            classes[fund_id], include_uncertain=include_uncertain,
            include_candidates=include_candidates,
        )]
    sheets = {fund_id: build_factsheet(connection, fund_id) for fund_id in fund_ids}
    profiles = {fund_id: _profile(sheet) for fund_id, sheet in sheets.items()}
    rows = []
    for index, left in enumerate(fund_ids):
        for right in fund_ids[index + 1:]:
            score = _similarity_score(profiles[left], profiles[right])
            rows.append({
                "left": left,
                "right": right,
                "left_name": sheets[left]["fund_name"],
                "right_name": sheets[right]["fund_name"],
                **score,
            })
    rows.sort(key=lambda row: (-row["score"], row["left"], row["right"]))
    return {
        "basis": "strategy, AUM, and reported metrics; excludes terms and return history",
        "activist_only": activist_only,
        "pairs": rows,
    }


def format_similar(result):
    target = result["target"]
    lines = [
        f"Similar funds to {target['fund_name']} ({target['fund_id']})",
        "-" * (len(target["fund_name"]) + len(target["fund_id"]) + 19),
        f"Context: {target['context']}",
        f"Basis: {result['basis']}",
        "",
    ]
    if not result["rows"]:
        lines.append("No peer funds available.")
        return "\n".join(lines)
    lines.append("Peer-by-peer difference from the target:")
    for index, row in enumerate(result["rows"], start=1):
        overlap = "; ".join(row["positive_reasons"][:2]) or "limited approved overlap"
        difference = row["difference"] or (
            row["negative_reasons"][0] if row["negative_reasons"] else "not enough approved detail"
        )
        lines.append(f"  {index}. {row['fund_name']} (score {row['score']:.2f})")
        lines.append(f"     Similarity: {overlap}.")
        lines.append(f"     Difference: {difference}.")
    return "\n".join(lines)


def format_pairwise(result, limit=30):
    lines = [
        "Fund similarity ranking",
        "-----------------------",
        f"Basis: {result['basis']}",
        "",
        f"  {'score':>5}  {'pair':<23} why",
    ]
    for row in result["pairs"][:limit]:
        why = "; ".join(row["positive_reasons"][:3]) or "limited approved overlap"
        lines.append(f"  {row['score']:>5.2f}  {row['left']} vs {row['right']:<9} {why}")
    return "\n".join(lines)


def _profile(sheet):
    text = {}
    numeric = {}
    for section_entries in (sheet.get("sections") or {}).values():
        for entry in section_entries:
            key = entry.get("field_key")
            if entry.get("value") is None:
                continue
            if key in TEXT_FIELDS:
                text[key] = _tokens(entry.get("value") or "")
            if key in NUMERIC_FIELDS:
                value = entry.get("value_num")
                if value is not None:
                    numeric[key] = float(value)
    return {"text": text, "numeric": numeric}


def _field_text(sheet, field_key):
    for section in sheet.get("sections", {}).values():
        for entry in section:
            if entry.get("field_key") == field_key and entry.get("value"):
                return str(entry["value"]).strip()
    return None


def _short(value, limit=105):
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[:limit - 3].rstrip() + "..."


def _fund_context(sheet):
    style = _field_text(sheet, "activism_style") or _field_text(sheet, "primary_strategy")
    geography = _field_text(sheet, "geography_focus")
    market_cap = _field_text(sheet, "market_cap_focus")
    bits = []
    if style:
        bits.append(f"is described as {_short(style, 125)}")
    if geography:
        bits.append(f"focuses on {_short(geography, 60)}")
    if market_cap:
        bits.append(f"targets {_short(market_cap, 60)}")
    if not bits:
        return "approved strategy context is currently sparse"
    return f"{sheet['fund_name']} " + "; ".join(bits) + "."


def _key_difference(target_sheet, peer_sheet):
    for field_key, label in (
        ("activism_style", "Activism style"),
        ("primary_strategy", "Primary strategy"),
        ("value_creation_approach", "Value-creation approach"),
        ("market_cap_focus", "Market-cap focus"),
        ("geography_focus", "Geography focus"),
    ):
        left, right = _field_text(target_sheet, field_key), _field_text(peer_sheet, field_key)
        if left and right and _tokens(left) != _tokens(right):
            return f"{label}: target says '{_short(left, 80)}' versus peer '{_short(right, 80)}'"
    return None


def _tokens(value):
    tokens = []
    for token in re.findall(r"[A-Za-z0-9]+", value.lower().replace("-", "")):
        if token in STOPWORDS or len(token) < 3:
            continue
        tokens.append(SYNONYMS.get(token, token))
    return set(tokens)


def _similarity_score(left, right):
    text_score, text_weight, positives, negatives = _text_similarity(left, right)
    numeric_score, numeric_weight, numeric_pos, numeric_neg = _numeric_similarity(left, right)
    total_weight = text_weight + numeric_weight
    raw_score = (text_score + numeric_score) / total_weight if total_weight else 0.0
    # This is a qualitative peer score. Numeric-only overlap can be useful color,
    # but it should never make a sparse profile look like a close strategy peer.
    qualitative_coverage = min(1.0, text_weight / 4.0)
    if text_weight == 0 and numeric_weight:
        qualitative_coverage = 0.15
        negatives.append("sparse approved qualitative overlap")
    score = raw_score * qualitative_coverage
    return {
        "score": round(score, 3),
        "positive_reasons": positives + numeric_pos,
        "negative_reasons": negatives + numeric_neg,
        "coverage": {
            "text_weight": round(text_weight, 2),
            "numeric_weight": round(numeric_weight, 2),
        },
    }


def _text_similarity(left, right):
    score = 0.0
    weight = 0.0
    positives = []
    negatives = []
    for field, field_weight in TEXT_FIELDS.items():
        a = left["text"].get(field)
        b = right["text"].get(field)
        if not a or not b:
            continue
        weight += field_weight
        overlap = a & b
        union = a | b
        similarity = len(overlap) / len(union) if union else 0.0
        score += field_weight * similarity
        label = field.replace("_", " ")
        if similarity >= 0.4:
            positives.append(f"similar {label}")
        elif similarity <= 0.1:
            negatives.append(f"different {label}")
    return score, weight, positives, negatives


def _numeric_similarity(left, right):
    score = 0.0
    weight = 0.0
    positives = []
    negatives = []
    for field, field_weight in NUMERIC_FIELDS.items():
        a = left["numeric"].get(field)
        b = right["numeric"].get(field)
        if a is None or b is None:
            continue
        weight += field_weight
        similarity = _number_similarity(field, a, b)
        score += field_weight * similarity
        label = field.replace("_", " ")
        if similarity >= 0.75:
            positives.append(f"similar {label}")
        elif similarity <= 0.35:
            negatives.append(f"different {label}")
    return score, weight, positives, negatives


def _number_similarity(field, left, right):
    if left == right:
        return 1.0
    if field == "aum":
        if left <= 0 or right <= 0:
            return 0.0
        distance = abs(math.log10(left) - math.log10(right))
        return max(0.0, 1.0 - distance / 2.0)
    scale = max(abs(left), abs(right), 1.0)
    return max(0.0, 1.0 - abs(left - right) / scale)

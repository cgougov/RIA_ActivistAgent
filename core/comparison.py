import math
import statistics
from collections import defaultdict

from core.db import many, one
from core.fund_views import build_fund_view


def _safe_float(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _round_one(value):
    numeric = _safe_float(value)
    if numeric is None:
        return None
    return round(numeric, 1)


def _tokenize(values):
    tokens = set()
    for value in values:
        if value is None:
            continue
        for token in str(value).lower().replace("/", " ").replace("-", " ").split():
            cleaned = "".join(char for char in token if char.isalnum())
            if len(cleaned) >= 3:
                tokens.add(cleaned)
    return tokens


def fund_characteristic_tokens(connection, fund_id):
    values = []
    fund = one(connection, "SELECT * FROM funds WHERE fund_id = ?", (fund_id,))
    if fund:
        values.extend(
            [
                fund.get("fund_name"),
                fund.get("manager_name"),
                fund.get("base_currency"),
                fund.get("primary_strategy"),
            ]
        )

    characteristic_rows = many(
        connection,
        """
        SELECT group_id, fact_category, fact_name, display_name, value_text, unit
        FROM fund_characteristics
        WHERE fund_id = ?
        """,
        (fund_id,),
    )
    if characteristic_rows:
        for row in characteristic_rows:
            values.extend(
                [
                    row.get("group_id"),
                    row.get("fact_category"),
                    row.get("fact_name"),
                    row.get("display_name"),
                    row.get("value_text"),
                    row.get("unit"),
                ]
            )
        return _tokenize(values)

    for table, columns in [
        ("fund_profile_attributes", ["attribute_name", "attribute_value"]),
        ("fund_terms", ["share_class", "hurdle", "lockup", "redemption_frequency", "notice_period", "gate", "minimum_investment"]),
        ("fund_strategy", ["strategy_primary", "strategy_secondary", "activism_style", "geography_focus", "market_cap_focus", "exposure_notes"]),
        ("fund_metrics", ["metric_name", "raw_value", "unit"]),
        ("fund_exposures", ["exposure_name", "raw_value", "unit"]),
        ("fund_flags", ["flag_type", "flag_text", "severity"]),
    ]:
        rows = many(connection, f"SELECT * FROM {table} WHERE fund_id = ?", (fund_id,))
        for row in rows:
            values.extend(row.get(column) for column in columns)

    return _tokenize(values)


def similarity_matrix(connection, fund_ids):
    tokens = {fund_id: fund_characteristic_tokens(connection, fund_id) for fund_id in fund_ids}
    matrix = []
    for left in fund_ids:
        row = {"fund_id": left}
        for right in fund_ids:
            if left == right:
                row[right] = 100.0
                continue
            union = tokens[left] | tokens[right]
            if not union:
                row[right] = None
            else:
                row[right] = round(100 * len(tokens[left] & tokens[right]) / len(union), 1)
        matrix.append(row)
    return matrix


def return_statistics(connection, fund_id):
    rows = many(
        connection,
        """
        SELECT period_type, period_end_date, return_value, return_type, share_class
        FROM performance_returns
        WHERE fund_id = ?
        ORDER BY period_end_date
        """,
        (fund_id,),
    )
    values = [_safe_float(row["return_value"]) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return {
            "fund_id": fund_id,
            "return_count": 0,
            "average_return": None,
            "volatility": None,
            "sharpe_like": None,
            "best_return": None,
            "worst_return": None,
        }

    average = statistics.mean(values)
    volatility = statistics.stdev(values) if len(values) >= 2 else None
    sharpe_like = average / volatility if volatility and volatility != 0 else None
    return {
        "fund_id": fund_id,
        "return_count": len(values),
        "average_return": round(average, 4),
        "volatility": round(volatility, 4) if volatility is not None else None,
        "sharpe_like": round(sharpe_like, 4) if sharpe_like is not None else None,
        "best_return": round(max(values), 4),
        "worst_return": round(min(values), 4),
    }


def aligned_return_comparison(connection, fund_ids):
    if not fund_ids:
        return []
    rows = many(
        connection,
        f"""
        SELECT fund_id, share_class, period_type, period_start_date, period_end_date,
               return_type, return_value, unit, source_document_id, page_number
        FROM performance_returns
        WHERE fund_id IN ({', '.join('?' for _ in fund_ids)})
        ORDER BY period_end_date, period_type, return_type, share_class, fund_id
        """,
        fund_ids,
    )
    by_key = defaultdict(dict)
    meta_by_key = {}
    for row in rows:
        value = _safe_float(row["return_value"])
        if value is None:
            continue
        key = (
            row.get("share_class"),
            row.get("period_type"),
            row.get("period_start_date"),
            row.get("period_end_date"),
            row.get("return_type") or "unknown",
            row.get("unit") or "percent",
        )
        by_key[key].setdefault(row["fund_id"], value)
        meta_by_key[key] = {
            "share_class": row.get("share_class"),
            "period_type": row.get("period_type"),
            "period_start_date": row.get("period_start_date"),
            "period_end_date": row.get("period_end_date"),
            "return_type": row.get("return_type") or "unknown",
            "unit": row.get("unit") or "percent",
        }

    aligned = []
    base_fund_id = fund_ids[0]
    for key, values_by_fund in by_key.items():
        if len(values_by_fund) < 2:
            continue
        row = dict(meta_by_key[key])
        for fund_id in fund_ids:
            row[fund_id] = values_by_fund.get(fund_id)
        base_value = values_by_fund.get(base_fund_id)
        for fund_id in fund_ids[1:]:
            comparison_value = values_by_fund.get(fund_id)
            diff_key = f"{fund_id}_minus_{base_fund_id}"
            if base_value is None or comparison_value is None:
                row[diff_key] = None
            else:
                row[diff_key] = round(comparison_value - base_value, 4)
        aligned.append(row)

    aligned.sort(key=lambda row: (row["period_end_date"] or "", row.get("share_class") or "", row["period_type"] or ""))
    return aligned


def latest_metrics(connection, fund_id):
    rows = many(
        connection,
        """
        SELECT metric_name, metric_value, raw_value, normalized_value, unit, as_of_date
        FROM fund_metrics
        WHERE fund_id = ?
        ORDER BY metric_name, as_of_date DESC, created_at DESC
        """,
        (fund_id,),
    )
    latest = {}
    for row in rows:
        latest.setdefault(row["metric_name"], row)
    return latest


def data_counts(connection, fund_id):
    counts = {}
    for table in [
        "fund_characteristics",
        "fund_profile_attributes",
        "fund_people",
        "fund_terms",
        "fund_strategy",
        "performance_returns",
        "fund_metrics",
        "fund_exposures",
        "fund_writeups",
        "fund_flags",
        "approved_facts",
    ]:
        counts[table] = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE fund_id = ?",
            (fund_id,),
        ).fetchone()[0]
    return counts


def readiness_score(counts):
    # This is a data-completeness/readiness score, not a quality or investment score.
    component_weights = {
        "fund_characteristics": 25,
        "fund_profile_attributes": 5,
        "fund_terms": 12,
        "fund_strategy": 12,
        "performance_returns": 20,
        "fund_metrics": 12,
        "fund_writeups": 7,
        "approved_facts": 7,
    }
    score = 0
    for table, weight in component_weights.items():
        if table == "fund_characteristics":
            target = 15
        elif table == "performance_returns":
            target = 12
        else:
            target = 3
        score += weight * min(counts.get(table, 0) / target, 1)
    score -= min(counts.get("fund_flags", 0) * 2, 10)
    return round(max(0, min(100, score)), 1)


def compare_funds(connection, fund_ids):
    views = {fund_id: build_fund_view(connection, fund_id) for fund_id in fund_ids}
    profiles = many(
        connection,
        f"""
        SELECT fund_id, fund_name, manager_name, status, base_currency, inception_date, primary_strategy
        FROM funds
        WHERE fund_id IN ({', '.join('?' for _ in fund_ids)})
        ORDER BY fund_name
        """,
        fund_ids,
    )
    stats = []
    for fund_id in fund_ids:
        view = views[fund_id]
        preferred = view["preferred_metrics"]
        calculated = view["calculated_return_statistics"]
        stats.append(
            {
                "fund_id": fund_id,
                "return_count": calculated["return_count"],
                "average_return": _round_one(calculated["average_return"]),
                "volatility": _round_one(preferred["volatility"]["numeric_value"]) if preferred["volatility"] else None,
                "volatility_source": preferred["volatility"]["source"] if preferred["volatility"] else "missing",
                "sharpe": _round_one(preferred["sharpe_ratio"]["numeric_value"]) if preferred["sharpe_ratio"] else None,
                "sharpe_source": preferred["sharpe_ratio"]["source"] if preferred["sharpe_ratio"] else "missing",
                "beta": _round_one(preferred["beta"]["numeric_value"]) if preferred["beta"] else None,
                "beta_source": preferred["beta"]["source"] if preferred["beta"] else "missing",
                "best_return": _round_one(calculated["best_return"]),
                "worst_return": _round_one(calculated["worst_return"]),
            }
        )
    aligned_returns = aligned_return_comparison(connection, fund_ids)
    metrics = {fund_id: views[fund_id]["preferred_metrics"] for fund_id in fund_ids}
    counts = {fund_id: data_counts(connection, fund_id) for fund_id in fund_ids}
    scores = [
        {
            "fund_id": fund_id,
            "analysis_readiness_score": readiness_score(counts[fund_id]),
            **counts[fund_id],
        }
        for fund_id in fund_ids
    ]
    similarities = similarity_matrix(connection, fund_ids)
    paragraph = comparison_paragraph(profiles, stats, scores, aligned_returns)
    return {
        "profiles": profiles,
        "return_statistics": stats,
        "aligned_returns": aligned_returns,
        "latest_metrics": metrics,
        "fund_views": views,
        "analysis_readiness_scores": scores,
        "similarity_matrix": similarities,
        "comparison_paragraph": paragraph,
    }


def comparison_paragraph(profiles, stats, scores, aligned_returns=None):
    if not profiles:
        return "No funds were selected for comparison."
    if len(profiles) == 1:
        return "One fund is selected. Add at least one additional fund for a comparative view."

    score_map = {row["fund_id"]: row["analysis_readiness_score"] for row in scores}
    stat_map = {row["fund_id"]: row for row in stats}
    names = {row["fund_id"]: row["fund_name"] for row in profiles}
    best_ready = max(scores, key=lambda row: row["analysis_readiness_score"])
    return_counts = {fund_id: stat_map[fund_id]["return_count"] for fund_id in names}
    most_returns = max(return_counts, key=return_counts.get)

    if all(count == 0 for count in return_counts.values()):
        return (
            f"The selected funds can be compared on approved characteristics, but approved return history is not yet available. "
            f"{names[best_ready['fund_id']]} currently has the highest analysis-readiness score at "
            f"{score_map[best_ready['fund_id']]} based on the amount of approved source-backed data stored. "
            "This score measures data coverage, not fund quality."
        )

    overlap_count = len(aligned_returns or [])
    if overlap_count:
        return (
            f"Approved structured return data is available for the selected funds, with {overlap_count} overlapping "
            f"period(s) suitable for aligned comparison. {names[most_returns]} has the largest approved return sample "
            f"with {return_counts[most_returns]} return observation(s). Return averages, volatility, and differences "
            "come only from approved structured return rows, while reported beta/Sharpe/volatility metrics are preferred when present. "
            "The readiness score remains a data-coverage measure, not an investment recommendation."
        )

    return (
        f"The selected funds differ primarily in available approved data coverage and return history. "
        f"{names[best_ready['fund_id']]} has the highest analysis-readiness score at {score_map[best_ready['fund_id']]}, "
        f"while {names[most_returns]} has the largest approved return sample with {return_counts[most_returns]} return observation(s). "
        "Approved return data exists, but no overlapping periods were found for direct aligned comparison. "
        "Return averages use approved structured rows, while reported risk metrics are preferred over calculated fallback values when available. "
        "The score is a data-readiness measure, not an investment recommendation."
    )

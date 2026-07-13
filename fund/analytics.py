"""Return analytics over approved normalized return rows.

This module is intentionally deterministic and LLM-free. Reported metrics such
as Sharpe, volatility, beta, and AUM remain sourced facts. Everything here is
computed internally from approved return rows and must be labeled that way by
callers.
"""
import math
import statistics

from fund.config import PERIODS_PER_YEAR, RISK_FREE_RATE

RETURN_TYPE_QUALITY = {"net": 2, "gross": 1, "unknown": 0}


def assumptions():
    return {
        "risk_free_rate": RISK_FREE_RATE,
        "periods_per_year": PERIODS_PER_YEAR,
    }


def round1(value):
    if value is None:
        return None
    return round(value, 1)


def compound(percent_values):
    total = 1.0
    for value in percent_values:
        total *= 1 + value / 100
    return total


def cumulative_return(percent_values):
    if not percent_values:
        return None
    return round1((compound(percent_values) - 1) * 100)


def month_key(date_text):
    if not date_text:
        return None
    return str(date_text)[:7]


def _period_sort_key(row):
    return row.get("period_end") or row.get("period") or ""


def group_by_class(rows):
    return [
        (share_class, [row for row in rows if (row.get("share_class") or "") == share_class])
        for share_class in sorted({row.get("share_class") or "" for row in rows})
    ]


def dominant_return_type(rows):
    if not rows:
        return None, False
    counts = {}
    for row in rows:
        rtype = row.get("return_type") or "unknown"
        counts[rtype] = counts.get(rtype, 0) + 1
    dominant = max(counts, key=lambda key: (counts[key], RETURN_TYPE_QUALITY.get(key, 0)))
    return dominant, len(counts) > 1


def dominant_return_rows(rows):
    dominant, mixed = dominant_return_type(rows)
    if dominant is None:
        return [], None, mixed
    return [
        row for row in rows
        if (row.get("return_type") or "unknown") == dominant
    ], dominant, mixed


def annual_from_monthly(monthly_rows):
    """Calendar-year returns compounded from complete monthly histories."""
    by_year = {}
    for row in monthly_rows:
        by_year.setdefault(str(row["period_end"])[:4], []).append(row)
    rows = []
    for year, year_rows in sorted(by_year.items()):
        months = {month_key(row["period_end"]) for row in year_rows}
        if len(months) != 12:
            continue
        values = [row["return_pct"] for row in sorted(year_rows, key=_period_sort_key)]
        rows.append({
            "period_end": f"{year}-12-31",
            "return_pct": cumulative_return(values),
            "return_type": "computed",
        })
    return rows


def annual_display_series(returns):
    """Representative annual display series by share class.

    Preference order:
    1. explicit annual rows
    2. year-end YTD rows, treated as full-year values
    3. computed from complete monthly history
    """
    series = []
    for share_class, rows in group_by_class(returns):
        annual = sorted(
            (row for row in rows if row["period_type"] == "annual"),
            key=_period_sort_key,
        )
        if annual:
            series.append({"share_class": share_class, "rows": annual, "source": "reported annual"})
            continue
        year_end_ytd = sorted(
            (
                row for row in rows
                if row["period_type"] == "ytd" and str(row["period_end"]).endswith("-12-31")
            ),
            key=_period_sort_key,
        )
        if year_end_ytd:
            series.append({
                "share_class": share_class,
                "rows": [
                    {
                        "period_end": row["period_end"],
                        "return_pct": row["return_pct"],
                        "return_type": row["return_type"],
                    }
                    for row in year_end_ytd
                ],
                "source": "year-end YTD",
            })
            continue
        computed = annual_from_monthly([row for row in rows if row["period_type"] == "monthly"])
        if computed:
            series.append({"share_class": share_class, "rows": computed, "source": "computed from monthly"})
    return series


def representative_annual_series(returns):
    """Pick one annual-like series for a fund.

    Preference order matches annual_display_series: reported annual, year-end
    YTD, then complete calendar years computed from monthly rows. When several
    share classes exist, use the longest available series.
    """
    display = annual_display_series(returns)
    if not display:
        return {"share_class": None, "source": None, "rows": [], "by_year": {}}
    best = max(display, key=lambda block: len(block["rows"]))
    rows = sorted(best["rows"], key=_period_sort_key)
    return {
        "share_class": best["share_class"] or "(unspecified class)",
        "source": best["source"],
        "rows": rows,
        "by_year": {str(row["period_end"])[:4]: row["return_pct"] for row in rows},
    }


def annual_series_by_fund(sheets, fund_ids):
    by_fund, class_used, source = {}, {}, {}
    for fund_id in fund_ids:
        series = representative_annual_series(sheets[fund_id]["returns"])
        by_fund[fund_id] = series["by_year"]
        class_used[fund_id] = series["share_class"]
        source[fund_id] = series["source"]
    return by_fund, class_used, source


def annual_stats(values):
    if not values:
        return {"count": 0}
    stats = {
        "count": len(values),
        "average": round1(statistics.mean(values)),
        "best": round1(max(values)),
        "worst": round1(min(values)),
        "cumulative": cumulative_return(values),
    }
    stats["volatility"] = round1(statistics.stdev(values)) if len(values) > 1 else None
    return stats


def annual_stats_from_rows(rows):
    return annual_stats([row["return_pct"] for row in rows])


def calendar_year_comparison(sheets, fund_ids):
    """Calendar-year return comparison using the representative annual series."""
    by_fund, class_used, source = annual_series_by_fund(sheets, fund_ids)
    years = sorted({year for series in by_fund.values() for year in series})
    rows = [{"year": year, **{fid: by_fund[fid].get(year) for fid in fund_ids}}
            for year in years]
    own_stats = {fid: annual_stats([by_fund[fid][y] for y in sorted(by_fund[fid])])
                 for fid in fund_ids}
    common_years = [year for year in years
                    if all(by_fund[fid].get(year) is not None for fid in fund_ids)]
    common_stats = {fid: annual_stats([by_fund[fid][y] for y in common_years])
                    for fid in fund_ids} if common_years else {}
    return {"years": years, "rows": rows, "class_used": class_used, "source": source,
            "own_stats": own_stats, "common_years": common_years,
            "common_stats": common_stats}


def max_drawdown(rows):
    ordered = sorted(rows, key=_period_sort_key)
    if not ordered:
        return None
    equity = 1.0
    peak = 1.0
    peak_date = ordered[0]["period_end"]
    max_dd = 0.0
    trough_date = None
    dd_peak_date = None
    recovery_date = None
    current_underwater = False
    for row in ordered:
        equity *= 1 + row["return_pct"] / 100
        if equity >= peak:
            if max_dd and recovery_date is None and current_underwater:
                recovery_date = row["period_end"]
            peak = equity
            peak_date = row["period_end"]
            current_underwater = False
        drawdown = (equity / peak - 1) * 100
        if drawdown < 0:
            current_underwater = True
        if drawdown < max_dd:
            max_dd = drawdown
            trough_date = row["period_end"]
            dd_peak_date = peak_date
            recovery_date = None
    if max_dd == 0:
        return {
            "max_drawdown": 0.0,
            "peak_date": None,
            "trough_date": None,
            "duration_periods": 0,
            "recovery_date": None,
            "current_underwater": False,
        }
    duration = 0
    in_window = False
    for row in ordered:
        if row["period_end"] == dd_peak_date:
            in_window = True
        if in_window:
            duration += 1
        if row["period_end"] == trough_date:
            break
    return {
        "max_drawdown": round1(abs(max_dd)),
        "peak_date": dd_peak_date,
        "trough_date": trough_date,
        "duration_periods": duration,
        "recovery_date": recovery_date,
        "current_underwater": current_underwater,
    }


def rolling_returns(rows, window=12):
    ordered = sorted(rows, key=_period_sort_key)
    if len(ordered) < window:
        return []
    output = []
    for index in range(window - 1, len(ordered)):
        window_rows = ordered[index - window + 1:index + 1]
        output.append({
            "period_end": ordered[index]["period_end"],
            "return_pct": cumulative_return([row["return_pct"] for row in window_rows]),
        })
    return output


def return_statistics_from_values(values, period_type="monthly", risk_free_rate=RISK_FREE_RATE):
    """Computed internal stats. Risk-free rate is annual percent points."""
    count = len(values)
    stats = {
        "period_type": period_type,
        "count": count,
        "risk_free_rate": risk_free_rate,
        "periods_per_year": PERIODS_PER_YEAR,
    }
    if not values:
        return stats
    stats.update({
        "average_period_return": round1(statistics.mean(values)),
        "average": round1(statistics.mean(values)),
        "cumulative": cumulative_return(values),
        "best": round1(max(values)),
        "worst": round1(min(values)),
        "positive_periods_pct": round1(100 * sum(1 for value in values if value > 0) / count),
    })
    if count < 2:
        return stats
    period_vol = statistics.stdev(values)
    annualized_vol = period_vol * math.sqrt(PERIODS_PER_YEAR)
    annualized_excess = statistics.mean(values) * PERIODS_PER_YEAR - risk_free_rate
    downside = [
        value - risk_free_rate / PERIODS_PER_YEAR
        for value in values
        if value < risk_free_rate / PERIODS_PER_YEAR
    ]
    downside_dev = statistics.stdev(downside) * math.sqrt(PERIODS_PER_YEAR) if len(downside) > 1 else None
    stats.update({
        "volatility": round1(period_vol),
        "annualized_volatility": round1(annualized_vol),
        "annualized_sharpe": round1(annualized_excess / annualized_vol) if annualized_vol else None,
        "sortino": round1(annualized_excess / downside_dev) if downside_dev else None,
    })
    return stats


def return_statistics(returns, period_type="monthly", share_class=None):
    rows = [
        row for row in returns
        if row["period_type"] == period_type
        and (share_class is None or (row.get("share_class") or "") == share_class)
    ]
    rows, dominant, mixed = dominant_return_rows(rows)
    stats = return_statistics_from_values([row["return_pct"] for row in rows], period_type=period_type)
    stats["return_type_used"] = dominant
    stats["mixed_return_types"] = mixed
    return stats


def common_period_statistics(overlap, fund_ids, period_type="monthly"):
    """Stats over an already-computed common-period intersection."""
    if len(overlap) < 2:
        return {"count": len(overlap), "period_start": None, "period_end": None, "by_fund": {}}
    by_fund = {}
    for fund_id in fund_ids:
        values = [row[fund_id] for row in overlap]
        by_fund[fund_id] = return_statistics_from_values(values, period_type=period_type)
    return {"count": len(overlap), "period_start": overlap[0]["period"],
            "period_end": overlap[-1]["period"], "by_fund": by_fund}


def monthly_analytics(returns, share_class=None):
    rows = [
        row for row in returns
        if row["period_type"] == "monthly"
        and (share_class is None or (row.get("share_class") or "") == share_class)
    ]
    rows, dominant, mixed = dominant_return_rows(rows)
    rows = sorted(rows, key=_period_sort_key)
    stats = return_statistics_from_values([row["return_pct"] for row in rows], period_type="monthly")
    stats.update({
        "share_class": share_class or "",
        "return_type_used": dominant,
        "mixed_return_types": mixed,
        "period_start": rows[0]["period_end"] if rows else None,
        "period_end": rows[-1]["period_end"] if rows else None,
        "max_drawdown": max_drawdown(rows),
        "rolling_12_month": rolling_returns(rows, window=12),
    })
    return stats


def best_monthly_series_by_class(returns):
    analytics = []
    for share_class, rows in group_by_class(returns):
        monthly = [row for row in rows if row["period_type"] == "monthly"]
        if monthly:
            analytics.append(monthly_analytics(returns, share_class=share_class))
    return sorted(analytics, key=lambda item: item.get("count", 0), reverse=True)

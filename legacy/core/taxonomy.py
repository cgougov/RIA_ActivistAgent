"""Canonical fund-characteristics tree.

This file is intentionally boring and explicit. Extraction, approval, promotion,
clean tables, dashboards, and later LLM analysis all depend on these definitions.
"""

CHARACTERISTIC_GROUPS = [
    ("fund_identity", None, "Fund Identity", "Stable identity and profile fields."),
    ("fund_people", None, "People", "People running, advising, or representing the fund."),
    ("fund_terms", None, "Terms", "Fees, liquidity, and investor terms."),
    ("fund_strategy", None, "Strategy And Exposure", "Strategy labels and portfolio/exposure descriptors."),
    ("fund_performance", None, "Performance", "Returns and point-in-time performance metrics."),
    ("public_activism", None, "Public Activism", "Public behavior, campaigns, events, and scores."),
    ("evidence", None, "Evidence", "Documents, pages, citations, and provenance."),
    ("workflow", None, "Workflow", "Extraction, approval, promotion, and analysis runs."),
    ("fund_writeups", None, "Neutral Writeups", "Short source-grounded neutral summaries for analysis."),
    ("fund_flags", None, "Flags", "Review flags or suspicious items found in manager materials."),
    ("periodic_returns", "fund_performance", "Periodic Returns", "Monthly, quarterly, annual, or YTD observations."),
    ("point_in_time_metrics", "fund_performance", "Point-In-Time Metrics", "AUM, NAV, risk, and ratio observations."),
]


CHARACTERISTIC_DEFINITIONS = [
    # Identity / profile
    ("fund_identity", "profile", "fund_name", "Fund name", "text", None, "funds", "fund_name", 1, "Fund-level materials"),
    ("fund_identity", "profile", "manager_name", "Manager name", "text", None, "funds", "manager_name", 1, "Fund-level materials"),
    ("fund_identity", "profile", "status", "Status", "text", None, "funds", "status", 1, "Reviewer"),
    ("fund_identity", "profile", "base_currency", "Base currency", "text", "currency", "funds", "base_currency", 1, "Fund-level materials"),
    ("fund_identity", "profile", "inception_date", "Inception date", "date", "date", "funds", "inception_date", 1, "Fund-level materials"),
    ("fund_identity", "profile", "primary_strategy", "Primary strategy", "text", None, "funds", "primary_strategy", 1, "Fund-level materials"),
    ("fund_identity", "profile", "domicile", "Domicile", "text", None, "fund_profile_attributes", "attribute_value", 1, "Fund-level materials"),
    ("fund_identity", "profile", "legal_structure", "Legal structure", "text", None, "fund_profile_attributes", "attribute_value", 1, "Fund-level materials"),
    ("fund_identity", "profile", "administrator", "Administrator", "text", None, "fund_profile_attributes", "attribute_value", 1, "Fund-level materials"),
    ("fund_identity", "profile", "auditor", "Auditor", "text", None, "fund_profile_attributes", "attribute_value", 1, "Fund-level materials"),
    ("fund_identity", "profile", "custodian", "Custodian", "text", None, "fund_profile_attributes", "attribute_value", 1, "Fund-level materials"),

    # People
    ("fund_people", "people", "person_name", "Person name", "text", None, "fund_people", "person_name", 1, "Fund-level materials"),
    ("fund_people", "people", "role_title", "Role title", "text", None, "fund_people", "role_title", 1, "Fund-level materials"),
    ("fund_people", "people", "bio_summary", "Bio summary", "text", None, "fund_people", "bio_summary", 1, "Fund-level materials"),
    ("fund_people", "people", "chief_investment_officer", "Chief investment officer", "text", None, "fund_people", "person_name", 1, "Fund-level materials"),
    ("fund_people", "people", "portfolio_manager", "Portfolio manager", "text", None, "fund_people", "person_name", 1, "Fund-level materials"),
    ("fund_people", "people", "founder", "Founder", "text", None, "fund_people", "person_name", 1, "Fund-level materials"),

    # Terms
    ("fund_terms", "terms", "share_class", "Share class", "text", None, "fund_terms", "share_class", 1, "Fund-level materials"),
    ("fund_terms", "terms", "management_fee", "Management fee", "number", "percent", "fund_terms", "management_fee", 1, "Fund-level materials"),
    ("fund_terms", "terms", "performance_fee", "Performance fee", "number", "percent", "fund_terms", "performance_fee", 1, "Fund-level materials"),
    ("fund_terms", "terms", "hurdle", "Hurdle", "text", None, "fund_terms", "hurdle", 1, "Fund-level materials"),
    ("fund_terms", "terms", "high_water_mark", "High-water mark", "text", None, "fund_terms", "high_water_mark", 1, "Fund-level materials"),
    ("fund_terms", "terms", "lockup", "Lockup", "text", None, "fund_terms", "lockup", 1, "Fund-level materials"),
    ("fund_terms", "terms", "redemption_frequency", "Redemption frequency", "text", None, "fund_terms", "redemption_frequency", 1, "Fund-level materials"),
    ("fund_terms", "terms", "notice_period", "Notice period", "text", None, "fund_terms", "notice_period", 1, "Fund-level materials"),
    ("fund_terms", "terms", "gate", "Gate", "text", None, "fund_terms", "gate", 1, "Fund-level materials"),
    ("fund_terms", "terms", "minimum_investment", "Minimum investment", "text", None, "fund_terms", "minimum_investment", 1, "Fund-level materials"),

    # Strategy / exposure
    ("fund_strategy", "strategy", "strategy_primary", "Primary strategy", "text", None, "fund_strategy", "strategy_primary", 1, "Fund-level materials"),
    ("fund_strategy", "strategy", "strategy_secondary", "Secondary strategy", "text", None, "fund_strategy", "strategy_secondary", 1, "Fund-level materials"),
    ("fund_strategy", "strategy", "strategy_description", "Strategy description", "text", None, "fund_strategy", "strategy_secondary", 1, "Fund-level materials"),
    ("fund_strategy", "strategy", "activism_style", "Activism style", "text", None, "fund_strategy", "activism_style", 1, "Fund-level materials"),
    ("fund_strategy", "strategy", "geography_focus", "Geography focus", "text", None, "fund_strategy", "geography_focus", 1, "Fund-level materials"),
    ("fund_strategy", "strategy", "market_cap_focus", "Market-cap focus", "text", None, "fund_strategy", "market_cap_focus", 1, "Fund-level materials"),
    ("fund_strategy", "exposure", "gross_exposure", "Gross exposure", "number", "percent", "fund_exposures", "exposure_value", 1, "Fund-level materials"),
    ("fund_strategy", "exposure", "net_exposure", "Net exposure", "number", "percent", "fund_exposures", "exposure_value", 1, "Fund-level materials"),
    ("fund_strategy", "exposure", "top_holdings", "Top holdings", "text", None, "fund_exposures", "raw_value", 1, "Fund-level materials"),

    # Performance
    ("periodic_returns", "performance", "monthly_return", "Monthly return", "number", "percent", "performance_returns", "return_value", 1, "Fund-level materials"),
    ("periodic_returns", "performance", "quarterly_return", "Quarterly return", "number", "percent", "performance_returns", "return_value", 1, "Fund-level materials"),
    ("periodic_returns", "performance", "annual_return", "Annual return", "number", "percent", "performance_returns", "return_value", 1, "Fund-level materials"),
    ("periodic_returns", "performance", "ytd_return", "YTD return", "number", "percent", "performance_returns", "return_value", 1, "Fund-level materials"),
    ("periodic_returns", "performance", "ytd_performance", "YTD performance", "number", "percent", "performance_returns", "return_value", 1, "Fund-level materials"),
    ("point_in_time_metrics", "performance", "aum", "AUM", "number", "currency", "fund_metrics", "metric_value", 1, "Fund-level materials"),
    ("point_in_time_metrics", "performance", "nav", "NAV", "number", "currency", "fund_metrics", "metric_value", 1, "Fund-level materials"),
    ("point_in_time_metrics", "performance", "beta", "Beta", "number", None, "fund_metrics", "metric_value", 1, "Fund-level materials"),
    ("point_in_time_metrics", "performance", "volatility", "Volatility", "number", "percent", "fund_metrics", "metric_value", 1, "Fund-level materials"),
    ("point_in_time_metrics", "performance", "sharpe_ratio", "Sharpe ratio", "number", None, "fund_metrics", "metric_value", 1, "Fund-level materials"),
    ("point_in_time_metrics", "performance", "information_ratio", "Information ratio", "number", None, "fund_metrics", "metric_value", 1, "Fund-level materials"),
    ("point_in_time_metrics", "performance", "benchmark_correlation", "Benchmark correlation", "number", None, "fund_metrics", "metric_value", 1, "Fund-level materials"),
    ("point_in_time_metrics", "performance", "max_drawdown", "Max drawdown", "number", "percent", "fund_metrics", "metric_value", 1, "Fund-level materials"),

    # Neutral writeups
    ("fund_writeups", "writeup", "neutral_summary", "Neutral summary", "text", None, "fund_writeups", "writeup_text", 1, "Fund-level materials"),
    ("fund_writeups", "writeup", "differentiating_edge", "Differentiating edge", "text", None, "fund_writeups", "writeup_text", 1, "Fund-level materials"),
    ("fund_writeups", "writeup", "data_limitations", "Data limitations", "text", None, "fund_writeups", "writeup_text", 1, "Fund-level materials"),

    # Flags
    ("fund_flags", "flag", "missing_key_terms", "Missing key terms", "text", None, "fund_flags", "flag_text", 1, "Fund-level materials"),
    ("fund_flags", "flag", "unclear_fee_terms", "Unclear fee terms", "text", None, "fund_flags", "flag_text", 1, "Fund-level materials"),
    ("fund_flags", "flag", "liquidity_constraint", "Liquidity constraint", "text", None, "fund_flags", "flag_text", 1, "Fund-level materials"),
    ("fund_flags", "flag", "short_track_record", "Short track record", "text", None, "fund_flags", "flag_text", 1, "Fund-level materials"),
    ("fund_flags", "flag", "high_volatility", "High volatility", "text", None, "fund_flags", "flag_text", 1, "Fund-level materials"),
    ("fund_flags", "flag", "high_drawdown", "High drawdown", "text", None, "fund_flags", "flag_text", 1, "Fund-level materials"),
    ("fund_flags", "flag", "concentration_risk", "Concentration risk", "text", None, "fund_flags", "flag_text", 1, "Fund-level materials"),
    ("fund_flags", "flag", "source_inconsistency", "Source inconsistency", "text", None, "fund_flags", "flag_text", 1, "Fund-level materials"),
    ("fund_flags", "flag", "unclear_aum_basis", "Unclear AUM basis", "text", None, "fund_flags", "flag_text", 1, "Fund-level materials"),
    ("fund_flags", "flag", "benchmark_mismatch", "Benchmark mismatch", "text", None, "fund_flags", "flag_text", 1, "Fund-level materials"),

    # Public activism
    ("public_activism", "campaign", "target_company_name", "Target company", "text", None, "public_campaigns", "target_company_name", 1, "Public source"),
    ("public_activism", "campaign", "target_ticker", "Target ticker", "text", None, "public_campaigns", "target_ticker", 1, "Public source"),
    ("public_activism", "campaign", "campaign_type", "Campaign type", "text", None, "public_campaigns", "campaign_type", 1, "Public source"),
    ("public_activism", "campaign_event", "event_summary", "Campaign event", "text", None, "campaign_events", "event_summary", 1, "Public source"),
]

FIELD_DEFINITIONS = {
    "fund_name": "The official fund or strategy name shown in the source material.",
    "manager_name": "The investment manager, adviser, or management company responsible for the fund.",
    "base_currency": "The primary reporting currency for returns, NAV, or fund accounting.",
    "inception_date": "The launch or inception date of the fund or share class.",
    "primary_strategy": "The main investment strategy stated by the manager.",
    "domicile": "The legal jurisdiction or domicile of the fund vehicle.",
    "legal_structure": "The fund vehicle type, such as trust, LP, company, or offshore fund.",
    "management_fee": "Recurring management fee charged to investors, normally expressed as percent per annum.",
    "performance_fee": "Incentive/performance allocation charged on gains, normally expressed as percent.",
    "hurdle": "Minimum return threshold required before performance fees apply.",
    "high_water_mark": "Whether prior losses must be recovered before performance fees apply.",
    "lockup": "Period or condition preventing redemption after investment.",
    "redemption_frequency": "How often investors may redeem, such as monthly or quarterly.",
    "notice_period": "Advance notice required before redemption.",
    "gate": "Restriction limiting redemption size or timing.",
    "minimum_investment": "Minimum subscription amount.",
    "strategy_primary": "Primary strategy label, such as activist, engagement, long/short, or event-driven.",
    "strategy_secondary": "Additional strategy description or secondary style.",
    "activism_style": "How the manager describes engagement, activism, constructivism, proxy activity, or public/private approach.",
    "geography_focus": "Primary geography such as Japan, Asia, or global.",
    "market_cap_focus": "Target company size range, such as small-cap, mid-cap, or all-cap.",
    "gross_exposure": "Total long plus short exposure, usually percent of NAV.",
    "net_exposure": "Long exposure minus short exposure, usually percent of NAV.",
    "top_holdings": "Named largest positions or portfolio holdings.",
    "monthly_return": "One-month fund return. Store percent points, e.g. 1.23 for 1.23%.",
    "quarterly_return": "One-quarter fund return. Store percent points, e.g. 2.50 for 2.50%.",
    "annual_return": "One-year or calendar-year fund return. Store percent points.",
    "ytd_return": "Year-to-date fund return through the stated as-of date.",
    "ytd_performance": "Year-to-date fund performance through the stated as-of date.",
    "aum": "Assets under management. Preserve currency and unit, such as USD million or JPY billion.",
    "nav": "Net asset value. Preserve currency and date.",
    "beta": "Beta to benchmark or market if stated.",
    "volatility": "Reported standard deviation or volatility. Preserve period and annualization context if stated.",
    "sharpe_ratio": "Reported Sharpe ratio. Preserve time window if stated.",
    "information_ratio": "Reported information ratio. Preserve benchmark/time window if stated.",
    "benchmark_correlation": "Reported correlation to benchmark. Preserve benchmark identity if stated.",
    "max_drawdown": "Reported maximum drawdown. Preserve time window if stated.",
    "neutral_summary": "One short paragraph describing the fund using only source-backed facts.",
    "differentiating_edge": "One short paragraph describing what appears distinctive versus peers, only if supported by the source.",
    "data_limitations": "One short paragraph describing what the source does not disclose or what remains unclear.",
}


RETURN_FACTS = {
    "monthly_return": "monthly",
    "quarterly_return": "quarterly",
    "annual_return": "annual",
    "ytd_return": "ytd",
    "ytd_performance": "ytd",
}


CORE_PROFILE_FIELDS = {
    "fund_name": "fund_name",
    "manager_name": "manager_name",
    "status": "status",
    "base_currency": "base_currency",
    "inception_date": "inception_date",
    "primary_strategy": "primary_strategy",
}


STRATEGY_FIELD_MAP = {
    "strategy_primary": "strategy_primary",
    "primary_strategy": "strategy_primary",
    "strategy_secondary": "strategy_secondary",
    "strategy_description": "strategy_secondary",
    "activism_style": "activism_style",
    "geography_focus": "geography_focus",
    "geographic_focus": "geography_focus",
    "market_cap_focus": "market_cap_focus",
}


TERMS_FIELD_MAP = {
    "share_class": "share_class",
    "management_fee": "management_fee",
    "performance_fee": "performance_fee",
    "hurdle": "hurdle",
    "high_water_mark": "high_water_mark",
    "lockup": "lockup",
    "redemption_frequency": "redemption_frequency",
    "notice_period": "notice_period",
    "gate": "gate",
    "minimum_investment": "minimum_investment",
}

EXTRACTION_SCOPES = {
    "profile_terms": {
        ("profile", "fund_name"),
        ("profile", "manager_name"),
        ("profile", "base_currency"),
        ("profile", "inception_date"),
        ("profile", "primary_strategy"),
        ("profile", "domicile"),
        ("profile", "legal_structure"),
        ("profile", "administrator"),
        ("profile", "auditor"),
        ("profile", "custodian"),
        *{("terms", name) for name in TERMS_FIELD_MAP},
    },
    "strategy_people": {
        ("people", "person_name"),
        ("people", "role_title"),
        ("people", "bio_summary"),
        ("people", "chief_investment_officer"),
        ("people", "portfolio_manager"),
        ("people", "founder"),
        ("strategy", "strategy_primary"),
        ("strategy", "strategy_secondary"),
        ("strategy", "strategy_description"),
        ("strategy", "activism_style"),
        ("strategy", "geography_focus"),
        ("strategy", "market_cap_focus"),
        ("exposure", "gross_exposure"),
        ("exposure", "net_exposure"),
        ("exposure", "top_holdings"),
    },
    "performance": {
        ("performance", "aum"),
        ("performance", "nav"),
        ("performance", "beta"),
        ("performance", "volatility"),
        ("performance", "sharpe_ratio"),
        ("performance", "information_ratio"),
        ("performance", "benchmark_correlation"),
        ("performance", "max_drawdown"),
    },
    "writeups_flags": {
        ("writeup", "neutral_summary"),
        ("writeup", "differentiating_edge"),
        ("flag", "missing_key_terms"),
        ("flag", "unclear_fee_terms"),
        ("flag", "liquidity_constraint"),
        ("flag", "short_track_record"),
        ("flag", "high_volatility"),
        ("flag", "high_drawdown"),
        ("flag", "concentration_risk"),
        ("flag", "source_inconsistency"),
        ("flag", "unclear_aum_basis"),
        ("flag", "benchmark_mismatch"),
    },
}


def valid_fact_names():
    return {(row[1], row[2]) for row in CHARACTERISTIC_DEFINITIONS}


def extraction_schema_text(scope=None):
    allowed = EXTRACTION_SCOPES.get(scope) if scope else None
    lines = []
    for group_id, category, fact_name, display, value_type, unit_hint, *_rest in CHARACTERISTIC_DEFINITIONS:
        if allowed is not None and (category, fact_name) not in allowed:
            continue
        definition = FIELD_DEFINITIONS.get(fact_name, display)
        lines.append(
            f"- {category}.{fact_name}: {display}; type={value_type}; "
            f"unit={unit_hint or 'none'}; group={group_id}; definition={definition}"
        )
    return "\n".join(lines)


def extraction_scope_names():
    return list(EXTRACTION_SCOPES)

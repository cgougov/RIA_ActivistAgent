"""The standardized factsheet vocabulary — the single source of truth.

Every descriptive fact in the system is one of these fields. Most fields are
fund-level, but a controlled subset can be qualified by share class. Returns
are the time-series exception and live in their own table.

Ported and consolidated from the legacy taxonomy (78 definitions), with the
duplicate machinery removed: no generic person_name/role_title facts, no
strategy_primary/primary_strategy aliases.
"""

SECTION_ORDER = ["overview", "people", "strategy", "terms", "metrics",
                 "notes", "presentation", "flags"]

SECTION_TITLES = {
    "overview": "Overview",
    "people": "People",
    "strategy": "Strategy & Exposure",
    "terms": "Terms",
    "metrics": "Reported Metrics",
    "notes": "Notes",
    "presentation": "Presentation Highlights",
    "flags": "Diligence Flags",
    "returns": "Returns",
}

# field_key -> (section, label, value_type, unit_hint, definition)
FIELDS = {
    # Overview
    "fund_name": ("overview", "Fund name", "text", None, "The official fund or strategy name shown in the source material."),
    "manager_name": ("overview", "Manager", "text", None, "The investment manager, adviser, or management company responsible for the fund."),
    "base_currency": ("overview", "Base currency", "text", "currency", "The primary reporting currency for returns, NAV, or fund accounting."),
    "inception_date": ("overview", "Inception date", "date", "date", "The launch or inception date of the fund or share class."),
    "domicile": ("overview", "Domicile", "text", None, "The legal jurisdiction or domicile of the fund vehicle."),
    "legal_structure": ("overview", "Legal structure", "text", None, "The fund vehicle type, such as trust, LP, company, or offshore fund."),
    "administrator": ("overview", "Administrator", "text", None, "The fund administrator named in the source."),
    "auditor": ("overview", "Auditor", "text", None, "The fund auditor named in the source."),
    "custodian": ("overview", "Custodian", "text", None, "The custodian or prime broker named in the source."),

    # People (role-specific fields only; key_people catches the rest)
    "chief_investment_officer": ("people", "CIO", "text", None, "The named chief investment officer."),
    "portfolio_manager": ("people", "Portfolio manager", "text", None, "The named lead portfolio manager."),
    "founder": ("people", "Founder", "text", None, "The named founder(s) of the manager."),
    "key_people": ("people", "Other key people", "text", None, "Other named people with meaningful roles, as 'Name (role); Name (role)'."),

    # Strategy & exposure
    "primary_strategy": ("strategy", "Primary strategy", "text", None, "Primary strategy label, such as activist, engagement, long/short, or event-driven."),
    "strategy_description": ("strategy", "Strategy description", "text", None, "Concise description of the investment strategy in the manager's words."),
    "activism_style": ("strategy", "Activism style", "text", None, "How the manager describes engagement, activism, constructivism, proxy activity, or public/private approach."),
    "geography_focus": ("strategy", "Geography focus", "text", None, "Primary geography such as Japan, Asia, or global."),
    "market_cap_focus": ("strategy", "Market-cap focus", "text", None, "Target company size range, such as small-cap, mid-cap, or all-cap."),
    "gross_exposure": ("strategy", "Gross exposure", "number", "percent", "Total long plus short exposure, usually percent of NAV."),
    "net_exposure": ("strategy", "Net exposure", "number", "percent", "Long exposure minus short exposure, usually percent of NAV."),
    "top_holdings": ("strategy", "Top holdings", "text", None, "Named largest positions or portfolio holdings."),

    # Terms
    "share_class": ("terms", "Share class", "text", None, "The share class the stated terms apply to."),
    "management_fee": ("terms", "Management fee", "number", "percent", "Recurring management fee, normally percent per annum."),
    "performance_fee": ("terms", "Performance fee", "number", "percent", "Incentive/performance allocation charged on gains, normally percent."),
    "hurdle": ("terms", "Hurdle", "text", None, "Minimum return threshold required before performance fees apply."),
    "high_water_mark": ("terms", "High-water mark", "text", None, "Whether prior losses must be recovered before performance fees apply."),
    "lockup": ("terms", "Lockup", "text", None, "Period or condition preventing redemption after investment."),
    "redemption_frequency": ("terms", "Redemption frequency", "text", None, "How often investors may redeem, such as monthly or quarterly."),
    "notice_period": ("terms", "Notice period", "text", None, "Advance notice required before redemption."),
    "gate": ("terms", "Gate", "text", None, "Restriction limiting redemption size or timing."),
    "minimum_investment": ("terms", "Minimum investment", "text", None, "Minimum subscription amount."),

    # Reported point-in-time metrics (returns time series live in the returns table)
    "aum": ("metrics", "AUM", "number", "currency", "Assets under management. Preserve currency and unit, such as USD million or JPY billion."),
    "nav": ("metrics", "NAV", "number", "currency", "Net asset value. Preserve currency and date."),
    "beta": ("metrics", "Beta", "number", None, "Beta to benchmark or market if stated."),
    "volatility": ("metrics", "Volatility", "number", "percent", "Reported standard deviation or volatility. Preserve period and annualization context if stated."),
    "sharpe_ratio": ("metrics", "Sharpe ratio", "number", None, "Reported Sharpe ratio. Preserve time window if stated."),
    "information_ratio": ("metrics", "Information ratio", "number", None, "Reported information ratio. Preserve benchmark/time window if stated."),
    "benchmark_correlation": ("metrics", "Benchmark correlation", "number", None, "Reported correlation to benchmark. Preserve benchmark identity if stated."),
    "max_drawdown": ("metrics", "Max drawdown", "number", "percent", "Reported maximum drawdown. Preserve time window if stated."),

    # Notes (one short paragraph each)
    "neutral_summary": ("notes", "Neutral summary", "text", None, "One short paragraph describing the fund using only source-backed facts."),
    "differentiating_edge": ("notes", "Differentiating edge", "text", None, "One short paragraph describing what appears distinctive versus peers, only if supported by the source."),
    "data_limitations": ("notes", "Data limitations", "text", None, "One short paragraph describing what the source does not disclose or what remains unclear."),

    # Presentation highlights (the qualitative story, sourced from the PRS deck)
    "investment_thesis": ("presentation", "Investment thesis", "text", None, "The manager's core investment thesis or philosophy, in their words."),
    "value_creation_approach": ("presentation", "Value-creation approach", "text", None, "How the manager drives value — engagement, governance, operational, or capital-allocation change."),
    "example_engagements": ("presentation", "Example engagements", "text", None, "Concrete example campaigns or case studies: named companies, actions taken, and outcomes, as 'Company: action -> outcome; ...'."),
    "competitive_edge": ("presentation", "What makes them unique", "text", None, "What the manager presents as distinctive versus peers — access, expertise, approach, or track record."),
    "track_record_highlights": ("presentation", "Track-record highlights", "text", None, "Notable outcomes or milestones the manager highlights, grounded in the deck."),

    # Diligence flags (neutral language; only for real diligence issues)
    "flag_missing_key_terms": ("flags", "Missing key terms", "text", None, "Important terms the document does not disclose."),
    "flag_unclear_fee_terms": ("flags", "Unclear fee terms", "text", None, "Fee terms that are ambiguous or inconsistent in the source."),
    "flag_liquidity_constraint": ("flags", "Liquidity constraint", "text", None, "Material lockup/gate/notice constraints a reviewer should inspect."),
    "flag_short_track_record": ("flags", "Short track record", "text", None, "Track record too short to evaluate."),
    "flag_high_volatility": ("flags", "High volatility", "text", None, "Reported volatility that is notably high."),
    "flag_high_drawdown": ("flags", "High drawdown", "text", None, "Reported drawdown that is notably high."),
    "flag_concentration_risk": ("flags", "Concentration risk", "text", None, "Notably concentrated portfolio or single-position risk."),
    "flag_source_inconsistency": ("flags", "Source inconsistency", "text", None, "Statements that conflict within or across the manager's materials."),
    "flag_unclear_aum_basis": ("flags", "Unclear AUM basis", "text", None, "AUM figures whose basis (strategy vs firm, gross vs net) is unclear."),
    "flag_benchmark_mismatch": ("flags", "Benchmark mismatch", "text", None, "Benchmark that appears mismatched to the stated strategy."),
}

# Extraction scopes: each LLM extraction call has one narrow job.
EXTRACTION_SCOPES = {
    "profile_terms": [k for k, v in FIELDS.items() if v[0] in ("overview", "terms")],
    "strategy_people": [k for k, v in FIELDS.items() if v[0] in ("people", "strategy")],
    "metrics": [k for k, v in FIELDS.items() if v[0] == "metrics"],
    "notes_flags": [k for k, v in FIELDS.items() if v[0] in ("notes", "flags")],
    "presentation": [k for k, v in FIELDS.items() if v[0] == "presentation"],
}

SCOPE_GUIDANCE = {
    "profile_terms": (
        "Extract only stable, explicitly disclosed identity, structure, service provider, fee, liquidity, "
        "and investor-term facts. Prefer one best value per field or per share class when the source clearly "
        "states class-specific terms."
    ),
    "strategy_people": (
        "Extract only named people with meaningful roles, concise strategy descriptions, activism style, "
        "geography/market-cap focus, and material exposure descriptors. Put each person in the most specific "
        "role field; use key_people only for people who fit no specific field."
    ),
    "metrics": (
        "Extract only point-in-time performance metrics such as AUM, NAV, beta, volatility, Sharpe ratio, "
        "information ratio, benchmark correlation, and max drawdown. Do not extract monthly, quarterly, "
        "annual, or YTD return observations; returns are handled by a separate workflow. If the source ties a "
        "metric to a specific share class, preserve that share class."
    ),
    "notes_flags": (
        "Write at most one neutral_summary and at most one differentiating_edge for this document. Extract flags "
        "only for real diligence issues, source inconsistencies, or missing important disclosures that materially "
        "affect review. Do not create flags for ordinary factsheet brevity."
    ),
    "presentation": (
        "This is a marketing/strategy presentation, not a fact sheet. Extract the qualitative story: the "
        "investment thesis, how the manager creates value, concrete example engagements or case studies (named "
        "companies, actions, outcomes), what they present as unique versus peers, and notable track-record "
        "highlights. Do not extract fees, terms, or point-in-time metrics here — those come from the fact sheet. "
        "Keep each field to one concise paragraph grounded in the deck."
    ),
}


def fields_for_scope(scope):
    return EXTRACTION_SCOPES[scope]


def field_schema_text(scope):
    lines = []
    for key in EXTRACTION_SCOPES[scope]:
        section, label, value_type, unit, definition = FIELDS[key]
        lines.append(f"- {key}: {label}; type={value_type}; unit={unit or 'none'}; definition={definition}")
    return "\n".join(lines)


def section_fields(section):
    return [k for k, v in FIELDS.items() if v[0] == section]


SHARE_CLASS_FIELDS = {
    "share_class",
    "base_currency",
    "inception_date",
    "nav",
    "beta",
    "volatility",
    "sharpe_ratio",
    "information_ratio",
    "benchmark_correlation",
    "max_drawdown",
    "management_fee",
    "performance_fee",
    "hurdle",
    "high_water_mark",
    "lockup",
    "redemption_frequency",
    "notice_period",
    "gate",
    "minimum_investment",
}


def supports_share_class(field_key):
    return field_key in SHARE_CLASS_FIELDS

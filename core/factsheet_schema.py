STANDARDIZED_SECTION_ORDER = ["overview", "manager", "strategy", "terms", "metrics", "notes_flags"]


SECTION_TITLES = {
    "overview": "Overview",
    "manager": "Manager / People",
    "strategy": "Strategy / Portfolio",
    "terms": "Terms",
    "metrics": "Reported Metrics",
    "notes_flags": "Notes / Flags",
    "other": "Other",
    "returns": "Returns",
}


STANDARDIZED_FIELD_SPECS = {
    "overview": [
        ("fund_name", "Fund name", {("profile", "fund_name")}),
        ("manager_name", "Manager", {("profile", "manager_name")}),
        ("base_currency", "Base currency", {("profile", "base_currency")}),
        ("domicile", "Domicile", {("profile", "domicile")}),
        ("legal_structure", "Structure", {("profile", "legal_structure")}),
        ("inception_date", "Inception date", {("profile", "inception_date")}),
        ("aum", "AUM", {("performance", "aum")}),
        ("nav", "NAV", {("performance", "nav")}),
    ],
    "strategy": [
        ("primary_strategy", "Primary strategy", {("profile", "primary_strategy"), ("strategy", "strategy_primary")}),
        ("activism_style", "Activism style", {("strategy", "activism_style")}),
        ("geography_focus", "Geography focus", {("strategy", "geography_focus")}),
        ("market_cap_focus", "Market cap focus", {("strategy", "market_cap_focus")}),
        ("gross_exposure", "Gross exposure", {("exposure", "gross_exposure")}),
        ("net_exposure", "Net exposure", {("exposure", "net_exposure")}),
        ("top_holdings", "Top holdings", {("exposure", "top_holdings")}),
    ],
    "terms": [
        ("share_class", "Share class", {("terms", "share_class")}),
        ("management_fee", "Management fee", {("terms", "management_fee")}),
        ("performance_fee", "Performance fee", {("terms", "performance_fee")}),
        ("hurdle", "Hurdle", {("terms", "hurdle")}),
        ("high_water_mark", "High-water mark", {("terms", "high_water_mark")}),
        ("lockup", "Lockup", {("terms", "lockup")}),
        ("redemption_frequency", "Redemption frequency", {("terms", "redemption_frequency")}),
        ("notice_period", "Notice period", {("terms", "notice_period")}),
        ("gate", "Gate", {("terms", "gate")}),
        ("minimum_investment", "Minimum investment", {("terms", "minimum_investment")}),
    ],
    "metrics": [
        ("beta", "Beta", {("performance", "beta")}),
        ("volatility", "Volatility", {("performance", "volatility")}),
        ("sharpe_ratio", "Sharpe", {("performance", "sharpe_ratio")}),
        ("information_ratio", "Information ratio", {("performance", "information_ratio")}),
        ("benchmark_correlation", "Benchmark correlation", {("performance", "benchmark_correlation")}),
        ("max_drawdown", "Max drawdown", {("performance", "max_drawdown")}),
    ],
    "notes_flags": [
        ("neutral_summary", "Neutral summary", {("writeup", "neutral_summary")}),
        ("differentiating_edge", "Differentiating edge", {("writeup", "differentiating_edge")}),
    ],
}

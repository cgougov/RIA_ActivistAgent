NOT_APPROVED = "Not yet approved"


def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _date_key(row, *fields):
    for field in fields:
        value = _clean(row.get(field))
        if value:
            return value
    return ""


def _with_date(value, row, *fields):
    date_value = _date_key(row, *fields)
    if date_value:
        return f"{value} (as of {date_value})"
    return value


def _unique(values):
    seen = set()
    result = []
    for value in values:
        value = _clean(value)
        if not value:
            continue
        key = " ".join(value.lower().split())
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _missing(value):
    return value if _clean(value) else NOT_APPROVED


def _characteristics(snapshot, fact_name, category=None):
    rows = []
    for row in snapshot.get("characteristics", []):
        if row.get("fact_name") != fact_name:
            continue
        if category and row.get("fact_category") != category:
            continue
        rows.append(row)
    rows.sort(key=lambda row: _date_key(row, "as_of_date"), reverse=True)
    return rows


def _characteristic_value(snapshot, fact_name, category=None):
    for row in _characteristics(snapshot, fact_name, category):
        value = _clean(row.get("value_text"))
        if value:
            return _with_date(value, row, "as_of_date")
    return None


def _profile_attribute(snapshot, attribute_name):
    rows = [
        row
        for row in snapshot.get("profile_attributes", [])
        if row.get("attribute_name") == attribute_name
    ]
    rows.sort(key=lambda row: _date_key(row, "effective_date"), reverse=True)
    for row in rows:
        value = _clean(row.get("normalized_value")) or _clean(row.get("attribute_value"))
        if value:
            return _with_date(value, row, "effective_date")
    return _characteristic_value(snapshot, attribute_name, "profile")


def _metric(snapshot, metric_name):
    rows = [
        row
        for row in snapshot.get("metrics", [])
        if row.get("metric_name") == metric_name
    ]
    rows.sort(key=lambda row: _date_key(row, "as_of_date"), reverse=True)
    for row in rows:
        value = _clean(row.get("normalized_value")) or _clean(row.get("raw_value"))
        unit = _clean(row.get("unit"))
        if value and unit and unit.lower() not in value.lower():
            value = f"{value} {unit}"
        if value:
            return _with_date(value, row, "as_of_date")
    return _characteristic_value(snapshot, metric_name, "performance")


def _latest_field(rows, field, sort_fields=("created_at",), display_fields=()):
    sorted_rows = sorted(rows, key=lambda row: _date_key(row, *sort_fields), reverse=True)
    for row in sorted_rows:
        value = _clean(row.get(field))
        if value:
            if display_fields:
                return _with_date(value, row, *display_fields)
            return value
    return None


def _strategy_field(snapshot, field_name, fact_name=None):
    value = _latest_field(snapshot.get("strategy", []), field_name)
    if value:
        return value
    return _characteristic_value(snapshot, fact_name or field_name, "strategy")


def _term(snapshot, field_name, fact_name=None):
    rows = sorted(
        snapshot.get("terms", []),
        key=lambda row: _date_key(row, "effective_date", "created_at"),
        reverse=True,
    )
    for row in rows:
        value = _clean(row.get(field_name))
        if value:
            if field_name in {"management_fee", "performance_fee"} and "%" not in value:
                value = f"{value}%"
            return _with_date(value, row, "effective_date")
    return _characteristic_value(snapshot, fact_name or field_name, "terms")


def _exposure_notes(snapshot):
    values = []
    for row in sorted(
        snapshot.get("exposures", []),
        key=lambda row: _date_key(row, "as_of_date", "created_at"),
        reverse=True,
    ):
        name = _clean(row.get("exposure_name"))
        value = _clean(row.get("normalized_value")) or _clean(row.get("raw_value"))
        unit = _clean(row.get("unit"))
        if not name or not value:
            continue
        if unit and unit.lower() not in value.lower():
            value = f"{value} {unit}"
        values.append(_with_date(f"{name.replace('_', ' ')}: {value}", row, "as_of_date"))

    strategy_exposure = _latest_field(snapshot.get("strategy", []), "exposure_notes")
    if strategy_exposure:
        values.insert(0, strategy_exposure)

    return "; ".join(_unique(values)) or None


def _people(snapshot):
    values = []
    generic_roles = {"person name", "role title", "bio summary"}
    for row in snapshot.get("people", []):
        name = _clean(row.get("person_name"))
        role = _clean(row.get("role_title"))
        if not name:
            continue
        if role and role.lower() not in generic_roles:
            values.append(f"{name} - {role}")
        else:
            values.append(name)

    role_labels = {
        "chief_investment_officer": "Chief investment officer",
        "portfolio_manager": "Portfolio manager",
        "founder": "Founder",
    }
    for fact_name, label in role_labels.items():
        for row in _characteristics(snapshot, fact_name, "people"):
            name = _clean(row.get("value_text"))
            if name:
                values.append(f"{name} - {label}")

    return _unique(values)


def _writeup(snapshot, writeup_type):
    rows = [
        row
        for row in snapshot.get("writeups", [])
        if row.get("writeup_type") == writeup_type
    ]
    for row in rows:
        value = _clean(row.get("writeup_text"))
        if value:
            return value
    return _characteristic_value(snapshot, writeup_type, "writeup")


def _flags(snapshot):
    values = []
    for row in snapshot.get("flags", []):
        text = _clean(row.get("flag_text"))
        if not text:
            continue
        flag_type = _clean(row.get("flag_type"))
        severity = _clean(row.get("severity"))
        label_parts = [part for part in [severity, flag_type] if part]
        label = " / ".join(label_parts)
        values.append(f"{label}: {text}" if label else text)
    return _unique(values)


def build_fund_brief(snapshot):
    fund = snapshot.get("fund", {})
    primary_strategy = (
        _clean(fund.get("primary_strategy"))
        or _strategy_field(snapshot, "strategy_primary")
        or _characteristic_value(snapshot, "primary_strategy", "profile")
    )
    base_currency = _clean(fund.get("base_currency")) or _characteristic_value(snapshot, "base_currency", "profile")
    inception_date = _clean(fund.get("inception_date")) or _characteristic_value(snapshot, "inception_date", "profile")

    return {
        "overview": [
            ("Fund name", _missing(fund.get("fund_name"))),
            ("Manager", _missing(fund.get("manager_name"))),
            ("Base currency", _missing(base_currency)),
            ("Domicile", _missing(_profile_attribute(snapshot, "domicile"))),
            ("Structure", _missing(_profile_attribute(snapshot, "legal_structure"))),
            ("Inception date", _missing(inception_date)),
            ("AUM", _missing(_metric(snapshot, "aum"))),
            ("NAV", _missing(_metric(snapshot, "nav"))),
        ],
        "strategy": [
            ("Primary strategy", _missing(primary_strategy)),
            ("Activism style", _missing(_strategy_field(snapshot, "activism_style"))),
            ("Geography focus", _missing(_strategy_field(snapshot, "geography_focus"))),
            ("Market cap focus", _missing(_strategy_field(snapshot, "market_cap_focus"))),
            ("Exposure notes", _missing(_exposure_notes(snapshot))),
        ],
        "people": _people(snapshot),
        "terms": [
            ("Management fee", _missing(_term(snapshot, "management_fee"))),
            ("Performance fee", _missing(_term(snapshot, "performance_fee"))),
            ("Hurdle", _missing(_term(snapshot, "hurdle"))),
            ("High-water mark", _missing(_term(snapshot, "high_water_mark"))),
            ("Lockup", _missing(_term(snapshot, "lockup"))),
            ("Redemption frequency", _missing(_term(snapshot, "redemption_frequency"))),
            ("Notice period", _missing(_term(snapshot, "notice_period"))),
            ("Gate", _missing(_term(snapshot, "gate"))),
            ("Minimum investment", _missing(_term(snapshot, "minimum_investment"))),
        ],
        "notes": {
            "neutral_summary": _missing(_writeup(snapshot, "neutral_summary")),
            "differentiating_edge": _missing(_writeup(snapshot, "differentiating_edge")),
            "flags": _flags(snapshot),
        },
    }

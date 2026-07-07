from pathlib import Path

import pandas as pd

from core.config import EXPORT_DIR


SOURCE_TRUTH_SHEETS = {
    "funds": "SELECT * FROM funds ORDER BY fund_id",
    "characteristics": "SELECT * FROM fund_characteristics ORDER BY fund_id, group_id, fact_category, fact_name, as_of_date",
    "attributes": "SELECT * FROM fund_profile_attributes ORDER BY fund_id, attribute_name",
    "people": "SELECT * FROM fund_people ORDER BY fund_id, person_name",
    "terms": "SELECT * FROM fund_terms ORDER BY fund_id, effective_date DESC",
    "strategy": "SELECT * FROM fund_strategy ORDER BY fund_id, created_at DESC",
    "returns": "SELECT * FROM performance_returns ORDER BY fund_id, period_end_date",
    "metrics": "SELECT * FROM fund_metrics ORDER BY fund_id, metric_name, as_of_date",
    "exposures": "SELECT * FROM fund_exposures ORDER BY fund_id, exposure_name, as_of_date",
    "writeups": "SELECT * FROM fund_writeups ORDER BY fund_id, writeup_type, created_at",
    "flags": "SELECT * FROM fund_flags ORDER BY fund_id, flag_type, created_at",
    "approved_facts": "SELECT * FROM approved_facts ORDER BY fund_id, fact_category, fact_name, approved_at",
}


def _with_fund_filter(sql, fund_ids):
    if not fund_ids or " fund_id" not in sql and "fund_id" not in sql:
        return sql, []
    placeholders = ", ".join("?" for _ in fund_ids)
    if " WHERE " in sql.upper():
        return sql.replace(" ORDER BY ", f" AND fund_id IN ({placeholders}) ORDER BY "), fund_ids
    return sql.replace(" ORDER BY ", f" WHERE fund_id IN ({placeholders}) ORDER BY "), fund_ids


def export_source_truth_workbook(connection, output_path=None, fund_ids=None):
    EXPORT_DIR.mkdir(exist_ok=True)
    output = Path(output_path) if output_path else EXPORT_DIR / "source_of_truth_workbook.xlsx"
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, sql in SOURCE_TRUTH_SHEETS.items():
            filtered_sql, params = _with_fund_filter(sql, fund_ids)
            pd.read_sql_query(filtered_sql, connection, params=params).to_excel(
                writer,
                sheet_name=sheet_name[:31],
                index=False,
            )
    return output


def export_analysis_workbook(connection, analysis_id=None, fund_ids=None, output_path=None):
    EXPORT_DIR.mkdir(exist_ok=True)
    if output_path:
        output = Path(output_path)
    elif analysis_id:
        output = EXPORT_DIR / f"{analysis_id}_analysis_workbook.xlsx"
    else:
        output = EXPORT_DIR / "analysis_workbook.xlsx"

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        export_tables = {
            "funds": "SELECT * FROM funds ORDER BY fund_id",
            "characteristics": "SELECT * FROM fund_characteristics ORDER BY fund_id, group_id, fact_category, fact_name, as_of_date",
            "writeups": "SELECT * FROM fund_writeups ORDER BY fund_id, writeup_type, created_at",
            "flags": "SELECT * FROM fund_flags ORDER BY fund_id, flag_type, created_at",
            "returns": "SELECT * FROM performance_returns ORDER BY fund_id, period_end_date",
            "metrics": "SELECT * FROM fund_metrics ORDER BY fund_id, metric_name, as_of_date",
            "terms": "SELECT * FROM fund_terms ORDER BY fund_id, effective_date DESC",
            "approved_facts": "SELECT * FROM approved_facts ORDER BY fund_id, fact_category, fact_name, approved_at",
        }
        for sheet_name, sql in export_tables.items():
            filtered_sql, params = _with_fund_filter(sql, fund_ids)
            pd.read_sql_query(filtered_sql, connection, params=params).to_excel(
                writer,
                sheet_name=sheet_name[:31],
                index=False,
            )

        if analysis_id:
            pd.read_sql_query(
                "SELECT * FROM analysis_runs WHERE analysis_id = ?",
                connection,
                params=[analysis_id],
            ).to_excel(writer, sheet_name="analysis_run", index=False)
        else:
            pd.read_sql_query(
                "SELECT * FROM analysis_runs ORDER BY created_at DESC",
                connection,
            ).to_excel(writer, sheet_name="analysis_runs", index=False)

    return output

import argparse
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from core.db import connect_db, utc_now
from core.return_rows import upsert_proposed_return_row


PERIOD_TO_FACT = {
    "monthly": "monthly_return",
    "quarterly": "quarterly_return",
    "annual": "annual_return",
    "ytd": "ytd_return",
}

REQUIRED_COLUMNS = {"period_type", "period_end_date", "return_value"}


def clean_optional(value):
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path")
    parser.add_argument("--fund-id", required=True)
    parser.add_argument("--source-document-id", required=True)
    parser.add_argument("--imported-by", default="system")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        raise SystemExit(f"Missing CSV: {csv_path}")

    df = pd.read_csv(csv_path)
    missing = sorted(REQUIRED_COLUMNS - set(df.columns))
    if missing:
        raise SystemExit(f"CSV missing columns: {', '.join(missing)}")

    with connect_db() as connection:
        document = connection.execute(
            "SELECT * FROM source_documents WHERE document_id = ?",
            (args.source_document_id,),
        ).fetchone()
        if document is None:
            raise SystemExit(f"Unknown source_document_id: {args.source_document_id}")

        fund = connection.execute(
            "SELECT * FROM funds WHERE fund_id = ?",
            (args.fund_id,),
        ).fetchone()
        if fund is None:
            raise SystemExit(f"Unknown fund_id: {args.fund_id}")

        import_id = f"import_{uuid4().hex}"
        run_id = f"run_{uuid4().hex}"
        now = utc_now()
        connection.execute(
            """
            INSERT INTO data_imports (
                import_id, import_type, fund_id, source_document_id, file_name,
                import_status, row_count, imported_by, imported_at, notes
            )
            VALUES (?, 'performance_returns_csv', ?, ?, ?, 'staged', ?, ?, ?, ?)
            """,
            (
                import_id,
                args.fund_id,
                args.source_document_id,
                csv_path.name,
                len(df),
                args.imported_by,
                now,
                args.notes,
            ),
        )
        connection.execute(
            """
            INSERT INTO extraction_runs (
                run_id, document_id, extraction_scope, extraction_method, model_name,
                prompt_version, run_status, created_at, finished_at, raw_output
            )
            VALUES (?, ?, 'performance_returns', 'csv_import', 'none', 'csv_v1', 'complete', ?, ?, ?)
            """,
            (run_id, args.source_document_id, now, now, df.to_json(orient="records")),
        )

        inserted = 0
        skipped = []
        for index, row in df.iterrows():
            period_type = str(row["period_type"]).strip().lower()
            if PERIOD_TO_FACT.get(period_type) is None:
                skipped.append((index + 2, f"unsupported period_type: {period_type}"))
                continue

            return_value = clean_optional(row["return_value"])
            period_end_date = clean_optional(row["period_end_date"])
            if not return_value or not period_end_date:
                skipped.append((index + 2, "missing return_value or period_end_date"))
                continue

            upsert_proposed_return_row(
                connection,
                run_id=run_id,
                fund_id=args.fund_id,
                source_document_id=args.source_document_id,
                page_number=clean_optional(row.get("page_number")),
                share_class=clean_optional(row.get("share_class")),
                period_type=period_type,
                period_start_date=clean_optional(row.get("period_start_date")),
                period_end_date=period_end_date,
                return_type=clean_optional(row.get("return_type")) or "unknown",
                return_value=return_value,
                raw_value_text=str(return_value),
                unit=clean_optional(row.get("unit")) or "percent",
                currency=clean_optional(row.get("currency")),
                benchmark_name=clean_optional(row.get("benchmark_name")),
                evidence_text=clean_optional(row.get("quoted_text")),
                source_import_id=import_id,
                row_label=clean_optional(row.get("row_label")),
                column_label=clean_optional(row.get("column_label")),
                table_name=clean_optional(row.get("table_name")),
            )
            inserted += 1

        connection.execute(
            "UPDATE data_imports SET row_count = ?, import_status = ? WHERE import_id = ?",
            (inserted, "staged_with_skips" if skipped else "staged", import_id),
        )
        connection.commit()

    print(f"Import staged: {import_id}")
    print(f"Pending return facts created: {inserted}")
    if skipped:
        print(f"Skipped rows: {len(skipped)}")
        for line_number, reason in skipped:
            print(f"  line {line_number}: {reason}")


if __name__ == "__main__":
    main()

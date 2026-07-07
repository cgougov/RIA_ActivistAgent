import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from core.config import EXPORT_DIR
from core.db import connect_db


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fund-id")
    parser.add_argument("--output")
    args = parser.parse_args()

    where = ""
    params = []
    if args.fund_id:
        where = "WHERE pr.fund_id = ?"
        params.append(args.fund_id)

    sql = f"""
        SELECT
            pr.fund_id,
            f.fund_name,
            pr.share_class,
            pr.period_type,
            pr.period_start_date,
            pr.period_end_date,
            pr.return_type,
            pr.return_value,
            pr.unit,
            pr.source_document_id,
            sd.document_title,
            sd.document_date,
            pr.page_number,
            pr.approved_fact_id,
            pr.source_import_id
        FROM performance_returns pr
        JOIN funds f ON f.fund_id = pr.fund_id
        JOIN source_documents sd ON sd.document_id = pr.source_document_id
        {where}
        ORDER BY pr.fund_id, pr.period_end_date
    """

    EXPORT_DIR.mkdir(exist_ok=True)
    output = Path(args.output) if args.output else EXPORT_DIR / "performance_returns.csv"
    with connect_db() as connection:
        df = pd.read_sql_query(sql, connection, params=params)
    df.to_csv(output, index=False)
    print(f"Exported {len(df)} rows: {output}")


if __name__ == "__main__":
    main()


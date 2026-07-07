import json

from core.db import many, one


class FundSnapshot:
    """Analysis-ready view of one fund assembled from relational source tables."""

    def __init__(self, connection, fund_id):
        self.connection = connection
        self.fund_id = fund_id

    def build(self):
        fund = one(
            self.connection,
            "SELECT * FROM funds WHERE fund_id = ?",
            (self.fund_id,),
        )
        if fund is None:
            raise ValueError(f"Unknown fund_id: {self.fund_id}")

        characteristics = many(
            self.connection,
            """
            SELECT
                fc.group_id,
                cg.display_name AS group_name,
                fc.fact_category,
                fc.fact_name,
                fc.display_name,
                fc.value_text,
                fc.value_number,
                fc.unit,
                fc.as_of_date,
                fc.source_document_id,
                fc.page_number,
                fc.quoted_text,
                fc.approved_fact_id
            FROM fund_characteristics fc
            JOIN characteristic_groups cg
                ON cg.group_id = fc.group_id
            WHERE fc.fund_id = ?
            ORDER BY cg.sort_order, fc.fact_category, fc.fact_name, fc.as_of_date DESC, fc.created_at DESC
            """,
            (self.fund_id,),
        )

        return {
            "fund": fund,
            "characteristics_tree": self._group_characteristics(characteristics),
            "characteristics": characteristics,
            "profile_attributes": many(
                self.connection,
                """
                SELECT attribute_name, attribute_value, normalized_value, effective_date,
                       source_document_id, page_number, approved_fact_id
                FROM fund_profile_attributes
                WHERE fund_id = ?
                ORDER BY attribute_name, effective_date
                """,
                (self.fund_id,),
            ),
            "people": many(
                self.connection,
                """
                SELECT person_name, role_title, bio_summary, source_document_id, page_number
                FROM fund_people
                WHERE fund_id = ?
                ORDER BY person_name
                """,
                (self.fund_id,),
            ),
            "terms": many(
                self.connection,
                "SELECT * FROM fund_terms WHERE fund_id = ? ORDER BY effective_date DESC",
                (self.fund_id,),
            ),
            "strategy": many(
                self.connection,
                "SELECT * FROM fund_strategy WHERE fund_id = ? ORDER BY created_at DESC",
                (self.fund_id,),
            ),
            "returns": many(
                self.connection,
                """
                SELECT pr.return_id, pr.share_class, pr.period_type, pr.period_start_date, pr.period_end_date,
                       pr.return_type, pr.return_value, pr.return_value_bps, pr.raw_value_text, pr.unit, pr.currency,
                       pr.benchmark_name, pr.evidence_text, pr.source_document_id, pr.page_number,
                       pr.approved_fact_id, pr.proposed_row_id, pr.source_import_id,
                       ppr.table_name, ppr.row_label, ppr.column_label
                FROM performance_returns pr
                LEFT JOIN proposed_return_rows ppr
                    ON ppr.proposed_row_id = pr.proposed_row_id
                WHERE pr.fund_id = ?
                ORDER BY pr.period_end_date, pr.period_type, pr.share_class
                """,
                (self.fund_id,),
            ),
            "metrics": many(
                self.connection,
                """
                SELECT metric_name, metric_value, raw_value, normalized_value, unit,
                       as_of_date, source_document_id, page_number
                FROM fund_metrics
                WHERE fund_id = ?
                ORDER BY metric_name, as_of_date DESC
                """,
                (self.fund_id,),
            ),
            "exposures": many(
                self.connection,
                "SELECT * FROM fund_exposures WHERE fund_id = ? ORDER BY as_of_date DESC",
                (self.fund_id,),
            ),
            "writeups": many(
                self.connection,
                """
                SELECT writeup_type, writeup_text, source_document_id, page_number, approved_fact_id
                FROM fund_writeups
                WHERE fund_id = ?
                ORDER BY created_at DESC
                """,
                (self.fund_id,),
            ),
            "flags": many(
                self.connection,
                """
                SELECT flag_type, flag_text, severity, source_document_id, page_number, approved_fact_id
                FROM fund_flags
                WHERE fund_id = ?
                ORDER BY severity, created_at DESC
                """,
                (self.fund_id,),
            ),
            "documents": many(
                self.connection,
                """
                SELECT document_id, document_type, document_title, document_date,
                       file_name, page_count, llm_extraction_status
                FROM source_documents
                WHERE fund_id = ?
                ORDER BY document_date DESC, document_id
                """,
                (self.fund_id,),
            ),
            "campaigns": many(
                self.connection,
                "SELECT * FROM public_campaigns WHERE fund_id = ? ORDER BY campaign_start_date DESC",
                (self.fund_id,),
            ),
        }

    @staticmethod
    def _group_characteristics(characteristics):
        tree = {}
        for row in characteristics:
            group = tree.setdefault(
                row["group_id"],
                {
                    "group_name": row["group_name"],
                    "facts": [],
                },
            )
            group["facts"].append(
                {
                    "fact_category": row["fact_category"],
                    "fact_name": row["fact_name"],
                    "display_name": row["display_name"],
                    "value_text": row["value_text"],
                    "value_number": row["value_number"],
                    "unit": row["unit"],
                    "as_of_date": row["as_of_date"],
                    "source_document_id": row["source_document_id"],
                    "page_number": row["page_number"],
                    "quoted_text": row["quoted_text"],
                    "approved_fact_id": row["approved_fact_id"],
                }
            )
        return tree

    def to_json(self):
        return json.dumps(self.build(), indent=2, ensure_ascii=False)

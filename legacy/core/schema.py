from core.taxonomy import CHARACTERISTIC_DEFINITIONS, CHARACTERISTIC_GROUPS


def create_schema(connection):
    connection.execute("PRAGMA foreign_keys = ON;")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS funds (
            fund_id TEXT PRIMARY KEY,
            fund_name TEXT NOT NULL,
            manager_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'needs_review',
            first_document TEXT,
            document_type TEXT,
            document_date TEXT,
            notes TEXT,
            base_currency TEXT,
            inception_date TEXT,
            primary_strategy TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS schema_migrations (
            migration_id TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS characteristic_groups (
            group_id TEXT PRIMARY KEY,
            parent_group_id TEXT,
            display_name TEXT NOT NULL,
            description TEXT,
            sort_order INTEGER NOT NULL DEFAULT 100,
            FOREIGN KEY (parent_group_id) REFERENCES characteristic_groups (group_id)
        );

        CREATE TABLE IF NOT EXISTS characteristic_definitions (
            group_id TEXT NOT NULL,
            fact_category TEXT NOT NULL,
            fact_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            value_type TEXT NOT NULL,
            unit_hint TEXT,
            clean_table TEXT NOT NULL,
            clean_field TEXT NOT NULL,
            approval_required INTEGER NOT NULL DEFAULT 1,
            source_priority TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            prompt_guidance TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (fact_category, fact_name),
            FOREIGN KEY (group_id) REFERENCES characteristic_groups (group_id)
        );

        CREATE TABLE IF NOT EXISTS source_documents (
            document_id TEXT PRIMARY KEY,
            fund_id TEXT,
            file_name TEXT NOT NULL,
            file_path TEXT,
            original_file_name TEXT,
            file_sha256 TEXT,
            file_size_bytes INTEGER,
            document_type TEXT NOT NULL,
            document_title TEXT NOT NULL,
            document_date TEXT,
            date_precision TEXT,
            source_evidence TEXT,
            review_status TEXT NOT NULL DEFAULT 'needs_review',
            intake_status TEXT NOT NULL DEFAULT 'registered',
            page_ingestion_status TEXT NOT NULL DEFAULT 'pending',
            llm_extraction_status TEXT NOT NULL DEFAULT 'not_started',
            uploaded_by TEXT NOT NULL DEFAULT 'system',
            uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            confidentiality_level TEXT NOT NULL DEFAULT 'internal',
            notes TEXT,
            page_count INTEGER,
            last_scanned_at TEXT,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id)
        );

        CREATE TABLE IF NOT EXISTS document_classification_suggestions (
            suggestion_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            suggested_fund_id TEXT,
            suggested_document_type TEXT,
            suggested_document_title TEXT,
            suggested_document_date TEXT,
            suggested_confidentiality_level TEXT,
            confidence_score REAL,
            evidence_text TEXT,
            suggestion_method TEXT NOT NULL DEFAULT 'manual_or_filename',
            model_name TEXT,
            approval_status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by TEXT,
            reviewed_at TEXT,
            review_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (suggested_fund_id) REFERENCES funds (fund_id)
        );

        CREATE TABLE IF NOT EXISTS document_pages (
            page_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            page_text TEXT,
            extraction_status TEXT NOT NULL DEFAULT 'pending',
            ocr_confidence REAL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (document_id) REFERENCES source_documents (document_id),
            UNIQUE (document_id, page_number)
        );

        CREATE TABLE IF NOT EXISTS document_page_images (
            image_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            image_path TEXT NOT NULL,
            image_width INTEGER,
            image_height INTEGER,
            render_dpi INTEGER NOT NULL DEFAULT 180,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (document_id) REFERENCES source_documents (document_id),
            UNIQUE (document_id, page_number, render_dpi)
        );

        CREATE TABLE IF NOT EXISTS citations (
            citation_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            page_id TEXT,
            page_number INTEGER,
            quoted_text TEXT,
            citation_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (page_id) REFERENCES document_pages (page_id)
        );

        CREATE TABLE IF NOT EXISTS extraction_runs (
            run_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL,
            extraction_scope TEXT NOT NULL DEFAULT 'fund_characteristics',
            extraction_method TEXT NOT NULL,
            model_name TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            run_status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT,
            error_message TEXT,
            raw_output TEXT,
            input_file_id TEXT,
            FOREIGN KEY (document_id) REFERENCES source_documents (document_id)
        );

        CREATE TABLE IF NOT EXISTS extracted_facts (
            fact_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            fund_id TEXT NOT NULL,
            document_id TEXT NOT NULL,
            fact_category TEXT NOT NULL,
            fact_name TEXT NOT NULL,
            raw_value TEXT NOT NULL,
            normalized_value TEXT,
            unit TEXT,
            as_of_date TEXT,
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            quoted_text TEXT,
            citation_id TEXT,
            structured_payload_json TEXT,
            extraction_method TEXT NOT NULL,
            confidence_score REAL,
            approval_status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by TEXT,
            reviewed_at TEXT,
            review_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (run_id) REFERENCES extraction_runs (run_id),
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (fact_category, fact_name) REFERENCES characteristic_definitions (fact_category, fact_name)
        );

        CREATE TABLE IF NOT EXISTS approved_facts (
            approved_fact_id TEXT PRIMARY KEY,
            source_fact_id TEXT,
            fund_id TEXT NOT NULL,
            fact_category TEXT NOT NULL,
            fact_name TEXT NOT NULL,
            approved_value TEXT NOT NULL,
            normalized_value TEXT,
            unit TEXT,
            as_of_date TEXT,
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            quoted_text TEXT,
            citation_id TEXT,
            structured_payload_json TEXT,
            approved_by TEXT NOT NULL,
            approved_at TEXT NOT NULL,
            approval_note TEXT,
            promotion_status TEXT NOT NULL DEFAULT 'pending',
            promoted_to_table TEXT,
            promoted_record_id TEXT,
            promoted_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_fact_id) REFERENCES extracted_facts (fact_id),
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (fact_category, fact_name) REFERENCES characteristic_definitions (fact_category, fact_name)
        );

        CREATE TABLE IF NOT EXISTS fund_characteristics (
            characteristic_id TEXT PRIMARY KEY,
            fund_id TEXT NOT NULL,
            group_id TEXT NOT NULL,
            fact_category TEXT NOT NULL,
            fact_name TEXT NOT NULL,
            display_name TEXT NOT NULL,
            value_text TEXT NOT NULL,
            value_number REAL,
            unit TEXT,
            as_of_date TEXT,
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            quoted_text TEXT,
            approved_fact_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (group_id) REFERENCES characteristic_groups (group_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (approved_fact_id) REFERENCES approved_facts (approved_fact_id),
            FOREIGN KEY (fact_category, fact_name) REFERENCES characteristic_definitions (fact_category, fact_name)
        );

        CREATE TABLE IF NOT EXISTS fund_profile_attributes (
            attribute_id TEXT PRIMARY KEY,
            fund_id TEXT NOT NULL,
            attribute_name TEXT NOT NULL,
            attribute_value TEXT NOT NULL,
            normalized_value TEXT,
            effective_date TEXT,
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            approved_fact_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (approved_fact_id) REFERENCES approved_facts (approved_fact_id)
        );

        CREATE TABLE IF NOT EXISTS fund_people (
            person_id TEXT PRIMARY KEY,
            fund_id TEXT NOT NULL,
            person_name TEXT NOT NULL,
            role_title TEXT,
            bio_summary TEXT,
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            approved_fact_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (approved_fact_id) REFERENCES approved_facts (approved_fact_id)
        );

        CREATE TABLE IF NOT EXISTS fund_terms (
            terms_id TEXT PRIMARY KEY,
            fund_id TEXT NOT NULL,
            share_class TEXT,
            management_fee REAL,
            performance_fee REAL,
            hurdle TEXT,
            high_water_mark TEXT,
            lockup TEXT,
            redemption_frequency TEXT,
            notice_period TEXT,
            gate TEXT,
            minimum_investment TEXT,
            effective_date TEXT,
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            approved_fact_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (approved_fact_id) REFERENCES approved_facts (approved_fact_id)
        );

        CREATE TABLE IF NOT EXISTS fund_strategy (
            strategy_id TEXT PRIMARY KEY,
            fund_id TEXT NOT NULL,
            strategy_primary TEXT,
            strategy_secondary TEXT,
            activism_style TEXT,
            geography_focus TEXT,
            market_cap_focus TEXT,
            exposure_notes TEXT,
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            approved_fact_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (approved_fact_id) REFERENCES approved_facts (approved_fact_id)
        );

        CREATE TABLE IF NOT EXISTS performance_returns (
            return_id TEXT PRIMARY KEY,
            fund_id TEXT NOT NULL,
            share_class TEXT,
            period_type TEXT NOT NULL,
            period_start_date TEXT,
            period_end_date TEXT NOT NULL,
            return_type TEXT NOT NULL DEFAULT 'unknown',
            return_value REAL NOT NULL,
            return_value_bps INTEGER,
            raw_value_text TEXT,
            unit TEXT,
            currency TEXT,
            benchmark_name TEXT,
            evidence_text TEXT,
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            approved_fact_id TEXT NOT NULL,
            proposed_row_id TEXT,
            source_import_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (approved_fact_id) REFERENCES approved_facts (approved_fact_id),
            FOREIGN KEY (source_import_id) REFERENCES data_imports (import_id)
        );

        CREATE TABLE IF NOT EXISTS benchmark_returns (
            benchmark_return_id TEXT PRIMARY KEY,
            benchmark_name TEXT NOT NULL,
            benchmark_ticker TEXT,
            period_type TEXT NOT NULL,
            period_start_date TEXT,
            period_end_date TEXT NOT NULL,
            return_value REAL NOT NULL,
            return_value_bps INTEGER,
            raw_value_text TEXT,
            unit TEXT NOT NULL DEFAULT 'percent',
            currency TEXT,
            evidence_text TEXT,
            source_document_id TEXT,
            external_source_id TEXT,
            source_import_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (external_source_id) REFERENCES external_sources (external_source_id),
            FOREIGN KEY (source_import_id) REFERENCES data_imports (import_id)
        );

        CREATE TABLE IF NOT EXISTS fund_metrics (
            metric_id TEXT PRIMARY KEY,
            fund_id TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            metric_value REAL,
            raw_value TEXT NOT NULL,
            normalized_value TEXT,
            unit TEXT,
            as_of_date TEXT,
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            approved_fact_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (approved_fact_id) REFERENCES approved_facts (approved_fact_id)
        );

        CREATE TABLE IF NOT EXISTS fund_exposures (
            exposure_id TEXT PRIMARY KEY,
            fund_id TEXT NOT NULL,
            exposure_name TEXT NOT NULL,
            exposure_value REAL,
            raw_value TEXT NOT NULL,
            normalized_value TEXT,
            unit TEXT,
            as_of_date TEXT,
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            approved_fact_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (approved_fact_id) REFERENCES approved_facts (approved_fact_id)
        );

        CREATE TABLE IF NOT EXISTS fund_writeups (
            writeup_id TEXT PRIMARY KEY,
            fund_id TEXT NOT NULL,
            writeup_type TEXT NOT NULL,
            writeup_text TEXT NOT NULL,
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            approved_fact_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (approved_fact_id) REFERENCES approved_facts (approved_fact_id)
        );

        CREATE TABLE IF NOT EXISTS fund_flags (
            flag_id TEXT PRIMARY KEY,
            fund_id TEXT NOT NULL,
            flag_type TEXT NOT NULL,
            flag_text TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'review',
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            approved_fact_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (approved_fact_id) REFERENCES approved_facts (approved_fact_id)
        );

        CREATE TABLE IF NOT EXISTS external_sources (
            external_source_id TEXT PRIMARY KEY,
            fund_id TEXT,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT,
            publisher TEXT,
            publication_date TEXT,
            retrieval_date TEXT NOT NULL,
            source_quality TEXT NOT NULL DEFAULT 'needs_review',
            raw_text TEXT,
            notes TEXT,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id)
        );

        CREATE TABLE IF NOT EXISTS data_imports (
            import_id TEXT PRIMARY KEY,
            import_type TEXT NOT NULL,
            fund_id TEXT,
            source_document_id TEXT,
            file_name TEXT NOT NULL,
            import_status TEXT NOT NULL DEFAULT 'staged',
            row_count INTEGER NOT NULL DEFAULT 0,
            imported_by TEXT NOT NULL DEFAULT 'system',
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            notes TEXT,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id)
        );

        CREATE TABLE IF NOT EXISTS proposed_return_rows (
            proposed_row_id TEXT PRIMARY KEY,
            legacy_fact_id TEXT UNIQUE,
            run_id TEXT,
            fund_id TEXT NOT NULL,
            source_document_id TEXT NOT NULL,
            page_number INTEGER,
            share_class TEXT,
            period_type TEXT NOT NULL,
            period_start_date TEXT,
            period_end_date TEXT NOT NULL,
            return_type TEXT NOT NULL DEFAULT 'unknown',
            return_value REAL NOT NULL,
            return_value_bps INTEGER,
            raw_value_text TEXT NOT NULL,
            unit TEXT NOT NULL DEFAULT 'percent',
            currency TEXT,
            benchmark_name TEXT,
            evidence_text TEXT,
            table_name TEXT,
            row_label TEXT,
            column_label TEXT,
            source_image_id TEXT,
            source_import_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_by TEXT,
            reviewed_at TEXT,
            review_note TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (legacy_fact_id) REFERENCES extracted_facts (fact_id),
            FOREIGN KEY (run_id) REFERENCES extraction_runs (run_id),
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (source_image_id) REFERENCES document_page_images (image_id),
            FOREIGN KEY (source_import_id) REFERENCES data_imports (import_id)
        );

        CREATE TABLE IF NOT EXISTS fact_conflicts (
            conflict_id TEXT PRIMARY KEY,
            fund_id TEXT NOT NULL,
            fact_category TEXT NOT NULL,
            fact_name TEXT NOT NULL,
            first_approved_fact_id TEXT NOT NULL,
            second_approved_fact_id TEXT NOT NULL,
            first_value TEXT NOT NULL,
            second_value TEXT NOT NULL,
            conflict_status TEXT NOT NULL DEFAULT 'open',
            resolution_note TEXT,
            resolved_by TEXT,
            resolved_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (first_approved_fact_id) REFERENCES approved_facts (approved_fact_id),
            FOREIGN KEY (second_approved_fact_id) REFERENCES approved_facts (approved_fact_id)
        );

        CREATE TABLE IF NOT EXISTS public_campaigns (
            campaign_id TEXT PRIMARY KEY,
            fund_id TEXT NOT NULL,
            target_company_name TEXT NOT NULL,
            target_ticker TEXT,
            campaign_start_date TEXT,
            campaign_status TEXT DEFAULT 'needs_review',
            campaign_type TEXT,
            objective_summary TEXT,
            primary_source_url TEXT,
            source_document_id TEXT,
            external_source_id TEXT,
            approval_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (external_source_id) REFERENCES external_sources (external_source_id)
        );

        CREATE TABLE IF NOT EXISTS campaign_events (
            event_id TEXT PRIMARY KEY,
            campaign_id TEXT NOT NULL,
            event_date TEXT NOT NULL,
            event_type TEXT NOT NULL,
            event_summary TEXT NOT NULL,
            source_url TEXT,
            filing_reference TEXT,
            source_document_id TEXT,
            external_source_id TEXT,
            approval_status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (campaign_id) REFERENCES public_campaigns (campaign_id),
            FOREIGN KEY (source_document_id) REFERENCES source_documents (document_id),
            FOREIGN KEY (external_source_id) REFERENCES external_sources (external_source_id)
        );

        CREATE TABLE IF NOT EXISTS analysis_runs (
            analysis_id TEXT PRIMARY KEY,
            analysis_type TEXT NOT NULL,
            fund_id TEXT,
            comparison_fund_ids TEXT,
            prompt_version TEXT,
            model_name TEXT,
            input_snapshot_json TEXT,
            external_context_json TEXT,
            output_text TEXT,
            output_json TEXT,
            run_status TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'system',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            error_message TEXT,
            FOREIGN KEY (fund_id) REFERENCES funds (fund_id)
        );

        CREATE TABLE IF NOT EXISTS change_history (
            change_id TEXT PRIMARY KEY,
            table_name TEXT NOT NULL,
            record_id TEXT NOT NULL,
            field_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_by TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            change_reason TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_source_documents_fund ON source_documents (fund_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_source_documents_sha_unique
        ON source_documents (file_sha256)
        WHERE file_sha256 IS NOT NULL;
        CREATE INDEX IF NOT EXISTS idx_document_pages_document ON document_pages (document_id, page_number);
        CREATE INDEX IF NOT EXISTS idx_document_page_images_document ON document_page_images (document_id, page_number);
        CREATE INDEX IF NOT EXISTS idx_document_classification_status ON document_classification_suggestions (approval_status, document_id);
        CREATE INDEX IF NOT EXISTS idx_extracted_facts_review ON extracted_facts (approval_status, source_document_id);
        CREATE INDEX IF NOT EXISTS idx_approved_facts_promotion ON approved_facts (promotion_status, approved_at);
        CREATE INDEX IF NOT EXISTS idx_fact_conflicts_status ON fact_conflicts (conflict_status, fund_id, fact_category, fact_name);
        CREATE INDEX IF NOT EXISTS idx_characteristic_defs_group ON characteristic_definitions (group_id, fact_category);
        CREATE INDEX IF NOT EXISTS idx_fund_characteristics_tree ON fund_characteristics (fund_id, group_id, fact_category, fact_name, as_of_date);
        CREATE INDEX IF NOT EXISTS idx_fund_characteristics_source ON fund_characteristics (source_document_id, page_number);
        CREATE INDEX IF NOT EXISTS idx_metrics_lookup ON fund_metrics (fund_id, metric_name, as_of_date);
        CREATE INDEX IF NOT EXISTS idx_returns_lookup ON performance_returns (fund_id, period_type, period_end_date);
        CREATE INDEX IF NOT EXISTS idx_proposed_return_rows_review ON proposed_return_rows (status, source_document_id, page_number);
        CREATE INDEX IF NOT EXISTS idx_proposed_return_rows_period ON proposed_return_rows (fund_id, period_type, period_end_date, share_class);
        CREATE INDEX IF NOT EXISTS idx_fund_writeups_lookup ON fund_writeups (fund_id, writeup_type, created_at);
        CREATE INDEX IF NOT EXISTS idx_fund_flags_lookup ON fund_flags (fund_id, flag_type, severity);
        CREATE INDEX IF NOT EXISTS idx_benchmark_returns_lookup ON benchmark_returns (benchmark_name, period_type, period_end_date);
        CREATE INDEX IF NOT EXISTS idx_data_imports_lookup ON data_imports (import_type, fund_id, import_status);
        """
    )


def seed_taxonomy(connection):
    connection.executemany(
        """
        INSERT OR IGNORE INTO characteristic_groups (
            group_id,
            parent_group_id,
            display_name,
            description
        )
        VALUES (?, ?, ?, ?)
        """,
        CHARACTERISTIC_GROUPS,
    )
    connection.executemany(
        """
        INSERT OR IGNORE INTO characteristic_definitions (
            group_id,
            fact_category,
            fact_name,
            display_name,
            value_type,
            unit_hint,
            clean_table,
            clean_field,
            approval_required,
            source_priority
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        CHARACTERISTIC_DEFINITIONS,
    )

import re
from pathlib import Path
from uuid import uuid4

from core.config import PDF_ROOT
from core.documents import sha256_file
from core.db import utc_now


def safe_filename(file_name):
    name = Path(file_name).name
    stem = Path(name).stem
    suffix = Path(name).suffix.lower() or ".pdf"
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._")
    return f"{cleaned or 'uploaded_document'}{suffix}"


def next_storage_path(original_file_name, file_hash):
    PDF_ROOT.mkdir(parents=True, exist_ok=True)
    safe_name = safe_filename(original_file_name)
    stem = Path(safe_name).stem
    suffix = Path(safe_name).suffix
    candidate = PDF_ROOT / safe_name
    if not candidate.exists():
        return candidate
    return PDF_ROOT / f"{stem}_{file_hash[:10]}{suffix}"


def duplicate_document(connection, file_hash):
    row = connection.execute(
        """
        SELECT document_id, document_title, file_name, fund_id
        FROM source_documents
        WHERE file_sha256 = ?
        """,
        (file_hash,),
    ).fetchone()
    return dict(row) if row else None


def register_pdf_file(
    connection,
    source_path,
    fund_id,
    document_type,
    document_title,
    document_date=None,
    confidentiality_level="internal",
    uploaded_by="system",
    notes="",
    create_suggestion=True,
):
    source_path = Path(source_path)
    if source_path.suffix.lower() != ".pdf":
        raise ValueError("Only PDF files are supported for document intake.")
    if not source_path.exists():
        raise FileNotFoundError(source_path)

    file_hash = sha256_file(source_path)
    duplicate = duplicate_document(connection, file_hash)
    if duplicate:
        return {
            "status": "duplicate",
            "document_id": duplicate["document_id"],
            "duplicate": duplicate,
        }

    storage_path = next_storage_path(source_path.name, file_hash)
    if source_path.resolve() != storage_path.resolve():
        storage_path.write_bytes(source_path.read_bytes())

    document_id = f"doc_{uuid4().hex[:12]}"
    now = utc_now()
    connection.execute(
        """
        INSERT INTO source_documents (
            document_id,
            fund_id,
            file_name,
            file_path,
            original_file_name,
            file_sha256,
            file_size_bytes,
            document_type,
            document_title,
            document_date,
            date_precision,
            source_evidence,
            review_status,
            intake_status,
            page_ingestion_status,
            llm_extraction_status,
            uploaded_by,
            uploaded_at,
            confidentiality_level,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'registered', 'pending', 'not_started', ?, ?, ?, ?)
        """,
        (
            document_id,
            fund_id,
            storage_path.name,
            str(storage_path.relative_to(PDF_ROOT.parent)),
            source_path.name,
            file_hash,
            storage_path.stat().st_size,
            document_type,
            document_title,
            document_date,
            "day" if document_date else None,
            "Uploaded PDF; metadata requires human review.",
            "needs_review",
            uploaded_by,
            now,
            confidentiality_level,
            notes,
        ),
    )

    if create_suggestion:
        connection.execute(
            """
            INSERT INTO document_classification_suggestions (
                suggestion_id,
                document_id,
                suggested_fund_id,
                suggested_document_type,
                suggested_document_title,
                suggested_document_date,
                suggested_confidentiality_level,
                confidence_score,
                evidence_text,
                suggestion_method,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual_or_filename', ?)
            """,
            (
                f"sugg_{uuid4().hex}",
                document_id,
                fund_id,
                document_type,
                document_title,
                document_date,
                confidentiality_level,
                0.75,
                f"Initial metadata supplied at upload for {source_path.name}.",
                now,
            ),
        )

    return {"status": "registered", "document_id": document_id, "file_name": storage_path.name}


def register_pdf_bytes(connection, file_name, file_bytes, **kwargs):
    PDF_ROOT.mkdir(parents=True, exist_ok=True)
    temp_path = PDF_ROOT / f".upload_{uuid4().hex}_{safe_filename(file_name)}"
    temp_path.write_bytes(file_bytes)
    try:
        return register_pdf_file(connection, temp_path, **kwargs)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def approve_document_metadata(connection, document_id, reviewer, note=""):
    now = utc_now()
    connection.execute(
        """
        UPDATE source_documents
        SET review_status = 'approved',
            uploaded_at = COALESCE(uploaded_at, ?)
        WHERE document_id = ?
        """,
        (now, document_id),
    )
    connection.execute(
        """
        UPDATE document_classification_suggestions
        SET approval_status = 'approved',
            reviewed_by = ?,
            reviewed_at = ?,
            review_note = ?
        WHERE document_id = ?
          AND approval_status = 'pending'
        """,
        (reviewer, now, note, document_id),
    )


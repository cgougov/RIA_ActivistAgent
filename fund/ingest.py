"""Stage 1 of the pipeline: absorb PDFs.

Registers funds and documents, normalizes PDF storage, extracts page text,
and renders page images on demand for vision extraction. No LLM calls happen
here.
"""
import csv
import hashlib
import re
import shutil
from pathlib import Path
from uuid import uuid4

import fitz

from fund.config import PAGE_IMAGE_DIR, PDF_DIR, PROJECT_ROOT, SEED_DIR
from fund.db import utc_now

CURRENT_DOC_TYPES = {"factsheet", "presentation"}


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_fund(connection, fund_id, fund_name, manager_name, notes=None):
    connection.execute(
        """
        INSERT INTO funds (fund_id, fund_name, manager_name, notes, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (fund_id) DO UPDATE SET
            fund_name = excluded.fund_name,
            manager_name = excluded.manager_name
        """,
        (fund_id, fund_name, manager_name, notes, utc_now()),
    )


def slugify(value):
    value = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return value or "fund"


def fund_slug(connection, fund_id):
    row = connection.execute(
        "SELECT fund_name FROM funds WHERE fund_id = ?",
        (fund_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown fund_id: {fund_id}")
    return slugify(row["fund_name"])


def _clean_date(doc_date):
    if not doc_date:
        return "undated"
    return re.sub(r"[^0-9-]", "", str(doc_date)) or "undated"


def normalized_relative_path(connection, fund_id, doc_type, doc_date, original_file_name, doc_id=None):
    slug = fund_slug(connection, fund_id)
    extension = Path(original_file_name).suffix or ".pdf"
    date_token = _clean_date(doc_date)
    base_name = f"{date_token}_{doc_type}_{slug}{extension.lower()}"
    relative = Path("data") / "pdfs" / slug / base_name
    if doc_id is None:
        return relative
    candidate = relative
    if (PROJECT_ROOT / candidate).exists():
        stem = candidate.stem
        candidate = candidate.with_name(f"{stem}_{doc_id}{candidate.suffix}")
    return candidate


def resolve_stored_path(stored_path):
    path = Path(stored_path)
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def document_pdf_path(document):
    stored = document.get("stored_path")
    if stored:
        path = resolve_stored_path(stored)
        if path.exists():
            return path
    legacy = document.get("file_name")
    if legacy:
        legacy_path = PDF_DIR / legacy
        if legacy_path.exists():
            return legacy_path
    raise FileNotFoundError(f"Missing PDF for {document.get('doc_id')}: {stored or legacy}")


def _upsert_current_document_state(connection, fund_id, doc_type, doc_id):
    supersedes_doc_id = None
    if doc_type not in CURRENT_DOC_TYPES:
        return 0, supersedes_doc_id
    current = connection.execute(
        """
        SELECT doc_id
        FROM documents
        WHERE fund_id = ? AND doc_type = ? AND is_current = 1 AND doc_id != ?
        ORDER BY COALESCE(doc_date, '') DESC, created_at DESC, doc_id DESC
        LIMIT 1
        """,
        (fund_id, doc_type, doc_id),
    ).fetchone()
    if current:
        supersedes_doc_id = current["doc_id"]
    connection.execute(
        "UPDATE documents SET is_current = 0 WHERE fund_id = ? AND doc_type = ? AND doc_id != ?",
        (fund_id, doc_type, doc_id),
    )
    return 1, supersedes_doc_id


def _copy_pdf_if_needed(source_path, target_path):
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if source_path.resolve() == target_path.resolve():
        return
    if target_path.exists() and sha256_file(target_path) == sha256_file(source_path):
        return
    shutil.copy2(source_path, target_path)


def register_document(
    connection,
    doc_id,
    fund_id,
    source_path,
    doc_type,
    title,
    doc_date=None,
    *,
    original_file_name=None,
    make_current=True,
):
    source_path = Path(source_path)
    if not source_path.exists():
        raise FileNotFoundError(f"Missing PDF: {source_path}")
    original_file_name = original_file_name or source_path.name
    relative = normalized_relative_path(
        connection,
        fund_id,
        doc_type,
        doc_date,
        original_file_name,
        doc_id=doc_id,
    )
    target_path = PROJECT_ROOT / relative
    _copy_pdf_if_needed(source_path, target_path)
    is_current, supersedes_doc_id = _upsert_current_document_state(
        connection,
        fund_id,
        doc_type,
        doc_id,
    ) if make_current else (0, None)
    connection.execute(
        """
        INSERT INTO documents (
            doc_id, fund_id, file_name, original_file_name, stored_path, sha256,
            doc_type, title, doc_date, is_current, supersedes_doc_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (doc_id) DO UPDATE SET
            fund_id = excluded.fund_id,
            file_name = excluded.file_name,
            original_file_name = excluded.original_file_name,
            stored_path = excluded.stored_path,
            sha256 = excluded.sha256,
            doc_type = excluded.doc_type,
            title = excluded.title,
            doc_date = excluded.doc_date,
            is_current = excluded.is_current,
            supersedes_doc_id = COALESCE(excluded.supersedes_doc_id, documents.supersedes_doc_id)
        """,
        (
            doc_id,
            fund_id,
            target_path.name,
            original_file_name,
            str(relative),
            sha256_file(target_path),
            doc_type,
            title,
            doc_date,
            is_current,
            supersedes_doc_id,
            utc_now(),
        ),
    )
    return {
        "doc_id": doc_id,
        "stored_path": str(relative),
        "is_current": bool(is_current),
        "supersedes_doc_id": supersedes_doc_id,
    }


def add_document(connection, fund_id, doc_type, doc_date, source_path, title=None):
    doc_id = f"doc_{uuid4().hex[:8]}"
    source_path = Path(source_path)
    title = title or source_path.stem.replace("_", " ")
    meta = register_document(
        connection,
        doc_id,
        fund_id,
        source_path,
        doc_type,
        title,
        doc_date,
        original_file_name=source_path.name,
        make_current=True,
    )
    ingest_result = ingest_pages(connection, doc_id, force=True)
    connection.commit()
    return {
        "doc_id": doc_id,
        "title": title,
        "doc_type": doc_type,
        "doc_date": doc_date,
        "stored_path": meta["stored_path"],
        "is_current": meta["is_current"],
        "supersedes_doc_id": meta["supersedes_doc_id"],
        "pages": ingest_result["pages"],
    }


def ingest_pages(connection, doc_id, force=False):
    """Extract text for every page of a registered document. Idempotent."""
    doc = connection.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    if doc is None:
        raise ValueError(f"Unknown doc_id: {doc_id}")
    existing = connection.execute(
        "SELECT COUNT(*) FROM pages WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()[0]
    if existing and not force:
        return {"doc_id": doc_id, "status": "already_ingested", "pages": existing}
    if force:
        connection.execute("DELETE FROM pages WHERE doc_id = ?", (doc_id,))

    pdf = fitz.open(document_pdf_path(dict(doc)))
    for index, page in enumerate(pdf, start=1):
        connection.execute(
            "INSERT INTO pages (doc_id, page_number, text) VALUES (?, ?, ?)",
            (doc_id, index, page.get_text()),
        )
    page_count = len(pdf)
    pdf.close()
    connection.execute(
        "UPDATE documents SET page_count = ? WHERE doc_id = ?",
        (page_count, doc_id),
    )
    return {"doc_id": doc_id, "status": "ingested", "pages": page_count}


def render_page_image(connection, doc_id, page_number, dpi=180):
    """Render one page to PNG for vision extraction. Cached on disk + in pages.image_path."""
    row = connection.execute(
        "SELECT image_path FROM pages WHERE doc_id = ? AND page_number = ?",
        (doc_id, page_number),
    ).fetchone()
    if row is None:
        raise ValueError(f"Page {page_number} of {doc_id} is not ingested.")
    if row["image_path"]:
        existing = PAGE_IMAGE_DIR / row["image_path"]
        if existing.exists():
            return existing

    doc = connection.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    output_dir = PAGE_IMAGE_DIR / doc_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"page_{page_number:03d}.png"

    pdf = fitz.open(document_pdf_path(dict(doc)))
    if page_number < 1 or page_number > len(pdf):
        raise ValueError(f"Page {page_number} out of range for {doc_id}")
    pdf[page_number - 1].get_pixmap(dpi=dpi, alpha=False).save(output_path)
    pdf.close()

    relative = output_path.relative_to(PAGE_IMAGE_DIR)
    connection.execute(
        "UPDATE pages SET image_path = ? WHERE doc_id = ? AND page_number = ?",
        (str(relative), doc_id, page_number),
    )
    return output_path


def prune_page_images(connection, doc_id=None, dry_run=False):
    """Delete cached page images and clear pages.image_path."""
    root = PAGE_IMAGE_DIR / doc_id if doc_id else PAGE_IMAGE_DIR
    removed, freed = 0, 0
    if root.exists():
        for path in root.rglob("*.png"):
            freed += path.stat().st_size
            removed += 1
            if not dry_run:
                path.unlink()
    if not dry_run:
        where, params = "WHERE image_path IS NOT NULL", []
        if doc_id:
            where += " AND doc_id = ?"
            params.append(doc_id)
        connection.execute(f"UPDATE pages SET image_path = NULL {where}", params)
        connection.commit()
        for directory in sorted(PAGE_IMAGE_DIR.glob("*"), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
    return {"images": removed, "bytes": freed}


def organize_existing_documents(connection):
    """Normalize stored PDF paths for documents already in the database."""
    rows = connection.execute(
        """
        SELECT doc_id, fund_id, file_name, original_file_name, stored_path, doc_type, doc_date
        FROM documents
        ORDER BY created_at, doc_id
        """
    ).fetchall()
    moved = []
    for row in rows:
        row = dict(row)
        original_name = row.get("original_file_name") or row["file_name"]
        current_path = None
        if row.get("stored_path"):
            candidate = resolve_stored_path(row["stored_path"])
            if candidate.exists():
                current_path = candidate
        if current_path is None:
            legacy = PDF_DIR / row["file_name"]
            if legacy.exists():
                current_path = legacy
        if current_path is None:
            continue
        relative = normalized_relative_path(
            connection,
            row["fund_id"],
            row["doc_type"],
            row["doc_date"],
            original_name,
            doc_id=row["doc_id"],
        )
        target_path = PROJECT_ROOT / relative
        _copy_pdf_if_needed(current_path, target_path)
        if current_path != target_path and current_path.exists():
            current_path.unlink()
        connection.execute(
            """
            UPDATE documents
            SET file_name = ?, original_file_name = ?, stored_path = ?, sha256 = ?
            WHERE doc_id = ?
            """,
            (
                target_path.name,
                original_name,
                str(relative),
                sha256_file(target_path),
                row["doc_id"],
            ),
        )
        moved.append({"doc_id": row["doc_id"], "stored_path": str(relative)})
    connection.commit()
    return moved


def ingest_from_seeds(connection):
    """Register all funds and documents from data/seed CSVs and ingest every page."""
    results = []
    with (SEED_DIR / "seed_funds.csv").open() as handle:
        for row in csv.DictReader(handle):
            register_fund(
                connection,
                row["fund_id"],
                row["fund_name"],
                row["manager_name"],
                notes=row.get("notes"),
            )
    with (SEED_DIR / "source_documents.csv").open() as handle:
        for row in csv.DictReader(handle):
            doc_date = row.get("document_date")
            if doc_date and not doc_date[:4].isdigit():
                doc_date = None
            source_path = PDF_DIR / row["file_name"]
            register_document(
                connection,
                row["document_id"],
                row["fund_id"],
                source_path,
                row["document_type"],
                row["document_title"],
                doc_date,
                original_file_name=row["file_name"],
                make_current=True,
            )
            results.append(ingest_pages(connection, row["document_id"]))
    connection.commit()
    return results

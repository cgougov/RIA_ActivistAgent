"""Stage 1 of the pipeline: absorb PDFs.

Registers funds and documents (from the seed CSVs or explicit arguments),
extracts page text, and renders page images on demand for vision extraction.
No LLM calls happen here.
"""
import csv
import hashlib

import fitz

from fund.config import PAGE_IMAGE_DIR, PDF_DIR, SEED_DIR
from fund.db import utc_now


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


def register_document(connection, doc_id, fund_id, file_name, doc_type, title, doc_date=None):
    pdf_path = PDF_DIR / file_name
    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing PDF: {pdf_path}")
    connection.execute(
        """
        INSERT INTO documents (doc_id, fund_id, file_name, sha256, doc_type, title, doc_date, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (doc_id) DO NOTHING
        """,
        (doc_id, fund_id, file_name, sha256_file(pdf_path), doc_type, title, doc_date, utc_now()),
    )


def ingest_pages(connection, doc_id, force=False):
    """Extract text for every page of a registered document. Idempotent."""
    doc = connection.execute("SELECT * FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    if doc is None:
        raise ValueError(f"Unknown doc_id: {doc_id}")
    existing = connection.execute(
        "SELECT COUNT(*) FROM pages WHERE doc_id = ?", (doc_id,)
    ).fetchone()[0]
    if existing and not force:
        return {"doc_id": doc_id, "status": "already_ingested", "pages": existing}
    if force:
        connection.execute("DELETE FROM pages WHERE doc_id = ?", (doc_id,))

    pdf = fitz.open(PDF_DIR / doc["file_name"])
    for index, page in enumerate(pdf, start=1):
        connection.execute(
            "INSERT INTO pages (doc_id, page_number, text) VALUES (?, ?, ?)",
            (doc_id, index, page.get_text()),
        )
    page_count = len(pdf)
    pdf.close()
    connection.execute(
        "UPDATE documents SET page_count = ? WHERE doc_id = ?", (page_count, doc_id)
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

    doc = connection.execute("SELECT file_name FROM documents WHERE doc_id = ?", (doc_id,)).fetchone()
    output_dir = PAGE_IMAGE_DIR / doc_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"page_{page_number:03d}.png"

    pdf = fitz.open(PDF_DIR / doc["file_name"])
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
    """Delete cached page images (regenerable on demand by render_page_image) and
    clear their pages.image_path. Sweeps the filesystem so orphaned renders are
    cleaned too. Safe retention: images are re-rendered from the source PDF
    whenever a vision extraction next needs them."""
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


def ingest_from_seeds(connection):
    """Register all funds and documents from data/seed CSVs and ingest every page."""
    results = []
    with (SEED_DIR / "seed_funds.csv").open() as handle:
        for row in csv.DictReader(handle):
            register_fund(connection, row["fund_id"], row["fund_name"], row["manager_name"],
                          notes=row.get("notes"))
    with (SEED_DIR / "source_documents.csv").open() as handle:
        for row in csv.DictReader(handle):
            doc_date = row.get("document_date")
            if doc_date and not doc_date[:4].isdigit():
                doc_date = None  # seeds use 'needs_review' as a placeholder; store as unknown
            register_document(
                connection, row["document_id"], row["fund_id"], row["file_name"],
                row["document_type"], row["document_title"], doc_date,
            )
            results.append(ingest_pages(connection, row["document_id"]))
    connection.commit()
    return results

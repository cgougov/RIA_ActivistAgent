import hashlib
from pathlib import Path
from uuid import uuid4

import fitz
from pypdf import PdfReader

from core.config import PAGE_IMAGE_ROOT, PDF_ROOT
from core.db import utc_now


def find_pdf(file_name):
    direct = PDF_ROOT / file_name
    if direct.exists():
        return direct
    matches = list(PDF_ROOT.rglob(Path(file_name).name))
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"Multiple matching PDFs found for {file_name}")
    raise FileNotFoundError(f"Missing PDF under {PDF_ROOT}: {file_name}")


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ingest_document_pages(connection, document_id, force=False):
    document = connection.execute(
        "SELECT * FROM source_documents WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    if document is None:
        raise ValueError(f"Unknown document_id: {document_id}")
    document = dict(document)

    pdf_path = find_pdf(document["file_name"])
    existing = connection.execute(
        "SELECT COUNT(*) FROM document_pages WHERE document_id = ?",
        (document_id,),
    ).fetchone()[0]
    if existing and not force:
        return {"document_id": document_id, "status": "skipped_existing", "pages": existing}

    if force:
        connection.execute("DELETE FROM document_pages WHERE document_id = ?", (document_id,))

    reader = PdfReader(str(pdf_path))
    page_count = len(reader.pages)
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        connection.execute(
            """
            INSERT INTO document_pages (
                page_id, document_id, page_number, page_text, extraction_status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"page_{uuid4().hex}",
                document_id,
                index,
                text,
                "complete" if text.strip() else "empty",
                utc_now(),
            ),
        )

    connection.execute(
        """
        UPDATE source_documents
        SET file_path = ?,
            original_file_name = COALESCE(original_file_name, file_name),
            file_size_bytes = ?,
            file_sha256 = ?,
            page_count = ?,
            page_ingestion_status = 'complete',
            intake_status = 'ingested',
            last_scanned_at = ?
        WHERE document_id = ?
        """,
        (
            str(pdf_path.relative_to(PDF_ROOT.parent)),
            pdf_path.stat().st_size,
            sha256_file(pdf_path),
            page_count,
            utc_now(),
            document_id,
        ),
    )
    return {"document_id": document_id, "status": "ingested", "pages": page_count}


def render_document_page_image(connection, document_id, page_number, dpi=180, force=False):
    document = connection.execute(
        "SELECT * FROM source_documents WHERE document_id = ?",
        (document_id,),
    ).fetchone()
    if document is None:
        raise ValueError(f"Unknown document_id: {document_id}")
    document = dict(document)

    existing = connection.execute(
        """
        SELECT *
        FROM document_page_images
        WHERE document_id = ?
          AND page_number = ?
          AND render_dpi = ?
        """,
        (document_id, page_number, dpi),
    ).fetchone()
    if existing and not force:
        return dict(existing)

    if existing and force:
        connection.execute(
            """
            DELETE FROM document_page_images
            WHERE document_id = ?
              AND page_number = ?
              AND render_dpi = ?
            """,
            (document_id, page_number, dpi),
        )

    pdf_path = find_pdf(document["file_name"])
    output_dir = PAGE_IMAGE_ROOT / document_id
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"page_{page_number:03d}_{dpi}dpi.png"

    pdf = fitz.open(pdf_path)
    if page_number < 1 or page_number > len(pdf):
        raise ValueError(f"Page {page_number} out of range for {document_id}")
    page = pdf[page_number - 1]
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    pixmap.save(output_path)
    pdf.close()

    image_id = f"img_{uuid4().hex}"
    relative_path = output_path.relative_to(PAGE_IMAGE_ROOT.parent)
    connection.execute(
        """
        INSERT INTO document_page_images (
            image_id, document_id, page_number, image_path, image_width,
            image_height, render_dpi, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            image_id,
            document_id,
            page_number,
            str(relative_path),
            pixmap.width,
            pixmap.height,
            dpi,
            utc_now(),
        ),
    )
    return {
        "image_id": image_id,
        "document_id": document_id,
        "page_number": page_number,
        "image_path": str(relative_path),
        "image_width": pixmap.width,
        "image_height": pixmap.height,
        "render_dpi": dpi,
    }


def render_document_page_images(connection, document_id, pages=None, dpi=180, force=False):
    if pages is None:
        page_rows = connection.execute(
            "SELECT page_number FROM document_pages WHERE document_id = ? ORDER BY page_number",
            (document_id,),
        ).fetchall()
        pages = [row["page_number"] for row in page_rows]
    return [
        render_document_page_image(connection, document_id, int(page), dpi=dpi, force=force)
        for page in pages
    ]

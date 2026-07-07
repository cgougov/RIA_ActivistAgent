import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect_db
from core.documents import render_document_page_images


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("document_id")
    parser.add_argument("--page", action="append", type=int)
    parser.add_argument("--dpi", type=int, default=180)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    with connect_db() as connection:
        images = render_document_page_images(
            connection,
            args.document_id,
            pages=args.page,
            dpi=args.dpi,
            force=args.force,
        )
        connection.commit()

    print(f"Rendered images: {len(images)}")
    for image in images:
        print(f"  page {image['page_number']}: {image['image_path']}")


if __name__ == "__main__":
    main()


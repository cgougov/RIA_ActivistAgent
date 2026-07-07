import re
import sqlite3
from pathlib import Path

from core.config import BACKUP_DIR
from core.db import utc_now


def _slug(value):
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("._-")
    return text or "backup"


def create_sqlite_backup(connection, label="manual", backup_dir=BACKUP_DIR):
    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = utc_now().replace(":", "").replace("-", "").replace("+00:00", "Z")
    backup_path = backup_dir / f"activist_funds_{_slug(label)}_{timestamp}.sqlite"
    backup_connection = sqlite3.connect(backup_path)
    try:
        connection.backup(backup_connection)
    finally:
        backup_connection.close()
    return backup_path

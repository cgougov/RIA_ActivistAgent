import contextlib
import sqlite3
from datetime import datetime, timezone

from core.config import DB_PATH


def utc_now():
    return datetime.now(timezone.utc).isoformat()


@contextlib.contextmanager
def connect_db(db_path=DB_PATH):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    try:
        yield connection
    finally:
        connection.close()


def rows_to_dicts(rows):
    return [dict(row) for row in rows]


def one(connection, sql, params=()):
    row = connection.execute(sql, params).fetchone()
    return dict(row) if row else None


def many(connection, sql, params=()):
    return rows_to_dicts(connection.execute(sql, params).fetchall())


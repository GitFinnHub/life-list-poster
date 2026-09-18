"""SQLite access. One short-lived connection per operation (WAL mode) rather
than a shared long-lived connection - simplest way to be safe across the
worker thread and the FastAPI request handlers without needing a lock."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .paths import DB_PATH

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# Additive schema changes for a database that already exists on a deployed
# disk (CREATE TABLE IF NOT EXISTS in schema.sql only helps a brand-new DB -
# an existing table needs its own ALTER TABLE, or a real migration tool,
# which is overkill for a schema this small).
_MIGRATIONS = [
    ("jobs", "restart_count", "ALTER TABLE jobs ADD COLUMN restart_count INTEGER NOT NULL DEFAULT 0"),
]


def _run_migrations(conn):
    for table, column, ddl in _MIGRATIONS:
        cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in cols:
            conn.execute(ddl)


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _run_migrations(conn)

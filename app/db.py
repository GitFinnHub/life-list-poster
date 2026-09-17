"""SQLite access. One short-lived connection per operation (WAL mode) rather
than a shared long-lived connection - simplest way to be safe across the
worker thread and the FastAPI request handlers without needing a lock."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

APP_DATA_DIR = Path(__file__).resolve().parent.parent / "app_data"
DB_PATH = APP_DATA_DIR / "app.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


@contextmanager
def get_db():
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

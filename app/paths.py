"""Single source of truth for where persistent state lives.

Locally, everything stays exactly where it's always been (project-root-
relative) so nothing changes for local dev or the CLI tool. In production,
set DATA_DIR to wherever the host's persistent disk is mounted (e.g.
/app/state on Render) so cache/, output/, and the SQLite DB all survive
redeploys - a host's disk is normally mounted at a single path, so
everything that needs to persist has to live under one root.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.environ.get("DATA_DIR", str(ROOT)))

APP_DATA_DIR = DATA_ROOT / "app_data"
UPLOAD_TMP_DIR = APP_DATA_DIR / "uploads"
DB_PATH = APP_DATA_DIR / "app.db"

CACHE_DIR = DATA_ROOT / "cache"
PHOTOS_DIR = CACHE_DIR / "photos"
CUTOUTS_DIR = CACHE_DIR / "cutouts"
MANUAL_DIR = CACHE_DIR / "cutouts_manual"

# User-supplied photos - unlike UPLOAD_TMP_DIR (the raw CSV, deleted right
# after parsing), these need to persist indefinitely: candidate_photos rows
# point at them by path for as long as they might get rendered into a poster.
ADMIN_PHOTOS_DIR = CACHE_DIR / "admin_photos"       # shared - becomes everyone's default
VISITOR_PHOTOS_DIR = CACHE_DIR / "visitor_photos"   # private - scoped to one job

OUTPUT_DIR = DATA_ROOT / "output" / "jobs"

for d in (APP_DATA_DIR, UPLOAD_TMP_DIR, PHOTOS_DIR, CUTOUTS_DIR, MANUAL_DIR,
          ADMIN_PHOTOS_DIR, VISITOR_PHOTOS_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

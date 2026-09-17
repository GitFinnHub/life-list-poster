"""Single background worker thread: polls for the oldest queued job and
processes one at a time. rembg is CPU-bound, so one-at-a-time is the
correct concurrency here, not just a cost-saving shortcut.

Reuses the existing CLI pipeline (scripts/lib/*) unmodified for Phase 1:
same shared cache/ directories as the local tool, so species already
resolved by the CLI or by any previous web job are nearly free to reuse.
"""
import datetime
import sys
import threading
import time
import traceback
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from lib import cutouts, photos, poster  # noqa: E402

from . import db  # noqa: E402

CACHE_DIR = ROOT / "cache"
PHOTOS_DIR = CACHE_DIR / "photos"
CUTOUTS_DIR = CACHE_DIR / "cutouts"
MANUAL_DIR = CACHE_DIR / "cutouts_manual"
OUTPUT_DIR = ROOT / "output" / "jobs"
POLL_INTERVAL_S = 2

for d in (PHOTOS_DIR, CUTOUTS_DIR, MANUAL_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)


def _now():
    return datetime.datetime.utcnow().isoformat()


def _reconcile_stuck_jobs():
    """A job left 'running' means the server died mid-job (crash/redeploy) -
    surface that as an error instead of leaving it stuck forever."""
    with db.get_db() as conn:
        conn.execute(
            "UPDATE jobs SET status='error', error_message=?, finished_at=? WHERE status='running'",
            ("Interrupted by a server restart - please try again.", _now()),
        )


def _next_queued_job():
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status='queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def _job_species(job_id):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM job_species WHERE job_id=? ORDER BY taxon_order",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def _update_job(job_id, **fields):
    sets = ", ".join(f"{k}=?" for k in fields)
    with db.get_db() as conn:
        conn.execute(f"UPDATE jobs SET {sets} WHERE id=?", (*fields.values(), job_id))


def _process_job(job):
    job_id = job["id"]
    _update_job(job_id, status="running", started_at=_now(), error_message=None)

    species_rows = _job_species(job_id)
    total = len(species_rows)
    _update_job(job_id, progress_total=total, progress_current=0)

    def log(msg):
        print(f"[{job_id}] {msg}", flush=True)

    credits = {}
    resolved = {}
    for i, sp in enumerate(species_rows):
        _update_job(job_id, progress_current=i, progress_note=sp["common_name"])
        photo_path = photos.fetch_photo(
            sp["scientific_name"], sp["common_name"], PHOTOS_DIR, credits, log
        )
        cutout_path = cutouts.get_cutout(
            sp["species_code"], photo_path, CUTOUTS_DIR, MANUAL_DIR, log
        )
        resolved[sp["species_code"]] = cutout_path

    _update_job(job_id, progress_current=total, progress_note="Laying out the poster...")

    life_df = pd.DataFrame(species_rows)
    n_missing = sum(1 for v in resolved.values() if v is None)
    subtitle = job["subtitle"] or f"{total - n_missing} species observed"

    out_path = OUTPUT_DIR / f"{job_id}.png"
    poster.build_poster(life_df, resolved, job["title"] or "A Life List", subtitle, out_path, log=log)

    credits_path = OUTPUT_DIR / f"{job_id}_credits.txt"
    photos.save_credits(credits, credits_path)

    _update_job(
        job_id,
        status="done",
        output_path=str(out_path),
        credits_path=str(credits_path),
        finished_at=_now(),
    )


def _worker_loop():
    _reconcile_stuck_jobs()
    while True:
        job = _next_queued_job()
        if job is None:
            time.sleep(POLL_INTERVAL_S)
            continue
        try:
            _process_job(job)
        except Exception as e:
            traceback.print_exc()
            _update_job(job["id"], status="error", error_message=str(e), finished_at=_now())


def start_worker():
    t = threading.Thread(target=_worker_loop, daemon=True, name="poster-worker")
    t.start()
    return t

"""Single background worker thread: polls for the oldest job needing work
and processes one at a time. rembg is CPU-bound, so one-at-a-time is the
correct concurrency here, not just a cost-saving shortcut.

Two kinds of work share this one queue/thread:
  - 'queued_candidates': gather each species' candidate photo list (fast,
    no image downloads/rembg) so the picker page can load instantly instead
    of hanging on live iNaturalist lookups for a minute-plus.
  - 'queued': the full render - fetch chosen photos, cut out, lay out,
    save the poster. Reuses the CLI pipeline (scripts/lib/*) unmodified
    apart from the small additive params added for the picker/coolness.
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

from . import coolness, db, photo_library  # noqa: E402
from .paths import CUTOUTS_DIR, MANUAL_DIR, OUTPUT_DIR, PHOTOS_DIR  # noqa: E402

POLL_INTERVAL_S = 2


def _now():
    return datetime.datetime.utcnow().isoformat()


def _reconcile_stuck_jobs():
    """A job left mid-flight means the server died (crash/redeploy) -
    surface that as an error instead of leaving it stuck forever."""
    with db.get_db() as conn:
        conn.execute(
            """UPDATE jobs SET status='error', error_message=?, finished_at=?
               WHERE status IN ('gathering_photos', 'running')""",
            ("Interrupted by a server restart - please try again.", _now()),
        )


def _next_job():
    with db.get_db() as conn:
        row = conn.execute(
            """SELECT * FROM jobs WHERE status IN ('queued_candidates', 'queued')
               ORDER BY created_at ASC LIMIT 1"""
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


def _log(job_id):
    def log(msg):
        print(f"[{job_id}] {msg}", flush=True)
    return log


def _gather_candidates(job):
    """Stage 1: just persist candidate photo lists so the picker is instant."""
    job_id = job["id"]
    _update_job(job_id, status="gathering_photos", started_at=_now(), error_message=None)

    species_rows = _job_species(job_id)
    total = len(species_rows)
    _update_job(job_id, progress_total=total, progress_current=0)
    log = _log(job_id)

    for i, sp in enumerate(species_rows):
        _update_job(job_id, progress_current=i, progress_note=sp["common_name"])
        photo_library.get_or_fetch_candidates(
            sp["species_code"], sp["scientific_name"], sp["common_name"], log
        )

    _update_job(job_id, status="picking", progress_note=None, started_at=None)


def _render(job):
    """Stage 2: the actual poster build, using whatever photo/coolness
    choices were made (or the sensible defaults if the visitor skipped
    customizing)."""
    job_id = job["id"]
    _update_job(job_id, status="running", started_at=_now(), error_message=None)

    species_rows = _job_species(job_id)
    total = len(species_rows)
    _update_job(job_id, progress_total=total, progress_current=0)
    log = _log(job_id)

    credits = {}
    resolved = {}
    for i, sp in enumerate(species_rows):
        _update_job(job_id, progress_current=i, progress_note=sp["common_name"])
        chosen = photo_library.resolve_chosen_photo(job_id, sp["species_code"])
        photo_path = photos.fetch_photo(
            sp["scientific_name"], sp["common_name"], PHOTOS_DIR, credits, log, chosen_photo=chosen
        )
        cache_key = photos.photo_cache_key(sp["scientific_name"], chosen)
        cutout_path = cutouts.get_cutout(
            cache_key, photo_path, CUTOUTS_DIR, MANUAL_DIR, log, manual_code=sp["species_code"]
        )
        resolved[sp["species_code"]] = cutout_path

    _update_job(job_id, progress_current=total, progress_note="Laying out the poster...")

    with db.get_db() as conn:
        size_multipliers = coolness.build_size_multipliers(
            conn, job_id, [sp["species_code"] for sp in species_rows]
        )

    life_df = pd.DataFrame(species_rows)
    n_missing = sum(1 for v in resolved.values() if v is None)
    subtitle = job["subtitle"] or f"{total - n_missing} species observed"

    out_path = OUTPUT_DIR / f"{job_id}.png"
    poster.build_poster(
        life_df, resolved, job["title"] or "A Life List", subtitle, out_path,
        log=log, size_multipliers=size_multipliers,
    )

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
        job = _next_job()
        if job is None:
            time.sleep(POLL_INTERVAL_S)
            continue
        try:
            if job["status"] == "queued_candidates":
                _gather_candidates(job)
            else:
                _render(job)
        except Exception as e:
            traceback.print_exc()
            _update_job(job["id"], status="error", error_message=str(e), finished_at=_now())


def start_worker():
    t = threading.Thread(target=_worker_loop, daemon=True, name="poster-worker")
    t.start()
    return t

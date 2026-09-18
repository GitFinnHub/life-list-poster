"""The shared, improving-over-time photo library: persists iNaturalist's
candidate photo list per species (instead of discarding all but the top
pick, like the plain CLI tool does) so the picker UI can show options, and
so repeat visits don't re-hit the API for species someone already resolved.

Deliberately lives in app/, not scripts/lib/ - it reaches into photos.py's
internal (underscore) helpers on purpose, since this module IS the second,
web-specific caller those helpers were always implicitly meant to support;
scripts/lib/ itself stays free of any dependency on the web app's database.
"""
import datetime
import io
import sys
import uuid
from pathlib import Path

import requests
from PIL import Image, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from lib import photos  # noqa: E402

from . import db  # noqa: E402
from .paths import ADMIN_PHOTOS_DIR, VISITOR_PHOTOS_DIR  # noqa: E402

MAX_CANDIDATES_STORED = 8  # curators' own top N; picker UI shows fewer
UPLOAD_RANK = -1  # sorts before every iNaturalist candidate (rank >= 0) so an
                   # uploaded photo is never truncated out by the picker's limit


def _now():
    return datetime.datetime.utcnow().isoformat()


def _row_to_candidate(row):
    return dict(row)


def _ensure_species(species_code, scientific_name, common_name=None):
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO species (species_code, scientific_name, common_name) VALUES (?,?,?)
               ON CONFLICT(species_code) DO UPDATE SET
                 scientific_name=excluded.scientific_name,
                 common_name=COALESCE(excluded.common_name, species.common_name)""",
            (species_code, scientific_name, common_name),
        )


def get_or_fetch_candidates(species_code, scientific_name, common_name=None, log=print):
    """Returns this species' candidate photos (list of dicts: id,
    inat_photo_id, medium_url, large_url, license_code, attribution,
    curator_rank), checking the DB first and only hitting iNaturalist on a
    cache miss. A species can genuinely have zero candidates (no match, or
    nothing openly licensed) - that's cached same as a real result, tracked
    by presence in the `species` table. A *transient* failure (network
    blip, DNS hiccup) is deliberately NOT cached that way - caching it would
    permanently blacklist the species for every future visitor until
    someone noticed and fixed it by hand, since nothing would ever trigger
    a retry again."""
    with db.get_db() as conn:
        already_checked = conn.execute(
            "SELECT 1 FROM species WHERE species_code=?", (species_code,)
        ).fetchone() is not None
        if already_checked:
            rows = conn.execute(
                "SELECT * FROM candidate_photos WHERE species_code=? ORDER BY curator_rank",
                (species_code,),
            ).fetchall()
            return [_row_to_candidate(r) for r in rows]

    try:
        taxon_id = photos._find_taxon_id(scientific_name)
        raw_candidates = (
            photos._candidate_photos(taxon_id)[:MAX_CANDIDATES_STORED]
            if taxon_id is not None else []
        )
    except requests.RequestException as e:
        log(f"  ! candidate lookup failed for {scientific_name}, will retry later: {e}")
        return []

    now = _now()
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO species (species_code, scientific_name, common_name) VALUES (?,?,?)
               ON CONFLICT(species_code) DO UPDATE SET
                 scientific_name=excluded.scientific_name,
                 common_name=COALESCE(excluded.common_name, species.common_name)""",
            (species_code, scientific_name, common_name),
        )
        for rank, photo in enumerate(raw_candidates):
            conn.execute(
                """INSERT INTO candidate_photos
                   (species_code, inat_photo_id, medium_url, large_url, license_code, attribution, curator_rank, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    species_code,
                    photo["id"],
                    photos._medium_url(photo),
                    photos._large_url(photo),
                    photo.get("license_code"),
                    photo.get("attribution"),
                    rank,
                    now,
                ),
            )
        rows = conn.execute(
            "SELECT * FROM candidate_photos WHERE species_code=? ORDER BY curator_rank",
            (species_code,),
        ).fetchall()
    return [_row_to_candidate(r) for r in rows]


def thumb_url(candidate):
    """Browser-facing URL for the picker grid's thumbnail. An uploaded
    photo has no iNaturalist URL to derive a thumbnail from - it's served
    straight from disk via the /photo/{id} route instead."""
    if candidate.get("source") in ("admin_upload", "visitor_upload"):
        return f"/photo/{candidate['id']}"
    return photos._size_url(candidate["medium_url"] or candidate["large_url"], "square")


def get_candidates_for_job(species_code, job_id):
    """All public candidates (iNaturalist results + admin uploads) plus
    this one job's own private visitor uploads - never another job's
    private uploads. UPLOAD_RANK sorts uploads first, so they're never
    truncated out by the picker's display limit."""
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM candidate_photos
               WHERE species_code=?
                 AND (source != 'visitor_upload' OR uploaded_for_job_id = ?)
               ORDER BY curator_rank""",
            (species_code, job_id),
        ).fetchall()
        return [_row_to_candidate(r) for r in rows]


def get_candidate(candidate_id):
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM candidate_photos WHERE id=?", (candidate_id,)).fetchone()
        return dict(row) if row else None


class InvalidImageError(ValueError):
    pass


def _normalize_image(file_bytes, max_dim=1600):
    """Validate it's actually a real image, strip EXIF/format quirks by
    re-encoding as plain JPEG, and cap resolution - an uploaded photo goes
    through the exact same rembg/cutout step as any other, so there's no
    reason to keep it any bigger than that step ever uses."""
    try:
        img = Image.open(io.BytesIO(file_bytes))
        img.verify()
        img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError) as e:
        raise InvalidImageError("That doesn't look like a valid image file.") from e
    if max(img.size) > max_dim:
        img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def _save_upload_file(dest_dir, stem, file_bytes):
    normalized = _normalize_image(file_bytes)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}.jpg"
    dest.write_bytes(normalized)
    return dest


def save_admin_upload(species_code, scientific_name, common_name, file_bytes):
    """The site owner's own photo for a species - becomes the shared
    default for everyone from now on, tagged as coming from them."""
    _ensure_species(species_code, scientific_name, common_name)
    dest = _save_upload_file(ADMIN_PHOTOS_DIR / species_code, uuid.uuid4().hex, file_bytes)

    now = _now()
    with db.get_db() as conn:
        cur = conn.execute(
            """INSERT INTO candidate_photos
               (species_code, curator_rank, fetched_at, source, local_path, attribution)
               VALUES (?, ?, ?, 'admin_upload', ?, 'Photo by Finn')""",
            (species_code, UPLOAD_RANK, now, str(dest)),
        )
        candidate_id = cur.lastrowid
        conn.execute(
            """INSERT INTO species_photo_default (species_code, candidate_photo_id, set_by, set_at)
               VALUES (?,?,?,?)
               ON CONFLICT(species_code) DO UPDATE SET
                 candidate_photo_id=excluded.candidate_photo_id,
                 set_by=excluded.set_by,
                 set_at=excluded.set_at""",
            (species_code, candidate_id, "Finn", now),
        )
    return candidate_id


def save_visitor_upload(job_id, species_code, scientific_name, common_name, visitor_name, file_bytes):
    """A visitor's own photo for a species - used only for their own
    poster, never becomes anyone else's default."""
    _ensure_species(species_code, scientific_name, common_name)
    dest = _save_upload_file(VISITOR_PHOTOS_DIR / job_id, species_code, file_bytes)

    now = _now()
    attribution = f"Uploaded by {visitor_name}" if visitor_name else "Uploaded photo"
    with db.get_db() as conn:
        cur = conn.execute(
            """INSERT INTO candidate_photos
               (species_code, curator_rank, fetched_at, source, local_path, uploaded_for_job_id, attribution)
               VALUES (?, ?, ?, 'visitor_upload', ?, ?, ?)""",
            (species_code, UPLOAD_RANK, now, str(dest), job_id, attribution),
        )
        candidate_id = cur.lastrowid
        conn.execute(
            """INSERT INTO poster_photo_choices (job_id, species_code, candidate_photo_id)
               VALUES (?,?,?)
               ON CONFLICT(job_id, species_code) DO UPDATE SET
                 candidate_photo_id=excluded.candidate_photo_id""",
            (job_id, species_code, candidate_id),
        )
    return candidate_id


def set_default(species_code, candidate_photo_id, set_by):
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO species_photo_default (species_code, candidate_photo_id, set_by, set_at)
               VALUES (?,?,?,?)
               ON CONFLICT(species_code) DO UPDATE SET
                 candidate_photo_id=excluded.candidate_photo_id,
                 set_by=excluded.set_by,
                 set_at=excluded.set_at""",
            (species_code, candidate_photo_id, set_by, _now()),
        )


def record_job_choice(job_id, species_code, candidate_photo_id):
    with db.get_db() as conn:
        conn.execute(
            """INSERT INTO poster_photo_choices (job_id, species_code, candidate_photo_id)
               VALUES (?,?,?)
               ON CONFLICT(job_id, species_code) DO UPDATE SET
                 candidate_photo_id=excluded.candidate_photo_id""",
            (job_id, species_code, candidate_photo_id),
        )


def get_default_candidate_id(species_code):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT candidate_photo_id FROM species_photo_default WHERE species_code=?",
            (species_code,),
        ).fetchone()
        return row["candidate_photo_id"] if row else None


def resolve_chosen_photo(job_id, species_code):
    """What photo this specific job should actually render with: this
    job's own explicit pick if it made one, else the shared library
    default, else None (fetch_photo will fall back to its own top-pick
    lookup - e.g. a visitor who skipped the picker for this species)."""
    with db.get_db() as conn:
        row = conn.execute(
            """SELECT cp.* FROM poster_photo_choices ppc
               JOIN candidate_photos cp ON cp.id = ppc.candidate_photo_id
               WHERE ppc.job_id=? AND ppc.species_code=?""",
            (job_id, species_code),
        ).fetchone()
        if row:
            return dict(row)
        row = conn.execute(
            """SELECT cp.* FROM species_photo_default spd
               JOIN candidate_photos cp ON cp.id = spd.candidate_photo_id
               WHERE spd.species_code=?""",
            (species_code,),
        ).fetchone()
        return dict(row) if row else None

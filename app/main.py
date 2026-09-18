"""Upload -> gather candidate photos -> customize (pick photos + nudge
coolness, both optional) -> render -> download. Background worker (see
worker.py) does the actual pipeline work; this module is just routing and
DB reads/writes for the web-facing flow.

Everything's behind one shared password (session cookie) - this is a
private site for friends/family, not a public signup product, so a single
password gate is the right amount of access control for now rather than
building real accounts.
"""
import datetime
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

sys.path.insert(0, str(ROOT / "scripts"))
from lib import life_list  # noqa: E402

from . import db, photo_library  # noqa: E402
from .worker import start_worker  # noqa: E402

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
UPLOAD_TMP_DIR = ROOT / "app_data" / "uploads"
TAXONOMY_PATH = ROOT / "data" / "ebird_taxonomy.csv"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # an eBird export is a few hundred KB at most
PICKER_CANDIDATE_LIMIT = 4
PUBLIC_PATHS = {"/login"}

SITE_PASSWORD = os.environ.get("SITE_PASSWORD")
SESSION_SECRET = os.environ.get("SESSION_SECRET")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
_taxonomy_df = None  # loaded once at startup, reused for every upload


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _taxonomy_df
    if not SITE_PASSWORD or not SESSION_SECRET:
        raise RuntimeError(
            "SITE_PASSWORD and SESSION_SECRET must be set (see .env.example) "
            "before starting the server."
        )
    db.init_db()
    _taxonomy_df = life_list.load_taxonomy(TAXONOMY_PATH)
    start_worker()
    yield


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def require_login(request: Request, call_next):
    if request.url.path not in PUBLIC_PATHS and not request.session.get("authed"):
        return RedirectResponse(f"/login?next={request.url.path}", status_code=303)
    return await call_next(request)


# Registered after require_login so it ends up OUTER in the middleware
# stack (Starlette wraps later-added middleware around earlier-added ones)
# and actually runs first, populating request.session before our auth
# check reads it - added the other way around, request.session isn't
# available yet and every request 500s.
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET or "dev-only-placeholder",
                    same_site="lax", max_age=60 * 60 * 24 * 30)


@app.get("/login", response_class=HTMLResponse)
async def login_form(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": None})


@app.post("/login")
async def login_submit(request: Request, password: str = Form(...), next: str = Form("/")):
    if password != SITE_PASSWORD:
        return templates.TemplateResponse(
            request, "login.html", {"next": next, "error": "Wrong password."}, status_code=401
        )
    request.session["authed"] = True
    return RedirectResponse(next or "/", status_code=303)


@app.get("/", response_class=HTMLResponse)
async def upload_form(request: Request):
    return templates.TemplateResponse(request, "upload.html", {})


@app.post("/jobs")
async def create_job(
    visitor_name: str = Form(...),
    title: str = Form("A Life List"),
    csv_file: UploadFile = File(...),
):
    if not csv_file.filename or not csv_file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Please upload a .csv file.")

    content = await csv_file.read()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "That file is larger than expected for an eBird export.")

    UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    job_id = str(uuid.uuid4())
    tmp_csv = UPLOAD_TMP_DIR / f"{job_id}.csv"
    tmp_csv.write_bytes(content)

    try:
        life_df, _unmatched = life_list.load_life_list(tmp_csv, _taxonomy_df)
    except Exception:
        raise HTTPException(
            400,
            "Couldn't read that as an eBird export. Make sure it's the CSV from "
            "ebird.org -> My eBird -> Download My Data.",
        )
    finally:
        tmp_csv.unlink(missing_ok=True)  # done with the raw personal sighting data

    if life_df.empty:
        raise HTTPException(400, "No species could be matched in that file.")

    now = datetime.datetime.utcnow().isoformat()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO jobs (id, status, visitor_name, title, created_at) VALUES (?,?,?,?,?)",
            (job_id, "queued_candidates", visitor_name, title, now),
        )
        conn.executemany(
            """INSERT INTO job_species
               (job_id, species_code, common_name, scientific_name, family_common, family_sci, taxon_order)
               VALUES (?,?,?,?,?,?,?)""",
            [
                (
                    job_id,
                    row["species_code"],
                    row["common_name"],
                    row["scientific_name"],
                    row["family_common"],
                    row["family_sci"],
                    row["taxon_order"],
                )
                for row in life_df.to_dict("records")
            ],
        )

    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_status_page(request: Request, job_id: str):
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    return templates.TemplateResponse(request, "status.html", {"job": job})


@app.get("/jobs/{job_id}/status")
async def job_status_json(job_id: str):
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job["status"] in ("queued_candidates", "queued"):
        with db.get_db() as conn:
            job["queue_position"] = conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE status=? AND created_at < ?",
                (job["status"], job["created_at"]),
            ).fetchone()["c"]
    else:
        job["queue_position"] = 0
    return job


@app.get("/jobs/{job_id}/customize", response_class=HTMLResponse)
async def customize_page(request: Request, job_id: str):
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job["status"] != "picking":
        return RedirectResponse(f"/jobs/{job_id}")

    with db.get_db() as conn:
        baselines = {
            r["species_code"]: r["score"]
            for r in conn.execute("SELECT species_code, score FROM coolness_baseline").fetchall()
        }

    species_view = []
    for sp in _job_species(job_id):
        code = sp["species_code"]
        candidates = photo_library.get_or_fetch_candidates(
            code, sp["scientific_name"], sp["common_name"]
        )[:PICKER_CANDIDATE_LIMIT]
        for c in candidates:
            c["thumb_url"] = photo_library.square_url(c)
        default_id = photo_library.get_default_candidate_id(code)
        if default_id is None and candidates:
            default_id = candidates[0]["id"]
        species_view.append({
            **sp,
            "candidates": candidates,
            "default_candidate_id": default_id,
            "baseline_score": baselines.get(code, 50),
        })

    return templates.TemplateResponse(request, "customize.html", {"job": job, "species": species_view})


@app.post("/jobs/{job_id}/customize")
async def customize_submit(request: Request, job_id: str):
    job = _get_job(job_id)
    if job is None:
        raise HTTPException(404, "Job not found.")
    if job["status"] != "picking":
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    form = await request.form()
    for sp in _job_species(job_id):
        code = sp["species_code"]

        photo_val = form.get(f"photo_{code}")
        if photo_val:
            candidate_id = int(photo_val)
            photo_library.set_default(code, candidate_id, job["visitor_name"])
            photo_library.record_job_choice(job_id, code, candidate_id)

        cool_val = form.get(f"cool_{code}")
        if cool_val and int(cool_val) != 0:
            with db.get_db() as conn:
                conn.execute(
                    """INSERT INTO coolness_override (job_id, species_code, delta) VALUES (?,?,?)
                       ON CONFLICT(job_id, species_code) DO UPDATE SET delta=excluded.delta""",
                    (job_id, code, int(cool_val)),
                )

    with db.get_db() as conn:
        conn.execute("UPDATE jobs SET status='queued' WHERE id=?", (job_id,))

    return RedirectResponse(f"/jobs/{job_id}", status_code=303)


@app.get("/jobs/{job_id}/poster.png")
async def job_poster(job_id: str):
    job = _get_job(job_id)
    if job is None or job["status"] != "done" or not job["output_path"]:
        raise HTTPException(404, "Poster not ready.")
    return FileResponse(job["output_path"], media_type="image/png")


@app.get("/jobs/{job_id}/credits.txt")
async def job_credits(job_id: str):
    job = _get_job(job_id)
    if job is None or job["status"] != "done" or not job["credits_path"]:
        raise HTTPException(404, "Not ready.")
    return FileResponse(job["credits_path"], media_type="text/plain")


@app.get("/admin/coolness", response_class=HTMLResponse)
async def admin_coolness_page(request: Request):
    # NOTE: unprotected for now - Phase 4 adds the owner-only access gate.
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT s.species_code, s.common_name, s.scientific_name,
                      COALESCE(cb.score, 50) AS score
               FROM species s
               LEFT JOIN coolness_baseline cb ON cb.species_code = s.species_code
               ORDER BY s.common_name"""
        ).fetchall()
    return templates.TemplateResponse(request, "admin_coolness.html", {"species": [dict(r) for r in rows]})


@app.post("/admin/coolness")
async def admin_coolness_submit(request: Request):
    form = await request.form()
    now = datetime.datetime.utcnow().isoformat()
    with db.get_db() as conn:
        for key, value in form.items():
            if not key.startswith("score_"):
                continue
            code = key[len("score_"):]
            try:
                score = max(0, min(100, int(value)))
            except ValueError:
                continue
            conn.execute(
                """INSERT INTO coolness_baseline (species_code, score, updated_at) VALUES (?,?,?)
                   ON CONFLICT(species_code) DO UPDATE SET score=excluded.score, updated_at=excluded.updated_at""",
                (code, score, now),
            )
    return RedirectResponse("/admin/coolness", status_code=303)


def _get_job(job_id):
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None


def _job_species(job_id):
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM job_species WHERE job_id=? ORDER BY taxon_order",
            (job_id,),
        ).fetchall()
        return [dict(r) for r in rows]

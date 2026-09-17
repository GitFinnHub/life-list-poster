"""Phase 1: upload -> queue -> background worker runs the existing CLI
pipeline unmodified -> poll for status -> download. No photo picker or
coolness ranking yet (Phase 2/3); this proves the web/job plumbing works
end to end against the exact same pipeline the CLI tool already uses.
"""
import datetime
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from lib import life_list  # noqa: E402

from . import db  # noqa: E402
from .worker import start_worker  # noqa: E402

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
UPLOAD_TMP_DIR = ROOT / "app_data" / "uploads"
TAXONOMY_PATH = ROOT / "data" / "ebird_taxonomy.csv"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # an eBird export is a few hundred KB at most

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
_taxonomy_df = None  # loaded once at startup, reused for every upload


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _taxonomy_df
    db.init_db()
    _taxonomy_df = life_list.load_taxonomy(TAXONOMY_PATH)
    start_worker()
    yield


app = FastAPI(lifespan=lifespan)


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
            (job_id, "queued", visitor_name, title, now),
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
    if job["status"] == "queued":
        with db.get_db() as conn:
            job["queue_position"] = conn.execute(
                "SELECT COUNT(*) c FROM jobs WHERE status='queued' AND created_at < ?",
                (job["created_at"],),
            ).fetchone()["c"]
    else:
        job["queue_position"] = 0
    return job


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


def _get_job(job_id):
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

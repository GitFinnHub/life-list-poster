-- Full schema for the life-list-poster website (see plans/elegant-jumping-ritchie.md).
-- Phase 1 only wires up `jobs` and `job_species`; the rest are created now
-- since the design is settled, but stay unused until Phase 2/3.

CREATE TABLE IF NOT EXISTS species (
    species_code TEXT PRIMARY KEY,
    scientific_name TEXT,
    common_name TEXT,
    family_common TEXT,
    family_sci TEXT,
    family_code TEXT,
    "order" TEXT,
    taxon_order REAL
);

CREATE TABLE IF NOT EXISTS candidate_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    species_code TEXT REFERENCES species(species_code),
    inat_photo_id INTEGER,
    medium_url TEXT,
    large_url TEXT,
    license_code TEXT,
    attribution TEXT,
    curator_rank INTEGER,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS species_photo_default (
    species_code TEXT PRIMARY KEY REFERENCES species(species_code),
    candidate_photo_id INTEGER REFERENCES candidate_photos(id),
    set_by TEXT,
    set_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'draft',   -- draft|queued|running|done|error
    visitor_name TEXT,
    title TEXT,
    subtitle TEXT,
    output_path TEXT,
    credits_path TEXT,
    error_message TEXT,
    progress_current INTEGER DEFAULT 0,
    progress_total INTEGER DEFAULT 0,
    progress_note TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS job_species (
    job_id TEXT REFERENCES jobs(id),
    species_code TEXT,
    common_name TEXT,
    scientific_name TEXT,
    family_common TEXT,
    family_sci TEXT,
    taxon_order REAL,
    PRIMARY KEY (job_id, species_code)
);

CREATE TABLE IF NOT EXISTS poster_photo_choices (
    job_id TEXT REFERENCES jobs(id),
    species_code TEXT,
    candidate_photo_id INTEGER REFERENCES candidate_photos(id),
    PRIMARY KEY (job_id, species_code)
);

CREATE TABLE IF NOT EXISTS coolness_baseline (
    species_code TEXT PRIMARY KEY REFERENCES species(species_code),
    score INTEGER,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS coolness_override (
    job_id TEXT REFERENCES jobs(id),
    species_code TEXT,
    delta INTEGER,
    PRIMARY KEY (job_id, species_code)
);

CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
CREATE INDEX IF NOT EXISTS idx_candidate_photos_species ON candidate_photos(species_code);

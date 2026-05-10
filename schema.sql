-- Single source of truth for jobs.db.
-- Edit this file to declare schema changes; `python db.py migrate` reconciles
-- the live DB toward this state. Comments and ordering are preserved here only;
-- SQLite stores the canonical form in sqlite_master after parsing.

CREATE TABLE jobs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Provenance.
    source          TEXT NOT NULL,
    source_id       TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    posted_at       TEXT,
    url             TEXT,

    -- Parsed-from-text fields.
    company         TEXT,
    title           TEXT,
    location        TEXT,
    remote          INTEGER DEFAULT 0,
    raw_text        TEXT,
    matched_keywords TEXT,

    -- Scoring (score_jobs.py).
    reviewed        INTEGER DEFAULT 0,
    score           INTEGER,
    interested      INTEGER DEFAULT 0,
    notes           TEXT,

    -- Sizing pipeline (size_companies.py).
    company_size        INTEGER,
    company_size_source TEXT,                        -- 'headcount' | 'funding_stage' | 'llm_guess'
    regex_checked       INTEGER DEFAULT 0,
    lookup_checked      INTEGER DEFAULT 0,
    fetch_checked       INTEGER DEFAULT 0,
    opus_checked        INTEGER DEFAULT 0,           -- vestigial; reserved for opus fallback
    -- When the row was inserted. SQLite won't accept CURRENT_TIMESTAMP as an
    -- ALTER ADD COLUMN default, so we fill it via the trigger below instead.
    created_at          TEXT,
    UNIQUE(source, source_id)
);

CREATE INDEX idx_jobs_source    ON jobs(source);
CREATE INDEX idx_jobs_company   ON jobs(company);
CREATE INDEX idx_jobs_remote    ON jobs(remote);
CREATE INDEX idx_jobs_posted_at ON jobs(posted_at);

-- Acts as a default for jobs.created_at without needing CURRENT_TIMESTAMP in
-- the column definition (which ALTER TABLE rejects).
CREATE TRIGGER jobs_set_created_at
AFTER INSERT ON jobs
WHEN NEW.created_at IS NULL
BEGIN
    UPDATE jobs SET created_at = datetime('now') WHERE id = NEW.id;
END;


CREATE TABLE companies (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    source          TEXT NOT NULL,                   -- where we first saw the company
    first_seen      TEXT NOT NULL,
    url             TEXT,
    tags            TEXT,
    notes           TEXT,

    -- Aggregated size (populate_companies.py rolls up from jobs).
    size            INTEGER,                         -- null when we genuinely don't know
    size_source     TEXT,                            -- mirrors jobs.company_size_source
    regex_checked   INTEGER DEFAULT 0,               -- regex pass has been run on at least one of this company's jobs
    fetch_checked   INTEGER DEFAULT 0,               -- Haiku has been attempted on at least one of this company's jobs
    last_fetch_checked TEXT,                         -- ISO timestamp of the most recent Haiku attempt
    job_count       INTEGER,

    -- ATS link extracted from raw_text.
    ats_provider    TEXT,                            -- 'greenhouse' | 'lever' | 'ashby' | 'workable'
    ats_url         TEXT,
    ats_slug        TEXT,

    -- Block all jobs from this company without spending a Haiku call.
    -- Set automatically by populate_companies.py for clear-pattern companies
    -- (e.g. defense contractors); can also be set manually via SQL.
    skip_reason     TEXT,                            -- 'defense' | 'manual: <reason>' | NULL

    UNIQUE(name, source)
);


CREATE TABLE seen_threads (
    hn_story_id     INTEGER PRIMARY KEY,
    title           TEXT,
    fetched_at      TEXT NOT NULL
);

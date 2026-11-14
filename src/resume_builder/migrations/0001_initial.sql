-- Initial schema (PRD R7.1–R7.3). One database per user, so there is no user column.
-- Integrity is enforced here as well as in code: foreign keys, CHECK constraints for allowed
-- values, UNIQUE ids and NOT NULL where required.

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE user_settings (
    key        TEXT PRIMARY KEY CHECK (key = 'user'),
    data       TEXT NOT NULL CHECK (json_valid(data)),
    created_by TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE content_items (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL CHECK (kind IN ('summary_phrase','role','project','bullet','education','certification','skill_group')),
    parent_id  TEXT REFERENCES content_items(id),
    position   INTEGER NOT NULL,
    status     TEXT CHECK (status IS NULL OR status IN ('draft','confirmed')),
    archived   INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),
    data       TEXT NOT NULL CHECK (json_valid(data)),
    created_by TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX content_items_kind ON content_items(kind, archived, position);

CREATE TABLE companies (
    slug       TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    position   INTEGER NOT NULL DEFAULT 0,
    archived   INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),
    data       TEXT NOT NULL CHECK (json_valid(data)),
    created_by TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE departments (
    company_slug TEXT NOT NULL REFERENCES companies(slug) ON UPDATE CASCADE,
    slug         TEXT NOT NULL,
    name         TEXT NOT NULL,
    position     INTEGER NOT NULL DEFAULT 0,
    archived     INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),
    data         TEXT NOT NULL CHECK (json_valid(data)),
    created_by TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (company_slug, slug)
);

CREATE TABLE applications (
    job_id          TEXT PRIMARY KEY,
    company_slug    TEXT NOT NULL REFERENCES companies(slug),
    department_slug TEXT,
    stage           TEXT NOT NULL CHECK (stage IN ('interested','tailoring','applied','acknowledged','screening','interviewing','offer','accepted','declined','rejected','withdrawn','ghosted')),
    archived        INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0,1)),
    posting_text    TEXT,
    data            TEXT NOT NULL CHECK (json_valid(data)),
    created_by TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
    FOREIGN KEY (company_slug, department_slug) REFERENCES departments(company_slug, slug)
);

CREATE TABLE events (
    job_id     TEXT NOT NULL REFERENCES applications(job_id),
    seq        INTEGER NOT NULL CHECK (seq >= 1),
    date       TEXT NOT NULL,
    type       TEXT NOT NULL CHECK (type IN ('stage_change','interview','follow_up_sent','message_received','note','document_sent','correction')),
    stage      TEXT CHECK (stage IS NULL OR stage IN ('interested','tailoring','applied','acknowledged','screening','interviewing','offer','accepted','declined','rejected','withdrawn','ghosted')),
    data       TEXT NOT NULL CHECK (json_valid(data)),
    actor      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (job_id, seq)
);

CREATE TABLE event_drafts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL REFERENCES applications(job_id),
    data       TEXT NOT NULL CHECK (json_valid(data)),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE contacts (
    job_id   TEXT NOT NULL REFERENCES applications(job_id),
    position INTEGER NOT NULL,
    name     TEXT NOT NULL,
    role     TEXT NOT NULL CHECK (role IN ('recruiter','hiring_manager','interviewer','referrer','other')),
    data     TEXT NOT NULL CHECK (json_valid(data)),
    PRIMARY KEY (job_id, position)
);

CREATE TABLE profiles (
    profile_key TEXT PRIMARY KEY,               -- 'public' or a job id
    job_id      TEXT REFERENCES applications(job_id),
    data        TEXT NOT NULL CHECK (json_valid(data)),
    created_by TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
    CHECK ((profile_key = 'public' AND job_id IS NULL) OR (profile_key = job_id))
);

CREATE TABLE documents (
    job_id     TEXT NOT NULL REFERENCES applications(job_id),
    kind       TEXT NOT NULL CHECK (kind IN ('fit_report','cover_letter','answer')),
    slug       TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL CHECK (status IN ('draft','approved')),
    question   TEXT,
    text       TEXT NOT NULL,
    warnings   TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(warnings)),
    approved_snapshot TEXT,
    created_by TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (job_id, kind, slug)
);

CREATE TABLE snapshots (
    id         TEXT PRIMARY KEY,
    job_key    TEXT NOT NULL,                   -- job id or 'public'
    doc_type   TEXT NOT NULL CHECK (doc_type IN ('resume','cover_letter','answer','fit_report','profile','public_site')),
    preview    INTEGER NOT NULL DEFAULT 0 CHECK (preview IN (0,1)),
    reason     TEXT NOT NULL CHECK (reason IN ('draft','approved','sent','public','preview')),
    pinned     INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0,1)),
    data       TEXT NOT NULL CHECK (json_valid(data)),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX snapshots_job ON snapshots(job_key, doc_type, created_at);

CREATE TABLE snapshot_bullets (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    bullet_id   TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, bullet_id)
);

CREATE TABLE audit_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    channel    TEXT NOT NULL,
    request_id TEXT NOT NULL,
    operation  TEXT NOT NULL,
    target     TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL
);

CREATE TABLE sync_files (
    path           TEXT PRIMARY KEY,            -- relative to the user's data folder
    hash           TEXT NOT NULL,
    content        TEXT CHECK (content IS NULL OR json_valid(content)),
    schema_version INTEGER,
    synced_at      TEXT NOT NULL,
    result         TEXT NOT NULL CHECK (result IN ('applied','failed','conflict','waiting','unchanged')),
    problems       TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(problems))
);

CREATE TABLE sync_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    applied     INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    summary     TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(summary))
);

CREATE TABLE tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    actor      TEXT NOT NULL CHECK (actor IN ('owner','agent')),
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0,1))
);

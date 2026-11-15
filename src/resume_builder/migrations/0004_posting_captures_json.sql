-- rb:foreign-keys-off
-- Allow captures of JSON API responses read by site adapters (PRD R13.7, R13.10, R13.16, D31).
-- SQLite can't change a CHECK constraint in place, so posting_captures is rebuilt with SQLite's
-- documented procedure: create the new table, copy every row (ids, hashes and timestamps
-- unchanged), drop the old table, rename the new one, then recreate the index and the append-only
-- triggers exactly as before.
--
-- intake_proposals.capture_id references posting_captures(id). The marker on the first line makes
-- the migration runner switch foreign keys off for this migration only (that can't be done inside a
-- transaction), run PRAGMA foreign_key_check before committing, and switch them back on.

DROP TRIGGER posting_captures_no_delete;
DROP TRIGGER posting_captures_no_edit;

CREATE TABLE posting_captures_rebuilt (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT REFERENCES applications(job_id),
    source_kind  TEXT NOT NULL CHECK (source_kind IN ('url','file','stdin')),
    source_url   TEXT,
    source_path  TEXT,                          -- relative to the user's data folder, for source_kind 'file'
    content_type TEXT NOT NULL CHECK (content_type IN ('text/html','text/plain','application/json')),
    raw_content  TEXT NOT NULL,
    markdown     TEXT NOT NULL,
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    captured_at  TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    CHECK (source_kind <> 'url' OR source_url IS NOT NULL),
    CHECK (source_kind <> 'file' OR source_path IS NOT NULL)
);

INSERT INTO posting_captures_rebuilt (id, job_id, source_kind, source_url, source_path, content_type, raw_content,
                                      markdown, content_hash, captured_at, created_by, created_at)
SELECT id, job_id, source_kind, source_url, source_path, content_type, raw_content,
       markdown, content_hash, captured_at, created_by, created_at
FROM posting_captures;

DROP TABLE posting_captures;
ALTER TABLE posting_captures_rebuilt RENAME TO posting_captures;

CREATE INDEX posting_captures_job ON posting_captures(job_id, captured_at, id);

CREATE TRIGGER posting_captures_no_delete
BEFORE DELETE ON posting_captures
BEGIN
    SELECT RAISE(ABORT, 'posting captures are append-only and cannot be deleted');
END;

CREATE TRIGGER posting_captures_no_edit
BEFORE UPDATE ON posting_captures
WHEN OLD.job_id IS NOT NULL
  OR NEW.id IS NOT OLD.id
  OR NEW.source_kind IS NOT OLD.source_kind
  OR NEW.source_url IS NOT OLD.source_url
  OR NEW.source_path IS NOT OLD.source_path
  OR NEW.content_type IS NOT OLD.content_type
  OR NEW.raw_content IS NOT OLD.raw_content
  OR NEW.markdown IS NOT OLD.markdown
  OR NEW.content_hash IS NOT OLD.content_hash
  OR NEW.captured_at IS NOT OLD.captured_at
  OR NEW.created_by IS NOT OLD.created_by
  OR NEW.created_at IS NOT OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'posting captures are append-only; only an unassigned capture can be linked to a job, once');
END;

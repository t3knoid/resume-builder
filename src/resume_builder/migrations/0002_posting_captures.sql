-- Captured job postings (PRD R13.10, D29): the posting exactly as it was fetched or pasted.
-- Append-only. A capture is never edited or deleted; re-capturing adds a new row and the latest
-- one for a job is current. job_id is NULL while a capture belongs to an intake that has not been
-- confirmed yet (R13.22); it can be set once, when the job is created, and never changed after.

CREATE TABLE posting_captures (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT REFERENCES applications(job_id),
    source_kind  TEXT NOT NULL CHECK (source_kind IN ('url','file','stdin')),
    source_url   TEXT,
    source_path  TEXT,                          -- relative to the user's data folder, for source_kind 'file'
    content_type TEXT NOT NULL CHECK (content_type IN ('text/html','text/plain')),
    raw_content  TEXT NOT NULL,
    markdown     TEXT NOT NULL,
    content_hash TEXT NOT NULL CHECK (length(content_hash) = 64),
    captured_at  TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    CHECK (source_kind <> 'url' OR source_url IS NOT NULL),
    CHECK (source_kind <> 'file' OR source_path IS NOT NULL)
);
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

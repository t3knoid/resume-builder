-- Changes to an application's own fields drafted by the AI agent (issue #38): interest level and
-- notes, source, next action, notes, fit summary, outcome reason. The owner confirms or discards
-- each one. A draft is never deleted or edited, and it is resolved only once.

CREATE TABLE application_drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL REFERENCES applications(job_id),
    status      TEXT NOT NULL CHECK (status IN ('pending','confirmed','discarded')),
    data        TEXT NOT NULL CHECK (json_valid(data)),   -- {"changes": {...}, "before": {...}}
    note        TEXT,                                      -- the agent's note to the owner
    created_by  TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_by  TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX application_drafts_status ON application_drafts(status, job_id, id);

CREATE TRIGGER application_drafts_no_delete
BEFORE DELETE ON application_drafts
BEGIN
    SELECT RAISE(ABORT, 'application drafts are kept; discard one instead of deleting it');
END;

CREATE TRIGGER application_drafts_resolve_once
BEFORE UPDATE ON application_drafts
WHEN OLD.status <> 'pending'
  OR NEW.job_id IS NOT OLD.job_id
  OR NEW.data IS NOT OLD.data
  OR NEW.note IS NOT OLD.note
  OR NEW.created_by IS NOT OLD.created_by
  OR NEW.created_at IS NOT OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'an application draft is resolved once and never edited');
END;

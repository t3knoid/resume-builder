-- Job intake proposals from the AI agent (PRD R13.22–R13.24): a captured posting plus the fields the
-- agent and the rules found, waiting for the owner. Confirming creates the job; discarding keeps the
-- record. A proposal is never deleted, and it is resolved (confirmed or discarded) only once.

CREATE TABLE intake_proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    capture_id  INTEGER NOT NULL REFERENCES posting_captures(id),
    status      TEXT NOT NULL CHECK (status IN ('pending','confirmed','discarded')),
    data        TEXT NOT NULL CHECK (json_valid(data)),   -- fields with passages, disagreements, notes, site adapter
    note        TEXT,                                      -- the agent's note to the owner
    job_id      TEXT REFERENCES applications(job_id),
    created_by  TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_by  TEXT NOT NULL, updated_at TEXT NOT NULL,
    CHECK (status <> 'confirmed' OR job_id IS NOT NULL),
    CHECK (status <> 'pending' OR job_id IS NULL)
);
CREATE INDEX intake_proposals_status ON intake_proposals(status, id);

CREATE TRIGGER intake_proposals_no_delete
BEFORE DELETE ON intake_proposals
BEGIN
    SELECT RAISE(ABORT, 'intake proposals are kept; discard one instead of deleting it');
END;

CREATE TRIGGER intake_proposals_resolve_once
BEFORE UPDATE ON intake_proposals
WHEN OLD.status <> 'pending'
  OR NEW.capture_id IS NOT OLD.capture_id
  OR NEW.data IS NOT OLD.data
  OR NEW.note IS NOT OLD.note
  OR NEW.created_by IS NOT OLD.created_by
  OR NEW.created_at IS NOT OLD.created_at
BEGIN
    SELECT RAISE(ABORT, 'an intake proposal is resolved once and never edited');
END;

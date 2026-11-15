-- Owner sign-in on the tracker website (issue #30, PRD 8.8, B9a, D32). Only hashes are stored,
-- like API tokens: the session id and the one-time sign-in code themselves never reach the database.

CREATE TABLE web_sessions (
    session_hash TEXT PRIMARY KEY CHECK (length(session_hash) = 64),
    actor        TEXT NOT NULL CHECK (actor = 'owner'),       -- only the owner can sign in to the website
    created_at   TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL,                                -- absolute limit; idle expiry is checked in code
    revoked      INTEGER NOT NULL DEFAULT 0 CHECK (revoked IN (0,1))
);

CREATE TABLE web_login_codes (
    code_hash  TEXT PRIMARY KEY CHECK (length(code_hash) = 64),
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT                                            -- set once; a code works a single time
);

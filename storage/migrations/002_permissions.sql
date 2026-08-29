-- migrate:up
CREATE TABLE permission_requests (
    approval_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES sessions(run_id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    arguments_hash TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    summary TEXT NOT NULL,
    status TEXT NOT NULL,
    scope TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    decided_at REAL
);
CREATE INDEX permission_requests_run_idx ON permission_requests(run_id,created_at DESC);
CREATE TABLE permission_grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    run_id TEXT,
    tool_name TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    UNIQUE(scope,run_id,tool_name)
);
-- migrate:down
DROP TABLE IF EXISTS permission_grants;
DROP INDEX IF EXISTS permission_requests_run_idx;
DROP TABLE IF EXISTS permission_requests;

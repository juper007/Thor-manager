-- migrate:up
CREATE TABLE sessions (
    run_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    iterations INTEGER NOT NULL DEFAULT 0,
    tool_calls INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    final_answer TEXT,
    resumed_from TEXT REFERENCES sessions(run_id) ON DELETE SET NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX sessions_updated_at_idx ON sessions(updated_at DESC);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES sessions(run_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL,
    UNIQUE(run_id,sequence)
);
CREATE TABLE run_events (
    run_id TEXT NOT NULL REFERENCES sessions(run_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    type TEXT NOT NULL,
    state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY(run_id,sequence)
);
CREATE TABLE plan_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES sessions(run_id) ON DELETE CASCADE,
    position INTEGER NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL, detail TEXT, UNIQUE(run_id,position)
);
CREATE TABLE tool_executions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES sessions(run_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL, name TEXT NOT NULL, arguments_json TEXT NOT NULL, result_json TEXT,
    status TEXT NOT NULL, error TEXT, error_code TEXT, seconds REAL, created_at REAL NOT NULL,
    UNIQUE(run_id,sequence)
);
CREATE TABLE approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES sessions(run_id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL, arguments_json TEXT NOT NULL, decision TEXT NOT NULL, scope TEXT,
    decided_at REAL NOT NULL, expires_at REAL
);
CREATE TABLE file_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES sessions(run_id) ON DELETE CASCADE,
    path TEXT NOT NULL, operation TEXT NOT NULL, diff_text TEXT, created_at REAL NOT NULL
);
CREATE TABLE verifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES sessions(run_id) ON DELETE CASCADE,
    name TEXT NOT NULL, status TEXT NOT NULL, output TEXT, created_at REAL NOT NULL
);
-- migrate:down
DROP TABLE IF EXISTS verifications;
DROP TABLE IF EXISTS file_changes;
DROP TABLE IF EXISTS approvals;
DROP TABLE IF EXISTS tool_executions;
DROP TABLE IF EXISTS plan_steps;
DROP TABLE IF EXISTS run_events;
DROP TABLE IF EXISTS messages;
DROP INDEX IF EXISTS sessions_updated_at_idx;
DROP TABLE IF EXISTS sessions;

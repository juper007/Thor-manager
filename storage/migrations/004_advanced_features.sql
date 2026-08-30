-- migrate:up
CREATE TABLE mcp_servers (
    name TEXT PRIMARY KEY, command_json TEXT NOT NULL, cwd TEXT, env_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE project_memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_key TEXT NOT NULL, memory_key TEXT NOT NULL,
    content TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    UNIQUE(project_key,memory_key)
);
CREATE INDEX project_memories_project_idx ON project_memories(project_key,updated_at DESC);
CREATE TABLE scheduled_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, prompt TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL, next_run_at REAL NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at REAL, last_status TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE notification_endpoints (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, url TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE TABLE usage_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, metric TEXT NOT NULL, value REAL NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
);
CREATE INDEX usage_samples_metric_idx ON usage_samples(metric,created_at DESC);
-- migrate:down
DROP INDEX IF EXISTS usage_samples_metric_idx;
DROP TABLE IF EXISTS usage_samples;
DROP TABLE IF EXISTS notification_endpoints;
DROP TABLE IF EXISTS scheduled_runs;
DROP INDEX IF EXISTS project_memories_project_idx;
DROP TABLE IF EXISTS project_memories;
DROP TABLE IF EXISTS mcp_servers;

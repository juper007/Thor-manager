-- migrate:up
CREATE TABLE permission_grants_v2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    grant_key TEXT NOT NULL UNIQUE,
    scope TEXT NOT NULL,
    run_id TEXT,
    tool_name TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL
);
INSERT OR IGNORE INTO permission_grants_v2(grant_key,scope,run_id,tool_name,risk_level,created_at,expires_at)
SELECT scope || ':' || COALESCE(run_id,'*') || ':' || tool_name || ':' || risk_level,
       scope,run_id,tool_name,risk_level,MAX(created_at),MAX(expires_at)
FROM permission_grants
GROUP BY scope,COALESCE(run_id,'*'),tool_name,risk_level;
DROP TABLE permission_grants;
ALTER TABLE permission_grants_v2 RENAME TO permission_grants;
-- migrate:down
CREATE TABLE permission_grants_v1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL,
    run_id TEXT,
    tool_name TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL,
    UNIQUE(scope,run_id,tool_name)
);
INSERT OR IGNORE INTO permission_grants_v1(scope,run_id,tool_name,risk_level,created_at,expires_at)
SELECT scope,run_id,tool_name,risk_level,created_at,expires_at FROM permission_grants;
DROP TABLE permission_grants;
ALTER TABLE permission_grants_v1 RENAME TO permission_grants;

-- migrate:up
ALTER TABLE sessions ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'thor';
CREATE INDEX sessions_owner_updated_idx ON sessions(owner_id,updated_at DESC);
ALTER TABLE permission_grants ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'thor';
UPDATE permission_grants SET grant_key='thor:' || grant_key;
-- migrate:down
UPDATE permission_grants SET grant_key=substr(grant_key,6) WHERE owner_id='thor';
ALTER TABLE permission_grants DROP COLUMN owner_id;
DROP INDEX IF EXISTS sessions_owner_updated_idx;
ALTER TABLE sessions DROP COLUMN owner_id;

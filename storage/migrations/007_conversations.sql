-- migrate:up
ALTER TABLE sessions ADD COLUMN conversation_id TEXT;
UPDATE sessions SET conversation_id=run_id;
CREATE INDEX sessions_owner_conversation_idx ON sessions(owner_id,conversation_id,updated_at DESC);

-- migrate:down
DROP INDEX IF EXISTS sessions_owner_conversation_idx;
ALTER TABLE sessions DROP COLUMN conversation_id;

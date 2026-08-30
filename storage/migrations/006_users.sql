-- migrate:up
CREATE TABLE users (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0 CHECK (is_admin IN (0,1)),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- migrate:down
DROP TABLE users;

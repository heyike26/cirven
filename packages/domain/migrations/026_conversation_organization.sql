CREATE TABLE conversation_organization (
    conversation_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision > 0),
    archived INTEGER NOT NULL CHECK (archived IN (0,1)),
    updated_at TEXT NOT NULL
);
CREATE TABLE conversation_organization_operations (
    operation_id TEXT PRIMARY KEY,
    request_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL
);

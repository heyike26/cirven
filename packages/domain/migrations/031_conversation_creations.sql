-- Empty user conversations are metadata, not fabricated execution sources.
CREATE TABLE conversation_creations (
    operation_id TEXT PRIMARY KEY,
    request_sha256 TEXT NOT NULL,
    conversation_id TEXT NOT NULL UNIQUE REFERENCES conversation_threads(conversation_id),
    result_json TEXT NOT NULL CHECK (length(CAST(result_json AS BLOB)) <= 8192),
    result_sha256 TEXT NOT NULL
);

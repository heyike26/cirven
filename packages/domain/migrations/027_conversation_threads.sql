CREATE TABLE conversation_threads (
    conversation_id TEXT PRIMARY KEY,
    initial_kind TEXT NOT NULL CHECK (initial_kind IN ('native','original','discovery')),
    project_id TEXT REFERENCES projects(id),
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE conversation_source_aliases (
    alias_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversation_threads(conversation_id),
    kind TEXT NOT NULL CHECK (kind IN ('native','original','discovery')),
    identity_json TEXT NOT NULL,
    source_json TEXT,
    source_sha256 TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX conversation_source_aliases_owner ON conversation_source_aliases(conversation_id);
CREATE TABLE conversation_native_routes (
    conversation_id TEXT NOT NULL REFERENCES conversation_threads(conversation_id),
    project_key TEXT NOT NULL,
    project_id TEXT REFERENCES projects(id),
    native_conversation_id TEXT NOT NULL,
    alias_id TEXT NOT NULL UNIQUE REFERENCES conversation_source_aliases(alias_id),
    PRIMARY KEY(conversation_id,project_key),
    UNIQUE(project_key,native_conversation_id)
);

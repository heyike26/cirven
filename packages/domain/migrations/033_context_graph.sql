CREATE TABLE context_scopes (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    parent_id TEXT REFERENCES context_scopes(id),
    kind TEXT NOT NULL CHECK(kind IN ('GLOBAL','PROJECT','TOPIC','TASK')),
    label TEXT NOT NULL,
    revision INTEGER NOT NULL,
    policy_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX context_scopes_parent ON context_scopes(parent_id);
CREATE TABLE context_origins (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    kind TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    title TEXT NOT NULL,
    forgotten INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE context_notes (
    origin_id TEXT PRIMARY KEY REFERENCES context_origins(id),
    text_content TEXT NOT NULL,
    basis_json TEXT NOT NULL
);
CREATE TABLE context_associations (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES context_scopes(id),
    origin_id TEXT NOT NULL REFERENCES context_origins(id),
    conversation_id TEXT REFERENCES conversation_threads(conversation_id),
    state TEXT NOT NULL CHECK(state IN ('CANDIDATE','ACTIVE','REJECTED','RETRACTED','EXCLUDED')),
    manual_locked INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX context_associations_identity ON context_associations(scope_id,origin_id,coalesce(conversation_id,''));
CREATE INDEX context_associations_conversation ON context_associations(conversation_id,state);
CREATE INDEX context_associations_origin ON context_associations(origin_id,state);
CREATE TABLE context_operations (
    operation_id TEXT PRIMARY KEY,
    request_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL
);
CREATE TABLE context_selections (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES context_scopes(id),
    conversation_id TEXT,
    selection_json TEXT NOT NULL,
    selection_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE context_storage_budget (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    reserved_bytes INTEGER NOT NULL CHECK(reserved_bytes>=0)
);
INSERT INTO context_storage_budget VALUES (1,0);

CREATE TABLE context_origin_files (
    origin_id TEXT PRIMARY KEY REFERENCES context_origins(id),
    file_key TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    locator_sha256 TEXT NOT NULL
);
CREATE INDEX context_origin_files_key ON context_origin_files(file_key);
CREATE TABLE context_folder_rules (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES context_scopes(id),
    conversation_id TEXT REFERENCES conversation_threads(conversation_id),
    folder_json TEXT NOT NULL,
    folder_sha256 TEXT NOT NULL,
    range_kind TEXT NOT NULL CHECK(range_kind IN ('CURRENT_DIRECTORY','INCLUDING_DESCENDANTS')),
    state TEXT NOT NULL CHECK(state IN ('ACTIVE','CANDIDATE','REJECTED','RETRACTED')),
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX context_folder_rules_scope ON context_folder_rules(scope_id,conversation_id,state);
CREATE TABLE context_file_exclusions (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES context_scopes(id),
    conversation_id TEXT REFERENCES conversation_threads(conversation_id),
    file_key TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('ACTIVE','RETRACTED')),
    revision INTEGER NOT NULL,
    association_id TEXT NOT NULL REFERENCES context_associations(id),
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX context_file_exclusions_identity ON context_file_exclusions(scope_id,coalesce(conversation_id,''),file_key);

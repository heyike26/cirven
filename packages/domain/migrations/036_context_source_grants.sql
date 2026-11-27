CREATE TABLE context_policy_defaults (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    policy_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision>=0),
    updated_at TEXT
);
INSERT INTO context_policy_defaults VALUES (1,'{}',0,NULL);
CREATE TABLE context_read_grants (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES context_scopes(id),
    conversation_id TEXT REFERENCES conversation_threads(conversation_id),
    origin_id TEXT NOT NULL REFERENCES context_origins(id),
    source_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('CANDIDATE','ACTIVE','REJECTED','RETRACTED','EXCLUDED')),
    revision INTEGER NOT NULL CHECK(revision>=1),
    review_sha256 TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX context_read_grants_identity ON context_read_grants(scope_id,coalesce(conversation_id,''),origin_id);
CREATE INDEX context_read_grants_origin ON context_read_grants(origin_id,state);
CREATE TABLE context_note_grants (
    note_origin_id TEXT NOT NULL REFERENCES context_notes(origin_id),
    dependency_origin_id TEXT NOT NULL REFERENCES context_origins(id),
    grant_id TEXT NOT NULL REFERENCES context_read_grants(id),
    grant_revision INTEGER NOT NULL,
    note_scope_id TEXT NOT NULL REFERENCES context_scopes(id),
    PRIMARY KEY(note_origin_id,dependency_origin_id)
);
CREATE TABLE context_fragment_bases (
    origin_id TEXT PRIMARY KEY REFERENCES context_origins(id),
    base_origin_id TEXT NOT NULL REFERENCES context_origins(id)
);

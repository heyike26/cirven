CREATE TABLE context_conversation_links (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL REFERENCES context_scopes(id),
    conversation_id TEXT NOT NULL REFERENCES conversation_threads(conversation_id),
    conversation_ref_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('CANDIDATE','ACTIVE','REJECTED','RETRACTED')),
    manual_locked INTEGER NOT NULL CHECK(manual_locked IN (0,1)),
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scope_id,conversation_id)
);
CREATE INDEX context_conversation_links_conversation ON context_conversation_links(conversation_id,state);
CREATE TABLE context_conversation_preferences (
    conversation_id TEXT PRIMARY KEY REFERENCES conversation_threads(conversation_id),
    preferred_scope_id TEXT REFERENCES context_scopes(id),
    retrieval_mode TEXT NOT NULL CHECK(retrieval_mode IN ('AUTO','MANUAL','OFF')),
    include_global INTEGER NOT NULL CHECK(include_global IN (0,1)),
    policy_json TEXT NOT NULL,
    revision INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);

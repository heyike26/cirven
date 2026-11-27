-- A listing observation is current metadata, never an execution identity or grant.
-- Keep the original execution-source aliases and frozen admissions unchanged.
CREATE TABLE conversation_listing_observations (
    conversation_id TEXT PRIMARY KEY REFERENCES conversation_threads(conversation_id),
    alias_id TEXT NOT NULL REFERENCES conversation_source_aliases(alias_id),
    source_json TEXT NOT NULL CHECK (length(CAST(source_json AS BLOB)) <= 8192),
    source_sha256 TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

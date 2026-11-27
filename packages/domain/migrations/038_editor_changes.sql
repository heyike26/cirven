-- A durable editor handoff blocks only overlapping original workspaces.
-- Expiry is an observation deadline, never permission to replay/release a write.
CREATE TABLE agent_editor_changes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation >= 1),
    workspace_id TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('RESERVING','READY','ARMED','BUFFER_CHANGED','SAVED','CANCELLED','MANUAL_SAVED','FAILED','UNKNOWN_OUTCOME')),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, session_id, operation_id)
);
CREATE INDEX agent_editor_changes_active ON agent_editor_changes(phase, updated_at);

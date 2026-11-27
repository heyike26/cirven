-- Immutable, metadata-only on-demand capture requests. No effect/permission grant.
CREATE TABLE agent_editor_capture_cohorts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    editor_client_id TEXT NOT NULL,
    capture_ref TEXT NOT NULL UNIQUE,
    request_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    expires_at_ms INTEGER NOT NULL CHECK(expires_at_ms>=1),
    runtime_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id,session_id,operation_id)
);
CREATE INDEX agent_editor_capture_requester ON agent_editor_capture_cohorts(editor_client_id,id);
CREATE INDEX agent_editor_capture_active ON agent_editor_capture_cohorts(runtime_id,expires_at_ms);

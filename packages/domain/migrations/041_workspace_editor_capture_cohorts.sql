-- Metadata purposes for an explicit workspace edit; no synthetic session owner.
CREATE TABLE workspace_editor_capture_cohorts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    workspace_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    editor_client_id TEXT NOT NULL,
    capture_ref TEXT NOT NULL UNIQUE,
    request_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    expires_at_ms INTEGER NOT NULL,
    runtime_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id,workspace_id,operation_id)
);
CREATE INDEX workspace_editor_capture_expiry ON workspace_editor_capture_cohorts(runtime_id,expires_at_ms);

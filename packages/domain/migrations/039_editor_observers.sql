-- Paired IDE observations never grant filesystem effects or model execution.
-- Stale/unknown observations retain their barrier until fresh capture or explicit retirement.
CREATE TABLE agent_editor_observer_runtime (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    runtime_id TEXT NOT NULL
);
CREATE TABLE agent_editor_observers (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    workspace_id TEXT NOT NULL,
    editor_client_id TEXT NOT NULL,
    editor_window_id TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('ACTIVE','RETIRED')),
    revision INTEGER NOT NULL CHECK(revision>=1),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id,workspace_id,editor_client_id,editor_window_id)
);
CREATE INDEX agent_editor_observers_active ON agent_editor_observers(phase,id);
CREATE TABLE agent_editor_observer_actions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    observer_id TEXT NOT NULL REFERENCES agent_editor_observers(id),
    request_sha256 TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id,session_id,operation_id)
);
CREATE INDEX agent_editor_observer_actions_owner ON agent_editor_observer_actions(observer_id);

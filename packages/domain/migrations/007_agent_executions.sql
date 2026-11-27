CREATE TABLE agent_executions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    operation_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    runtime TEXT NOT NULL,
    workspace TEXT NOT NULL,
    execution_status TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    session_id TEXT,
    model_profile TEXT NOT NULL,
    sdk_version TEXT NOT NULL,
    request TEXT NOT NULL,
    result TEXT,
    created_at TEXT NOT NULL,
    ended_at TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    UNIQUE(project_id,operation_id)
);
CREATE TABLE agent_events (
    execution_id TEXT NOT NULL REFERENCES agent_executions(id),
    event_seq INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(execution_id,event_seq)
);

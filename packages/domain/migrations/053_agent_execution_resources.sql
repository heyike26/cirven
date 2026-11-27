CREATE TABLE agent_execution_resources (
    execution_id TEXT PRIMARY KEY REFERENCES agent_executions(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    root_execution_id TEXT NOT NULL REFERENCES agent_executions(id),
    runtime TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    scope_sha256 TEXT NOT NULL,
    resource_json TEXT NOT NULL CHECK(length(resource_json)<=16384),
    created_at TEXT NOT NULL
);
CREATE INDEX agent_execution_resources_root ON agent_execution_resources(project_id,root_execution_id);

CREATE TABLE agent_execution_resource_gaps (
    execution_id TEXT PRIMARY KEY REFERENCES agent_executions(id),
    project_id TEXT NOT NULL REFERENCES projects(id),
    resource_id TEXT,
    reason TEXT NOT NULL,
    first_seen_at TEXT NOT NULL
);
CREATE INDEX agent_execution_resource_gaps_resource ON agent_execution_resource_gaps(project_id,resource_id);

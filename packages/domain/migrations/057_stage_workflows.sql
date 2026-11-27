CREATE TABLE stage_workflow_definitions (
    project_id TEXT NOT NULL REFERENCES projects(id),
    id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    body TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(project_id, id, revision)
);
CREATE TABLE stage_workflow_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    conversation_id TEXT NOT NULL REFERENCES conversation_threads(conversation_id),
    body TEXT NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE INDEX stage_workflow_run_owner ON stage_workflow_runs(project_id,conversation_id);
CREATE TABLE stage_workflow_children (
    conversation_id TEXT PRIMARY KEY REFERENCES conversation_threads(conversation_id),
    run_id TEXT NOT NULL REFERENCES stage_workflow_runs(id),
    node_id TEXT NOT NULL
);
CREATE TABLE stage_workflow_approvals (
    run_id TEXT NOT NULL REFERENCES stage_workflow_runs(id),
    node_id TEXT NOT NULL,
    body TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(run_id,node_id,sha256)
);
CREATE TABLE stage_workflow_operations (
    operation_id TEXT PRIMARY KEY,
    request_sha256 TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    definition_revision INTEGER,
    completed INTEGER NOT NULL CHECK(completed IN (0,1))
);
CREATE TABLE stage_workflow_templates (
    project_id TEXT NOT NULL REFERENCES projects(id),
    conversation_id TEXT NOT NULL REFERENCES conversation_threads(conversation_id),
    id TEXT NOT NULL,
    body TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(project_id,conversation_id,id)
);
CREATE TABLE stage_workflow_attempts (
    run_id TEXT NOT NULL REFERENCES stage_workflow_runs(id),
    node_id TEXT NOT NULL,
    operation_id TEXT NOT NULL REFERENCES stage_workflow_operations(operation_id),
    body TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(run_id,node_id,operation_id)
);

CREATE TABLE stage_workflow_stage_history (
    conversation_id TEXT PRIMARY KEY REFERENCES stage_workflow_children(conversation_id),
    run_id TEXT NOT NULL REFERENCES stage_workflow_runs(id),
    node_id TEXT NOT NULL,
    body TEXT NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE TABLE stage_workflow_planning_reuse (
    project_id TEXT NOT NULL REFERENCES projects(id),
    conversation_id TEXT NOT NULL REFERENCES conversation_threads(conversation_id),
    input_sha256 TEXT NOT NULL,
    source_run_id TEXT NOT NULL REFERENCES stage_workflow_runs(id),
    source_node_id TEXT NOT NULL,
    approval_sha256 TEXT NOT NULL,
    PRIMARY KEY(project_id,conversation_id,input_sha256)
);

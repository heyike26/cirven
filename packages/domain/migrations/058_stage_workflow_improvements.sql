CREATE TABLE stage_workflow_improvements (
    operation_id TEXT PRIMARY KEY REFERENCES stage_workflow_operations(operation_id),
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL REFERENCES stage_workflow_children(conversation_id),
    approval_sha256 TEXT NOT NULL,
    body TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    FOREIGN KEY(run_id,node_id,approval_sha256) REFERENCES stage_workflow_approvals(run_id,node_id,sha256)
);
CREATE INDEX stage_workflow_improvement_source
    ON stage_workflow_improvements(run_id,node_id,conversation_id,approval_sha256);

CREATE TABLE stage_workflow_transitions (
    operation_id TEXT PRIMARY KEY REFERENCES stage_workflow_operations(operation_id),
    run_id TEXT NOT NULL REFERENCES stage_workflow_runs(id),
    body TEXT NOT NULL,
    sha256 TEXT NOT NULL
);

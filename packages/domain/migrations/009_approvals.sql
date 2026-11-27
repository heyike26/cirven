CREATE TABLE approvals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    run_id TEXT NOT NULL UNIQUE REFERENCES task_runs(id),
    action TEXT NOT NULL,
    action_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    consumed_at TEXT
);
CREATE TRIGGER approval_scope_no_update BEFORE UPDATE OF
    project_id,run_id,action,action_hash,input_hash,spec_hash ON approvals
BEGIN
    SELECT RAISE(ABORT, 'Approval action and input scope are immutable');
END;

CREATE TABLE task_native_reviews (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    run_id TEXT NOT NULL REFERENCES task_runs(id),
    operation_id TEXT NOT NULL,
    native_operation_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    parent_review_id TEXT REFERENCES task_native_reviews(id),
    input_hash TEXT NOT NULL,
    frozen_scope TEXT NOT NULL,
    native_request TEXT NOT NULL,
    control TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    admission_status TEXT NOT NULL,
    native_execution_id TEXT REFERENCES agent_executions(id),
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error_type TEXT,
    UNIQUE(project_id, operation_id),
    UNIQUE(project_id, native_operation_id)
);
CREATE UNIQUE INDEX one_native_review_root_per_formal_run ON task_native_reviews(project_id,run_id) WHERE kind='submit';
CREATE TRIGGER task_native_review_input_immutable
BEFORE UPDATE OF project_id,run_id,operation_id,native_operation_id,kind,parent_review_id,input_hash,frozen_scope,native_request,control,request_hash,created_at
ON task_native_reviews BEGIN SELECT RAISE(ABORT, 'Task native review input is immutable'); END;
CREATE TRIGGER task_native_review_execution_immutable
BEFORE UPDATE OF native_execution_id ON task_native_reviews
WHEN OLD.native_execution_id IS NOT NULL AND NEW.native_execution_id IS NOT OLD.native_execution_id
BEGIN SELECT RAISE(ABORT, 'Task native review execution association is immutable'); END;
CREATE TRIGGER task_native_review_preserve_record
BEFORE DELETE ON task_native_reviews BEGIN SELECT RAISE(ABORT, 'Task native review record is immutable'); END;

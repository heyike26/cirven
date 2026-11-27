CREATE TABLE workbench_feedback (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 payload TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE workbench_feedback_operations (
 project_id TEXT NOT NULL REFERENCES projects(id), operation_id TEXT NOT NULL,
 request_sha256 TEXT NOT NULL, feedback_id TEXT NOT NULL REFERENCES workbench_feedback(id),
 result TEXT NOT NULL, result_sha256 TEXT NOT NULL, PRIMARY KEY(project_id,operation_id)
);
CREATE TRIGGER workbench_feedback_no_update BEFORE UPDATE ON workbench_feedback
 BEGIN SELECT RAISE(ABORT,'Feedback is immutable'); END;
CREATE TRIGGER workbench_feedback_no_delete BEFORE DELETE ON workbench_feedback
 BEGIN SELECT RAISE(ABORT,'Feedback is immutable'); END;
CREATE TRIGGER workbench_feedback_operations_no_update BEFORE UPDATE ON workbench_feedback_operations
 BEGIN SELECT RAISE(ABORT,'Feedback receipts are immutable'); END;
CREATE TRIGGER workbench_feedback_operations_no_delete BEFORE DELETE ON workbench_feedback_operations
 BEGIN SELECT RAISE(ABORT,'Feedback receipts are immutable'); END;

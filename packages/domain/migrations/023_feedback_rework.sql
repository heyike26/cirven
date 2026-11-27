CREATE TABLE feedback_rework_records (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 kind TEXT NOT NULL CHECK(kind IN ('BINDING','FEEDBACK','PLAN','APPLY')),
 payload TEXT NOT NULL, sha256 TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE feedback_rework_operations (
 project_id TEXT NOT NULL REFERENCES projects(id), operation_id TEXT NOT NULL,
 kind TEXT NOT NULL, request_sha256 TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PROCESSING','COMPLETED')),
 record_id TEXT, result TEXT, result_sha256 TEXT,
 PRIMARY KEY(project_id,operation_id)
);
CREATE TRIGGER feedback_rework_records_no_update BEFORE UPDATE ON feedback_rework_records
 BEGIN SELECT RAISE(ABORT,'Feedback/rework records are immutable'); END;
CREATE TRIGGER feedback_rework_records_no_delete BEFORE DELETE ON feedback_rework_records
 BEGIN SELECT RAISE(ABORT,'Feedback/rework records are immutable'); END;
CREATE TRIGGER feedback_rework_operations_identity BEFORE UPDATE ON feedback_rework_operations
 WHEN OLD.status!='PROCESSING' OR NEW.project_id!=OLD.project_id
 OR NEW.operation_id!=OLD.operation_id OR NEW.kind!=OLD.kind
 OR NEW.request_sha256!=OLD.request_sha256
 BEGIN SELECT RAISE(ABORT,'Feedback/rework operation identity/result are immutable'); END;
CREATE TRIGGER feedback_rework_operations_no_delete BEFORE DELETE ON feedback_rework_operations
 BEGIN SELECT RAISE(ABORT,'Feedback/rework operations are immutable'); END;

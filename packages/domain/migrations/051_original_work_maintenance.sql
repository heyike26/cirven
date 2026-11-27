-- A maintenance work references an immutable completed delivery, not a new task.
-- No deployment, source permission, automatic closure or acceptance is granted.
CREATE TABLE original_work_maintenance_operations (
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    root_operation_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision>=1 AND revision<=128),
    request_json TEXT NOT NULL CHECK(length(CAST(request_json AS BLOB))<=32768),
    request_sha256 TEXT NOT NULL,
    receipt_json TEXT NOT NULL CHECK(length(CAST(receipt_json AS BLOB))<=32768),
    receipt_sha256 TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id,operation_id),
    UNIQUE(project_id,session_id,root_operation_id,revision)
);
CREATE INDEX original_work_maintenance_by_root
    ON original_work_maintenance_operations(project_id,session_id,root_operation_id,revision);
CREATE TRIGGER original_work_maintenance_no_update BEFORE UPDATE ON original_work_maintenance_operations
BEGIN SELECT RAISE(ABORT,'Original maintenance decisions are immutable'); END;
CREATE TRIGGER original_work_maintenance_no_delete BEFORE DELETE ON original_work_maintenance_operations
BEGIN SELECT RAISE(ABORT,'Original maintenance decisions are immutable'); END;

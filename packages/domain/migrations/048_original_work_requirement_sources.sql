-- Read-only foreign requirements, explicitly reviewed for one original root.
-- Original projects, documents, workspaces and execution authority never move.
CREATE TABLE original_work_requirement_source_operations (
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    root_operation_id TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision>=1),
    request_json TEXT NOT NULL CHECK(length(CAST(request_json AS BLOB))<=32768),
    request_sha256 TEXT NOT NULL,
    receipt_json TEXT NOT NULL CHECK(length(CAST(receipt_json AS BLOB))<=32768),
    receipt_sha256 TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id,operation_id),
    UNIQUE(project_id,session_id,root_operation_id,grant_id,revision)
);
CREATE TABLE original_work_requirement_source_heads (
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    root_operation_id TEXT NOT NULL,
    grant_id TEXT NOT NULL,
    root_sha256 TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision>=1),
    operation_id TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id,root_operation_id,grant_id),
    FOREIGN KEY(project_id,session_id,operation_id)
        REFERENCES original_work_requirement_source_operations(project_id,session_id,operation_id)
);
CREATE TRIGGER original_work_requirement_source_no_update
BEFORE UPDATE ON original_work_requirement_source_operations
BEGIN SELECT RAISE(ABORT,'Original work source decisions are immutable'); END;
CREATE TRIGGER original_work_requirement_source_no_delete
BEFORE DELETE ON original_work_requirement_source_operations
BEGIN SELECT RAISE(ABORT,'Original work source decisions are immutable'); END;

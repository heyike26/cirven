-- Declared requirement-to-case mappings. Actual report/requirement stores remain
-- authoritative; metadata never changes the original execution or acceptance.
CREATE TABLE original_work_case_operations (
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    root_operation_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    request_json TEXT NOT NULL CHECK(length(CAST(request_json AS BLOB)) <= 32768),
    request_sha256 TEXT NOT NULL,
    receipt_json TEXT NOT NULL CHECK(length(CAST(receipt_json AS BLOB)) <= 32768),
    receipt_sha256 TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id,operation_id),
    UNIQUE(project_id,session_id,root_operation_id,revision)
);
CREATE TABLE original_work_case_heads (
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    root_operation_id TEXT NOT NULL,
    root_sha256 TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    operation_id TEXT NOT NULL,
    active_json TEXT NOT NULL CHECK(length(CAST(active_json AS BLOB)) <= 16384),
    PRIMARY KEY(project_id,session_id,root_operation_id),
    FOREIGN KEY(project_id,session_id,operation_id)
        REFERENCES original_work_case_operations(project_id,session_id,operation_id)
);
CREATE TRIGGER original_work_case_no_update BEFORE UPDATE ON original_work_case_operations
BEGIN SELECT RAISE(ABORT,'Original work case operations are immutable'); END;
CREATE TRIGGER original_work_case_no_delete BEFORE DELETE ON original_work_case_operations
BEGIN SELECT RAISE(ABORT,'Original work case operations are immutable'); END;

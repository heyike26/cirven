-- Frozen retest intents point to one existing original MESSAGE operation.
-- Actual execution remains in the original owner and its durable journal.
CREATE TABLE original_work_retest_intents (
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    root_operation_id TEXT NOT NULL,
    request_json TEXT NOT NULL CHECK(length(CAST(request_json AS BLOB)) <= 32768),
    request_sha256 TEXT NOT NULL,
    preparation_json TEXT NOT NULL CHECK(length(CAST(preparation_json AS BLOB)) <= 65536),
    preparation_sha256 TEXT NOT NULL,
    native_request_json TEXT NOT NULL CHECK(length(CAST(native_request_json AS BLOB)) <= 32768),
    native_request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id,operation_id)
);
CREATE INDEX original_work_retest_roots ON original_work_retest_intents(project_id,session_id,root_operation_id);
CREATE TRIGGER original_work_retest_no_update BEFORE UPDATE ON original_work_retest_intents
BEGIN SELECT RAISE(ABORT,'Original work retest intents are immutable'); END;
CREATE TRIGGER original_work_retest_no_delete BEFORE DELETE ON original_work_retest_intents
BEGIN SELECT RAISE(ABORT,'Original work retest intents are immutable'); END;

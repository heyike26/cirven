CREATE TABLE project_taskbooks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    current_revision INTEGER NOT NULL CHECK(current_revision > 0)
);
CREATE TABLE project_taskbook_versions (
    taskbook_id TEXT NOT NULL REFERENCES project_taskbooks(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(taskbook_id, revision)
);
CREATE TABLE project_taskbook_operations (
    project_id TEXT NOT NULL REFERENCES projects(id),
    idempotency_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    result TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(project_id, idempotency_key)
);
CREATE TRIGGER project_taskbook_versions_no_update BEFORE UPDATE ON project_taskbook_versions
BEGIN SELECT RAISE(ABORT, 'Taskbook revisions are immutable'); END;
CREATE TRIGGER project_taskbook_versions_no_delete BEFORE DELETE ON project_taskbook_versions
BEGIN SELECT RAISE(ABORT, 'Taskbook revisions are immutable'); END;
CREATE TRIGGER project_taskbook_operations_no_update BEFORE UPDATE ON project_taskbook_operations
BEGIN SELECT RAISE(ABORT, 'Taskbook operations are immutable'); END;
CREATE TRIGGER project_taskbook_operations_no_delete BEFORE DELETE ON project_taskbook_operations
BEGIN SELECT RAISE(ABORT, 'Taskbook operations are immutable'); END;

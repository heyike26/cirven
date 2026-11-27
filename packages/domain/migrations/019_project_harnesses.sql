CREATE TABLE project_harnesses (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    current_revision INTEGER NOT NULL CHECK(current_revision > 0)
);
CREATE TABLE project_harness_versions (
    harness_id TEXT NOT NULL REFERENCES project_harnesses(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(harness_id,revision)
);
CREATE TABLE project_harness_operations (
    project_id TEXT NOT NULL REFERENCES projects(id),
    idempotency_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    result TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY(project_id,idempotency_key)
);
CREATE TABLE project_harness_node_starts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    harness_id TEXT NOT NULL,
    harness_revision INTEGER NOT NULL,
    harness_sha256 TEXT NOT NULL,
    node_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES task_runs(id),
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(project_id,idempotency_key),
    FOREIGN KEY(harness_id,harness_revision) REFERENCES project_harness_versions(harness_id,revision)
);
CREATE TRIGGER project_harness_versions_no_update BEFORE UPDATE ON project_harness_versions
BEGIN SELECT RAISE(ABORT, 'Harness revisions are immutable'); END;
CREATE TRIGGER project_harness_versions_no_delete BEFORE DELETE ON project_harness_versions
BEGIN SELECT RAISE(ABORT, 'Harness revisions are immutable'); END;
CREATE TRIGGER project_harness_operations_no_update BEFORE UPDATE ON project_harness_operations
BEGIN SELECT RAISE(ABORT, 'Harness operations are immutable'); END;
CREATE TRIGGER project_harness_operations_no_delete BEFORE DELETE ON project_harness_operations
BEGIN SELECT RAISE(ABORT, 'Harness operations are immutable'); END;
CREATE TRIGGER project_harness_node_starts_no_update BEFORE UPDATE ON project_harness_node_starts
BEGIN SELECT RAISE(ABORT, 'Harness starts are immutable'); END;
CREATE TRIGGER project_harness_node_starts_no_delete BEFORE DELETE ON project_harness_node_starts
BEGIN SELECT RAISE(ABORT, 'Harness starts are immutable'); END;

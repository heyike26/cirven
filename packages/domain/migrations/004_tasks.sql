CREATE TABLE tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    current_revision INTEGER NOT NULL
);
CREATE TABLE task_versions (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    revision INTEGER NOT NULL,
    spec TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(task_id,revision)
);
CREATE TRIGGER task_versions_no_update BEFORE UPDATE ON task_versions
BEGIN
    SELECT RAISE(ABORT, 'Task versions are immutable');
END;
CREATE TRIGGER task_versions_no_delete BEFORE DELETE ON task_versions
BEGIN
    SELECT RAISE(ABORT, 'Task versions are immutable');
END;
CREATE TABLE task_dependencies (
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL,
    dependency_id TEXT NOT NULL,
    dependency_revision INTEGER NOT NULL,
    PRIMARY KEY(task_id,task_revision,dependency_id),
    FOREIGN KEY(task_id,task_revision) REFERENCES task_versions(task_id,revision),
    FOREIGN KEY(dependency_id,dependency_revision) REFERENCES task_versions(task_id,revision)
);
CREATE TABLE task_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL,
    attempt_id INTEGER NOT NULL,
    operation_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    spec_hash TEXT NOT NULL,
    stack_lock_hash TEXT NOT NULL,
    frozen_inputs TEXT NOT NULL,
    workspace TEXT NOT NULL,
    execution_status TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    worker_id TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    error_type TEXT,
    UNIQUE(project_id,operation_id),
    FOREIGN KEY(task_id,task_revision) REFERENCES task_versions(task_id,revision)
);
CREATE TABLE run_events (
    run_id TEXT NOT NULL REFERENCES task_runs(id),
    event_seq INTEGER NOT NULL,
    event_key TEXT NOT NULL,
    payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id,event_seq),
    UNIQUE(run_id,event_key)
);

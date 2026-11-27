CREATE TABLE project_intakes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    current_revision INTEGER NOT NULL
);
CREATE TABLE project_intake_versions (
    intake_id TEXT NOT NULL REFERENCES project_intakes(id),
    revision INTEGER NOT NULL,
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (intake_id, revision)
);
CREATE TRIGGER project_intake_versions_no_update BEFORE UPDATE ON project_intake_versions
BEGIN
    SELECT RAISE(ABORT, 'Intake source snapshots are immutable');
END;
CREATE TRIGGER project_intake_versions_no_delete BEFORE DELETE ON project_intake_versions
BEGIN
    SELECT RAISE(ABORT, 'Intake source snapshots are immutable');
END;
CREATE TABLE project_intake_operations (
    project_id TEXT NOT NULL REFERENCES projects(id),
    idempotency_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    intake_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    PRIMARY KEY (project_id, idempotency_key)
);
CREATE TABLE project_intake_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    intake_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    kind TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

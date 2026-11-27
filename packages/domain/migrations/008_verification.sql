CREATE TABLE verification_baselines (
    run_id TEXT PRIMARY KEY REFERENCES task_runs(id),
    baseline_path TEXT NOT NULL,
    baseline_hash TEXT NOT NULL,
    expected_path TEXT NOT NULL,
    expected_hash TEXT NOT NULL,
    test_ids TEXT NOT NULL
);
CREATE TRIGGER verification_baseline_no_update BEFORE UPDATE ON verification_baselines
BEGIN
    SELECT RAISE(ABORT, 'Approved verification baselines are immutable');
END;
CREATE TABLE verification_evidence (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    run_id TEXT NOT NULL REFERENCES task_runs(id),
    payload TEXT NOT NULL,
    result TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER verification_evidence_no_update BEFORE UPDATE ON verification_evidence
BEGIN
    SELECT RAISE(ABORT, 'Verification evidence is immutable');
END;
CREATE TRIGGER verification_evidence_no_delete BEFORE DELETE ON verification_evidence
BEGIN
    SELECT RAISE(ABORT, 'Verification evidence is immutable');
END;
CREATE TABLE repair_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL,
    initial_run_id TEXT NOT NULL REFERENCES task_runs(id),
    current_run_id TEXT NOT NULL REFERENCES task_runs(id),
    max_repairs INTEGER NOT NULL,
    repairs_started INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    ended_at TEXT
);

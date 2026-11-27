CREATE TABLE extension_snapshots (
    id TEXT PRIMARY KEY, provider TEXT NOT NULL, version TEXT NOT NULL,
    manifest TEXT NOT NULL, files_hash TEXT NOT NULL, directory TEXT NOT NULL,
    created_at TEXT NOT NULL, UNIQUE(provider, version)
);
CREATE TABLE extension_evidence (
    id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES extension_snapshots(id),
    payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE extension_stable (
    provider TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES extension_snapshots(id)
);
CREATE TABLE extension_reviews (
    id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL REFERENCES extension_snapshots(id),
    action TEXT NOT NULL, action_hash TEXT NOT NULL, expires_at TEXT NOT NULL,
    status TEXT NOT NULL, created_at TEXT NOT NULL, decided_at TEXT
);
CREATE TABLE extension_actions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id TEXT NOT NULL REFERENCES extension_snapshots(id),
    action TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE extension_bindings (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    snapshot_id TEXT NOT NULL REFERENCES extension_snapshots(id), lock_payload TEXT NOT NULL,
    lock_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE project_extension_binding (
    project_id TEXT PRIMARY KEY REFERENCES projects(id), binding_id TEXT NOT NULL REFERENCES extension_bindings(id)
);
CREATE TRIGGER extension_snapshot_immutable BEFORE UPDATE ON extension_snapshots
BEGIN SELECT RAISE(ABORT, 'Extension snapshot is immutable'); END;
CREATE TRIGGER extension_evidence_immutable BEFORE UPDATE ON extension_evidence
BEGIN SELECT RAISE(ABORT, 'Extension evidence is immutable'); END;
CREATE TRIGGER extension_binding_immutable BEFORE UPDATE ON extension_bindings
BEGIN SELECT RAISE(ABORT, 'Extension binding is immutable'); END;
CREATE TRIGGER extension_review_scope_immutable BEFORE UPDATE OF snapshot_id, action, action_hash, expires_at ON extension_reviews
BEGIN SELECT RAISE(ABORT, 'Extension approval scope is immutable'); END;

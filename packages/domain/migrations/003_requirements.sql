CREATE TABLE requirement_sets (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    current_revision INTEGER NOT NULL
);
CREATE TABLE requirement_versions (
    requirement_id TEXT NOT NULL REFERENCES requirement_sets(id),
    revision INTEGER NOT NULL,
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(requirement_id,revision)
);
CREATE TRIGGER requirement_versions_no_update BEFORE UPDATE ON requirement_versions
BEGIN
    SELECT RAISE(ABORT, 'Requirement versions are immutable');
END;
CREATE TRIGGER requirement_versions_no_delete BEFORE DELETE ON requirement_versions
BEGIN
    SELECT RAISE(ABORT, 'Requirement versions are immutable');
END;
CREATE TABLE planning_tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    requirement_id TEXT NOT NULL REFERENCES requirement_sets(id),
    requirement_revision INTEGER NOT NULL,
    requirement_key TEXT NOT NULL,
    goal TEXT NOT NULL,
    requirement_hash TEXT NOT NULL,
    acceptance TEXT NOT NULL,
    acceptance_hash TEXT NOT NULL,
    review_status TEXT NOT NULL CHECK(review_status IN ('DRAFT','NEEDS_REVIEW')),
    UNIQUE(requirement_id,requirement_revision,requirement_key)
);

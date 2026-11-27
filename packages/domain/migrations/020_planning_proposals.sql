CREATE TABLE planning_proposals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    current_revision INTEGER NOT NULL CHECK(current_revision > 0)
);
CREATE TABLE planning_proposal_versions (
    proposal_id TEXT NOT NULL REFERENCES planning_proposals(id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(proposal_id,revision)
);
CREATE TABLE planning_proposal_operations (
    project_id TEXT NOT NULL REFERENCES projects(id),
    operation_id TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PROCESSING','COMPLETED','FAILED')),
    result TEXT,
    result_sha256 TEXT,
    PRIMARY KEY(project_id,operation_id)
);
CREATE TABLE planning_proposal_answers (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    proposal_revision INTEGER NOT NULL,
    question_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    FOREIGN KEY(proposal_id,proposal_revision) REFERENCES planning_proposal_versions(proposal_id,revision)
);
CREATE TABLE planning_proposal_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    proposal_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL
);
CREATE TRIGGER planning_versions_no_update BEFORE UPDATE ON planning_proposal_versions
BEGIN SELECT RAISE(ABORT,'Planning versions are immutable'); END;
CREATE TRIGGER planning_versions_no_delete BEFORE DELETE ON planning_proposal_versions
BEGIN SELECT RAISE(ABORT,'Planning versions are immutable'); END;
CREATE TRIGGER planning_answers_no_update BEFORE UPDATE ON planning_proposal_answers
BEGIN SELECT RAISE(ABORT,'Planning answers are immutable'); END;
CREATE TRIGGER planning_answers_no_delete BEFORE DELETE ON planning_proposal_answers
BEGIN SELECT RAISE(ABORT,'Planning answers are immutable'); END;
CREATE TRIGGER planning_events_no_update BEFORE UPDATE ON planning_proposal_events
BEGIN SELECT RAISE(ABORT,'Planning events are immutable'); END;
CREATE TRIGGER planning_events_no_delete BEFORE DELETE ON planning_proposal_events
BEGIN SELECT RAISE(ABORT,'Planning events are immutable'); END;
CREATE TRIGGER planning_operations_identity BEFORE UPDATE ON planning_proposal_operations
WHEN NEW.project_id != OLD.project_id OR NEW.operation_id != OLD.operation_id
OR NEW.request_sha256 != OLD.request_sha256 OR NEW.proposal_id != OLD.proposal_id
OR OLD.status != 'PROCESSING'
BEGIN SELECT RAISE(ABORT,'Planning operation identity/result are immutable'); END;
CREATE TRIGGER planning_operations_no_delete BEFORE DELETE ON planning_proposal_operations
BEGIN SELECT RAISE(ABORT,'Planning operations are immutable'); END;

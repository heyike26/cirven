CREATE TABLE project_questions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id),
    current_revision INTEGER NOT NULL CHECK (current_revision >= 1)
);
CREATE TABLE project_question_versions (
    question_id TEXT NOT NULL REFERENCES project_questions(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    semantic_key TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (question_id, revision)
);
CREATE TABLE project_question_task_refs (
    question_id TEXT NOT NULL,
    question_revision INTEGER NOT NULL,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    task_revision INTEGER NOT NULL,
    spec_hash TEXT NOT NULL,
    PRIMARY KEY (question_id, question_revision, task_id),
    FOREIGN KEY (question_id, question_revision)
        REFERENCES project_question_versions(question_id, revision)
);
CREATE INDEX project_question_task_lookup ON project_question_task_refs(task_id, task_revision);
CREATE TABLE project_answers (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    question_id TEXT NOT NULL REFERENCES project_questions(id),
    current_revision INTEGER NOT NULL CHECK (current_revision >= 1)
);
CREATE TABLE project_answer_versions (
    answer_id TEXT NOT NULL REFERENCES project_answers(id),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (answer_id, revision)
);
CREATE TABLE project_question_operations (
    project_id TEXT NOT NULL REFERENCES projects(id),
    idempotency_key TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    receipt TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    PRIMARY KEY (project_id, idempotency_key)
);
CREATE TABLE project_question_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    question_id TEXT NOT NULL REFERENCES project_questions(id),
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER project_question_versions_no_update BEFORE UPDATE ON project_question_versions
BEGIN SELECT RAISE(ABORT, 'Question versions are immutable'); END;
CREATE TRIGGER project_question_versions_no_delete BEFORE DELETE ON project_question_versions
BEGIN SELECT RAISE(ABORT, 'Question versions are immutable'); END;
CREATE TRIGGER project_question_task_refs_no_update BEFORE UPDATE ON project_question_task_refs
BEGIN SELECT RAISE(ABORT, 'Question task scopes are immutable'); END;
CREATE TRIGGER project_question_task_refs_no_delete BEFORE DELETE ON project_question_task_refs
BEGIN SELECT RAISE(ABORT, 'Question task scopes are immutable'); END;
CREATE TRIGGER project_answer_versions_no_update BEFORE UPDATE ON project_answer_versions
BEGIN SELECT RAISE(ABORT, 'Answer versions are immutable'); END;
CREATE TRIGGER project_answer_versions_no_delete BEFORE DELETE ON project_answer_versions
BEGIN SELECT RAISE(ABORT, 'Answer versions are immutable'); END;
CREATE TRIGGER project_question_operations_no_update BEFORE UPDATE ON project_question_operations
BEGIN SELECT RAISE(ABORT, 'Question operation receipts are immutable'); END;
CREATE TRIGGER project_question_operations_no_delete BEFORE DELETE ON project_question_operations
BEGIN SELECT RAISE(ABORT, 'Question operation receipts are immutable'); END;
CREATE TRIGGER project_question_events_no_update BEFORE UPDATE ON project_question_events
BEGIN SELECT RAISE(ABORT, 'Question events are immutable'); END;
CREATE TRIGGER project_question_events_no_delete BEFORE DELETE ON project_question_events
BEGIN SELECT RAISE(ABORT, 'Question events are immutable'); END;
CREATE TRIGGER project_questions_identity_no_update BEFORE UPDATE OF id,project_id,seq ON project_questions
BEGIN SELECT RAISE(ABORT, 'Question parent identities are immutable'); END;
CREATE TRIGGER project_questions_no_delete BEFORE DELETE ON project_questions
BEGIN SELECT RAISE(ABORT, 'Question parent identities are immutable'); END;
CREATE TRIGGER project_answers_identity_no_update BEFORE UPDATE OF id,question_id,seq ON project_answers
BEGIN SELECT RAISE(ABORT, 'Answer parent identities are immutable'); END;
CREATE TRIGGER project_answers_no_delete BEFORE DELETE ON project_answers
BEGIN SELECT RAISE(ABORT, 'Answer parent identities are immutable'); END;

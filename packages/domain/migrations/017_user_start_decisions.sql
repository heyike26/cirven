CREATE TABLE project_question_resolutions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id),
    question_id TEXT NOT NULL REFERENCES project_questions(id),
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL,
    spec_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id,task_revision) REFERENCES task_versions(task_id,revision)
);
CREATE INDEX project_resolution_scope ON project_question_resolutions(project_id,question_id,task_id,task_revision,spec_hash,seq);
CREATE TABLE project_start_decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id),
    task_id TEXT NOT NULL,
    task_revision INTEGER NOT NULL,
    spec_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (task_id,task_revision) REFERENCES task_versions(task_id,revision)
);
CREATE INDEX project_start_scope ON project_start_decisions(project_id,task_id,task_revision,spec_hash,seq);
CREATE TRIGGER project_question_resolutions_no_update BEFORE UPDATE ON project_question_resolutions
BEGIN SELECT RAISE(ABORT, 'Question resolutions are immutable'); END;
CREATE TRIGGER project_question_resolutions_no_delete BEFORE DELETE ON project_question_resolutions
BEGIN SELECT RAISE(ABORT, 'Question resolutions are immutable'); END;
CREATE TRIGGER project_start_decisions_no_update BEFORE UPDATE ON project_start_decisions
BEGIN SELECT RAISE(ABORT, 'User start decisions are immutable'); END;
CREATE TRIGGER project_start_decisions_no_delete BEFORE DELETE ON project_start_decisions
BEGIN SELECT RAISE(ABORT, 'User start decisions are immutable'); END;

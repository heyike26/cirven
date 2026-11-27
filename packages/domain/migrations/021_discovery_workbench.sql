CREATE TABLE discovery_sessions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 current_revision INTEGER NOT NULL CHECK(current_revision>0)
);
CREATE TABLE discovery_versions (
 session_id TEXT NOT NULL REFERENCES discovery_sessions(id),
 revision INTEGER NOT NULL CHECK(revision>0), payload TEXT NOT NULL,
 sha256 TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(session_id,revision)
);
CREATE TABLE discovery_operations (
 project_id TEXT NOT NULL REFERENCES projects(id), operation_id TEXT NOT NULL,
 session_id TEXT NOT NULL, kind TEXT NOT NULL, request_sha256 TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('PROCESSING','COMPLETED','FAILED','CANCELLED','UNKNOWN_OUTCOME')),
 result TEXT, result_sha256 TEXT, PRIMARY KEY(project_id,operation_id)
);
CREATE TABLE discovery_answers (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, session_revision INTEGER NOT NULL,
 question_id TEXT NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
 FOREIGN KEY(session_id,session_revision) REFERENCES discovery_versions(session_id,revision)
);
CREATE TABLE discovery_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT,project_id TEXT NOT NULL REFERENCES projects(id),
 session_id TEXT NOT NULL,operation_id TEXT NOT NULL,payload TEXT NOT NULL,sha256 TEXT NOT NULL
);
CREATE TRIGGER discovery_versions_no_update BEFORE UPDATE ON discovery_versions
 BEGIN SELECT RAISE(ABORT,'Discovery versions are immutable'); END;
CREATE TRIGGER discovery_versions_no_delete BEFORE DELETE ON discovery_versions
 BEGIN SELECT RAISE(ABORT,'Discovery versions are immutable'); END;
CREATE TRIGGER discovery_answers_no_update BEFORE UPDATE ON discovery_answers
 BEGIN SELECT RAISE(ABORT,'Discovery answers are immutable'); END;
CREATE TRIGGER discovery_answers_no_delete BEFORE DELETE ON discovery_answers
 BEGIN SELECT RAISE(ABORT,'Discovery answers are immutable'); END;
CREATE TRIGGER discovery_events_no_update BEFORE UPDATE ON discovery_events
 BEGIN SELECT RAISE(ABORT,'Discovery events are immutable'); END;
CREATE TRIGGER discovery_events_no_delete BEFORE DELETE ON discovery_events
 BEGIN SELECT RAISE(ABORT,'Discovery events are immutable'); END;
CREATE TRIGGER discovery_operations_identity BEFORE UPDATE ON discovery_operations
 WHEN NEW.project_id!=OLD.project_id OR NEW.operation_id!=OLD.operation_id
 OR NEW.session_id!=OLD.session_id OR NEW.kind!=OLD.kind OR NEW.request_sha256!=OLD.request_sha256
 OR OLD.status!='PROCESSING'
 BEGIN SELECT RAISE(ABORT,'Discovery operation identity/result are immutable'); END;
CREATE TRIGGER discovery_operations_no_delete BEFORE DELETE ON discovery_operations
 BEGIN SELECT RAISE(ABORT,'Discovery operations are immutable'); END;

CREATE TABLE workbench_state_heads (
 project_id TEXT NOT NULL REFERENCES projects(id), key_sha256 TEXT NOT NULL,
 key_json TEXT NOT NULL, current_revision INTEGER NOT NULL CHECK(current_revision>0),
 PRIMARY KEY(project_id,key_sha256)
);
CREATE TABLE workbench_state_versions (
 project_id TEXT NOT NULL, key_sha256 TEXT NOT NULL,
 state_revision INTEGER NOT NULL CHECK(state_revision>0),
 payload TEXT NOT NULL, sha256 TEXT NOT NULL,
 dirty INTEGER NOT NULL CHECK(dirty IN (0,1)), updated_at TEXT NOT NULL,
 PRIMARY KEY(project_id,key_sha256,state_revision),
 FOREIGN KEY(project_id,key_sha256) REFERENCES workbench_state_heads(project_id,key_sha256)
);
CREATE TABLE workbench_state_operations (
 project_id TEXT NOT NULL REFERENCES projects(id), operation_id TEXT NOT NULL,
 request_sha256 TEXT NOT NULL, result TEXT NOT NULL, result_sha256 TEXT NOT NULL,
 PRIMARY KEY(project_id,operation_id)
);
CREATE INDEX workbench_state_dirty ON workbench_state_versions(project_id,dirty,key_sha256,state_revision);
CREATE TRIGGER workbench_state_heads_identity BEFORE UPDATE ON workbench_state_heads
 WHEN NEW.project_id!=OLD.project_id OR NEW.key_sha256!=OLD.key_sha256
 OR NEW.key_json!=OLD.key_json OR NEW.current_revision!=OLD.current_revision+1
 BEGIN SELECT RAISE(ABORT,'Workbench state key is immutable and revisions advance once'); END;
CREATE TRIGGER workbench_state_heads_no_delete BEFORE DELETE ON workbench_state_heads
 BEGIN SELECT RAISE(ABORT,'Workbench presentation must not be silently deleted'); END;
CREATE TRIGGER workbench_state_versions_no_update BEFORE UPDATE ON workbench_state_versions
 BEGIN SELECT RAISE(ABORT,'Workbench presentation snapshots are immutable'); END;
CREATE TRIGGER workbench_state_versions_no_delete BEFORE DELETE ON workbench_state_versions
 BEGIN SELECT RAISE(ABORT,'Workbench presentation snapshots are immutable'); END;
CREATE TRIGGER workbench_state_operations_no_update BEFORE UPDATE ON workbench_state_operations
 BEGIN SELECT RAISE(ABORT,'Workbench operation receipts are immutable'); END;
CREATE TRIGGER workbench_state_operations_no_delete BEFORE DELETE ON workbench_state_operations
 BEGIN SELECT RAISE(ABORT,'Workbench operation receipts are immutable'); END;

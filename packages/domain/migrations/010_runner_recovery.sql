ALTER TABLE runner_jobs ADD COLUMN journal_path TEXT;
ALTER TABLE runner_jobs ADD COLUMN request_hash TEXT;
ALTER TABLE runner_jobs ADD COLUMN supervisor_pid INTEGER;
ALTER TABLE runner_jobs ADD COLUMN supervisor_created REAL;
ALTER TABLE runner_jobs ADD COLUMN child_created REAL;

CREATE TRIGGER runner_scope_immutable BEFORE UPDATE OF run_id, command_hash, workspace,
    stdout_path, stderr_path, journal_path, request_hash ON runner_jobs
BEGIN SELECT RAISE(ABORT, 'Runner scope is immutable'); END;

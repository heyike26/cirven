ALTER TABLE task_runs ADD COLUMN runner_job_id TEXT;
ALTER TABLE task_runs ADD COLUMN session_id TEXT;
ALTER TABLE task_runs ADD COLUMN heartbeat_at TEXT;
CREATE TABLE runner_jobs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES task_runs(id),
    pid INTEGER,
    command_hash TEXT NOT NULL,
    workspace TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    exit_code INTEGER,
    error_type TEXT,
    stdout_path TEXT NOT NULL,
    stderr_path TEXT NOT NULL
);

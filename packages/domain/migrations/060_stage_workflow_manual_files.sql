CREATE TABLE stage_workflow_manual_files (
    run_id TEXT NOT NULL REFERENCES stage_workflow_runs(id),
    sha256 TEXT NOT NULL,
    bytes INTEGER NOT NULL CHECK(bytes > 0 AND bytes <= 1048576),
    content BLOB NOT NULL CHECK(length(content) = bytes),
    PRIMARY KEY(run_id, sha256)
);

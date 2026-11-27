CREATE TABLE project_operations (
    operation_id TEXT PRIMARY KEY,
    request_sha256 TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id),
    result_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL
);

-- A current organization acknowledgement never rewrites an SDK open request.
CREATE TABLE configuration_adoptions (
    conversation_id TEXT NOT NULL REFERENCES conversation_threads(conversation_id),
    operation_id TEXT NOT NULL,
    original_project_key TEXT NOT NULL,
    original_operation_id TEXT NOT NULL,
    request_json TEXT NOT NULL CHECK (length(CAST(request_json AS BLOB)) <= 16384),
    request_sha256 TEXT NOT NULL,
    authority_json TEXT NOT NULL CHECK (length(CAST(authority_json AS BLOB)) <= 32768),
    authority_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id,operation_id)
);
CREATE TABLE configuration_adopted_scopes (
    conversation_id TEXT NOT NULL,
    original_project_key TEXT NOT NULL,
    original_operation_id TEXT NOT NULL,
    adoption_operation_id TEXT NOT NULL,
    PRIMARY KEY (conversation_id,original_project_key,original_operation_id),
    FOREIGN KEY (conversation_id,adoption_operation_id)
        REFERENCES configuration_adoptions(conversation_id,operation_id)
);

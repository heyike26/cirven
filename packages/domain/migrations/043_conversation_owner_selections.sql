-- Selecting an existing actor is metadata, independent of SDK/configuration opens.
CREATE TABLE conversation_owner_selections (
    conversation_id TEXT NOT NULL REFERENCES conversation_threads(conversation_id),
    operation_id TEXT NOT NULL,
    request_json TEXT NOT NULL CHECK (length(CAST(request_json AS BLOB)) <= 8192),
    request_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, operation_id)
);
CREATE TABLE conversation_selected_owners (
    conversation_id TEXT PRIMARY KEY REFERENCES conversation_threads(conversation_id),
    operation_id TEXT NOT NULL,
    FOREIGN KEY (conversation_id, operation_id)
        REFERENCES conversation_owner_selections(conversation_id, operation_id)
);

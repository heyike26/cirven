CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    filename TEXT NOT NULL,
    current_revision INTEGER NOT NULL,
    deleted_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE document_versions (
    document_id TEXT NOT NULL REFERENCES documents(id),
    revision INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    raw_bytes BLOB NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(document_id,revision)
);
CREATE TRIGGER document_versions_no_update BEFORE UPDATE ON document_versions
BEGIN
    SELECT RAISE(ABORT, 'Document versions are immutable');
END;
CREATE TRIGGER document_versions_no_delete BEFORE DELETE ON document_versions
BEGIN
    SELECT RAISE(ABORT, 'Document versions are immutable');
END;

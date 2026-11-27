CREATE TABLE document_parses (
    document_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    format TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    outline TEXT NOT NULL,
    graph TEXT NOT NULL,
    warnings TEXT NOT NULL,
    PRIMARY KEY(document_id, revision),
    FOREIGN KEY(document_id, revision) REFERENCES document_versions(document_id, revision)
);
CREATE TABLE document_assets (
    document_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    asset_id TEXT NOT NULL,
    member_path TEXT NOT NULL,
    media_type TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    byte_length INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    raw_bytes BLOB NOT NULL,
    PRIMARY KEY(document_id, revision, asset_id),
    FOREIGN KEY(document_id, revision) REFERENCES document_versions(document_id, revision)
);
CREATE TRIGGER document_parses_no_update BEFORE UPDATE ON document_parses
BEGIN SELECT RAISE(ABORT, 'Document parses are immutable'); END;
CREATE TRIGGER document_parses_no_delete BEFORE DELETE ON document_parses
BEGIN SELECT RAISE(ABORT, 'Document parses are immutable'); END;
CREATE TRIGGER document_assets_no_update BEFORE UPDATE ON document_assets
BEGIN SELECT RAISE(ABORT, 'Document assets are immutable'); END;
CREATE TRIGGER document_assets_no_delete BEFORE DELETE ON document_assets
BEGIN SELECT RAISE(ABORT, 'Document assets are immutable'); END;

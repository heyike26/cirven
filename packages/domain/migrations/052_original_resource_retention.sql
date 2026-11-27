-- Bounded attribution into original immutable events, never a second transcript.
CREATE TABLE original_resource_messages (
    project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT NOT NULL,
    session_sha256 TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
    native_session_id TEXT NOT NULL, message_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user','assistant','unresolved')),
    parent_user_message_id TEXT, resource_json TEXT,
    pending_bytes INTEGER NOT NULL CHECK(pending_bytes>=0),
    accounting_unconfirmed INTEGER NOT NULL CHECK(accounting_unconfirmed IN (0,1)),
    last_event_seq INTEGER NOT NULL CHECK(last_event_seq>=0), last_event_sha256 TEXT,
    PRIMARY KEY(project_id,session_id,generation,native_session_id,message_id)
);
CREATE INDEX original_resource_message_parent ON original_resource_messages(project_id,session_id,generation,native_session_id,parent_user_message_id);
CREATE TABLE original_resource_children (
    project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT NOT NULL,
    session_sha256 TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
    native_session_id TEXT NOT NULL, resource_json TEXT NOT NULL,
    parent_native_session_id TEXT NOT NULL, assistant_message_id TEXT NOT NULL,
    source_part_sha256 TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id,generation,native_session_id)
);
CREATE TABLE original_resource_gaps (
    project_id TEXT NOT NULL REFERENCES projects(id), session_id TEXT NOT NULL,
    first_event_seq INTEGER NOT NULL CHECK(first_event_seq>=0), reason TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id)
);

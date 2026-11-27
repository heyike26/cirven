-- Derived locations into the original immutable session events, never a transcript.
CREATE TABLE original_message_locations (
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL,
    native_session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    slot_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('INFO','TEXT','OTHER','REMOVED','DIRTY','MESSAGE_REMOVED')),
    event_seq INTEGER NOT NULL CHECK(event_seq>0),
    event_sha256 TEXT NOT NULL,
    value_sha256 TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK(generation>0),
    PRIMARY KEY(project_id,session_id,native_session_id,message_id,slot_id)
);
CREATE INDEX original_message_locations_message ON original_message_locations(project_id,session_id,native_session_id,message_id,event_seq);
CREATE TABLE original_message_location_gaps (
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL,
    first_gap_seq INTEGER NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id)
);
ALTER TABLE context_conversation_links ADD COLUMN navigation_only INTEGER NOT NULL DEFAULT 0 CHECK(navigation_only IN (0,1));

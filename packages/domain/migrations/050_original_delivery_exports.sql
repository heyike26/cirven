-- Native Save As is a separate original operation, never a new delivery/build.
CREATE TABLE original_delivery_export_intents (
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    root_operation_id TEXT NOT NULL,
    delivery_operation_id TEXT NOT NULL,
    request_json TEXT NOT NULL CHECK(length(CAST(request_json AS BLOB))<=8192),
    request_sha256 TEXT NOT NULL,
    descriptor_json TEXT NOT NULL CHECK(length(CAST(descriptor_json AS BLOB))<=16384),
    descriptor_sha256 TEXT NOT NULL,
    origin_client_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id,operation_id),
    FOREIGN KEY(project_id,session_id,delivery_operation_id)
        REFERENCES original_work_delivery_intents(project_id,session_id,operation_id)
);
CREATE INDEX original_delivery_export_root
    ON original_delivery_export_intents(project_id,session_id,root_operation_id,created_at,operation_id);
CREATE TABLE original_delivery_export_events (
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('CHOICE','DESTINATION','CREATED','RESULT','RECOVERY')),
    event_json TEXT NOT NULL CHECK(length(CAST(event_json AS BLOB))<=65536),
    event_sha256 TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id,operation_id,kind),
    FOREIGN KEY(project_id,session_id,operation_id)
        REFERENCES original_delivery_export_intents(project_id,session_id,operation_id)
);
CREATE TRIGGER original_delivery_export_intent_no_update BEFORE UPDATE ON original_delivery_export_intents
BEGIN SELECT RAISE(ABORT,'Original export intents are immutable'); END;
CREATE TRIGGER original_delivery_export_intent_no_delete BEFORE DELETE ON original_delivery_export_intents
BEGIN SELECT RAISE(ABORT,'Original export intents are immutable'); END;
CREATE TRIGGER original_delivery_export_event_no_update BEFORE UPDATE ON original_delivery_export_events
BEGIN SELECT RAISE(ABORT,'Original export events are immutable'); END;
CREATE TRIGGER original_delivery_export_event_no_delete BEFORE DELETE ON original_delivery_export_events
BEGIN SELECT RAISE(ABORT,'Original export events are immutable'); END;

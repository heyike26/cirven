CREATE TABLE conversation_message_admissions (
 event_key TEXT PRIMARY KEY,
 conversation_id TEXT NOT NULL REFERENCES conversation_threads(conversation_id),
 source_alias_id TEXT NOT NULL REFERENCES conversation_source_aliases(alias_id),
 operation_id TEXT NOT NULL,
 request_sha256 TEXT NOT NULL,
 scope_json TEXT NOT NULL,
 scope_sha256 TEXT NOT NULL,
 organization_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE INDEX conversation_admissions_by_thread ON conversation_message_admissions(conversation_id,created_at,event_key);

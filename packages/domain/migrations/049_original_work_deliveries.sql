-- Immutable selected saved-file engineering deliveries on an original work root.
-- Packaging is not a build/deployment/acceptance decision or execution grant.
CREATE TABLE original_work_delivery_intents (
    project_id TEXT NOT NULL REFERENCES projects(id),
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    root_operation_id TEXT NOT NULL,
    root_sha256 TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK(expected_revision>=0),
    request_json TEXT NOT NULL CHECK(length(CAST(request_json AS BLOB))<=131072),
    request_sha256 TEXT NOT NULL,
    manifest_json TEXT NOT NULL CHECK(length(CAST(manifest_json AS BLOB))<=2097152),
    manifest_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id,operation_id)
);
CREATE INDEX original_work_deliveries_by_root
    ON original_work_delivery_intents(project_id,session_id,root_operation_id);
CREATE TABLE original_work_delivery_report_blobs (
    project_id TEXT NOT NULL REFERENCES projects(id),
    sha256 TEXT NOT NULL,
    raw_bytes BLOB NOT NULL CHECK(length(raw_bytes)<=262144),
    PRIMARY KEY(project_id,sha256)
);
CREATE TABLE original_work_delivery_receipts (
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    root_operation_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision>=1),
    receipt_json TEXT NOT NULL CHECK(length(CAST(receipt_json AS BLOB))<=131072),
    receipt_sha256 TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id,operation_id),
    UNIQUE(project_id,session_id,root_operation_id,revision),
    FOREIGN KEY(project_id,session_id,operation_id)
        REFERENCES original_work_delivery_intents(project_id,session_id,operation_id)
);
CREATE TABLE original_work_delivery_retentions (
    project_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    receipt_json TEXT NOT NULL CHECK(length(CAST(receipt_json AS BLOB))<=16384),
    receipt_sha256 TEXT NOT NULL,
    PRIMARY KEY(project_id,session_id,operation_id),
    FOREIGN KEY(project_id,session_id,operation_id)
        REFERENCES original_work_delivery_intents(project_id,session_id,operation_id)
);
CREATE TRIGGER original_delivery_intent_no_update BEFORE UPDATE ON original_work_delivery_intents
BEGIN SELECT RAISE(ABORT,'Delivery original intents are immutable'); END;
CREATE TRIGGER original_delivery_intent_no_delete BEFORE DELETE ON original_work_delivery_intents
BEGIN SELECT RAISE(ABORT,'Delivery original intents are immutable'); END;
CREATE TRIGGER original_delivery_receipt_no_update BEFORE UPDATE ON original_work_delivery_receipts
BEGIN SELECT RAISE(ABORT,'Delivery receipts are immutable'); END;
CREATE TRIGGER original_delivery_receipt_no_delete BEFORE DELETE ON original_work_delivery_receipts
BEGIN SELECT RAISE(ABORT,'Delivery receipts are immutable'); END;
CREATE TRIGGER original_delivery_retention_no_update BEFORE UPDATE ON original_work_delivery_retentions
BEGIN SELECT RAISE(ABORT,'Delivery retained outcomes are immutable'); END;
CREATE TRIGGER original_delivery_retention_no_delete BEFORE DELETE ON original_work_delivery_retentions
BEGIN SELECT RAISE(ABORT,'Delivery retained outcomes are immutable'); END;
CREATE TRIGGER original_delivery_report_no_update BEFORE UPDATE ON original_work_delivery_report_blobs
BEGIN SELECT RAISE(ABORT,'Delivery original report snapshots are immutable'); END;
CREATE TRIGGER original_delivery_report_no_delete BEFORE DELETE ON original_work_delivery_report_blobs
BEGIN SELECT RAISE(ABORT,'Delivery original report snapshots are immutable'); END;

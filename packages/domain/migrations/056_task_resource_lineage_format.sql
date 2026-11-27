-- Older programs must reject this migration prefix before resuming a reply
-- with their pre-lineage independent ledger interpretation.
CREATE TABLE task_resource_lineage_format (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    format_tag TEXT NOT NULL
);
INSERT INTO task_resource_lineage_format VALUES
    (1, 'cirven.canonical-work-resource-anchor.v1');

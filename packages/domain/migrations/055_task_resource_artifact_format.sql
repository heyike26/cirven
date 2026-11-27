CREATE TABLE task_resource_artifact_format (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    format_tag TEXT NOT NULL
);
INSERT INTO task_resource_artifact_format VALUES (1,'cirven.task-resource-retentions.v1-512-192k');

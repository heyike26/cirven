-- rowid is the trailing index key, supporting bounded newest/older page reads.
CREATE INDEX discovery_operation_recovery_page
 ON discovery_operations(project_id,session_id)
 WHERE kind IN ('ADVANCE','PLAN');

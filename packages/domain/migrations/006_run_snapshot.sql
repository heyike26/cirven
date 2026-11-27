CREATE TRIGGER task_run_snapshot_no_update BEFORE UPDATE OF
    project_id,task_id,task_revision,attempt_id,operation_id,request_hash,spec_hash,stack_lock_hash,frozen_inputs,workspace
    ON task_runs
BEGIN
    SELECT RAISE(ABORT, 'Run inputs and versions are immutable');
END;

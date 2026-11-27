CREATE TABLE agent_scopes (
    execution_id TEXT PRIMARY KEY REFERENCES agent_executions(id),
    scope TEXT NOT NULL,
    scope_hash TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    root_execution_id TEXT NOT NULL REFERENCES agent_executions(id),
    root_budget_usd TEXT,
    prior_spent_usd TEXT,
    parent_reported_usd TEXT,
    call_budget_usd TEXT,
    native_call_execution_id TEXT NOT NULL REFERENCES agent_executions(id)
);
CREATE TABLE agent_controls (
    execution_id TEXT PRIMARY KEY REFERENCES agent_executions(id),
    parent_execution_id TEXT NOT NULL REFERENCES agent_executions(id),
    kind TEXT NOT NULL CHECK(kind IN ('resume','fork','steer')),
    expected_session_id TEXT NOT NULL,
    parent_request_hash TEXT NOT NULL,
    parent_result_hash TEXT NOT NULL,
    checkpoint_hash TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE agent_checkpoints (
    execution_id TEXT PRIMARY KEY REFERENCES agent_executions(id),
    session_id TEXT NOT NULL,
    transcript_path TEXT NOT NULL,
    transcript_sha256 TEXT NOT NULL,
    workspace_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER agent_scopes_no_update BEFORE UPDATE ON agent_scopes
BEGIN SELECT RAISE(ABORT, 'Agent scope is immutable'); END;
CREATE TRIGGER agent_scopes_no_delete BEFORE DELETE ON agent_scopes
BEGIN SELECT RAISE(ABORT, 'Agent scope is immutable'); END;
CREATE TRIGGER agent_controls_no_update BEFORE UPDATE ON agent_controls
BEGIN SELECT RAISE(ABORT, 'Agent control is immutable'); END;
CREATE TRIGGER agent_controls_no_delete BEFORE DELETE ON agent_controls
BEGIN SELECT RAISE(ABORT, 'Agent control is immutable'); END;
CREATE TRIGGER agent_checkpoints_no_update BEFORE UPDATE ON agent_checkpoints
BEGIN SELECT RAISE(ABORT, 'Agent checkpoint is immutable'); END;
CREATE TRIGGER agent_checkpoints_no_delete BEFORE DELETE ON agent_checkpoints
BEGIN SELECT RAISE(ABORT, 'Agent checkpoint is immutable'); END;
CREATE TRIGGER agent_executions_frozen BEFORE UPDATE ON agent_executions
WHEN NEW.project_id IS NOT OLD.project_id OR NEW.operation_id IS NOT OLD.operation_id
 OR NEW.request_hash IS NOT OLD.request_hash OR NEW.runtime IS NOT OLD.runtime
 OR NEW.workspace IS NOT OLD.workspace OR NEW.model_profile IS NOT OLD.model_profile
 OR NEW.sdk_version IS NOT OLD.sdk_version OR NEW.request IS NOT OLD.request
 OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'Agent execution input is immutable'); END;
CREATE TRIGGER agent_events_no_update BEFORE UPDATE ON agent_events
BEGIN SELECT RAISE(ABORT, 'Agent event is immutable'); END;
CREATE TRIGGER agent_events_no_delete BEFORE DELETE ON agent_events
BEGIN SELECT RAISE(ABORT, 'Agent event is immutable'); END;

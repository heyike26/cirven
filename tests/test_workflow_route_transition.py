"""Real policy/CAS and relay-admission tests; no native runtime qualification."""
from copy import deepcopy
import sqlite3
from types import SimpleNamespace

import pytest

from packages.agent_sessions import workflow
from packages.agent_sessions.service import AgentSessions
from packages.domain.store import Conflict
from test_workflow_adapter_races import UnitSourceOwner, current_policy, check


@pytest.mark.parametrize('selection', [
    {'model': {'mode': 'MANUAL', 'value': 'new-unadmitted-account'},
     'depth': {'mode': 'AUTO', 'value': None}},
    {'model': {'mode': 'AUTO', 'value': None},
     'depth': {'mode': 'MANUAL', 'value': 'xhigh'}},
])
def test_new_account_or_depth_pin_preserves_inflight_relay_and_requires_safe_reconnect(tmp_path, selection):
    owner = UnitSourceOwner(tmp_path,resources=True)
    owner.manifest['request']['max_calls'] = 10
    owner._update(owner.pid, owner.ident, model_calls_reserved=1)
    revised = deepcopy(owner.first['plan'])
    revised['nodes'][0]['agent_policy'] = selection
    saved = owner.dynamic_workflows.replan(owner.pid, owner.first['id'], revised,
        expected_ref=owner.first['workflow_ref'], operation_id='new-human-route',
        actor='USER', reason='Human changes the next task, not the executing child')
    with pytest.raises(Conflict):
        workflow.workflow_scope(owner, owner.pid, owner.manifest['request'], owner.profile)
    result = workflow.sync_workflow_policies(owner, owner.pid, owner.first['id'])
    check(result[0]['status'] == 'RECONNECT_REQUIRED')
    check('workflow_policy_sync_error' not in owner.context)
    policy = current_policy(owner)
    check(policy['plan_ref'] == saved['workflow_ref'])
    check(policy['nodes'][0]['agent_policy'] == selection, 'Never silently restore or weaken the human pin')
    receipt = AgentSessions._reserve_rpc(owner, owner.pid, owner.ident, 'f' * 64)
    check(owner._read(owner._path(owner.pid, owner.ident, 'rpc', receipt+'.json'))['operation_id'] == 'original_message')
    check(owner._state(owner.pid, owner.ident)['model_calls_reserved'] == 2)


def test_real_policy_file_failure_still_blocks_unconfirmed_forwarding(tmp_path, monkeypatch):
    owner = UnitSourceOwner(tmp_path,resources=True)
    owner.manifest['request']['max_calls'] = 10
    owner._update(owner.pid, owner.ident, model_calls_reserved=1)
    revised = deepcopy(owner.first['plan'])
    revised['nodes'][0]['description'] = 'Changed policy source'
    owner.dynamic_workflows.replan(owner.pid, owner.first['id'], revised,
        expected_ref=owner.first['workflow_ref'], operation_id='change-source', actor='USER', reason='New source')
    def unavailable(*args, **kwargs):
        raise OSError('Explicit original filesystem failure fixture')
    monkeypatch.setattr(workflow, 'write_workflow_policy', unavailable)
    check(workflow.sync_workflow_policies(owner, owner.pid, owner.first['id'])[0]['status'] == 'POLICY_SYNC_UNCONFIRMED')
    with pytest.raises(Conflict):
        AgentSessions._reserve_rpc(owner, owner.pid, owner.ident, 'f' * 64)
    check(owner._state(owner.pid, owner.ident)['model_calls_reserved'] == 1)

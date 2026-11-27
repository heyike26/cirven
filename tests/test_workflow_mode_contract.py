"""Mode definition/registry validation, not native execution qualification."""
from copy import deepcopy

from packages.agent_sessions.work_modes import (WORKFLOW_PROMPT, native_workflow_definition,
    observed_workflow_definition, workflow_mode_admitted)


def test_workflow_inherits_existing_grants_and_requires_both_native_observations():
    grants = {'*': 'deny', 'question': 'allow', 'todowrite': 'allow', 'edit': 'deny'}
    expected = native_workflow_definition('existing/model', grants, 7)
    assert expected['permission'] is grants and grants['edit'] == 'deny'
    effective = {'agent': {'workflow': deepcopy(expected)}}
    registry = [{'name': 'workflow', 'mode': 'primary', 'prompt': WORKFLOW_PROMPT}]
    definition = observed_workflow_definition(expected, effective, registry)
    assert workflow_mode_admitted({'workflow_mode': definition})
    for key, wrong in [('prompt', 'another policy'), ('mode', 'subagent'), ('model', 'other/model'), ('permission', {'*': 'allow'}), ('steps', 99)]:
        changed = deepcopy(effective)
        changed['agent']['workflow'][key] = wrong
        assert observed_workflow_definition(expected, changed, registry) is None
    assert observed_workflow_definition(expected, effective, []) is None
    assert observed_workflow_definition(expected, effective, registry * 2) is None
    assert not workflow_mode_admitted({'workflow_mode': {**definition, 'policy_sha256': '0' * 64}})
    assert not workflow_mode_admitted({})

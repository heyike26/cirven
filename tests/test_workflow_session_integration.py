"""Original owner/plugin integration. Only the external model is substituted."""
import asyncio
import json

from packages.agent_sessions.workflow import read_workflow_projection, sync_workflow_policies
from packages.dynamic_workflow.store import DynamicWorkflowStore
from packages.domain.tasks import canonical
from test_agent_sessions import scene, owner, ModelWire, control, tool, until


def test_selected_workflow_loads_actual_plugin_and_original_child_without_core_patch(scene):
    wire = ModelWire([]).start()
    async def run():
        service, request = owner(scene, wire)
        core = service.dynamic_workflows = DynamicWorkflowStore(scene['store'])
        created = core.create(scene['pid'], {'title': '实际插件链', 'goal': '准确定位子任务',
            'nodes': [{'id': 'review', 'title': '核对用户资料',
                       'acceptance': [{'id': 'source', 'text': '保持原文件不变'}]}]}, operation_id='create-workflow')
        request.update(workflow_ref=created['workflow_ref'])
        try:
            opened = await service.open(scene['pid'], request)
            ident = opened['session_ref']['id'];context = service.active[ident]
            assert opened['workflow_plugin']['status'] == 'ORIGINAL_TOOL_AND_ALIASES_OBSERVED'
            alias = context['workflow']['aliases'][0]['alias']
            wire.replies[:] = [tool('task', {'description': 'Review actual material',
                'prompt': 'Read only review', 'subagent_type': alias}), 'ACTUAL_WORKFLOW_CHILD', 'ACTUAL_WORKFLOW_PARENT']
            await service.send(scene['pid'], ident, {**control(opened, 'workflow-message'), 'raw_text': '核对资料'})
            pending = await until(service, scene, ident, lambda item: bool(item['pending_permissions']))
            permission = pending['pending_permissions'][0]
            await service.reply(scene['pid'], ident, permission['request_ref']['id'],
                {**control(opened, 'allow-workflow-child'), 'request_ref': permission['request_ref'], 'reply': 'once'}, kind='PERMISSION')
            final = await until(service, scene, ident, lambda item: item['active_operation_id'] is None)
            assert len(final['children']) == 1 and len(wire.requests) == 3
            projection = read_workflow_projection(service, scene['pid'], ident)
            assert projection['status'] == 'PARTIAL_EVENT_OBSERVATION'
            dispatches = projection['projection']['actual']['dispatches']
            assert len(dispatches) == 1 and dispatches[0]['node_id'] == 'review'
            current = core.get(scene['pid'], created['id']);plan = current['plan']
            plan['nodes'][0]['agent_policy']['model'] = {'mode': 'MANUAL', 'value': 'session-provider'}
            changed = core.replan(scene['pid'], created['id'], plan, expected_ref=current['workflow_ref'],
                operation_id='pin-current-model', actor='USER', reason='人工作为后续委派选择')
            activation = sync_workflow_policies(service, scene['pid'], created['id'])
            assert activation[0]['status'] == 'NEXT_DELEGATION'
            assert context['workflow_plan_ref'] == changed['workflow_ref']
            assert len(wire.requests) == 3, 'Read/policy sync must not create a model turn'
            assert scene['actual'].read_bytes() == scene['original']
            scene['data'].joinpath('workflow-integration-evidence.json').write_text(canonical({
                'session': final, 'projection': projection, 'wire_requests': wire.requests,
                'activation': activation, 'qualification': 'ORIGINAL_RUNTIME_EXTERNAL_MODEL_FIXTURE_ONLY'}))
        finally:
            await service.close()
    try:
        asyncio.run(run())
    finally:
        wire.close()

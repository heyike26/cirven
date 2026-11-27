"""Real pinned original loop/permissions; only external model replies are fixtures."""
import asyncio

import pytest

from packages.domain.local_workspaces import WorkspaceInput
from test_agent_sessions import ModelWire, control, owner, scene, tool, until


@pytest.mark.parametrize('kind,pattern', [('glob', '*.md'), ('grep', 'Actual')])
def test_original_optional_search_metadata_remains_readable_and_requires_explicit_permission(scene, kind, pattern):
    bound = scene['workspaces'].bind(scene['pid'], WorkspaceInput(
        idempotency_key='explicit-search-compatibility', root=str(scene['folder']),
        allowed_write_paths=['result.txt']))
    scene['bound'] = bound
    wire = ModelWire([tool(kind, {'pattern': pattern}), 'SEARCH_COMPLETED']).start()

    async def run():
        value, request = owner(scene, wire)
        request['allowed_effects'] = ['WORKSPACE_WRITE']
        try:
            opened = await value.open(scene['pid'], request)
            ident = opened['session_ref']['id']
            await value.send(scene['pid'], ident, {
                **control(opened, 'search-original-defaults'), 'agent': 'build',
                'raw_text': 'Search this selected workspace only; do not change any file.'})
            waiting = await until(value, scene, ident, lambda v: bool(v['pending_permissions']))
            assert not waiting['observation_error']
            assert len(wire.requests) == 1, 'Original search must wait for explicit approval'
            pending, = waiting['pending_permissions']
            native = pending['native_request']
            assert native['permission'] == kind
            assert native['patterns'] == [pattern]
            assert native['metadata']['path'] == str(scene['folder'])
            if kind == 'grep':
                assert native['metadata']['include'] == ''
            original = await value._http(value.active[ident], 'GET', '/permission')
            assert [item['id'] for item in original] == [native['id']]
            assert pending['allowed_choices'] == ['once', 'reject']
            reply = await value.reply(scene['pid'], ident, native['id'], {
                **control(opened, 'allow-exact-search-once'),
                'request_ref': pending['request_ref'], 'reply': 'once'}, kind='PERMISSION')
            assert reply['status'] == 'RETURNED'
            done = await until(value, scene, ident, lambda v: v['active_operation_id'] is None)
            assert value.operation(scene['pid'], ident, 'search-original-defaults')['status'] == 'RETURNED'
            assert not done['pending_permissions'] and not done['observation_error']
            assert len(wire.requests) == 2
            assert 'SEARCH_COMPLETED' in str(done['messages'])
            assert scene['actual'].read_bytes() == scene['original']
            assert not (scene['folder'] / 'result.txt').exists()
        finally:
            await value.close()

    try:
        asyncio.run(run())
    finally:
        wire.close()

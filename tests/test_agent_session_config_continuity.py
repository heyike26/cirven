"""Exact-owner lifecycle after future-owner configuration changes.

Controlled cases use the real service/Store/capability persistence and existing
SDK/process facets (no OS process or provider). The final parametrized case uses
unmodified pinned OpenCode and a local external-model wire, never paid inference.
No Windows, accounts, user projects or API keys are exercised.
"""
import asyncio
from copy import deepcopy
from datetime import timedelta
import hashlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from packages.domain.store import Conflict
from packages.domain.tasks import canonical
from test_agent_sessions_deadline_recovery import Scene, Clock, clock, until, check
from test_agent_sessions import ModelWire, scene as original_scene, owner, control, until as native_until


def change_capability(scene):
    return scene.caps.import_skill({'id': 'source51-qa', 'expected_revision': 0, 'operation_id': 'qa-import',
        'enabled': False, 'content': '---\nname: source51-qa\ndescription: Original disabled Source51 fixture\n---\nNo invocation.'})


def bind_controlled_transport(scene):
    """UNIT OS facet only. No production open/admission method is replaced."""
    config = scene.owner._path(scene.pid, scene.sid, 'runtime', 'home', 'config', 'opencode', 'opencode.json')
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('{"permission":{"*":"deny"}}')
    # A synthetic process facet can expose fixed metadata without controlling
    # any real PID. The real-CLI case below covers native startup capture.
    scene.context['process'].pid = 510051
    identity = {'pid': 510051, 'created': 51.0}
    scene.owner._update(scene.pid, scene.sid, process_identity=identity, controller_identity=scene.owner._identity)
    scene.context['owner_binding'] = {'process': scene.context['process'], 'client': scene.client,
        'session_id': scene.sdk.native, 'directory': str(scene.workspace), 'generation': scene.context['generation'],
        'process_identity': identity, 'config_path': str(config),
        'config_file_sha256': hashlib.sha256(config.read_bytes()).hexdigest()}
    return config


def count_gets(scene):
    calls = []
    async def observed(request):
        if request.method == 'GET': calls.append(request.url.path)
    scene.client.event_hooks['request'].append(observed)
    return calls


def test_changed_index_observes_true_old_result_without_admitting_new_input(tmp_path, clock):
    async def run():
        s = Scene(tmp_path)
        try:
            bind_controlled_transport(s)
            accepted = await s.send()
            change_capability(s)
            check(s.owner.get(s.pid, s.sid)['configuration_current'] is False)
            s.sdk.terminal(accepted['native_message_id'], error=False)
            view = await s.inspect()
            check(view['observation_is_current'] is True)
            check(s.operation()['status'] == 'RETURNED' and view['active_operation_id'] is None)
            before = deepcopy(s.sdk.posts)
            with pytest.raises(Conflict): await s.send('new-message')
            with pytest.raises(Conflict): s.owner._reserve_rpc(s.pid, s.sid, 'unit-request')
            check(s.sdk.posts == before and s.operation()['status'] == 'RETURNED')
        finally: await s.close()
    asyncio.run(run())


@pytest.mark.parametrize('kind', ['PERMISSION', 'QUESTION'])
def test_changed_index_blocks_permission_question_and_guidance_but_stop_remains(tmp_path, clock, kind):
    async def run():
        s = Scene(tmp_path)
        try:
            bind_controlled_transport(s)
            accepted = await s.send()
            s.sdk.tool_wait(accepted['native_message_id'], kind)
            await s.inspect()
            pending = s.state()['pending_permissions' if kind == 'PERMISSION' else 'pending_questions'][0]
            change_capability(s)
            before = deepcopy(s.sdk.posts)
            body = s.body('grant', request_ref=pending['request_ref'],
                **({'reply': 'once'} if kind == 'PERMISSION' else {'answers': [['Yes']]}))
            with pytest.raises(Conflict): await s.owner.reply(s.pid, s.sid, pending['request_ref']['id'], body, kind=kind)
            with pytest.raises(Conflict): await s.owner.guidance.submit(s.pid, s.sid,
                s.body('guidance', raw_text='Continue this task', expected_active_operation_id='parent'))
            check(s.sdk.posts == before)
            stopped = await s.stop()
            check(stopped['status'] == 'RETURNED' and stopped['original_abort_acknowledged'] is True)
            check(s.sdk.post_count('/prompt_async') == 1 and s.sdk.post_count('/abort') == 1)
            check(s.operation()['status'] == 'ACCEPTED')  # ACK is not completion.
        finally: await s.close()
    asyncio.run(run())


def test_changed_project_origins_and_expired_authority_cannot_block_exact_stop(tmp_path, clock):
    async def run():
        s = Scene(tmp_path)
        try:
            bind_controlled_transport(s)
            await s.send()
            (s.workspace / 'opencode.json').write_text('{"plugin":["must-never-load"]}')
            view = await s.inspect()
            check(view['observation_is_current'] is True)
            with pytest.raises(Conflict): s.owner._context(s.pid, s.sid, generation=3)
            s.context['expires_at'] = (clock.now(clock.instant.tzinfo)-timedelta(seconds=1)).isoformat()
            stopped = await s.stop()
            check(stopped['original_abort_acknowledged'] is True and s.sdk.post_count('/abort') == 1)
        finally: await s.close()
    asyncio.run(run())


def test_changed_index_monitor_keeps_deadline_and_ttl_cleanup(tmp_path, clock):
    async def run():
        s = Scene(tmp_path)
        try:
            bind_controlled_transport(s)
            await s.send()
            change_capability(s)
            monitor = s.monitor()
            await asyncio.sleep(.2)
            check(not monitor.done() and s.operation()['status'] == 'ACCEPTED')
            clock.advance(11)
            await until(lambda: s.operation()['status'] == 'UNKNOWN_OUTCOME')
            historical = deepcopy(s.operation())
            check(s.sdk.post_count('/abort') == 1)
            clock.advance(600)
            await until(lambda: monitor.done())
            check(s.sid not in s.owner.active and s.context['process'].terminated == 1)
            check(s.state()['owner_status'] == 'EXPIRED' and s.operation() == historical)
            check(s.sdk.post_count('/prompt_async') == 1)
        finally: await s.close()
    asyncio.run(run())


@pytest.mark.parametrize('drift', ['credential', 'workspace', 'private-config', 'private-config-deleted', 'authorization-epoch'])
def test_hard_scope_drift_yields_cached_evidence_then_owned_cleanup(tmp_path, clock, monkeypatch, drift):
    async def run():
        s = Scene(tmp_path); backup = None
        try:
            config = bind_controlled_transport(s)
            await s.send()
            await s.inspect()
            gets = count_gets(s)
            if drift == 'credential':
                s.registry.secret_resolver = lambda _: 'different-unit-material'
            elif drift == 'workspace':
                backup = s.workspace.with_name('old-original-workspace')
                s.workspace.rename(backup); s.workspace.mkdir()
            elif drift == 'private-config': config.write_text('{"permission":{"*":"allow"}}')
            elif drift == 'private-config-deleted': config.unlink()
            else:
                from packages.adapters import opencode_account
                s.context['profile'] = SimpleNamespace(auth_mode='OAUTH_SUBSCRIPTION', authorization_epoch='a'*32,
                    account_data_home=str(s.data/'unit-account-facet'))
                monkeypatch.setattr(opencode_account, 'authorization_epoch', lambda _: 'b'*32)
            view = await s.inspect()
            check(view['observation_is_current'] is False and gets == [])
            check(view['messages'] == s.state()['messages'])
            with pytest.raises(Conflict): await s.send('no-fresh-execution')
            # Negative control remains bound to the old server despite drift.
            check((await s.stop())['original_abort_acknowledged'] is True)
            monitor = s.monitor(); await until(lambda: monitor.done())
            check(s.sid not in s.owner.active and s.context['process'].terminated == 1)
            check(s.operation()['status'] == 'UNKNOWN_OUTCOME' and s.state()['owner_status'] == 'BLOCKED')
            check(s.sdk.post_count('/prompt_async') == 1)
        finally:
            if backup is not None:
                s.workspace.rmdir(); backup.rename(s.workspace)
            await s.close()
    asyncio.run(run())


@pytest.mark.parametrize('identity', ['generation', 'native-session', 'client', 'process'])
def test_identity_mismatch_has_no_native_get_or_stop_and_cleanup_uses_captured_handles(tmp_path, clock, identity):
    async def run():
        s = Scene(tmp_path); foreign = None
        try:
            bind_controlled_transport(s); await s.send(); gets = count_gets(s)
            original_process = s.context['process']; original_posts = len(s.sdk.posts)
            if identity == 'generation': s.owner._update(s.pid, s.sid, generation=4)
            elif identity == 'native-session': s.context['session_id'] = 'ses_unrelated'
            elif identity == 'client':
                async def reject(request): raise AssertionError('Foreign transport must not receive cleanup')
                foreign = httpx.AsyncClient(base_url='http://foreign.invalid', transport=httpx.MockTransport(reject))
                s.context['client'] = foreign
            else: s.context['process'] = SimpleNamespace(returncode=None, pid=510052)
            view = await s.inspect()
            check(view['observation_is_current'] is False and gets == [])
            with pytest.raises(Conflict): await s.stop()
            check(len(s.sdk.posts) == original_posts)
            await s.owner.disconnect(s.pid, s.sid)
            check(original_process.terminated == 1 and s.sdk.post_count('/abort') == 1)
            check(s.sdk.post_count('/prompt_async') == 1)
            if identity == 'generation': check(s.state()['generation'] == 4 and s.state()['owner_status'] == 'READY')
        finally:
            if foreign is not None: await foreign.aclose()
            await s.close()
    asyncio.run(run())


def test_monitor_unexpected_observation_error_preserves_unknown_and_ttl_without_replay(tmp_path, clock):
    async def run():
        s = Scene(tmp_path)
        try:
            bind_controlled_transport(s); await s.send()
            transport = s.client._transport
            original_handle = transport.handler
            calls = 0
            async def first_failure(request):
                nonlocal calls
                calls += 1
                if calls == 1: raise RuntimeError('External UNIT observation fault')
                return await original_handle(request)
            transport.handler = first_failure
            monitor = s.monitor()
            await until(lambda: s.operation()['status'] == 'UNKNOWN_OUTCOME')
            historical = deepcopy(s.operation())
            await asyncio.sleep(.35)
            check(not monitor.done() and calls > 1 and s.operation() == historical)
            clock.advance(601)
            await until(lambda: monitor.done())
            check(s.context['process'].terminated == 1 and s.sid not in s.owner.active)
            check(s.operation() == historical and s.sdk.post_count('/prompt_async') == 1)
        finally: await s.close()
    asyncio.run(run())


@pytest.mark.parametrize('change', ['future-disabled-skill', 'frozen-skill-bytes'])
def test_real_pinned_loop_observes_its_true_result_then_blocks_new_execution_and_stops(original_scene, change):
    """Actual CLI + SDK + local wire, 0 paid calls; not Windows qualification."""
    wire = ModelWire(['HOLD']).start()
    async def run():
        s = original_scene
        if change == 'frozen-skill-bytes':
            imported = s['manager'].import_skill({'id':'source51-frozen','operation_id':'initial-skill',
                'expected_revision':0,'enabled':True,
                'content':'---\nname: source51-frozen\ndescription: Original Source51 fixture\n---\nRead only.'})
        value, request = owner(s, wire)
        try:
            opened = await value.open(s['pid'], request); ident = opened['session_ref']['id']
            context = value.active[ident]
            check(context['owner_binding']['process_identity']['pid'] == context['process'].pid > 0)
            accepted = await value.send(s['pid'], ident, {**control(opened,'first'), 'raw_text':'Reply briefly.', 'timeout_seconds':30})
            check(await asyncio.to_thread(wire.received.wait, 15), 'Actual original model wire was not entered')
            if change == 'future-disabled-skill':
                s['manager'].import_skill({'id':'source51-future','operation_id':'future-skill',
                    'expected_revision':0,'enabled':False,
                    'content':'---\nname: source51-future\ndescription: Disabled future fixture\n---\nDo not load.'})
            else:
                ref = imported['config_ref']
                path = s['manager']._path('skill-files',ref['id'],f"{ref['revision']:010d}",'SKILL.md')
                path.write_text(path.read_text()+'\nUNIT changed frozen bytes; not new authority.')
            wire.release.set()
            view = await native_until(value,s,ident,lambda v:v['active_operation_id'] is None)
            check(value.operation(s['pid'],ident,'first')['status']=='RETURNED')
            check(view['configuration_current'] is False and view['observation_is_current'] is True)
            check(len(wire.requests)==1 and any(m['info'].get('parentID')==accepted['native_message_id'] for m in view['messages']))
            with pytest.raises(Conflict): await value.send(s['pid'],ident,{**control(opened,'blocked'),'raw_text':'Must not execute.'})
            stopped = await value.interrupt(s['pid'],ident,control(opened,'stop-after-change'))
            check(stopped['status']=='RETURNED' and stopped['original_abort_acknowledged'] is True)
            closed = await value.controlled_disconnect(s['pid'],ident,control(opened,'disconnect'))
            check(closed['last_control_operation']['exact_owned_process_stop_confirmed'] is True)
            check(ident not in value.active and context['process'].returncode is not None and len(wire.requests)==1)
        finally:
            wire.release.set(); await value.close()
    try: asyncio.run(run())
    finally: wire.close()


@pytest.mark.parametrize('stop_state', ['inflight', 'unknown-ack'])
def test_second_stop_cannot_bypass_existing_abort_barrier_and_replay_never_reposts(tmp_path, clock, stop_state):
    async def run():
        s = Scene(tmp_path); first_task = None
        try:
            bind_controlled_transport(s)
            accepted = await s.send()
            if stop_state == 'inflight':
                s.sdk.hold_abort = True
                first_task = asyncio.create_task(s.stop('first-stop'))
                await s.sdk.abort_started.wait()
                with pytest.raises(Conflict): await s.stop('second-stop')
                check(s.sdk.post_count('/abort') == 1)
                s.sdk.abort_release.set()
                first = await first_task
            else:
                s.sdk.abort_result = 'TIMEOUT'
                with pytest.raises(httpx.ReadTimeout): await s.stop('first-stop')
                first = s.owner.operation(s.pid, s.sid, 'first-stop')
                check(first['status'] == 'UNKNOWN_OUTCOME')
            with pytest.raises(Conflict): await s.stop('third-stop-before-idle')
            check(s.sdk.post_count('/abort') == 1)
            replay = await s.stop('first-stop')
            check(replay == first and s.sdk.post_count('/abort') == 1)
            s.sdk.abort_result = True
            s.sdk.terminal(accepted['native_message_id'])
            await s.inspect()
            check(s.state()['active_operation_id'] is None and 'interrupt_barrier' not in s.context)
            # Scene.stop intentionally targets the old parent. Once that
            # parent really settled, retain its exact-target refusal and use
            # the original session-level stop contract for a new idle stop.
            with pytest.raises(Conflict): await s.stop('old-parent-after-idle')
            after = await s.owner.interrupt(s.pid, s.sid, s.body('new-stop-after-real-idle'))
            check(after['original_abort_acknowledged'] is True and s.sdk.post_count('/abort') == 2)
            check(s.owner.operation(s.pid,s.sid,'first-stop') == first)
            check(s.sdk.post_count('/prompt_async') == 1)
        finally:
            if first_task is not None and not first_task.done():
                s.sdk.abort_release.set(); await first_task
            await s.close()
    asyncio.run(run())

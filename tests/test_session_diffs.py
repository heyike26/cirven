"""Real Store/HTTP readonly history; original metadata is an explicit UNIT fixture.

No task is run to manufacture patch evidence. The original-CLI test only opens
an empty original session and GETs its diff; it makes zero model requests.
"""
import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
import pytest

from apps.api.agent_sessions import register_agent_session_routes
from packages.agent_sessions import AgentSessions
from packages.agent_sessions.diffs import file_detail, parse_hunks, project_diffs, MAX_PATCH_BYTES
from packages.domain.local_workspaces import LocalWorkspaces, WorkspaceInput
from packages.domain.store import Store, Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, digest

ROOT = Path(__file__).resolve().parents[1]
PATCH = 'Index: a.py\n===================================================================\n--- a.py\n+++ a.py\n@@ -1,3 +1,3 @@\n keep\n-old\n+new\n tail\n'


def check(value, message='historical diff acceptance failed'):
    if not value:
        raise AssertionError(message)


def message(patch=PATCH, *, path='a.py', call='call_one', part='prt_one', role='assistant', session='ses_root'):
    return {'info': {'id': 'msg_'+call, 'role': role, 'sessionID': session},
            'parts': [{'type': 'tool', 'tool': 'edit', 'id': part, 'messageID': 'msg_'+call,
                       'sessionID': session, 'callID': call,
                       'state': {'status': 'completed', 'input': {'oldString': 'PRIVATE_INPUT'},
                                 'metadata': {'filediff': {'file': path, 'patch': patch, 'additions': 1, 'deletions': 1}},
                                 'output': 'PRIVATE_TOOL_OUTPUT'}}]}


def observed(messages=None, *, root='/qa'):
    return {'project_id': 'project', 'session_ref': {'id': 'a'*32, 'revision': 1, 'sha256': 'b'*64},
            'generation': 3, 'workspace_ref': {'id': 'c'*64, 'revision': 1, 'binding_sha256': 'd'*64},
            'original_session_id': 'ses_root', 'messages': [message()] if messages is None else messages,
            'observation_at': '2026-10-16T00:00:00Z'}


def test_exact_original_tool_hunks_are_not_full_files_or_current_disk():
    source = observed(); before = deepcopy(source)
    directory, content = project_diffs(source, {'root': '/qa'})
    detail = file_detail(directory, content, directory['snapshot_ref'], directory['files'][0]['diff_ref'])
    check(source == before)
    check(detail['hunks'] == [{'old_start': 1, 'old_lines': 3, 'new_start': 1, 'new_lines': 3,
                              'before': 'keep\nold\ntail\n', 'after': 'keep\nnew\ntail\n'}])
    check(detail['composition'] == 'LOCATION_MARKED_HUNK_EXCERPTS' and detail['content_scope'] == 'HUNK_EXCERPTS')
    check(detail['patch'] == PATCH and 'Historical before lines 1,3' in detail['before'])
    check(detail['current_disk_compared'] is False and detail['editor_dirty_state'] == 'NOT_OBSERVED')
    check(detail['file_hashes'] == 'NOT_OBSERVED' and detail['model_calls'] == 0)
    check('PRIVATE' not in canonical(detail))


def test_original_summary_add_delete_and_multiple_hunks_preserve_text_and_newline_markers():
    patch = ('--- x\n+++ x\n@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file\n'
             '@@ -8,0 +9,2 @@\n+插入\n+second\n')
    hunks = parse_hunks(patch)
    check(hunks[0]['before'] == 'old\n' and hunks[0]['after'] == 'new')
    check(hunks[1]['old_lines'] == 0 and hunks[1]['before'] == '' and hunks[1]['after'] == '插入\nsecond\n')
    msg = {'info': {'id': 'msg_user', 'sessionID': 'ses_root', 'role': 'user',
                   'summary': {'diffs': [{'file': 'new.py', 'patch': patch, 'additions': 3, 'deletions': 1, 'status': 'added'},
                                         {'file': 'gone.py', 'patch': '--- x\n+++ x\n@@ -1 +0,0 @@\n-old\n',
                                          'additions': 0, 'deletions': 1, 'status': 'deleted'}]}}, 'parts': []}
    d, c = project_diffs(observed([msg]), {'root': '/qa'})
    check([r['status'] for r in d['files']] == ['added', 'deleted'])
    check(all(r['content_scope'] == 'HUNK_EXCERPTS' for r in d['files']))
    check(len(c) == 2)


def test_foreign_roots_paths_commands_uris_and_traversal_are_excluded_without_path_leak():
    paths = ['/qa/a.py', '/outside/secret.py', '../a.py', '/qa/../secret.py', 'file:///qa/a.py',
             'vscode-remote://wsl+Ubuntu/a.py', r'\\server\share\a.py', 'a.py; rm -r /', '.git/config',
             '.eeas-runtime/secret', 'a.py\x00bad']
    messages = [message(path=p, call='call_'+str(i), part='prt_'+str(i)) for i, p in enumerate(paths)]
    messages.append(message(path='a.py', call='child', session='ses_child'))
    messages.append(message(path='a.py', call='foreignpart'))
    messages[-1]['parts'][0]['messageID'] = 'msg_somebody_else'
    d, _ = project_diffs(observed(messages), {'root': '/qa'})
    check(len(d['files']) == 1 and d['files'][0]['path'] == 'a.py')
    check('secret.py' not in canonical(d) and 'PATH_OUTSIDE_WORKSPACE' in d['coverage']['reason_codes'])
    win = [message(path=r'D:\qa\src\a.py'), message(path=r'C:\elsewhere\a.py', call='other', part='other')]
    d, _ = project_diffs(observed(win), {'root': r'D:\qa'})
    check(len(d['files']) == 1 and d['files'][0]['path'] == 'src/a.py')


def test_unknown_write_binary_large_truncated_and_redacted_do_not_invent_before():
    write = message(call='write', part='write')
    write['parts'][0].update(tool='write')
    write['parts'][0]['state']['metadata'] = {'filepath': '/qa/new.py', 'exists': False}
    messages = [write, message('Binary files a and b differ\n', call='binary', part='binary'),
                message('x'* (MAX_PATCH_BYTES+1), call='large', part='large'),
                message('--- a\n+++ a\n@@ -1,2 +1,2 @@\n-a\n+b\n', call='cut', part='cut'),
                message(PATCH.replace('old', '[REDACTED_RUNTIME_CREDENTIAL]'), call='secret', part='secret')]
    d, c = project_diffs(observed(messages), {'root': '/qa'})
    check([r['availability'] for r in d['files']] == ['UNAVAILABLE', 'BINARY', 'TOO_LARGE', 'UNAVAILABLE', 'UNAVAILABLE'])
    check(not c and all(r['content_scope'] == 'UNAVAILABLE' for r in d['files']))
    for row in d['files']:
        with pytest.raises(Conflict):
            file_detail(d, c, d['snapshot_ref'], row['diff_ref'])
    empty, _ = project_diffs(observed([]), {'root': '/qa'})
    check(not empty['files'] and 'NO_HISTORICAL_DIFF' in empty['coverage']['reason_codes'])


def test_repeat_call_identity_is_stable_but_changed_same_evidence_is_rejected():
    one = message(); two = message(call='call_two', part='prt_two')
    d, _ = project_diffs(observed([one, deepcopy(one), two]), {'root': '/qa'})
    check(len(d['files']) == 2 and len({r['diff_ref']['id'] for r in d['files']}) == 2)
    changed = deepcopy(one); changed['parts'][0]['state']['metadata']['filediff']['patch'] = PATCH.replace('+new', '+late')
    with pytest.raises(Conflict):
        project_diffs(observed([one, changed]), {'root': '/qa'})
    d2, c2 = project_diffs(observed([changed]), {'root': '/qa'})
    with pytest.raises(Conflict):
        file_detail(d2, c2, d['snapshot_ref'], d['files'][0]['diff_ref'])


@pytest.fixture
def durable(tmp_path):
    data = tmp_path/'data'; data.mkdir()
    store = Store(data/'eeas.sqlite3'); pid = store.create_project('原创历史差异', 'UNIT original observations')['id']
    folder = tmp_path/'original workspace'; folder.mkdir(); (folder/'a.py').write_text('USER_MANUAL_AFTER_TASK\n')
    ws = LocalWorkspaces(store, data)
    binding = ws.bind(pid, WorkspaceInput(idempotency_key='diff-workspace', root=str(folder), allowed_write_paths=[]))
    service = AgentSessions(store, data, provider_registry=None, workspaces=ws, capabilities=None)
    ident = 'a'*32; workspace = {k: binding[k] for k in ['id', 'revision', 'binding_sha256']}
    manifest = {'id': ident, 'project_id': pid, 'request': {'workspace_ref': workspace},
                'directory_identity': binding['root_identity'], 'workspace_kind': 'LOCAL_WORKSPACE'}
    service._write(service._path(pid, ident, 'manifest.json'), manifest)
    service._write(service._path(pid, ident, 'state.json'), {
        'generation': 3, 'original_session_id': 'ses_root', 'messages': [message(path=str(folder/'a.py'))],
        'observation_at': '2026-10-16T00:00:00Z', 'owner_status': 'DISCONNECTED', 'native_status': 'idle'})
    expected = {'session_sha256': digest(manifest), 'generation': 3, 'workspace_ref': workspace}
    return service, pid, ident, expected, folder


def app_for(service):
    app = FastAPI()
    for error, status in [(Conflict, 409), (InvalidInput, 422), (NotFound, 404)]:
        def handler(_, error, code=status):
            return JSONResponse({'error': type(error).__name__, 'message': str(error)}, status_code=code)
        app.add_exception_handler(error, handler)
    register_agent_session_routes(app, None, None, service=service)
    return app


def query(expected):
    w = expected['workspace_ref']
    return {'session_sha256': expected['session_sha256'], 'generation': expected['generation'],
            'workspace_id': w['id'], 'binding_revision': w['revision'], 'binding_sha256': w['binding_sha256']}


def test_actual_store_routes_restart_and_saved_bytes_without_file_read_or_network(durable, monkeypatch):
    service, pid, ident, expected, folder = durable
    state_path = service._path(pid, ident, 'state.json'); original = state_path.read_bytes()
    real_read = Path.read_bytes
    def no_current_file(path):
        if path == folder/'a.py':
            raise AssertionError('Historical diff must not read current user bytes')
        return real_read(path)
    monkeypatch.setattr(Path, 'read_bytes', no_current_file)
    with TestClient(app_for(service)) as client:
        url = f'/api/projects/{pid}/agent-sessions/{ident}/diffs'
        reply = client.get(url, params=query(expected)); check(reply.status_code == 200, reply.text)
        d = reply.json(); row = d['files'][0]
        q = {**query(expected), 'snapshot_sha256': d['snapshot_ref']['sha256'], 'diff_sha256': row['diff_ref']['sha256']}
        reply = client.get(url+'/'+row['diff_ref']['id'], params=q); check(reply.status_code == 200, reply.text)
        check('USER_MANUAL' not in reply.json()['after'] and 'new\n' in reply.json()['after'])
        check(client.get(url, params={**query(expected), 'generation': 2}).status_code == 409)
        check(client.get(url, params={**query(expected), 'session_sha256': 'f'*64}).status_code == 409)
        check(client.get(url, params={**query(expected), 'binding_sha256': 'f'*64}).status_code == 409)
        foreign = service.store.create_project('other', 'not authorized')['id']
        check(client.get(url.replace(pid, foreign), params=query(expected)).status_code == 404)
        check(client.get(url+'/'+row['diff_ref']['id'], params={**q, 'diff_sha256': 'f'*64}).status_code == 409)
    restarted = AgentSessions(service.store, service.data_dir, provider_registry=None,
                              workspaces=service.workspaces, capabilities=None)
    check(restarted.diffs(pid, ident, **expected) == d)
    check(state_path.read_bytes() == original and not restarted.active)


def test_source_observation_change_and_replaced_root_fail_closed(durable):
    service, pid, ident, expected, folder = durable
    d = service.diffs(pid, ident, **expected); row = d['files'][0]
    state = service._state(pid, ident)
    state['messages'].append(message(call='late', part='late'))
    service._write(service._path(pid, ident, 'state.json'), state, replace=True)
    with pytest.raises(Conflict):
        service.diff_file(pid, ident, **expected, snapshot_ref=d['snapshot_ref'], diff_ref=row['diff_ref'])
    folder.rename(folder.with_name('preserved-original-workspace')); folder.mkdir()
    with pytest.raises(Conflict):
        service.diffs(pid, ident, **expected)


def test_actual_tcp_api_to_main_mapper_reads_only_saved_metadata(durable, tmp_path):
    from test_workflow_app_runtime import LoggedServer
    service, pid, ident, expected, _ = durable
    server = LoggedServer(service.data_dir)
    script = tmp_path/'read-diffs.cjs'
    script.write_text(r"""
const fs=require('node:fs'),assert=require('node:assert/strict');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const {createIdeApiAdapter}=require(input.adapter);
(async()=>{const bootstrap=await fetch(input.origin+'/api/bootstrap',{headers:{'X-EEAS-Bootstrap':'1'}});assert.equal(bootstrap.status,200);
 const cookie=bootstrap.headers.getSetCookie().map(x=>x.split(';')[0]).join(';'),trace=[];
 const dispatch=createIdeApiAdapter({request:async(url,body)=>{assert.equal(body,undefined);const r=await fetch(input.origin+url,{headers:{Cookie:cookie}});trace.push({method:'GET',url,status:r.status});const value=await r.json();if(!r.ok)throw Object.assign(Error('only fixture API error'),{status:r.status});return value;}});
 const scope=input.scope,w=input.workspace_ref;
 const d=await dispatch('session.diff',{scope,payload:{workspace_ref:w}});assert.equal(d.files.length,1);
 const f=await dispatch('session.diff.file',{scope,payload:{workspace_ref:w,snapshot_ref:d.snapshot_ref,diff_ref:d.files[0].diff_ref}});
 assert.equal(f.content_scope,'HUNK_EXCERPTS');assert.match(f.after,/new/);assert.doesNotMatch(f.after,/USER_MANUAL/);
 assert.equal(trace.length,2);assert.ok(trace.every(x=>x.method==='GET'&&x.url.includes('/diffs')));
 process.stdout.write(JSON.stringify({trace,directory:d,file:f,model_calls:0,source_boundary:'UNIT_SYNTHETIC_ORIGINAL_METADATA_REAL_STORE_HTTP_MAPPER'}));
})().catch(e=>{console.error(e);process.exitCode=1;});
""", encoding='utf-8')
    try:
        server.start()
        result = subprocess.run([str(ROOT/'.node-tools/node_modules/node/bin/node'), str(script)],
            input=canonical({'origin': server.origin, 'adapter': str(ROOT/'apps/desktop-host/ide-api-adapter.cjs'),
                             'scope': {'project_id': pid, 'session_ref': {'id': ident, 'revision': 1, 'sha256': expected['session_sha256']},
                                       'generation': 3}, 'workspace_ref': expected['workspace_ref']}),
            text=True, capture_output=True, timeout=25)
        (tmp_path/'actual-mapper-result.txt').write_text(result.stdout+'\n'+result.stderr)
        check(result.returncode == 0, result.stdout+result.stderr)
        check(len(json.loads(result.stdout)['trace']) == 2)
    finally:
        server.stop()


def test_actual_fixed_original_empty_diff_uses_no_model_or_snapshot(tmp_path):
    # The actual original CLI is used; no source patch/task is manufactured.
    from test_agent_sessions import scene as scene_fixture, owner, ModelWire
    # Reuse the fixture's actual Store/namespace setup through its fixture body.
    scene = scene_fixture.__wrapped__(tmp_path)
    wire = ModelWire([]).start()
    async def run():
        value, request = owner(scene, wire)
        try:
            opened = await value.open(scene['pid'], request)
            ident = opened['session_ref']['id']; context = value.active[ident]
            actual = await value._http(context, 'GET', '/session/'+opened['original_session_id']+'/diff')
            check(actual == [] and len(wire.requests) == 0)
            expected = {'session_sha256': opened['session_ref']['sha256'], 'generation': opened['generation'],
                        'workspace_ref': opened['workspace_ref']}
            d = value.diffs(scene['pid'], ident, **expected)
            check(d['coverage']['snapshot_enabled'] is False and not d['files'])
            check('NO_HISTORICAL_DIFF' in d['coverage']['reason_codes'])
        finally:
            await value.close()
    try:
        asyncio.run(run())
    finally:
        wire.close()

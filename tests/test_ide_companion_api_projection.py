"""IDE mapper against actual TCP API/store/pinned original CLI.

The first case replaces external model output and injects one lost API reply
after its real POST commits. The separately labelled
controlled-abort case also gates the native abort transport to prove races; it
is not unmodified runtime qualification. No paired bridge/VSIX/Windows claim.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import httpx
import pytest

from packages.adapters.opencode_profile import CLI_SHA, VERSION
from packages.agent_sessions.models import SessionControl
from packages.agent_sessions.service import AgentSessions
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Conflict
from test_agent_sessions import CLI, KEY, MODEL, ModelWire, control, owner, scene, until
from test_session_guidance_api import HeldModelGate, same_process_alive
from test_workflow_app_runtime import LoggedServer
from test_session_guidance import UnitOwner

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / '.node-tools/node_modules/node/bin/node'
ADAPTER = ROOT / 'apps/desktop-host/ide-api-adapter.cjs'


def check(value, message='IDE adapter acceptance failed'):
    if not value:
        raise AssertionError(message)


@pytest.mark.parametrize('first', ['read', 'write'])
def test_unit_record_io_static_and_instance_share_complete_critical_section(tmp_path, monkeypatch, first):
    """UNIT gated FS primitive; verifies coordination, not cross-process/Disk guarantees."""
    path = tmp_path / 'record.json'
    AgentSessions._write(path, {'version': 1})
    value = AgentSessions.__new__(AgentSessions)
    entered, release, second_started, second_done = (threading.Event() for _ in range(4))
    outcomes, errors = {}, []
    original_read, original_write = Path.read_bytes, NativeCapabilities._write

    def gated_read(current):
        if current == path and threading.current_thread().name == 'record-first':
            entered.set()
            check(release.wait(5), 'test did not release bounded read gate')
        return original_read(current)

    def gated_write(current, data, *, replace=False):
        if current == path:
            # A nested static read exercises reentrancy on the same authority.
            check(AgentSessions._read(path) == {'version': 1})
            # Deliberately expose an unavailable file INSIDE the gated primitive;
            # the separate reader must not inspect it until replacement ends.
            path.unlink()
            entered.set()
            check(release.wait(5), 'test did not release bounded write gate')
        original_write(current, data, replace=replace)

    if first == 'read':
        monkeypatch.setattr(Path, 'read_bytes', gated_read)
        first_action = lambda: AgentSessions._read(path)
        second_action = lambda: value._write(path, {'version': 2}, replace=True)
    else:
        monkeypatch.setattr(NativeCapabilities, '_write', staticmethod(gated_write))
        first_action = lambda: value._write(path, {'version': 2}, replace=True)
        second_action = lambda: AgentSessions._read(path)

    def run(label, action, second=False):
        if second:
            second_started.set()
        try:
            outcomes[label] = action()
        except BaseException as error:
            errors.append(error)
        finally:
            if second:
                second_done.set()

    primary = threading.Thread(target=run, args=('first', first_action), name='record-first', daemon=True)
    secondary = threading.Thread(target=run, args=('second', second_action, True), daemon=True)
    try:
        primary.start()
        check(entered.wait(3), 'first operation never entered protected primitive')
        secondary.start()
        check(second_started.wait(3))
        check(not second_done.wait(.15), 'read/write escaped the shared complete critical section')
    finally:
        release.set()
        primary.join(5)
        if secondary.ident is not None:
            secondary.join(5)
    check(not primary.is_alive() and not secondary.is_alive(), 'record lock deadlocked')
    check(not errors, repr(errors))
    check(outcomes['first' if first == 'read' else 'second'] == {'version': 1 if first == 'read' else 2})
    check(AgentSessions._read(path) == {'version': 2})


def test_unit_budget_callback_actual_sqlite_record_reader_serialization_and_fail_closed(tmp_path, monkeypatch):
    """UNIT configuration admission facade; real Store, _reserve_rpc and records."""
    value = UnitOwner(tmp_path / 'budget-owner')
    monkeypatch.setattr(value, '_configuration_current', lambda _manifest: True)
    path = value._path(value.pid, value.sid, 'state.json')
    entered, release, reader_started, reader_done = (threading.Event() for _ in range(4))
    outcomes, errors = {}, []
    original_write = NativeCapabilities._write

    def gated_write(current, data, *, replace=False):
        if current == path and data.get('model_calls_reserved') == 2:
            current.unlink()
            entered.set()
            check(release.wait(5), 'budget write gate not released')
        original_write(current, data, replace=replace)

    monkeypatch.setattr(NativeCapabilities, '_write', staticmethod(gated_write))

    def reserve():
        try:
            outcomes['receipt'] = value._reserve_rpc(value.pid, value.sid, 'a' * 64)
        except BaseException as error:
            errors.append(error)

    def observe():
        reader_started.set()
        try:
            outcomes['state'] = value._state(value.pid, value.sid)
        except BaseException as error:
            errors.append(error)
        finally:
            reader_done.set()

    writer = threading.Thread(target=reserve, daemon=True)
    reader = threading.Thread(target=observe, daemon=True)
    try:
        writer.start()
        check(entered.wait(3), 'real reserve callback did not reach durable replacement')
        reader.start()
        check(reader_started.wait(3))
        check(not reader_done.wait(.15), 'budget observer saw in-flight replacement')
    finally:
        release.set()
        writer.join(5)
        if reader.ident is not None:
            reader.join(5)
    check(not writer.is_alive() and not reader.is_alive(), 'budget callback deadlocked')
    check(not errors, repr(errors))
    check(outcomes['state']['model_calls_reserved'] == 2)
    receipt = AgentSessions._read(value._path(value.pid, value.sid, 'rpc', outcomes['receipt'] + '.json'))
    check(receipt['request_sha256'] == 'a' * 64 and receipt['generation'] == 3 and receipt['operation_id'] == 'first')
    # The coordination must not turn true corruption/missing/oversize/budget
    # failures into successful observations or additional model reservations.
    monkeypatch.setattr(NativeCapabilities, '_write', staticmethod(original_write))
    value._update(value.pid, value.sid, model_calls_reserved=40)
    with pytest.raises(Conflict):
        value._reserve_rpc(value.pid, value.sid, 'b' * 64)
    check(len(list(path.parent.joinpath('rpc').glob('*.json'))) == 1)
    path.write_text('{"value":{},"sha256":"wrong"}')
    with pytest.raises(Conflict, match='integrity'):
        AgentSessions._read(path)
    path.write_bytes(b' ' * (8 * 1024 * 1024 + 1))
    with pytest.raises(Conflict, match='oversized'):
        value._state(value.pid, value.sid)
    path.unlink()
    with pytest.raises(Conflict, match='missing'):
        AgentSessions._read(path)
    with pytest.raises(Conflict, match='bound'):
        AgentSessions._write(path, {'text': 'x' * (8 * 1024 * 1024)})


def test_real_tcp_two_mapper_clients_same_original_owner_guidance_and_get_only_recovery(tmp_path):
    """Actual API and mapper; the two client identities are synthetic, not VSIX."""
    approved = Path(os.environ.get('EEAS_TEST_APPROVED_CLI_CACHE', str(CLI)))
    check(hashlib.sha256(approved.read_bytes()).hexdigest() == CLI_SHA[os.name])
    installed = tmp_path / 'data/provider-settings/runtime' / VERSION / 'opencode'
    installed.parent.mkdir(parents=True)
    if approved.stat().st_dev == installed.parent.stat().st_dev:
        os.link(approved, installed)
    else:
        shutil.copy2(approved, installed)
    wire = ModelWire(['HOLD', 'ACTUAL_IDE_GUIDED_FINAL'])
    wire.release = HeldModelGate()
    wire.start()
    server = LoggedServer(tmp_path / 'data')
    if sys.flags.optimize:
        server.env['PYTHONOPTIMIZE'] = str(sys.flags.optimize)
    opened = closed = result = None
    client = process = None
    stop_release = threading.Event()
    release_marker = tmp_path / 'release-wire'
    model_started = tmp_path / 'model-started'

    def release_when_guided():
        while not stop_release.wait(.02):
            if wire.received.is_set() and not model_started.exists():
                model_started.write_text('actual first RPC received')
            if release_marker.exists():
                wire.release.set()
                return

    release_thread = threading.Thread(target=release_when_guided, daemon=True)
    script = tmp_path / 'actual-two-clients.cjs'
    script.write_text(r"""
'use strict';
const fs=require('node:fs'),assert=require('node:assert/strict'),crypto=require('node:crypto');
const {createIdeApiAdapter}=require(process.env.IDE_TEST_ADAPTER);
const {IdeCompanionBridge,PROTOCOL}=require(process.env.IDE_TEST_BRIDGE);
const origin=process.env.IDE_TEST_ORIGIN,trace=[];
let bridge;
(async()=>{
  const bootstrap=await fetch(origin+'/api/bootstrap',{headers:{'X-EEAS-Bootstrap':'1'}});assert.equal(bootstrap.status,200);
  const csrf=(await bootstrap.json()).csrf_token,cookie=bootstrap.headers.getSetCookie().map(v=>v.split(';')[0]).join(';');
  let loseFirstReply=true;
  const request=async(path,body,signal)=>{
    const response=await fetch(origin+path,{method:body===undefined?'GET':'POST',headers:{Cookie:cookie,Origin:origin,'X-EEAS-CSRF':csrf,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal});
    trace.push({path,method:body===undefined?'GET':'POST',status:response.status});
    const result=await response.json();if(!response.ok){const e=Error('fixture API rejected');e.status=response.status;throw e;}
    if(loseFirstReply&&body&&path.endsWith('/messages')){loseFirstReply=false;throw Error('external lost response fixture');}return result;
  };
  bridge=new IdeCompanionBridge({dataDir:process.env.IDE_TEST_BRIDGE_DATA,dispatch:createIdeApiAdapter({request}),requestPairing:async()=>({approved:true}),requestTimeoutMs:30000,pairingTimeoutMs:5000});
  const discovery=await bridge.start();
  const bridgeRequest=async(path,credential,body)=>{
    const response=await fetch('http://127.0.0.1:'+discovery.port+path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json',...credential?{Authorization:'Bearer '+credential.client_secret,'X-EEAS-Client-Id':credential.client_id}:{}},body:body===undefined?undefined:JSON.stringify(body)});
    const envelope=await response.json();assert.equal(response.ok,true,JSON.stringify(envelope));return envelope;
  };
  const paired=async(label)=>{
    const credential={client_id:crypto.randomUUID(),client_secret:crypto.randomBytes(32).toString('hex'),client_label:label};
    await bridgeRequest('/v1/pair',null,{protocol:PROTOCOL,...credential,nonce:discovery.nonce});
    for(let n=0;n<50;n++){if((await bridgeRequest('/v1/pair/status',credential)).result.pairing_status==='PAIRED')return credential;await new Promise(r=>setTimeout(r,5));}throw Error('actual bridge synthetic approval failed');
  };
  const A=await paired('fixture-editor-A'),B=await paired('fixture-editor-B');
  const recoveries=[],canon=v=>Array.isArray(v)?'['+v.map(canon).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canon(v[k])).join(',')+'}':JSON.stringify(v);
  const remote=credential=>async(method,params)=>{
    const request_id=crypto.randomUUID();let envelope=await bridgeRequest('/v1/rpc',credential,{protocol:PROTOCOL,request_id,method,...params});
    for(let n=0;n<5&&['UNKNOWN','PENDING'].includes(envelope.status);n++){await new Promise(r=>setTimeout(r,100));envelope=await bridgeRequest('/v1/rpc/'+request_id,credential);}
    if(envelope.status!=='OK'&&['session.message','session.guidance','session.cancel'].includes(method)){
      // A transport deadline never permits another mutating RPC. Recover this
      // exact Core operation through a new readonly request and bind all input.
      const original=envelope.status,p=params.payload,read_id=crypto.randomUUID();
      envelope=await bridgeRequest('/v1/rpc',credential,{protocol:PROTOCOL,request_id:read_id,method:'session.operation',scope:params.scope,payload:{operation_id:p.operation_id}});
      assert.equal(envelope.status,'OK',JSON.stringify(envelope));const op=envelope.result;
      const body={operation_id:p.operation_id,session_ref:params.scope.session_ref,generation:params.scope.generation};
      if(op.kind==='MESSAGE')Object.assign(body,{raw_text:p.raw_text,agent:p.agent??'build',timeout_seconds:p.timeout_seconds??60});
      else if(op.kind==='GUIDANCE')Object.assign(body,{raw_text:p.raw_text,intent:p.intent??'AUTO',expected_active_operation_id:p.expected_active_operation_id??'ide-root-message'});
      else Object.assign(body,{expected_active_operation_id:p.expected_active_operation_id});
      assert.equal(op.request_sha256,crypto.createHash('sha256').update(canon(body)).digest('hex'));
      recoveries.push({request_id,operation_id:p.operation_id,original_status:original,readonly_recovery:true});
    }
    assert.equal(envelope.status,'OK',JSON.stringify(envelope));return envelope.result;
  };
  const first=remote(A),second=remote(B),scope=JSON.parse(process.env.IDE_TEST_SCOPE);
  const listed=await first('sessions.list',{scope:{project_id:scope.project_id}},A);assert(listed.sessions.some(s=>s.session_ref.id===scope.session_ref.id));
  const accepted=await first('session.message',{scope,payload:{operation_id:'ide-root-message',raw_text:'核对原资料，不改用户原件',agent:'plan',timeout_seconds:120}},A);assert.equal(accepted.status,'ACCEPTED');
  const modelDeadline=Date.now()+12000;while(!fs.existsSync(process.env.IDE_TEST_MODEL_STARTED)&&Date.now()<modelDeadline)await new Promise(r=>setTimeout(r,20));assert(fs.existsSync(process.env.IDE_TEST_MODEL_STARTED));
  let current=await second('session.get',{scope},B);assert.equal(current.active_operation_id,'ide-root-message');
  const status=await second('session.guidance',{scope,payload:{operation_id:'ide-local-status',raw_text:'现在进度如何？',intent:'AUTO',expected_active_operation_id:'ide-root-message'}},B);assert.equal(status.role,'LOCAL_STATUS');assert.equal(status.model_calls,0);
  const guidance=await second('session.message',{scope,payload:{operation_id:'ide-correction',raw_text:'补充：每项结论保留来源，先列不确定事项'}},B);assert.equal(guidance.kind,'GUIDANCE');assert.equal(guidance.original_active_operation_id,'ide-root-message');
  fs.writeFileSync(process.env.IDE_TEST_RELEASE,'go');
  const deadline=Date.now()+35000;do{current=await first('session.get',{scope},A);if(!current.active_operation_id)break;await new Promise(r=>setTimeout(r,80));}while(Date.now()<deadline);
  assert.equal(current.active_operation_id,null);
  const parent=await first('session.operation',{scope,payload:{operation_id:'ide-root-message'}},A),correction=await second('session.operation',{scope,payload:{operation_id:'ide-correction'}},B);
  assert.equal(parent.status,'RETURNED');assert.equal(correction.status,'RETURNED');assert.equal(correction.native_user_observed,true);
  assert(parent.continuation_guidance_ids.includes('ide-correction'));assert(current.messages.some(m=>m.parts.some(p=>p.text==='ACTUAL_IDE_GUIDED_FINAL')));
  for(let n=0;n<3;n++){await second('session.operation',{scope,payload:{operation_id:'ide-correction'}},B);await first('session.get',{scope},A);}
  const events=await second('session.events',{scope,payload:{after:0,limit:30}},B);assert.equal(events.native_replay,false);
  assert.equal(trace.filter(row=>row.method==='POST'&&row.path.endsWith('/messages')).length,1);
  assert.equal(trace.filter(row=>row.method==='POST'&&row.path.endsWith('/guidance')).length,2);
  fs.writeFileSync(process.env.IDE_TEST_RESULT,JSON.stringify({trace,recoveries,parent,correction,final:current,events,client_count:2,native_windows:'NOT_RUN',paired_bridge:'ACTUAL_HTTP_AND_FS_SYNTHETIC_APPROVAL_NO_WINDOWS_CONSENT'},null,2));
})().catch(e=>{console.error(e.stack);process.exitCode=1;}).finally(async()=>{if(bridge)await bridge.close();});
""")
    try:
        server.start()
        client = httpx.Client(base_url=server.origin, trust_env=False, timeout=60)
        bootstrap = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
        bootstrap.raise_for_status()
        client.headers.update({'Origin': server.origin, 'X-EEAS-CSRF': bootstrap.json()['csrf_token']})

        def call(method, path, body=None):
            response = client.request(method, path, json=body)
            check(response.is_success, f'actual API {path}: {response.status_code}')
            return response.json()

        pid = call('POST', '/api/projects', {'name': 'IDE真实共会话夹具', 'description': 'No real credentials'})['id']
        profile = call('POST', '/api/planning/providers', {'operation_id': 'save-ide-wire', 'id': 'ide-wire',
            'provider_url': wire.url, 'model_id': MODEL, 'api_key': KEY, 'auth_mode': 'API_KEY'})
        config = call('GET', '/api/agent-sessions/configuration')
        opened = call('POST', f'/api/projects/{pid}/agent-sessions/open', {'operation_id': 'open-ide-owner',
            'provider_profile_ref': profile['profile_ref'], 'capability_binding_sha256': config['capability_binding_sha256'],
            'ttl_seconds': 180, 'max_calls': 8, 'max_output_tokens': 1024})
        scope = {'project_id': pid, 'session_ref': opened['session_ref'], 'generation': opened['generation']}
        session_path = f'/api/projects/{pid}/agent-sessions/{opened["session_ref"]["id"]}'
        release_thread.start()
        result_path = tmp_path / 'actual-two-clients-result.json'
        env = {**os.environ, 'IDE_TEST_ADAPTER': str(ADAPTER), 'IDE_TEST_ORIGIN': server.origin,
            'IDE_TEST_BRIDGE':str(ROOT/'apps/desktop-host/ide-companion-bridge.cjs'),'IDE_TEST_BRIDGE_DATA':str(tmp_path/'paired-bridge'),
            'IDE_TEST_SCOPE': json.dumps(scope), 'IDE_TEST_RELEASE': str(release_marker), 'IDE_TEST_MODEL_STARTED':str(model_started), 'IDE_TEST_RESULT': str(result_path)}
        with (tmp_path / 'node-client.stdout').open('w') as stdout, (tmp_path / 'node-client.stderr').open('w') as stderr:
            process = subprocess.Popen([str(NODE), str(script)], env=env, stdout=stdout, stderr=stderr)
            check(process.wait(timeout=65) == 0, 'actual mapper clients failed; retained stderr')
        result = json.loads(result_path.read_text())
        journal = (tmp_path/'paired-bridge/ide-companion/clients.json').read_text()
        check('核对原资料' not in journal and 'ACTUAL_IDE_GUIDED_FINAL' not in journal and KEY not in journal,
              'paired bridge journal leaked question, answer, or model credential')
        check(result['final']['original_session_id'] == opened['original_session_id'])
        check(len(wire.requests) == 2, 'interleaved GET/status/recovery started another model call')
        last_user = next(row for row in reversed(wire.requests[1]['body']['messages']) if row['role'] == 'user')
        check('每项结论保留来源' in json.dumps(last_user, ensure_ascii=False))
        check(not wire.release.timed_out)
        closed = call(
            'POST', session_path + '/disconnect', {'session_ref': scope['session_ref'], 'generation': scope['generation'], 'operation_id': 'close-ide-owner'})
        check(closed['last_control_operation']['exact_owned_process_stop_confirmed'])
        check(not same_process_alive(opened['process_identity']))
    finally:
        stop_release.set()
        wire.release.set()
        if release_thread.is_alive():
            release_thread.join(timeout=2)
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        if opened and not closed and client:
            try:
                response = client.post(session_path + '/disconnect', json={'session_ref': opened['session_ref'],
                    'generation': opened['generation'], 'operation_id': 'close-ide-owner'})
                if response.is_success:
                    closed = response.json()
            except Exception:
                pass
        if client:
            client.close()
        server.stop()
        wire.close()
        (tmp_path / 'actual-api-evidence.json').write_text(json.dumps({'qualification': 'ACTUAL_PAIRED_HTTP_NODE_MAPPER_TCP_API_STORE_ORIGINAL_CLI_EXTERNAL_MODEL_AND_ONE_POST_REPLY_LOSS_FIXTURE',
            'fixture_bridge_deadline_ms':30000,'production_bridge_deadline_qualification':'NOT_TESTED',
            'native_windows': 'NOT_RUN', 'paired_bridge': 'ACTUAL_HTTP_FS_SYNTHETIC_APPROVAL', 'vsix': 'NOT_TESTED_HERE',
            'model_requests': wire.requests, 'opened': opened, 'closed': closed, 'result': result,
            'server_returncode': server.process.returncode if server.process else None}, ensure_ascii=False, indent=2))


def test_controlled_actual_owner_exact_cancel_race_idle_while_abort_inflight_unknown_no_repost(scene):
    """Controlled-native-transport UNIT on real owner/store/CLI, not pure runtime."""
    wire = ModelWire(['FIRST_FINAL', 'HOLD', 'AFTER_BARRIER']).start()

    async def run():
        value, request = owner(scene, wire)
        gate, entered = asyncio.Event(), asyncio.Event()
        abort_count = 0
        original_http = value._http

        async def gated_http(context, method, path, *args, **kwargs):
            nonlocal abort_count
            if method == 'POST' and path.endswith('/abort'):
                abort_count += 1
                entered.set()
                await gate.wait()
                await original_http(context, method, path, *args, **kwargs)
                raise TimeoutError('CONTROLLED lost abort response')
            return await original_http(context, method, path, *args, **kwargs)

        try:
            view = await value.open(scene['pid'], request)
            sid = view['session_ref']['id']
            await value.send(scene['pid'], sid, {**control(view, 'old'), 'raw_text': 'first'})
            await until(value, scene, sid, lambda current: current['active_operation_id'] is None)
            wire.received.clear()
            await value.send(scene['pid'], sid, {**control(view, 'new'), 'raw_text': 'held second'})
            check(await asyncio.to_thread(wire.received.wait, 10))
            value._http = gated_http
            with pytest.raises(Conflict):
                await value.interrupt(scene['pid'], sid, {**control(view, 'stale-stop'), 'expected_active_operation_id': 'old'})
            with pytest.raises(Conflict):
                await value.interrupt(scene['pid'], sid, {**control(view, 'wrong-gen-stop'), 'generation': view['generation'] + 1, 'expected_active_operation_id': 'new'})
            check(abort_count == 0)
            stop_body = {**control(view, 'exact-stop'), 'expected_active_operation_id': 'new'}
            stopping = asyncio.create_task(value.interrupt(scene['pid'], sid, stop_body))
            await asyncio.wait_for(entered.wait(), 3)
            observed = await asyncio.wait_for(value.inspect(scene['pid'], sid), 3)
            check(observed['active_operation_id'] == 'new', 'read blocked behind abort I/O')
            with pytest.raises(Conflict):
                await asyncio.wait_for(value.send(scene['pid'], sid, {**control(view, 'blocked-new'), 'raw_text': 'must not start'}), 1)
            # Let the old run return naturally while abort remains in flight.
            # Its idle observation cannot release the admission barrier yet.
            wire.release.set()
            await until(value, scene, sid, lambda current: current['active_operation_id'] is None)
            check(value.active[sid]['interrupt_barrier']['http_finished'] is False)
            with pytest.raises(Conflict):
                await value.guidance.submit(scene['pid'], sid, {**control(view, 'blocked-guide'), 'raw_text': 'new instruction',
                    'intent': 'STEER', 'expected_active_operation_id': None})
            check(len(wire.requests) == 2)
            gate.set()
            with pytest.raises(TimeoutError):
                await stopping
            receipt = value.operation(scene['pid'], sid, 'exact-stop')
            check(receipt['status'] == 'UNKNOWN_OUTCOME' and receipt['target_operation_id'] == 'new')
            check(await value.interrupt(scene['pid'], sid, stop_body) == receipt)
            check(abort_count == 1)
            await value.inspect(scene['pid'], sid)
            check('interrupt_barrier' not in value.active[sid])
            await value.send(scene['pid'], sid, {**control(view, 'after-stop'), 'raw_text': 'after idle confirmed'})
            await until(value, scene, sid, lambda current: current['active_operation_id'] is None)
            check(len(wire.requests) == 3)
            legacy = SessionControl.model_validate(control(view, 'legacy-shape')).model_dump()
            check('expected_active_operation_id' not in legacy)
        except BaseException as error:
            paths = list((scene['data'] / 'agent-sessions').glob('*/*/state.json'))
            evidence = {'error_type': type(error).__name__, 'error': str(error), 'abort_count': abort_count,
                        'states': [{'path': str(p), 'exists': p.exists(), 'is_file': p.is_file(),
                                    'size': p.stat().st_size if p.exists() else None} for p in paths]}
            (scene['data'] / 'controlled-abort-failure.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
            raise
        finally:
            gate.set()
            wire.release.set()
            value._http = original_http
            await value.close()
    try:
        asyncio.run(run())
    finally:
        wire.close()

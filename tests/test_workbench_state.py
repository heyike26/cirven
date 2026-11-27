"""Real HTTP + SQLite presentation lifecycle; no storage/model execution mocks."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient
import uvicorn

from apps.api.config import Settings
from apps.api.main import create_app
from apps.api.workbench_state import register_workbench_state_routes
from packages.domain.local_workspaces import LocalWorkspaces
from packages.domain.store import Conflict, Store
from packages.domain.workbench_state import (
    MAX_STRING_BYTES, WorkbenchKey, WorkbenchLoadInput, WorkbenchSaveInput, WorkbenchState,
)

ROOT = Path(__file__).resolve().parents[1]


def check(value, message='presentation acceptance failed'):
    if not value: raise AssertionError(message)


@contextmanager
def client_at(directory):
    settings = Settings(directory)
    app = create_app(settings)
    if not getattr(app.state, 'workbench_state', None):
        register_workbench_state_routes(app, app.state.store, directory, workspaces=app.state.local_workspaces)
    with TestClient(app, base_url=settings.origin) as client:
        response = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
        client.headers.update({'Origin': settings.origin, 'X-EEAS-CSRF': response.json()['csrf_token']})
        yield client


class Evidence:
    def __init__(self, directory):
        self.directory, self.calls = directory, []
        directory.mkdir(parents=True, exist_ok=True)
    def call(self, client, method, path, body=None, status=200, *, raw=None):
        response = client.request(method, path, json=body) if raw is None else client.request(
            method, path, content=raw, headers={'Content-Type': 'application/json'})
        value = response.json()
        self.calls.append({'method': method, 'path': path, 'request': body if raw is None else '<explicit invalid JSON numeric fixture>',
                           'status': response.status_code, 'response': value})
        self.flush()
        check(response.status_code == status, f'expected {status}: {response.text[:700]}')
        return value
    def flush(self):
        (self.directory/'receipt.json').write_text(json.dumps({'qualification': 'REAL_ASGI_HTTP_SQLITE_OR_TCP_WITH_ORIGINAL_FIXTURES',
            'windows_gui': 'NOT_RUN', 'model_dispatched': False, 'calls': self.calls}, ensure_ascii=True, indent=2))


@pytest.fixture
def scene(tmp_path, request):
    evidence = Evidence(Path(os.environ.get('EEAS_WORKBENCH_STATE_EVIDENCE', tmp_path/'evidence'))/request.node.name)
    with client_at(tmp_path/'data') as client:
        pid = evidence.call(client, 'POST', '/api/projects', {'name': '可中断工作台原创夹具', 'description': ''}, 201)['id']
        folder = tmp_path/'selected'; folder.mkdir(); (folder/'note.md').write_text('用户正文\r\n保留人工修改。\n')
        bound = evidence.call(client, 'POST', f'/api/projects/{pid}/local-workspaces',
            {'idempotency_key': 'selected-original', 'root': str(folder), 'allowed_write_paths': ['note.md']}, 201)
        source = evidence.call(client, 'GET', f'/api/projects/{pid}/local-workspaces/{bound["id"]}/file?path=note.md')['source_ref']
        key = {'project_id': pid, 'workspace_id': bound['id'], 'binding_revision': bound['revision'],
            'binding_sha256': bound['binding_sha256'], 'kind': 'local-file', 'object_id': source['path'],
            'revision': source['revision'], 'sha256': source['sha256']}
        yield {'client': client, 'evidence': evidence, 'pid': pid, 'base': f'/api/projects/{pid}/workbench-state',
               'key': key, 'source': source, 'bound': bound, 'folder': folder, 'data': tmp_path/'data',
               'store': client.app.state.store, 'service': client.app.state.workbench_state}


def call(s, method, suffix, body=None, status=200, **kwargs):
    return s['evidence'].call(s['client'], method, s['base']+suffix, body, status, **kwargs)


def save_body(s, operation='save-one', revision=0, **changes):
    return {'key': deepcopy(s['key']), 'operation_id': operation, 'expected_revision': revision,
            'fields': {'text': '尚未保存的人工正文\n', 'sourceRef': s['source']},
            'view': {'scroll': {'x': 0, 'y': 84}, 'hidden': False}, 'dirty': True, **changes}


def counts(store):
    with store.connect() as db:
        return {name: db.execute('SELECT count(*) FROM '+name).fetchone()[0] for name in
            ('workbench_state_heads', 'workbench_state_versions', 'workbench_state_operations', 'agent_executions', 'task_runs')}


def test_empty_load_is_stable_read_only_and_navigation_has_no_authority(scene):
    s=scene; before=counts(s['store'])
    first=call(s,'POST','/load',{'key':s['key']}); second=call(s,'POST','/load',{'key':s['key']})
    check(first==second and first=={'status':'EMPTY','schema_version':1,'key':s['key'],'state_revision':0,
        'fields':{},'view':{},'dirty':False,'sha256':None,'updated_at':None})
    check(counts(s['store'])==before)
    key={**s['key'],'kind':'workspace','object_id':s['bound']['id'],'revision':s['bound']['revision'],'sha256':s['bound']['binding_sha256']}
    value=call(s,'POST','/save',save_body(s,key=key,fields={},view={'active_key':s['key'],
        'panes':{'files_width':240,'agent_width':380,'files_hidden':True,'agent_hidden':False,'workbench_hidden':True},'tab':'current'}))
    check(value['view']['active_key']==s['key'] and value['dirty'])
    check(counts(s['store'])['agent_executions']==0 and counts(s['store'])['task_runs']==0)


def test_real_sqlite_restart_keeps_exact_fields_view_and_original_receipt(tmp_path):
    directory=tmp_path/'data'; evidence=Evidence(tmp_path/'restart-evidence')
    with client_at(directory) as client:
        pid=evidence.call(client,'POST','/api/projects',{'name':'真实重启','description':''},201)['id']
        base=f'/api/projects/{pid}/workbench-state'
        key={'project_id':pid,'kind':'discovery','object_id':'original-discovery','revision':2,'sha256':'a'*64,'session_id':'original-thread'}
        body={'key':key,'operation_id':'durable-save','expected_revision':0,'fields':{'answers':{'q1':'下次补充'},'accepted':['r1'],
            'graphsAccepted':['g1'],'decisions':{'q2':{'assumption':'用户尚未提交的选项','reason':'待核对'}},'reason':'保留修改依据'},
            'view':{'graphs':{'g1':{'zoom':1.8,'pan':{'x':-32,'y':48},'positions':{'n1':{'x':31,'y':44}},'selected':['n1'],'mode':'mindmap','search':'节点'}},'hidden':True},'dirty':True}
        saved=evidence.call(client,'POST',base+'/save',body)
    with client_at(directory) as restarted:
        restored=evidence.call(restarted,'POST',base+'/load',{'key':key})
        original=evidence.call(restarted,'GET',base+'/operations/durable-save')
        check(restored['fields']==body['fields'] and restored['view']==body['view'] and restored['state_revision']==1)
        check(original=={**saved,'replayed':True})
        check(counts(restarted.app.state.store)['agent_executions']==0)
        with restarted.app.state.store.connect() as db:
            check(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok')


def test_cas_conflict_keeps_old_draft_and_same_operation_cannot_change_input(scene):
    s=scene; original=call(s,'POST','/save',save_body(s)); before=counts(s['store'])
    call(s,'POST','/save',save_body(s,'stale-tab',fields={'text':'不应覆盖另一页'}),409)
    call(s,'GET','/operations/stale-tab',status=404)
    call(s,'POST','/save',save_body(s,fields={'text':'相同operation不能偷偷改输入'}),409)
    check(counts(s['store'])==before)
    check(call(s,'POST','/load',{'key':s['key']})['fields']==original['fields'])
    newer=call(s,'POST','/save',save_body(s,'save-two',1,fields={'text':'人工第二次修改'},dirty=False))
    check(newer['state_revision']==2 and call(s,'GET','/drafts')['drafts']==[])
    replay=call(s,'POST','/save',save_body(s)); inspected=call(s,'GET','/operations/save-one')
    check(replay==inspected and replay['state_revision']==1 and replay['replayed'])
    check(call(s,'POST','/load',{'key':s['key']})['state_revision']==2)


def test_real_concurrent_sqlite_cas_has_one_winner_and_preserves_loser(scene):
    s=scene; call(s,'POST','/save',save_body(s))
    barrier=threading.Barrier(2)
    def competing(index):
        service=WorkbenchState(Store(s['data']/'eeas.sqlite3'),s['data'],workspaces=LocalWorkspaces(s['store'],s['data']))
        body=WorkbenchSaveInput.model_validate(save_body(s,'tab-'+str(index),1,fields={'text':'人工页签'+str(index)}))
        barrier.wait(timeout=5)
        try: return ('SAVED',service.save(s['pid'],body))
        except Conflict as error: return ('CONFLICT',str(error))
    with ThreadPoolExecutor(max_workers=2) as pool: values=list(pool.map(competing,[1,2]))
    check(sorted(value[0] for value in values)==['CONFLICT','SAVED'])
    winner=next(value[1] for value in values if value[0]=='SAVED')
    check(call(s,'POST','/load',{'key':s['key']})['fields']==winner['fields'])
    check(counts(s['store'])['workbench_state_versions']==2 and counts(s['store'])['workbench_state_operations']==2)
    s['evidence'].calls.append({'concurrent_result':values});s['evidence'].flush()


def test_full_baseline_scope_isolation_includes_old_versions_hash_and_session(scene):
    s=scene; bodies=[]
    for index,changes in enumerate([{}, {'revision':2}, {'sha256':'b'*64}, {'session_id':'thread-one'}, {'session_id':'thread-two'}]):
        body=save_body(s,'isolated-'+str(index),key={**s['key'],**changes},fields={'text':'独立草稿'+str(index)})
        bodies.append(body);call(s,'POST','/save',body)
    second=s['folder'].parent/'second';second.mkdir()
    bound=s['evidence'].call(s['client'],'POST',f'/api/projects/{s["pid"]}/local-workspaces',
        {'idempotency_key':'second-selected','root':str(second),'allowed_write_paths':[]},201)
    body=save_body(s,'second-binding',key={**s['key'],'workspace_id':bound['id'],'binding_revision':bound['revision'],'binding_sha256':bound['binding_sha256']},fields={'text':'另一个真实目录的草稿'})
    bodies.append(body);call(s,'POST','/save',body)
    for body in bodies: check(call(s,'POST','/load',{'key':body['key']})['fields']==body['fields'])
    first=call(s,'GET','/drafts?limit=2');check(first['has_more'] and first['next_offset']==2)
    all_values=first['drafts']; offset=first['next_offset']
    while offset is not None:
        page=call(s,'GET','/drafts?limit=2&offset='+str(offset));all_values+=page['drafts'];offset=page['next_offset']
    check(len(all_values)==6 and len({json.dumps(v['key'],sort_keys=True) for v in all_values})==6)


def test_cross_project_route_binding_and_navigation_target_are_rejected_without_leak(scene):
    s=scene;call(s,'POST','/save',save_body(s));before=counts(s['store'])
    other=s['evidence'].call(s['client'],'POST','/api/projects',{'name':'另一项目','description':''},201)['id']
    otherbase=f'/api/projects/{other}/workbench-state'
    s['evidence'].call(s['client'],'POST',otherbase+'/load',{'key':s['key']},422)
    s['evidence'].call(s['client'],'GET',otherbase+'/operations/save-one',status=404)
    s['evidence'].call(s['client'],'POST',otherbase+'/save',save_body(s,key={**s['key'],'project_id':other},fields={'text':'跨项目的绑定不能保存'}),404)
    call(s,'POST','/save',save_body(s,'bad-binding',key={**s['key'],'binding_sha256':'f'*64,'revision':2}),409)
    call(s,'POST','/save',save_body(s,'bad-navigation',view={'active_key':{**s['key'],'project_id':other}}),422)
    check(counts(s['store'])==before)


def test_registered_historical_binding_drafts_survive_unavailable_folder_and_manual_edits(scene):
    s=scene;body=save_body(s);saved=call(s,'POST','/save',body)
    (s['folder']/'note.md').write_text('另一处人工修改，呈现恢复不可覆盖。')
    archived=s['folder'].with_name('archived-selected');s['folder'].rename(archived)
    check(call(s,'POST','/load',{'key':s['key']})['fields']==saved['fields'])
    check(call(s,'GET','/drafts')['drafts'][0]['key']==s['key'])
    newer=call(s,'POST','/save',save_body(s,'old-binding-new-ui',1,fields={'text':'旧基准下的额外草稿','sourceRef':s['source']}))
    check(newer['fields']['sourceRef']==s['source'])
    call(s,'POST','/save',save_body(s,'unregistered-new-basis',key={**s['key'],'revision':9}),404)
    check((archived/'note.md').read_text()=='另一处人工修改，呈现恢复不可覆盖。')
    check(call(s,'GET','/operations/save-one')=={**saved,'replayed':True})


BAD = [
    ('business-field', {'fields': {'status':'PASSED'}}),
    ('business-view', {'view': {'verification_status':'PASSED'}}),
    ('wrong-field-type', {'fields': {'text':{'nested':'not text'}}}),
    ('pollution-root', {'fields': {'constructor':'bad'}}),
    ('pollution-deep', {'key_kind':'proposal','fields':{'candidate':{'nested':{'__proto__':{'polluted':True}}}}}),
    ('pollution-prototype', {'view':{'positions':{'prototype':{'x':1,'y':2}}}}),
    ('negative-zoom', {'view':{'zoom':0}}),
    ('wrong-position', {'view':{'pan':{'x':0,'y':1,'z':2}}}),
    ('unknown-graph-field', {'view':{'graphs':{'g':{'approved':True}}}}),
    ('too-many-positions', {'view':{'positions':{str(i):{'x':i,'y':0} for i in range(2001)}}}),
    ('too-many-graphs', {'view':{'graphs':{str(i):{} for i in range(65)}}}),
    ('nul', {'fields':{'text':'invalid\x00draft'}}),
    ('surrogate', {'fields':{'text':'invalid\ud800draft'}}),
    ('unsafe-large-int', {'view':{'pan':{'x':10**400,'y':0}}}),
    ('null-fields', {'fields':None}),
    ('dirty-number', {'dirty':1}),
    ('partial-binding', {'drop_key':'binding_revision'}),
    ('empty-object', {'key_changes':{'object_id':'   '}}),
    ('control-identity', {'key_changes':{'session_id':'thread\nsecond'}}),
    ('wrong-revision-type', {'key_changes':{'revision':True}}),
]


def test_invalid_ui_payloads_reject_without_truncation_or_destroying_old_draft(scene):
    s=scene;original=call(s,'POST','/save',save_body(s));before=counts(s['store'])
    for label,changes in BAD:
        body=save_body(s,'invalid-'+label,1);changes=deepcopy(changes)
        if 'key_kind' in changes: body['key']['kind']=changes.pop('key_kind')
        if 'drop_key' in changes:body['key'].pop(changes.pop('drop_key'))
        body['key'].update(changes.pop('key_changes',{}));body.update(changes)
        # ASCII escape preserves intentionally invalid surrogate in HTTP fixture.
        call(s,'POST','/save',status=422,raw=json.dumps(body,ensure_ascii=True))
        check(counts(s['store'])==before and call(s,'POST','/load',{'key':s['key']})['fields']==original['fields'],label)


def test_non_finite_numeric_json_is_rejected_and_old_draft_preserved(scene):
    s=scene;original=call(s,'POST','/save',save_body(s));before=counts(s['store'])
    for numeric in (float('nan'),float('inf'),float('-inf')):
        body=save_body(s,'not-finite',1,view={'zoom':numeric})
        call(s,'POST','/save',status=422,raw=json.dumps(body,allow_nan=True))
        check(counts(s['store'])==before and call(s,'POST','/load',{'key':s['key']})['fields']==original['fields'])


def test_byte_depth_item_limits_leave_original_snapshots_intact(scene):
    s=scene;original=call(s,'POST','/save',save_body(s));before=counts(s['store'])
    too_deep={};cursor=too_deep
    for _ in range(18):cursor['child']={};cursor=cursor['child']
    candidates=[{'text':'x'*(MAX_STRING_BYTES+1)}, {'candidate':too_deep},
        {'candidate':{'items':[list(range(13)) for _ in range(4096)]}},
        {'candidate':{'a':'x'*MAX_STRING_BYTES,'b':'y'*MAX_STRING_BYTES}}]
    for i,fields in enumerate(candidates):
        call(s,'POST','/save',save_body(s,'bounded-'+str(i),1,key={**s['key'],'kind':'proposal'} if 'candidate' in fields else s['key'],fields=fields),413 if i==3 else 422)
    check(counts(s['store'])==before and call(s,'POST','/load',{'key':s['key']})['fields']==original['fields'])


def test_corrupt_state_is_reported_but_not_erased_and_other_drafts_remain_available(scene):
    s=scene;original=call(s,'POST','/save',save_body(s))
    other=call(s,'POST','/save',save_body(s,'healthy-old-version',key={**s['key'],'revision':2},fields={'text':'仍可读取的旧版本草稿'}))
    with s['store'].connect() as db:
        db.execute('DROP TRIGGER workbench_state_versions_no_update')
        db.execute('UPDATE workbench_state_versions SET payload=? WHERE sha256=?',('corrupt-preserved-original',original['sha256']))
    before=counts(s['store']);call(s,'POST','/load',{'key':s['key']},409)
    call(s,'POST','/save',save_body(s,'must-not-reset-corrupt',1),409)
    listing=call(s,'GET','/drafts')
    check(len(listing['unavailable'])==1 and listing['unavailable'][0]['status']=='INTEGRITY_ERROR')
    check(len(listing['drafts'])==1 and listing['drafts'][0]['key']==other['key'])
    check(counts(s['store'])==before)
    with s['store'].connect() as db:check(db.execute('SELECT payload FROM workbench_state_versions WHERE sha256=?',(original['sha256'],)).fetchone()[0]=='corrupt-preserved-original')


def test_sqlite_atomic_failure_cannot_advance_head_without_operation_receipt(scene):
    s=scene;original=call(s,'POST','/save',save_body(s));before=counts(s['store'])
    with s['store'].connect() as db:
        db.execute("CREATE TRIGGER qualification_write_failure BEFORE INSERT ON workbench_state_operations BEGIN SELECT RAISE(ABORT,'controlled actual SQLite write rejection'); END")
    call(s,'POST','/save',save_body(s,'storage-failure',1,fields={'text':'未提交完整的写入'}),503)
    check(counts(s['store'])==before and call(s,'POST','/load',{'key':s['key']})['fields']==original['fields'])
    call(s,'GET','/operations/storage-failure',status=404)
    with s['store'].connect() as db:check(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok')


def test_source_ref_is_preserved_as_data_and_never_grants_permissions(scene):
    s=scene;source=deepcopy(s['source']);saved=call(s,'POST','/save',save_body(s))
    check(saved['fields']['sourceRef']==source)
    call(s,'POST','/save',save_body(s,'bad-source',1,fields={'sourceRef':{**source,'write_authorized':True}}),422)
    call(s,'POST','/save',save_body(s,'cross-source',1,fields={'sourceRef':{**source,'project_id':'another-project'}}),422)
    check((s['folder']/'note.md').read_text()=='用户正文\n保留人工修改。\n')
    check(counts(s['store'])['agent_executions']==0 and counts(s['store'])['task_runs']==0)


def test_new_routes_keep_existing_origin_csrf_boundary(scene):
    s=scene;before=counts(s['store'])
    response=s['client'].post(s['base']+'/save',json=save_body(s),headers={'Origin':'https://other.invalid'})
    check(response.status_code==403 and response.json()['error']['code']=='ORIGIN_DENIED')
    response=s['client'].post(s['base']+'/save',json=save_body(s),headers={'X-EEAS-CSRF':''})
    check(response.status_code==403 and counts(s['store'])==before)


def test_schema023_upgrades_to024_without_changing_original_document_bytes(tmp_path):
    database=tmp_path/'legacy.sqlite3';stamp='2026-10-15T00:00:00+00:00';raw=b'Original exact bytes\r\n'
    migrations=sorted((ROOT/'packages/domain/migrations').glob('*.sql'))
    workbench_migration=next(path for path in migrations if path.name=='024_workbench_state.sql')
    check(migrations.index(workbench_migration)==23)
    # The v3 source-final receipt pins the historical 023 -> 024 transition.
    check(hashlib.sha256(workbench_migration.read_bytes()).hexdigest()=='5316d8c071d6176814a273a4c86f9e134983478b9039ee0347d60c9f9284cb52')
    with sqlite3.connect(database) as db:
        db.execute('PRAGMA foreign_keys=ON');db.execute('CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)')
        for path in migrations[:23]:
            db.executescript(path.read_text());db.execute('INSERT INTO schema_migrations VALUES (?)',(path.name,))
        db.execute('INSERT INTO projects VALUES (?,?,?,?)',('legacy','旧项目','',stamp))
        db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?)',('legacy-document','legacy','original.md',1,None,stamp))
        db.execute('INSERT INTO document_versions VALUES (?,?,?,?,?,?,?)',('legacy-document',1,hashlib.sha256(raw).hexdigest(),raw,raw.decode(),'fixture:legacy',stamp))
    # Inspect the exact historical boundary without pre-applying 024 to the
    # original fixture that Store will upgrade through its normal entry point.
    with sqlite3.connect(database) as original, sqlite3.connect(tmp_path/'at024.sqlite3') as db:
        original.backup(db)
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript(workbench_migration.read_text())
        db.execute('INSERT INTO schema_migrations VALUES (?)',(workbench_migration.name,))
        check(db.execute("SELECT count(*) FROM workbench_state_versions").fetchone()==(0,))
        check(db.execute("SELECT raw_bytes,sha256 FROM document_versions WHERE document_id=? AND revision=1",('legacy-document',)).fetchone()==(raw,hashlib.sha256(raw).hexdigest()))
    store=Store(database);check(store.document('legacy','legacy-document',1)['raw_bytes']==raw)
    with store.connect() as db:
        check([r[0] for r in db.execute('SELECT version FROM schema_migrations ORDER BY version')]==[path.name for path in migrations])
    service=WorkbenchState(store,tmp_path)
    key=WorkbenchKey(project_id='legacy',kind='report',object_id='original',revision=1,sha256='a'*64)
    service.save('legacy',WorkbenchSaveInput(key=key,operation_id='after-upgrade',expected_revision=0,fields={'comment':'升级后新草稿'},view={},dirty=True))
    check(WorkbenchState(Store(database),tmp_path).load('legacy',WorkbenchLoadInput(key=key))['fields']['comment']=='升级后新草稿')
    check(store.document('legacy','legacy-document',1)['raw_bytes']==raw)


def test_real_tcp_dropped_response_is_recovered_only_by_inspecting_original_operation(tmp_path):
    evidence=Evidence(tmp_path/'tcp-evidence');listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    settings=Settings(tmp_path/'tcp-data',port=port);app=create_app(settings)
    if not getattr(app.state,'workbench_state',None):register_workbench_state_routes(app,app.state.store,settings.data_dir,workspaces=app.state.local_workspaces)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
    try:
        for _ in range(200):
            if server.started:break
            check(thread.is_alive(),'real TCP server stopped before ready');time.sleep(.01)
        check(server.started,'real TCP server did not become ready')
        with httpx.Client(base_url=settings.origin,trust_env=False) as client:
            response=client.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'});csrf=response.json()['csrf_token']
            client.headers.update({'Origin':settings.origin,'X-EEAS-CSRF':csrf})
            pid=evidence.call(client,'POST','/api/projects',{'name':'实际TCP未知回执','description':''},201)['id'];base=f'/api/projects/{pid}/workbench-state'
            key={'project_id':pid,'kind':'report','object_id':'tcp-original','revision':1,'sha256':'d'*64}
            body={'key':key,'operation_id':'response-lost','expected_revision':0,'fields':{'comment':'HTTP响应未读，但草稿必须可追溯'},'view':{},'dirty':True}
            raw=json.dumps(body,ensure_ascii=True).encode();cookie='; '.join(k+'='+v for k,v in client.cookies.items())
            headers=(f'POST {base}/save HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: {settings.origin}\r\nX-EEAS-CSRF: {csrf}\r\nCookie: {cookie}\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}\r\nConnection: close\r\n\r\n').encode('ascii')
            with socket.create_connection(('127.0.0.1',port),timeout=2) as sent:
                sent.sendall(headers+raw);sent.shutdown(socket.SHUT_WR)
                # Close without reading any response. Only GET inspection follows.
            observed=None
            for _ in range(100):
                response=client.get(base+'/operations/response-lost')
                if response.status_code==200:observed=response.json();break
                check(response.status_code==404,response.text[:200]);time.sleep(.02)
            check(observed is not None and observed['fields']==body['fields'] and observed['replayed'])
            evidence.calls.append({'method':'RAW_TCP_POST','response_read':False,'automatic_repost_count':0,'inspected_receipt':observed});evidence.flush()
            loaded=evidence.call(client,'POST',base+'/load',{'key':key});check(loaded['state_revision']==1)
            check(counts(app.state.store)['workbench_state_operations']==1 and counts(app.state.store)['workbench_state_versions']==1)
    finally:
        server.should_exit=True;thread.join(timeout=10);listener.close()
        check(not thread.is_alive(),'owned TCP server did not stop normally')


def test_corrupt_operation_receipt_cannot_be_replayed_or_repaired_by_save(scene):
    s=scene;saved=call(s,'POST','/save',save_body(s));before=counts(s['store'])
    with s['store'].connect() as db:
        db.execute('DROP TRIGGER workbench_state_operations_no_update')
        db.execute("UPDATE workbench_state_operations SET result='corrupt-preserved-operation' WHERE operation_id='save-one'")
    call(s,'GET','/operations/save-one',status=409)
    call(s,'POST','/save',save_body(s),409)
    check(counts(s['store'])==before and call(s,'POST','/load',{'key':s['key']})['fields']==saved['fields'])
    with s['store'].connect() as db:
        check(db.execute("SELECT result FROM workbench_state_operations WHERE operation_id='save-one'").fetchone()[0]=='corrupt-preserved-operation')


def test_sqlite_triggers_preserve_old_snapshots_and_operation_identity(scene):
    s=scene;call(s,'POST','/save',save_body(s));before=counts(s['store'])
    statements=["UPDATE workbench_state_versions SET dirty=0", "DELETE FROM workbench_state_versions",
        "UPDATE workbench_state_operations SET request_sha256='changed'", "DELETE FROM workbench_state_operations",
        "UPDATE workbench_state_heads SET current_revision=current_revision+2", "DELETE FROM workbench_state_heads"]
    for statement in statements:
        with pytest.raises(sqlite3.IntegrityError):
            with s['store'].connect() as db:db.execute(statement)
    check(counts(s['store'])==before and call(s,'POST','/load',{'key':s['key']})['dirty'])


def annotation_fields():
    quote='原位😀批注'
    selected={'text':quote,'quote_sha256':hashlib.sha256(quote.encode()).hexdigest(),'offset_unit':'UNICODE_CODEPOINT',
        'locator':{'kind':'CANDIDATE_FIELD','field_pointer':'/claims/0/text','char_start':3,'char_end':3+len(quote)}}
    return {'graphFeedback':{'graph_1':{'comment':'尚在写的修改意见','tool':'RECTANGLE','selection':{
        'graph_id':'graph_1','node_ids':['node_1'],'normalized_points':[{'x':.1,'y':.2}],
        'viewport':{'width':1000,'height':700,'zoom':1.4,'pan':{'x':-21,'y':35}}}}},
        'textFeedback':{'selection_1':{'comment':'未提交意见','selection':selected}}}


def test_exact_graph_and_text_feedback_drafts_roundtrip_without_submission_authority(scene):
    s=scene;fields=annotation_fields()
    for kind in ('discovery','proposal','report'):
        key={**s['key'],'kind':kind}
        saved=call(s,'POST','/save',save_body(s,'annotations-'+kind,key=key,fields=fields))
        check(saved['fields']==fields)
        check(call(s,'POST','/load',{'key':key})['fields']==fields)
    # These are only incomplete gestures and quote self-consistency, never a
    # claim that a report/OCR/current application identity was reverified here.
    for index,locator in enumerate([
        {'kind':'REPORT_PARAGRAPH','source_kind':'DISCOVERY','source_id':'original-discovery','artifact_id':'b'*64,
         'docx_sha256':'c'*64,'paragraph_index':1,'char_start':3,'char_end':8},
        {'kind':'OCR_LINE','observation_ref':{'id':'original-observation','revision':2,'sha256':'d'*64},
         'line_id':'line_1','char_start':3,'char_end':8},
    ]):
        changed=deepcopy(fields);changed['textFeedback']['selection_1']['selection']['locator']=locator
        value=call(s,'POST','/save',save_body(s,'locator-'+str(index),index+1,key={**s['key'],'kind':'report'},fields=changed))
        check(value['fields']==changed)
    check(counts(s['store'])['agent_executions']==0 and counts(s['store'])['task_runs']==0)


def test_feedback_draft_limits_hash_and_partial_input_rejection_keep_saved_comments(scene):
    s=scene;fields=annotation_fields();key={**s['key'],'kind':'discovery'}
    call(s,'POST','/save',save_body(s,'annotation-original',key=key,fields=fields));before=counts(s['store'])
    variants=[]
    altered=deepcopy(fields);altered['graphFeedback']['graph_1']['sent']=True;variants.append(altered)
    altered=deepcopy(fields);altered['graphFeedback']['graph_1']['tool']=[];variants.append(altered)
    altered=deepcopy(fields);altered['graphFeedback']['graph_1']['selection']['graph_id']='different';variants.append(altered)
    altered=deepcopy(fields);altered['graphFeedback']['graph_1']['selection']['normalized_points']=[{'x':0,'y':0}]*513;variants.append(altered)
    altered=deepcopy(fields);altered['graphFeedback']['graph_1']['selection']['normalized_points']=[{'x':2,'y':0}];variants.append(altered)
    altered=deepcopy(fields);altered['textFeedback']['selection_1']['selection']['quote_sha256']='0'*64;variants.append(altered)
    altered=deepcopy(fields);altered['textFeedback']['selection_1']['selection'].pop('offset_unit');variants.append(altered)
    altered=deepcopy(fields);altered['textFeedback']['selection_1']['selection']['locator']['char_end']=3;variants.append(altered)
    altered=deepcopy(fields);altered['textFeedback']['selection_1']['selection']['locator']['kind']='BLOB_URL';variants.append(altered)
    altered=deepcopy(fields);altered['textFeedback']={str(i):fields['textFeedback']['selection_1'] for i in range(65)};variants.append(altered)
    for index,changed in enumerate(variants):
        call(s,'POST','/save',save_body(s,'annotation-invalid-'+str(index),1,key=key,fields=changed),422)
        check(counts(s['store'])==before and call(s,'POST','/load',{'key':key})['fields']==fields)


def test_draft_pages_have_aggregate_byte_budget_without_deleting_large_drafts(scene):
    s=scene;keys=[]
    for index in range(3):
        key={**s['key'],'kind':'proposal','revision':index+1};keys.append(key)
        call(s,'POST','/save',save_body(s,'large-draft-'+str(index),key=key,
            fields={'candidate':{'notes':'x'*(1024*1024),'details':'y'*(512*1024)}}))
    first=call(s,'GET','/drafts?limit=100');check(len(first['drafts'])==2 and first['has_more'] and first['next_offset']==2)
    check(sum(len(json.dumps(v,ensure_ascii=False,separators=(',',':')).encode()) for v in first['drafts'])<=4*1024*1024)
    second=call(s,'GET','/drafts?limit=100&offset='+str(first['next_offset']))
    check(len(second['drafts'])==1 and not second['has_more'])
    check({json.dumps(v['key'],sort_keys=True) for v in first['drafts']+second['drafts']}=={json.dumps(v,sort_keys=True) for v in keys})
    check(counts(s['store'])['workbench_state_heads']==3)


def test_local_file_ocr_text_draft_uses_real_image_source_and_existing_strict_selection_shape(scene):
    from PIL import Image, ImageDraw
    s=scene;image=Image.new('RGB',(240,80),'white');ImageDraw.Draw(image).text((10,20),'original visible words',fill='black')
    image.save(s['folder']/'ocr-original.png');image.close()
    source=s['evidence'].call(s['client'],'GET',f'/api/projects/{s["pid"]}/local-workspaces/{s["bound"]["id"]}/file?path=ocr-original.png')['source_ref']
    key={**s['key'],'object_id':source['path'],'revision':source['revision'],'sha256':source['sha256']}
    fields={'sourceRef':source,'textFeedback':annotation_fields()['textFeedback']}
    fields['textFeedback']['selection_1']['selection']['locator']={'kind':'OCR_LINE',
        'observation_ref':{'id':'caller-attached-observation','revision':1,'sha256':'d'*64},'line_id':'line_1','char_start':3,'char_end':8}
    saved=call(s,'POST','/save',save_body(s,'local-ocr-text',key=key,fields=fields))
    check(saved['fields']==fields)
    reopened=WorkbenchState(Store(s['data']/'eeas.sqlite3'),s['data'],workspaces=LocalWorkspaces(s['store'],s['data']))
    check(reopened.load(s['pid'],WorkbenchLoadInput(key=WorkbenchKey.model_validate(key)))['fields']==fields)
    before=counts(s['store']);wrong=deepcopy(fields);wrong['textFeedback']['selection_1']['selection']['quote_sha256']='0'*64
    call(s,'POST','/save',save_body(s,'bad-local-quote',1,key=key,fields=wrong),422)
    call(s,'POST','/save',save_body(s,'local-graph-not-supported',1,key=key,fields={'graphFeedback':annotation_fields()['graphFeedback']}),422)
    check(counts(s['store'])==before and call(s,'POST','/load',{'key':key})['fields']==fields)
    check((s['folder']/'ocr-original.png').read_bytes() and counts(s['store'])['agent_executions']==0)
    # This slice preserves an attached OCR locator and selected quote. It does
    # not claim that the OCR engine recognized this caller-attached observation.

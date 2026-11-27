"""Real router/SQLite, controlled executor. Exercise retries and review gates, never a model."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from packages.dynamic_workflow.stage_runs import StageRuns
from packages.domain.store import Conflict

ROOT = '/api/stage-workflows'


def ok(response):
    assert 200 <= response.status_code < 300, response.text
    return response.json()


def scope(client, label='Workflow'):
    pid = ok(client.post('/api/projects',json={'name':label}))['id']
    cid = ok(client.post('/api/conversations',json={'project_id':pid,'operation_id':'conversation_'+pid}))['conversation_ref']['id']
    return {'project_id':pid,'conversation_id':cid}


def node(ident, inputs=None):
    return {'id':ident,'kind':'custom','name':'审核 '+ident,'inputs':inputs or []}


def define(client, owner, nodes, rev=0, name='Custom'):
    return ok(client.post(ROOT+'/definitions',json={**owner,'operation_id':f'def_{name}_{rev}',
        'expected_revision':rev,'definition':{'id':name,'name':name,'nodes':nodes}}))


def start(client, owner, definition='Custom', op='run', *, stored_version=None):
    run = ok(client.post(ROOT+'/runs',json={**owner,'operation_id':op,'definition_id':definition}))
    if stored_version is not None:
        runtime = client.app.state.stage_workflows
        with runtime.store.connect() as db:
            run = runtime._run(db,owner['project_id'],owner['conversation_id'],run['id'])
            run['contract_version'] = stored_version
            runtime._save(db,run)
    return run


def action(client,owner,run,op,kind,payload=None,**edits):
    body = {**owner,'operation_id':op,'expected_revision':run['revision'],'node_id':run['active_node_id'],
            'action':kind,'payload':payload or {},**edits}
    return client.post(ROOT+'/runs/'+run['id']+'/actions',json=body)


def sample(title):
    return {'summary':title,'items':[{'id':'R1','title':'范围','description':title},
                                    {'id':'R2','title':'环境','description':'保留'}]}


def test_execution_log_identifies_when_a_new_artifact_is_visible(client):
    owner = scope(client)
    define(client, owner, [node('A')])
    run = start(client, owner)
    store = client.app.state.stage_workflows
    original = sample('可审阅成果')
    store.executor = lambda _: {'artifact': original}
    run = ok(action(client, owner, run, 'reading-original', 'execute'))

    def failed(_):
        raise RuntimeError('暂不能继续')

    for op, executor, status, writes in (
        ('reading-failed', failed, 'failed', False),
        ('reading-questions', lambda _: {'questions': ['确认格式？']}, 'needs_input', False),
        ('reading-identical', lambda _: {'artifact': original}, 'review', True),
        ('reading-partial', lambda _: {'artifact': {**original, 'questions': ['确认格式？']}}, 'needs_input', True),
    ):
        store.executor = executor
        run = ok(action(client, owner, run, op, 'execute'))
        stage = run['stages'][0]
        assert stage['status'] == status
        assert stage['log'][-1]['operation_id'] == op
        assert bool(stage['log'][-1].get('artifact_written')) == writes
        if op != 'reading-partial':
            assert stage['artifact'] == original
    staged = ok(client.get(ROOT+'/runs/'+run['id'],params=owner))['stages'][0]
    assert staged['log'][-1] == stage['log'][-1]


def test_project_owner_snapshot_review_and_stage_context(client):
    owner = scope(client)
    default = ok(client.get(ROOT,params=owner))['definitions'][0]
    assert default['name']=='默认' and len(default['nodes'])==8
    define(client,owner,[node('A'),node('B'),node('C',['A'])])
    run = start(client,owner)
    assert start(client,owner)==run
    child_a = run['stages'][0]['stage_session_id']
    assert child_a != owner['conversation_id'] and len(child_a)>64
    assert ok(client.get(ROOT,params={**owner,'conversation_id':child_a}))['runs']==[run]
    assert sum(bool(s['stage_session_id']) for s in run['stages'])==1
    assert action(client,owner,run,'bad-approve','approve').status_code==409
    define(client,owner,[node('C')],rev=1)
    assert len(ok(client.get(ROOT+'/runs/'+run['id'],params=owner))['definition']['nodes'])==3
    calls = []
    def execute(context):
        calls.append(context)
        return {'artifact':sample(context['user_input'].get('answer','original'))}
    store = client.app.state.stage_workflows
    store.executor = execute
    first_run = run
    run = ok(action(client,owner,run,'exec-a','execute',{'user_input':{'secret':'only A'}}))
    assert run['stages'][0]['status']=='review' and not run['stages'][0]['approval_issues']
    assert calls[0]['annotations']==[] and calls[0]['inputs']=={}
    assert ok(action(client,owner,first_run,'exec-a','execute',{'user_input':{'secret':'only A'}}))==run
    assert len(calls)==1
    assert action(client,owner,first_run,'exec-a','execute',{'user_input':{'secret':'other'}}).status_code==409
    run = ok(action(client,owner,run,'mark-a','annotate',{'target':'R1','comment':'revise',
            'anchor':{'type':'stroke','points':[[0.2,0.3],[0.6,0.7]]}}))
    run = ok(action(client,owner,run,'mark-b','annotate',{'target':'R1','comment':'second'}))
    assert action(client,owner,run,'declined','approve').status_code==409
    aid,bid = [a['id'] for a in run['stages'][0]['annotations']]
    for i,ident in enumerate([aid,bid]):
        run = ok(action(client,owner,run,'revise-'+str(i),'execute',{'annotation_ids':[ident],
                         'user_input':{'answer':'revision'+str(i)}}))
        assert [a['id'] for a in calls[-1]['annotations']]==[ident]
    run = ok(action(client,owner,run,'note-a','note',{'text':'observed lesson'}))
    run = ok(action(client,owner,run,'approve-a','approve'))
    child_b = run['stages'][1]['stage_session_id']
    assert child_b not in [child_a,owner['conversation_id']] and run['active_node_id']=='B'
    assert action(client,{**owner,'conversation_id':child_a},run,'wrong-child','execute').status_code==409
    run = ok(action(client,owner,run,'exec-b','execute',{'user_input':{'private':'only B'}}))
    assert calls[-1]['inputs']=={} and calls[-1]['user_input']=={'private':'only B'}
    run = ok(action(client,owner,run,'approve-b','approve'))
    run = ok(action(client,owner,run,'exec-c','execute'))
    assert calls[-1]['inputs']=={'A':sample('revision1')}
    assert calls[-1]['prior_artifact'] is None and calls[-1]['annotations']==[] and calls[-1]['user_input']=={}
    assert 'only A' not in repr(calls[-1]['approved_by_kind'])
    run = ok(action(client,owner,run,'approve-c','approve'))
    assert run['status']=='completed' and run['active_node_id'] is None
    reopened = StageRuns(store.store,store.data_dir,store.create_child,store.executor)
    assert reopened.get(owner['project_id'],owner['conversation_id'],run['id'])==run
    other = scope(client,'Other')
    assert client.get(ROOT+'/runs/'+run['id'],params=other).status_code==404
    assert client.get(ROOT,params={**owner,'project_id':other['project_id']}).status_code==404
    assert {s['stage_session_id'] for s in run['stages']} <= {r['id'] for r in ok(client.get('/api/conversations'))['items']}


def test_template_pinning_and_actual_clarification(client):
    owner = scope(client)
    template = ok(client.post(ROOT+'/template',data=owner,files={'file':('acceptance.csv','项目,通过标准'.encode(),'text/csv')}))
    assert template['format']=='csv' and template['text']=='项目,通过标准'
    define(client,owner,[node('A')])
    run = start(client,owner)
    calls = []
    def execute(context):
        calls.append(context)
        if not context['user_input'].get('answer'):
            return {'questions':['项目背景是什么？']}
        return {'artifact':sample('answer')}
    client.app.state.stage_workflows.executor = execute
    run = ok(action(client,owner,run,'template-exec','execute',{'template':{**template,'text':'forged'}}))
    assert calls[-1]['template']['text']==template['text'] and run['stages'][0]['status']=='needs_input'
    run = ok(action(client,owner,run,'clarify','execute',{'user_input':{'answer':'device'}}))
    assert run['stages'][0]['approval_issues'] # Model omitted template output.
    assert action(client,owner,run,'invalid','approve').status_code==409
    other = scope(client,'Other')
    define(client,other,[node('only')],name='Other')
    run = start(client,other,'Other','another')
    assert action(client,other,run,'foreign-template','execute',{'template':template}).status_code==404


def test_concurrent_dispatch_is_observable_and_not_replayed(client):
    owner = scope(client)
    define(client,owner,[node('A')])
    run = start(client,owner)
    store = client.app.state.stage_workflows
    entered, release, calls = Event(), Event(), []
    def execute(context):
        calls.append(context['operation_id'])
        context['_progress'](1,2,'完成第一项')
        entered.set()
        assert release.wait(8)
        return {'artifact':sample('done')}
    store.executor = execute
    args = (owner['project_id'],owner['conversation_id'],run['id'],'once',run['revision'],'A','execute',{})
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(store.action,*args)
        assert entered.wait(8)
        try:
            mid = store.action(*args)
            assert mid['stages'][0]['status']=='running' and mid['stages'][0]['progress']['completed']==1
            with pytest.raises(Conflict):
                store.action(*args[:3],'different',mid['revision'],'A','execute',{})
            with pytest.raises(Conflict):
                store.action(*args[:3],'recover',mid['revision'],'A','recover',{})
        finally:
            release.set()
        result = future.result(timeout=8)
    assert result['stages'][0]['status']=='review' and calls==['once']
    assert store.action(*args)==result


def test_stop_targets_only_the_running_attempt_and_keeps_partial_evidence(client):
    from packages.dynamic_workflow import stage_workspace
    from packages.dynamic_workflow.stage_tool_server import check_owner

    owner = scope(client)
    define(client,owner,[node('A')])
    run = start(client,owner)
    stage = run['stages'][0]
    child = {**owner,'conversation_id':stage['stage_session_id']}
    store = client.app.state.stage_workflows
    entered, release = Event(), Event()
    observed = {}
    def execute(context):
        observed['context'] = context
        observed['file'] = stage_workspace.file_write(context,'partial.txt','first result','')
        with stage_workspace.ledger(context['_data_dir']) as db:
            observed['connector'] = stage_workspace._record(db,context,'connector_call',server='meter',tool='observe',
                status='unconfirmed',arguments_sha256='a'*64,config_ref={'id':'meter','revision':1})
        context['_progress'](1,2,'完成第一项')
        entered.set()
        assert release.wait(12)
        assert context['_stop_requested']()
        with pytest.raises(ValueError,match='本次执行已结束'):
            check_owner(context)
        return {'artifact':sample('late result')}
    store.executor = execute
    args = (owner['project_id'],owner['conversation_id'],run['id'],'run-original',run['revision'],'A','execute',{})
    original = {'operation_id':'run-original'}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(store.action,*args)
        assert entered.wait(8)
        try:
            check_owner(observed['context'])
            path=ROOT+'/runs/'+run['id']+'/stages/A/execution-record'
            audit={**child,'operation_id':'run-original'}
            live_record=ok(client.get(path,params=audit))
            assert live_record['live'] and live_record['status']=='running'
            assert live_record['started_at']==live_record['at']
            assert live_record['evidence'][0]['recorded_at']==observed['file']['recorded_at']
            assert live_record['evidence'][0]['previous_sha256'] is None
            assert live_record['evidence'][0]['sha256']==observed['file']['sha256']
            assert client.get(path,params={**owner,'operation_id':'run-original'}).status_code==404
            assert action(client,owner,run,'stop-parent','stop',original).status_code==409
            assert action(client,child,run,'stop-other','stop',{'operation_id':'another-run'}).status_code==409
            requested=ok(action(client,child,run,'stop-first','stop',original))
            assert requested['stages'][0]['stop_request']['status']=='requested'
            assert ok(action(client,child,run,'stop-racing-window','stop',original))==requested
            live=ok(client.get(ROOT+'/runs/'+run['id'],params=child))
            assert live['stages'][0]['execution_state']=='stopping'
            assert ok(client.get(path,params=audit))['status']=='stopping'
        finally:
            release.set()
        stopped=future.result(timeout=8)
    stage=stopped['stages'][0]
    assert stage['status']=='failed' and stage['stop_request']['status']=='stopped'
    assert stage['artifact'] is None and stage['verified_evidence']==[observed['file'],observed['connector']]
    assert [e['event'] for e in stage['log']].count('stop_requested')==1
    url=ROOT+'/runs/'+run['id']+'/stages/A/execution-record'
    record=ok(client.get(url,params={**child,'operation_id':'run-original'}))
    assert record['status']=='stopped' and not record['live'] and record['evidence']==live_record['evidence']
    assert stage['execution_origin']=={'operation_id':'run-original','kernel':'opencode','at':record['at']}
    assert record['kernel']=='opencode'
    assert record['evidence'][1]=={k:observed['connector'][k] for k in ('id','kind','server','tool','status','recorded_at')}
    assert ok(action(client,child,run,'stop-first','stop',original))==stopped
    assert action(client,child,stopped,'stop-late','stop',original).status_code==409
    configured=ok(action(client,child,stopped,'change-engine','configure',{'kernel':'claude-code'}))
    stage=configured['stages'][0]
    assert stage['config']['kernel']=='claude-code'
    assert stage['execution_origin']==stopped['stages'][0]['execution_origin']
    assert ok(client.get(ROOT+'/runs/'+run['id'],params=child))['stages'][0]['execution_origin']==stage['execution_origin']

    def retry(context):
        assert context['kernel']=='claude-code'
        return {'artifact':sample('explicit retry')}
    store.executor = retry
    retried=ok(action(client,child,configured,'fresh-retry','execute'))
    assert retried['stages'][0]['status']=='review' and 'stop_request' not in retried['stages'][0]
    assert ok(client.get(url,params={**child,'operation_id':'run-original'}))==record
    assert ok(client.get(url,params={**child,'operation_id':'fresh-retry'}))['kernel']=='claude-code'


def test_legacy_failure_only_infers_engine_for_matching_execution_config(client):
    from packages.domain.tasks import canonical, digest

    owner=scope(client)
    define(client,owner,[node('A')])
    run=start(client,owner)
    child={**owner,'conversation_id':run['stages'][0]['stage_session_id']}
    store=client.app.state.stage_workflows

    def fail(_):
        raise ValueError('Fixture engine unavailable')
    store.executor=fail
    failed=ok(action(client,child,run,'old-attempt','execute'))
    # Restore the format persisted before the attempt stored an explicit engine.
    with store.store.connect() as db:
        stored=store._run(db,owner['project_id'],owner['conversation_id'],run['id'])
        start_event=next(event for event in stored['stages'][0]['log'] if event['event']=='started')
        start_event.pop('kernel')
        db.execute('UPDATE stage_workflow_runs SET body=?,sha256=? WHERE id=?',
                   (canonical(stored),digest(stored),run['id']))
        raw=db.execute('SELECT body FROM stage_workflow_attempts WHERE operation_id=?',('old-attempt',)).fetchone()
        import json
        record=json.loads(raw['body'])
        record.pop('kernel')
        serialized=canonical(record)
        db.execute('UPDATE stage_workflow_attempts SET body=?,sha256=? WHERE operation_id=?',
                   (serialized,digest(record),'old-attempt'))
        db.commit()

    url=ROOT+'/runs/'+run['id']
    details=url+'/stages/A/execution-record'
    original=ok(client.get(url,params=child))['stages'][0]['execution_origin']
    assert original['kernel']=='opencode'
    assert ok(client.get(details,params={**child,'operation_id':'old-attempt'}))['kernel']=='opencode'
    configured=ok(action(client,child,failed,'new-config','configure',{'kernel':'claude-code'}))
    assert configured['stages'][0]['execution_origin']=={**original,'kernel':None}
    assert ok(client.get(details,params={**child,'operation_id':'old-attempt'}))['kernel'] is None
    with store.store.connect() as db:
        assert db.execute('SELECT body FROM stage_workflow_attempts WHERE operation_id=?',
                          ('old-attempt',)).fetchone()['body']==serialized


def register_meter(service,tmp_path):
    import hashlib
    from pathlib import Path
    import sys
    from packages.domain.native_capabilities import NativeCapabilities

    fixture = tmp_path/'meter.py'
    fixture.write_text('# Controlled stage executor records the original dispatch.\n')
    cmd = [str(Path(sys.executable).resolve()),str(fixture)]
    NativeCapabilities(service.store,service.data_dir).configure_mcp({
        'id':'meter','operation_id':'stage-retry-connector','expected_revision':0,
        'configuration':{'type':'local','enabled':True,'command':cmd,'timeout':3000,
                         'files_sha256':{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in cmd}}})


def test_connector_retry_requires_the_current_evidence_acknowledgement(client,tmp_path):
    from packages.dynamic_workflow import stage_workspace

    service = client.app.state.stage_workflows
    register_meter(service,tmp_path)
    owner = scope(client)
    define(client,owner,[{**node('A'),'capabilities':{'mcp':['meter']}}])
    run = start(client,owner)
    child = {**owner,'conversation_id':run['stages'][0]['stage_session_id']}
    calls = []
    uncertain = []

    def execute(ctx):
        calls.append(ctx['operation_id'])
        if len(calls)==1:
            with stage_workspace.ledger(ctx['_data_dir']) as db:
                for tool in ('observe','snapshot'):
                    uncertain.append(stage_workspace._record(db,ctx,'connector_call',server='meter',tool=tool,
                                                             status='unconfirmed'))
            raise RuntimeError('Original connection ended before its reply')
        return {'artifact':sample('confirmed retry')}

    service.executor = execute
    failed = ok(action(client,child,run,'original','execute'))
    reading = ROOT+'/runs/'+run['id']+'/stages/A/execution-record'
    original = ok(client.get(reading,params={**child,'operation_id':'original'}))
    assert len(original['evidence'])==2
    ack = {'operation_id':'original','evidence_ids':[item['id'] for item in uncertain]}
    refused = (
        {},
        {'connector_retry_ack':{**ack,'operation_id':'different-original'}},
        {'connector_retry_ack':{**ack,'evidence_ids':ack['evidence_ids'][:1]}},
        {'connector_retry_ack':{**ack,'evidence_ids':[*ack['evidence_ids'],'extra']}},
    )
    for i,payload in enumerate(refused):
        assert action(client,child,failed,f'not-confirmed-{i}','execute',payload).status_code==409
    assert calls==['original']
    assert ok(client.get(ROOT+'/runs/'+run['id'],params=child))==failed
    request = {'connector_retry_ack':ack}
    accepted = ok(action(client,child,failed,'confirmed','execute',request))
    assert accepted['stages'][0]['status']=='review'
    assert ok(action(client,child,failed,'confirmed','execute',request))==accepted
    assert calls==['original','confirmed']
    started = next(event for event in accepted['stages'][0]['log'] if event['event']=='started' and event['operation_id']=='confirmed')
    assert started['connector_retry_ack']=={**ack,'evidence_ids':sorted(ack['evidence_ids'])}
    assert ok(client.get(reading,params={**child,'operation_id':'original'}))==original
    assert action(client,child,accepted,'stale-confirmation','execute',request).status_code==409
    assert ok(action(client,child,accepted,'normal-next-execution','execute'))['stages'][0]['status']=='review'
    assert calls==['original','confirmed','normal-next-execution']


def test_connector_retry_follows_a_revised_node_and_protects_original_scope(client,tmp_path):
    from packages.dynamic_workflow import stage_workspace

    service = client.app.state.stage_workflows
    register_meter(service,tmp_path)
    owner = scope(client)
    caps = {'mcp':['meter']}
    define(client,owner,[{**node('A'),'capabilities':caps},{**node('B'),'capabilities':caps}])
    run = start(client,owner)
    calls = []
    def execute(ctx):
        op = ctx['operation_id']
        calls.append(op)
        if op=='initial-tool':
            with stage_workspace.ledger(ctx['_data_dir']) as db:
                stage_workspace._record(db,ctx,'connector_call',server='meter',tool='observe',status='unconfirmed')
        else:
            assert all(item.get('tool')!='observe' for item in ctx['prior_evidence'])
        return {'artifact':sample('Exact version'),'verified_evidence':stage_workspace.operation_receipts(ctx)}

    service.executor = execute
    original_scope = {**owner,'conversation_id':run['stages'][0]['stage_session_id']}
    run = ok(action(client,original_scope,run,'initial-tool','execute'))
    operation = {**original_scope,'operation_id':'initial-tool'}
    reading = f"{ROOT}/runs/{run['id']}/stages/A/execution-record"
    original = ok(client.get(reading,params=operation))
    record, = original['evidence']
    run = ok(action(client,original_scope,run,'approve-tool-version','approve'))
    assert run['active_node_id']=='B' and 'connector_retry' not in run['stages'][1]
    run = ok(action(client,owner,run,'independent-node','execute'))
    run = ok(action(client,original_scope,run,'first-revision','revise',{'text':'Revise details'},node_id='A'))
    stage = run['stages'][0]
    assert stage['stage_session_id'] != original_scope['conversation_id'] and stage['verified_evidence']==[]
    fresh = {**owner,'conversation_id':stage['stage_session_id']}
    assert action(client,fresh,run,'no-confirmation','execute').status_code==409
    summary = {'operation_id':None,'evidence':[{
        'id':record['id'],'server':'meter','tool':'observe','previous':True,'recorded_at':record['recorded_at']}]}
    assert stage['connector_retry']==summary
    assert ok(client.get(ROOT,params=fresh))['runs'][0]==run
    assert ok(client.get(reading,params=operation))==original

    run = ok(action(client,fresh,run,'remove-meter','configure',{'capabilities':{'mcp':[]}}))
    assert 'connector_retry' not in run['stages'][0]
    run = ok(action(client,fresh,run,'run-without-meter','execute'))
    run = ok(action(client,fresh,run,'approve-second-version','approve'))
    run = ok(action(client,fresh,run,'second-revision','revise',{'text':'Add another detail'},node_id='A'))
    latest = {**owner,'conversation_id':run['stages'][0]['stage_session_id']}
    run = ok(action(client,latest,run,'add-meter-back','configure',{'capabilities':caps}))
    assert run['stages'][0]['connector_retry']==summary

    destination = ok(client.post('/api/projects',json={'name':'Separate history archive'}))['id']
    with service.store.connect() as db:
        db.execute('UPDATE conversation_threads SET project_id=? WHERE conversation_id=?',
                   (destination,original_scope['conversation_id']))
    assert client.get(reading,params=operation).status_code==404
    run = ok(client.get(ROOT+'/runs/'+run['id'],params=latest))
    assert run['stages'][0]['connector_retry']=={'operation_id':None,'evidence':[{
        'id':record['id'],'server':'meter','previous':True}]}
    ack = {'operation_id':None,'evidence_ids':[record['id']]}
    for i,invalid in enumerate(({}, {'operation_id':'initial-tool','evidence_ids':[record['id']]},
                                {'evidence_ids':[record['id']]})):
        assert action(client,latest,run,f'unconfirmed-{i}','execute',{'connector_retry_ack':invalid}).status_code==409
    assert calls==['initial-tool','independent-node','run-without-meter']
    accepted = ok(action(client,latest,run,'verified-lineage','execute',{'connector_retry_ack':ack}))
    assert accepted['stages'][0]['verified_evidence']==[] and 'connector_retry' not in accepted['stages'][0]
    attempt = f"{ROOT}/runs/{run['id']}/stages/A/attempts"
    assert ok(client.get(attempt,params={**latest,'operation_id':'verified-lineage'}))['connector_retry_ack']==ack
    assert ok(action(client,latest,run,'verified-lineage','execute',{'connector_retry_ack':ack}))==accepted
    assert calls.count('verified-lineage')==1
    accepted = ok(action(client,latest,accepted,'approve-confirmed','approve'))
    accepted = ok(action(client,latest,accepted,'third-revision','revise',{'text':'Final polish'},node_id='A'))
    assert 'connector_retry' not in accepted['stages'][0]


def test_invalid_order_foreign_proof_and_failed_write(client):
    owner = scope(client)
    response = client.post(ROOT+'/definitions',json={**owner,'operation_id':'invalid','expected_revision':0,
        'definition':{'id':'broken','name':'Broken','nodes':[node('B',['A']),node('A')]}})
    assert response.status_code==422
    errs = StageRuns._evidence_issues('testing',{'items':[{'case_id':'C2','evidence_refs':['r']}]},
                        [{'id':'r','kind':'check','status':'passed','exit_code':0,'case_id':'C1'}])
    assert errs
    assert StageRuns._evidence_issues('delivery',{'items':[{'id':'d','path':'a.txt','sha256':'a'*64}]},
                            [{'id':'f','kind':'file','path':'a.txt','sha256':'b'*64}])
    define(client,owner,[node('A')])
    run = start(client,owner)
    def failed(context):
        raise RuntimeError('review output required')
    client.app.state.stage_workflows.executor=failed
    run = ok(action(client,owner,run,'failed','execute'))
    assert run['stages'][0]['status']=='failed' and run['stages'][0]['error']=='review output required'
    assert action(client,owner,run,'failed-approve','approve').status_code==409


def test_approved_lesson_applies_one_registered_capability_to_future_definitions(client):
    from packages.domain.native_capabilities import NativeCapabilities
    from packages.domain.tasks import digest

    owner=scope(client)
    alien=scope(client,'Foreign improvement')
    store=client.app.state.stage_workflows
    registry=NativeCapabilities(client.app.state.store,store.data_dir)
    skill={'operation_id':'imp-skill','id':'qa-record','expected_revision':0,'enabled':True,
        'content':'---\nname: qa-record\ndescription: Review the approved fixture.\n---\nCompare the file digest.\n'}
    skill_ref=registry.import_skill(skill)['config_ref']
    service_ref=registry.configure_mcp({'operation_id':'imp-mcp','id':'fixture-capability','expected_revision':0,
        'configuration':{'type':'remote','url':'https://mcp.example.invalid/mcp','enabled':True,'timeout':2000}})['config_ref']
    base=define(client,owner,[node('A'),node('B',['A'])],name='Improvement')
    run=start(client,owner,'Improvement','imp-original')
    quote='交付前核对文件摘要。'
    store.executor=lambda _:{'artifact':{**sample('原成果'),'lessons':[quote],
        'plugin_suggestions':['核对实际文件所用的工具。']}}
    run=ok(action(client,owner,run,'imp-exec','execute'))
    stage=run['stages'][0]
    child={**owner,'conversation_id':stage['stage_session_id']}
    url=ROOT+'/runs/'+run['id']+'/stages/A/improvements'
    query={**child,'approval_sha256':'0'*64}
    packet={**query,'operation_id':'imp-tools','source_field':'lessons','source_index':0,'target_node_id':'B',
            'category':'tools','change':'add','capability_id':'workspace_read','mode':'update'}
    assert client.post(url,json=packet).status_code==404
    run=ok(action(client,owner,run,'imp-approve','approve'))
    approval=run['stages'][0]['approval_sha256']
    query['approval_sha256']=packet['approval_sha256']=approval
    assert ok(client.get(url,params=query))==[]
    assert client.post(url,json={**packet,**alien}).status_code==404
    assert client.post(url,json={**packet,'source_index':1}).status_code==404
    assert client.post(url,json={**packet,'target_node_id':'foreign'}).status_code==404
    assert client.post(url,json={**packet,'capability_id':'unregistered'}).status_code==409
    applied=ok(client.post(url,json=packet))
    reference=applied['receipt']
    new=applied['definition']
    assert reference['source']['text_sha256']==digest(quote)
    assert reference['source']['approval_sha256']==approval
    assert new['revision']==2 and new['id']==base['id']
    assert new['nodes'][0]==base['nodes'][0]
    assert new['nodes'][1]['capabilities']['tools']==['workspace_read']
    history_url=ROOT+'/definitions/'+new['id']+'/improvements'
    def definition_history(version, caller=owner, **params):
        return ok(client.get(history_url,params={**caller,'revision':version,**params}))
    def source_version(operation, version, caller=owner):
        return client.get(history_url+'/'+operation,params={**caller,'revision':version})
    assert definition_history(1)=={'records':[],'next_before':None}
    added,=definition_history(2)['records']
    assert added=={'operation_id':'imp-tools','revision':2,'state':'matches','at':reference['at'],
        'target_name':'审核 B','category':'tools','change':'add','capability_id':'workspace_read'}
    origin=ok(source_version('imp-tools',2))
    assert origin['quote']==quote and origin['approval_sha256']==approval
    assert origin['stage_session_id']==child['conversation_id'] and origin['historical'] is False
    assert origin['stage_name']=='审核 A' and origin['field']=='lessons'
    assert source_version('imp-tools',1).status_code==404
    assert client.get(history_url,params={**owner,'revision':99}).status_code==404
    assert client.get(history_url,params={**owner,'conversation_id':alien['conversation_id'],'revision':2}).status_code==404
    ok(client.post(ROOT+'/definitions',json={**alien,'operation_id':'imp-other-same-id','definition':base,'expected_revision':0}))
    assert definition_history(1,caller=alien)['records']==[]
    assert source_version('imp-tools',1,caller=alien).status_code==404
    assert ok(client.get(ROOT,params=alien))['improved_definition_ids']==[]
    assert ok(client.get(ROOT,params=owner))['improved_definition_ids']==[new['id']]
    assert ok(client.get(ROOT+'/runs/'+run['id'],params=owner))==run
    assert client.post(url,json={**packet,'operation_id':'imp-stale','capability_id':'workspace_list'}).status_code==409
    edited=ok(client.post(ROOT+'/definitions',json={**owner,'operation_id':'imp-edit-head','definition':new,'expected_revision':2}))
    assert edited['revision']==3
    assert definition_history(3)['records']==[added]
    assert ok(source_version('imp-tools',3))==origin
    assert ok(client.post(url,json=packet))==applied
    assert client.post(url,json={**packet,'capability_id':'workspace_list'}).status_code==409

    for category,ref in [('skills',skill_ref),('mcp',service_ref)]:
        options={**packet,'operation_id':'imp-copy-'+category,'mode':'copy','copy_name':'隔离的 '+category,
                 'category':category,'capability_id':ref['id'],'capability_sha256':ref['sha256'],
                 'source_field':'plugin_suggestions'}
        assert client.post(url,json={**options,'capability_sha256':'0'*64}).status_code==409
        changed=ok(client.post(url,json=options))
        assert changed['receipt']['capability_ref']==ref
        assert changed['definition']['revision']==1 and changed['definition']['id']!=base['id']
        copied_url=ROOT+'/definitions/'+changed['definition']['id']+'/improvements'
        copied,=ok(client.get(copied_url,params={**owner,'revision':1}))['records']
        assert copied['operation_id']==options['operation_id'] and copied['state']=='matches'
        source=ok(client.get(copied_url+'/'+copied['operation_id'],params={**owner,'revision':1}))
        assert source['quote']=='核对实际文件所用的工具。' and source['stage_session_id']==child['conversation_id']
        assert source['field']=='plugin_suggestions'
        assert source_version(copied['operation_id'],3).status_code==404
        next_run=start(client,owner,changed['definition']['id'],'imp-run-'+category)
        assert next_run['stages'][0]['config']['capabilities'][category]==[]
        assert next_run['stages'][1]['policy_pin']['capability_refs'][category][ref['id']]==ref
        next_run=ok(action(client,owner,next_run,'imp-exec-a-'+category,'execute'))
        next_run=ok(action(client,owner,next_run,'imp-approve-a-'+category,'approve'))
        captured=[]
        def inspect(context):
            captured.append(context)
            return {'artifact':sample('选用后执行')}
        execute,store.executor=store.executor,inspect
        try:
            ok(action(client,owner,next_run,'imp-exec-b-'+category,'execute'))
        finally:
            store.executor=execute
        assert captured[0]['capabilities'][category]==[ref['id']]
        assert captured[0]['policy_pin']['capability_refs'][category][ref['id']]==ref

    revised=ok(action(client,child,run,'imp-revision','revise',{'text':'下一版'},node_id='A'))
    assert revised['stages'][0]['stage_session_id']!=child['conversation_id']
    records=ok(client.get(url,params=query))
    assert len(records)==3 and records[-1]==reference
    assert ok(source_version('imp-tools',3))=={**origin,'historical':True}
    assert ok(client.post(url,json=packet))==applied
    assert client.get(url,params={**query,'conversation_id':revised['stages'][0]['stage_session_id']}).status_code==404

    newest=start(client,owner,base['id'],'imp-newest')
    assert newest['stages'][1]['config']['capabilities']['tools']==['workspace_read']
    newest=ok(action(client,owner,newest,'imp-execute-newest','execute'))
    newest=ok(action(client,owner,newest,'imp-approve-newest','approve'))
    stage=newest['stages'][0]
    removed=ok(client.post(ROOT+'/runs/'+newest['id']+'/stages/A/improvements',json={
        **packet,'operation_id':'imp-remove','change':'remove','conversation_id':stage['stage_session_id'],
        'approval_sha256':stage['approval_sha256']}))
    assert removed['definition']['revision']==4 and removed['definition']['nodes'][1]['capabilities']['tools']==[]
    deleted, older=definition_history(4)['records']
    assert (deleted['revision'],deleted['state'],deleted['change'])==(4,'matches','remove')
    assert older=={**added,'state':'changed'} and definition_history(4,before=4)['records']==[older]
    assert source_version('imp-remove',3).status_code==404
    assert ok(source_version('imp-remove',4))['stage_session_id']==stage['stage_session_id']
    assert ok(client.get(ROOT+'/runs/'+newest['id'],params=owner))==newest
    fresh=start(client,owner,base['id'],'imp-without-tool')
    assert fresh['stages'][1]['config']['capabilities']['tools']==[]
    reduced={**removed['definition'],'nodes':[removed['definition']['nodes'][0]]}
    ok(client.post(ROOT+'/definitions',json={**owner,'operation_id':'imp-remove-node','definition':reduced,'expected_revision':4}))
    assert [row['state'] for row in definition_history(5)['records']]==['node_removed','node_removed']
    assert ok(source_version('imp-tools',5))['quote']==quote
    with store.store.connect() as db:
        db.execute("UPDATE conversation_threads SET deleted_at='2026-11-26T00:00:00Z' WHERE conversation_id=?",
                   (child['conversation_id'],))
    assert len(definition_history(5)['records'])==2
    assert source_version('imp-tools',5).status_code==404


@pytest.mark.parametrize("with_observation", [False, True], ids=["automated", "mixed"])
def test_default_eight_stages_actual_file_failed_check_rework_and_download(client, with_observation, tmp_path):
    from copy import deepcopy
    from base64 import b64encode
    import hashlib
    from packages.dynamic_workflow import stage_workspace as workspace
    from packages.dynamic_workflow.stage_contracts import SCENARIO_V4_CONTRACT_VERSION
    from test_engineering_stage_contracts import APPROVED, scenario

    intake = {'ur':'生成状态文件 status.txt\n状态内容必须是 fixed'}
    supplement = b'success'*(600_000 if with_observation else 1)
    manual = {'id':'front-panel','method':'manual_observation','instruction':'查看设备待机指示','expected':'指示灯亮绿'}
    owner = scope(client)
    if not with_observation:
        register_meter(client.app.state.stage_workflows,tmp_path)
    run = start(client,owner,definition='default',stored_version=SCENARIO_V4_CONTRACT_VERSION)
    overview_url = ROOT+'/runs/'+run['id']+'/coverage-overview'
    def overview(current, viewer=None):
        report = ok(client.get(overview_url,params=viewer or owner))
        assert report['revision']==current['revision']
        return {row['row_id']:row['states'] for row in report['rows']}
    assert overview(run)=={}
    assert ok(client.get(overview_url,params=owner))['pending_sources']==1
    assert client.get(overview_url,params=scope(client,'unrelated coverage project')).status_code==404
    change_test_input = False
    def machine(context):
        kind = context['kind']
        artifact = deepcopy(APPROVED[kind])
        if kind == 'requirements':
            for i,row in enumerate(artifact['items']):
                row.update(description=intake['ur'].splitlines()[i],surfaces=[],design_spec='文本状态文件',
                           acceptance=['输出文件包含指定文本'])
        if kind == 'cases':
            artifact['items'][0].update(content='读取文件并核对内容',method='读取阶段文件',coverage=['runtime'],
                    steps=['读取 status.txt'],checks=['与固定文本逐字节匹配'],expected='fixed',
                    actions=[{'id':'content','method':'text_equals','path':'status.txt','expected':'fixed'},
                             *([manual] if with_observation else [])])
        if kind == 'design':
            artifact['items'][0]['directory']='status.txt'
            artifact['items'][0]['behavior_scenarios']=[
                scenario('S-1','OR-1','content'),scenario('S-2','OR-2','content')]
            if context['operation_id']=='skip-design-scenario':
                artifact['items'][0]['behavior_scenarios'].pop()
        if kind == 'development':
            prior = workspace.file_list(context)
            if not prior and not with_observation:
                with workspace.ledger(context['_data_dir']) as db:
                    workspace._record(db,context,'connector_call',server='meter',tool='observe',status='unconfirmed')
            if prior:
                assert context['rework_request']['case_ids']==['TC-1']
                assert context['rework_request']['observations'][0]['status']=='failed'
            content = 'fixed' if prior else 'wrong'
            actual = workspace.file_write(context,'status.txt',content,prior[0]['sha256'] if prior else '')
            close = workspace.complete_design_item(context,'DS-1',[actual['id']])
            artifact['items'][0]['evidence_refs'] = [close['id']]
        elif kind == 'testing':
            if context['operation_id']=='retest-current-file':
                current, = workspace.file_list(context)
                workspace.file_write(context,'status.txt','fixed',current['sha256'])
            result = workspace.check(context,'TC-1','status.txt','text_equals','fixed')
            if change_test_input:
                assert result['status']=='passed'
                workspace.file_write(context,'status.txt','changed after the check',result['sha256'])
            artifact['items'][0].update(status=result['status'],evidence_refs=[result['id']],rework=[])
        elif kind == 'delivery':
            files = [r for r in context['approved_evidence'] if r.get('kind')=='file']
            assert len(files)==1
            target = files[0]
            if context['operation_id']=='package-changed-member':
                workspace.file_write(context,target['path'],'substituted',target['sha256'])
                changed = workspace.package_files(context,'substituted.zip',[target['path']])
                artifact['items'][0].update(path=changed['path'],sha256=changed['sha256'])
                return {'artifact':artifact,'verified_evidence':workspace.operation_receipts(context)}
            if not with_observation:
                current = workspace.file_read(context,target['path'])
                workspace.file_write(context,target['path'],'fixed',current['sha256'])
            bundle = workspace.package_files(context,'delivery.zip',[files[0]['path']])
            artifact['items'][0].update(path=bundle['path'],sha256=bundle['sha256'])
            other = workspace.file_write_bytes(context,'docs/校验数据.bin',supplement,'')
            artifact['items'].append({**artifact['items'][0],'id':'PKG-2','title':'校验数据',
                                     'path':other['path'],'sha256':other['sha256']})
        return {'artifact':artifact,'verified_evidence':workspace.operation_receipts(context)}
    client.app.state.stage_workflows.executor = machine
    for i in range(6):
        kind = run['active_node_id']
        if kind=='design':
            run = ok(action(client,owner,run,'skip-design-scenario','execute'))
            assert "OR-2 第 1 条通过标准尚未落到具体设计场景" in run['stages'][i]['approval_issues']
            assert action(client,owner,run,'reject-incomplete-design','approve').status_code==409
        if kind=='development' and not with_observation:
            run = ok(action(client,owner,run,'equip-dev','configure',{'capabilities':{
                **run['stages'][i]['config']['capabilities'],'mcp':['meter']}}))
        run = ok(action(client,owner,run,'exec-'+kind,'execute',
                        {'user_input':intake if kind=='requirements' else {}}))
        assert not run['stages'][i]['approval_issues']
        run = ok(action(client,owner,run,'approve-'+kind,'approve'))
    old_cases = deepcopy(run['stages'][3])
    old_dev = deepcopy(run['stages'][5])
    expected = {'requirements':'covered','alternatives':'covered','design':'covered',
                'development':'covered','cases':'covered','testing':'missing'}
    assert overview(run,{**owner,'conversation_id':old_dev['stage_session_id']})=={'OR-1':expected,'OR-2':expected}
    design_stage = run['stages'][4]
    design_record = ok(client.get(ROOT+'/runs/'+run['id']+'/stages/design/coverage-record',params={
        **owner,'conversation_id':design_stage['stage_session_id'],'row_id':'DS-1',
        'operation_id':design_stage['operation_id']}))
    assert any(item['stage_session_id']==old_cases['stage_session_id'] for item in design_record['inputs'])
    def implementation(current):
        params = {**owner,'conversation_id':current['stages'][1]['stage_session_id'],
                  'selector':'or','row_id':'OR-1'}
        result = ok(client.get(ROOT+'/runs/'+current['id']+'/stages/requirements/coverage',params=params))
        return result['requirements'][0]['implementation']
    traced = implementation(run)
    leaf, = traced['alternatives']
    design, = traced['design']
    dev, = traced['development']
    assert design['basis_ids']==[leaf['row_id']] and dev['basis_ids']==[design['row_id']]
    assert dev['status']=='registered' and dev['stage_session_id']==old_dev['stage_session_id']
    detail_ref = ROOT+'/runs/'+run['id']+'/stages/development/coverage-record'
    detail_params = {**owner,'conversation_id':dev['stage_session_id'],
                     'row_id':dev['row_id'],'operation_id':dev['operation_id']}
    assert ok(client.get(detail_ref,params=detail_params))['inputs'][0]['stage_session_id']==design['stage_session_id']
    def manual_action(current, op, status, observation, bench=None, attachments=None):
        child={**owner,'conversation_id':current['stages'][6]['stage_session_id']}
        return action(client,child,current,op,'record_manual_check',{
            'case_id':'TC-1','action_id':manual['id'],'status':status,
            'environment':'受控验收台，版本由本工作流冻结','observation':observation,
            **({'bench':bench} if bench is not None else {}),
            **({'attachments':attachments} if attachments is not None else {})})
    def manual_download(current, receipt, ident, conversation=None):
        return client.get(ROOT+'/runs/'+current['id']+'/stages/testing/manual-files/'+receipt['id']+'/'+ident,
                          params={**owner,'conversation_id':conversation or receipt['stage_session_id']})
    def rework_evidence(current, node_id, session, **query):
        return client.get(ROOT+'/runs/'+current['id']+'/stages/'+node_id+'/rework-evidence',
                          params={**owner,'conversation_id':session,**query})
    def attachment(ident, name, content):
        return {'id':ident,'name':name,'content_base64':b64encode(content).decode('ascii')}
    def bench_files(current, conversation=None, **query):
        scope={**owner,'conversation_id':conversation or current['stages'][6]['stage_session_id']}
        return client.get(ROOT+'/runs/'+current['id']+'/stages/testing/bench-firmware',params={**scope,**query})
    retake_query={'case_id':'TC-1','action_id':manual['id']}
    if with_observation:
        starting = run
        assert manual_action(starting,'missing-observation','passed',' ').status_code==422
        assert bench_files(starting,owner['conversation_id']).status_code==404
        assert bench_files(starting,case_id='TC-1').status_code==422
        assert 'previous_bench' not in ok(bench_files(starting,**retake_query))
        initial_firmware,=ok(bench_files(run))['files']
        assert initial_firmware['approval_sha256']==old_dev['approval_sha256']
        assert initial_firmware['stage_session_id']==old_dev['stage_session_id']
        bench={'host':'工装软件 v2','fixture':'IO 适配器 CH-A','devices':[
            {'label':'主控 Rev.A','identity':'DUT-001','link':'COM7 / 115200',
             'firmware_label':'现场 v0.1', 'firmware_id':initial_firmware['id']},
            {'label':'采集板 Rev.B','identity':'DUT-002'}]}
        assert manual_action(starting,'forged-firmware','failed','指示灯未亮',
            {'devices':[{'label':'主控 Rev.A','firmware_id':'outside_approval'}]}).status_code==422
        old_serial=b'COM7: no handshake\r\n\x00'
        original_files=[attachment('1'*32,'串口.log',old_serial),attachment('2'*32,'scope.csv',b't,v\n0,0\n')]
        for bad_files in ([original_files[0],attachment('3'*32,'../scope.csv',b'x')],
                          [original_files[0],attachment('4'*32,'large.log',bytes(1024**2))],
                          [original_files[0],original_files[0]]):
            assert manual_action(starting,'bad-attachments','failed','指示灯未亮',attachments=bad_files).status_code==422
        with client.app.state.store.connect() as db:
            assert db.execute('SELECT count(*) FROM stage_workflow_manual_files').fetchone()[0]==0
        run = ok(manual_action(starting,'original-manual','failed','上电后指示灯未亮',bench,original_files))
        assert ok(manual_action(starting,'original-manual','failed','上电后指示灯未亮',bench,original_files))==run
        changed=[attachment('1'*32,'串口.log',b'different bytes'),original_files[1]]
        assert manual_action(starting,'original-manual','failed','上电后指示灯未亮',bench,changed).status_code==409
        assert len(run['stages'][6]['verified_evidence'])==1
        first_manual=run['stages'][6]['verified_evidence'][0]
        assert first_manual['attachments'][0]=={'id':'1'*32,'name':'串口.log','bytes':len(old_serial),
                                                 'sha256':hashlib.sha256(old_serial).hexdigest()}
        downloaded=manual_download(run,first_manual,'1'*32)
        assert downloaded.status_code==200 and downloaded.content==old_serial
        assert '%E4%B8%B2%E5%8F%A3.log' in downloaded.headers['content-disposition']
        assert downloaded.headers['x-content-type-options']=='nosniff'
        assert manual_download(run,first_manual,'1'*32,owner['conversation_id']).status_code==404
        assert run['stages'][6]['verified_evidence'][0]['input_versions']['development']['approval_sha256']==old_dev['approval_sha256']
        first_bench=run['stages'][6]['verified_evidence'][0]['bench']
        assert first_bench['devices'][0]['firmware']==initial_firmware and first_bench['devices'][1]['identity']=='DUT-002'
        assert run['stages'][6]['artifact']['items'][0]['status']=='failed'
    run = ok(action(client,owner,run,'failed-case','execute'))
    old_test = deepcopy(run['stages'][6])
    assert old_test['artifact']['items'][0]['status']=='failed'
    assert overview(run)['OR-1']=={**expected,'testing':'failed'}
    test_session=old_test['stage_session_id']
    before_rework=ok(rework_evidence(run,'testing',test_session,revision=run['revision']))
    assert before_rework['stage_session_id']==test_session and before_rework['next_offset'] is None
    automatic=[row for row in before_rework['records'] if row['kind']=='check']
    assert len(automatic)==1 and automatic[0]['exit_code']==1 and automatic[0]['path']=='status.txt'
    assert rework_evidence(run,'testing',test_session,revision=run['revision']-1).status_code==409
    assert rework_evidence(run,'testing',owner['conversation_id'],revision=run['revision']).status_code==404
    if with_observation:
        selected,=[row for row in before_rework['records'] if row['kind']=='manual_check']
        assert selected['id']==first_manual['id'] and selected['attachments']==first_manual['attachments']
        assert selected['bench']==first_bench
    assert action(client,owner,run,'denied','approve').status_code==409
    evidence = ok(client.get(ROOT+'/runs/'+run['id']+'/stages/testing/attempts',params={**owner,'operation_id':'failed-case'}))
    assert evidence['verified_evidence'][0]['exit_code']==1
    run = ok(action(client,owner,run,'back','rework',{'target_node_id':'development','reason':'Correct file','case_ids':['TC-1']}))
    development_session=run['stages'][5]['stage_session_id']
    after_rework=ok(rework_evidence(run,'development',development_session))
    assert {row['id']:row for row in after_rework['records']}=={row['id']:row for row in before_rework['records']}
    assert after_rework['stage_session_id']==test_session
    assert rework_evidence(run,'development',old_dev['stage_session_id']).status_code==404
    assert run['stages'][3]==old_cases and run['active_node_id']=='development'
    assert run['stages'][5]['stage_session_id']!=old_dev['stage_session_id']
    assert implementation(run)=={**traced,'development':[]}
    assert overview(run)['OR-1']=={**expected,'development':'stale','testing':'stale'}
    assert ok(client.get(detail_ref,params=detail_params))['historical']
    old = ok(client.get(ROOT,params={**owner,'conversation_id':old_test['stage_session_id']}))
    assert old['historical_stage']==old_test and old['historical_node_id']=='testing'
    for kind in ['development','testing','delivery']:
        assert run['active_node_id']==kind
        payload = {}
        if kind=='development' and not with_observation:
            source = next(item for item in old_dev['verified_evidence'] if item.get('server')=='meter')
            stage = run['stages'][5]
            assert stage['connector_retry']['evidence'][0]['id']==source['id']
            assert stage['connector_retry']['evidence'][0]['previous']
            assert all(record['id']!=source['id'] for record in stage['verified_evidence'])
            assert action(client,owner,run,'unchecked-rework','execute').status_code==409
            payload['connector_retry_ack'] = {'operation_id':None,'evidence_ids':[source['id']]}
        if kind=='testing' and not with_observation:
            config = run['stages'][6]['config']['capabilities']
            run = ok(action(client,owner,run,'allow-diagnostic-write','configure',{'capabilities':{
                **config,'tools':[*config['tools'],'workspace_write']}}))
            change_test_input = True
        if kind=='delivery' and not with_observation:
            config = run['stages'][7]['config']['capabilities']
            run = ok(action(client,owner,run,'allow-delivery-write','configure',{'capabilities':{
                **config,'tools':[*config['tools'],'workspace_write']}}))
            run = ok(action(client,owner,run,'package-changed-member','execute'))
            response = action(client,owner,run,'reject-substitution','approve')
            assert response.status_code==409,response.text
            assert 'substituted.zip!/status.txt' in response.text
            assert run['stages'][7]['approval_issues']
        run = ok(action(client,owner,run,'fix-'+kind,'execute',payload))
        stage = next(s for s in run['stages'] if s['node_id']==kind)
        if kind=='testing':
            assert stage['stage_session_id']!=old_test['stage_session_id']
            assert stage['verified_evidence'][0]['exit_code']==0
            if not with_observation:
                response = action(client,owner,run,'stale-file-denied','approve')
                assert response.status_code==409,response.text
                assert '复测' in response.text
                assert stage['artifact']['items'][0]['status']=='passed'
                assert 'TC-1' in stage['test_input_issues']
                req = run['stages'][1]
                path = ROOT+'/runs/'+run['id']+'/stages/requirements/coverage'
                trace_scope = {**owner,'conversation_id':req['stage_session_id'],'selector':'or',
                               'row_id':req['artifact']['items'][0]['id']}
                coverage = ok(client.get(path,params=trace_scope))
                result, = coverage['requirements'][0]['cases'][0]['results']
                assert result['status']=='unverified'
                assert overview(run)['OR-1']['testing']=='unverified'
                evidence = ok(client.get(ROOT+'/runs/'+run['id']+'/stages/testing/attempts',
                                         params={**owner,'operation_id':'fix-testing'}))
                assert evidence['artifact']['items'][0]['status']=='passed'
                assert evidence['verified_evidence'][0]==stage['verified_evidence'][0]
                change_test_input = False
                run = ok(action(client,owner,run,'retest-current-file','execute'))
                stage = run['stages'][6]
                assert 'test_input_issues' not in stage
                coverage = ok(client.get(path,params=trace_scope))
                result, = coverage['requirements'][0]['cases'][0]['results']
                assert result['status']=='passed'
                assert overview(run)['OR-1']['testing']=='review'
            if with_observation:
                assert not any(r['kind']=='manual_check' for r in stage['verified_evidence'])
                assert stage['artifact']['items'][0]['status']=='not_run'
                assert action(client,owner,run,'requires-new-manual','approve').status_code==409
                assert bench_files(run,old_test['stage_session_id']).status_code==404
                assert manual_action(run,'stale-firmware','passed','上电后指示灯亮绿',bench).status_code==422
                bench_context=ok(bench_files(run,**retake_query))
                donor=bench_context['previous_bench']
                assert donor=={'status':'failed','at':old_test['verified_evidence'][0]['at'],'setup':{
                    'host':'工装软件 v2','fixture':'IO 适配器 CH-A','devices':[
                        {'label':'主控 Rev.A','identity':'DUT-001','link':'COM7 / 115200'},
                        {'label':'采集板 Rev.B','identity':'DUT-002'}]}}
                assert 'previous_bench' not in ok(bench_files(run,case_id='TC-1',action_id='different-action'))
                origin=old_test['stage_session_id']
                new_project=ok(client.post('/api/projects',json={'name':'复测来源权限验证'}))['id']
                def move_previous(target,op):
                    header=client.app.state.conversation_threads.metadata_header(origin)
                    return ok(client.post('/api/conversations/'+origin+'/organization',json={
                        'operation_id':op,'source':header['source'],'expected_revision':header['organization']['revision'],
                        'action':'move','target_project_id':target}))
                move_previous(new_project,'move-bench-origin')
                assert ok(bench_files(run,**retake_query))=={'files':bench_context['files']}
                assert manual_download(run,first_manual,'1'*32).status_code==404
                assert rework_evidence(run,'development',development_session).status_code==404
                move_previous(owner['project_id'],'restore-bench-origin')
                assert ok(bench_files(run,**retake_query))==bench_context
                assert manual_download(run,first_manual,'1'*32).content==old_serial
                assert ok(rework_evidence(run,'development',development_session))==after_rework
                legacy=deepcopy(run)
                next(entry for entry in reversed(legacy['stages'][6]['log']) if entry['event']=='rework').pop('case_ids')
                service=client.app.state.stage_workflows
                with service.store.connect() as db:
                    assert service._previous_test_bench(db,legacy,legacy['stages'][6],legacy['definition']['nodes'][6],
                                                       'TC-1',manual['id'])==donor
                new_firmware,=bench_context['files']
                assert new_firmware['path']==initial_firmware['path'] and new_firmware['sha256']!=initial_firmware['sha256']
                bench=deepcopy(donor['setup'])
                bench['devices'][0].update(firmware_id=new_firmware['id'],firmware_label='现场 v0.2')
                new_serial=b'COM7: handshake OK\r\n'
                run = ok(manual_action(run,'fixed-manual','passed','上电后指示灯亮绿',bench,
                                       [attachment('3'*32,'串口.log',new_serial),original_files[1]]))
                stage = run['stages'][6]
                assert stage['artifact']['items'][0]['status']=='passed'
                assert stage['verified_evidence'][-1]['stage_session_id']==stage['stage_session_id']
                assert stage['verified_evidence'][-1]['input_versions']['development']['approval_sha256']!=old_dev['approval_sha256']
                assert stage['verified_evidence'][-1]['bench']['devices'][0]['firmware']==new_firmware
                assert old_test['verified_evidence'][0]['bench']==first_bench
                second_manual=stage['verified_evidence'][-1]
                assert manual_download(run,second_manual,'3'*32).content==new_serial
                assert manual_download(run,first_manual,'1'*32,stage['stage_session_id']).status_code==404
                assert ok(rework_evidence(run,'development',development_session))==after_rework
                with client.app.state.store.connect() as db:
                    assert db.execute('SELECT count(*) FROM stage_workflow_manual_files').fetchone()[0]==3
        assert not stage['approval_issues']
        run = ok(action(client,owner,run,'confirm-'+kind,'approve'))
    assert run['status']=='completed'
    assert overview(run)=={'OR-1':{**expected,'testing':'covered'},'OR-2':{**expected,'testing':'covered'}}
    file = next(r for r in reversed(run['stages'][5]['verified_evidence']) if r['kind']=='file')
    response = client.get(ROOT+'/runs/'+run['id']+'/files/'+file['id'],params=owner)
    assert response.status_code==200 and response.content==b'fixed'
    from io import BytesIO
    from zipfile import ZipFile
    zip_receipt = next(r for r in run['stages'][7]['verified_evidence'] if r['path']=='delivery.zip')
    download = client.get(ROOT+'/runs/'+run['id']+'/files/'+zip_receipt['id'],params=owner)
    assert download.status_code==200
    with ZipFile(BytesIO(download.content)) as archive:
        assert archive.namelist()==['status.txt'] and archive.read('status.txt')==b'fixed'
    with client.app.state.store.connect() as db:
        import json
        approved_delivery=json.loads(db.execute('SELECT body FROM stage_workflow_approvals WHERE run_id=? '
            'AND node_id=?',(run['id'],'delivery')).fetchone()[0])
        assert {r['path'] for r in approved_delivery['verified_evidence']}=={'delivery.zip','docs/校验数据.bin'}
    old_file = next(r for r in old_dev['verified_evidence'] if r['kind']=='file')
    old_response = client.get(ROOT+'/runs/'+run['id']+'/files/'+old_file['id'],params=owner)
    assert old_response.status_code == 200 and old_response.content == b'wrong'
    assert client.get(ROOT+'/runs/'+run['id']+'/files/unknown',params=owner).status_code==404
    other = scope(client,'Other')
    assert client.get(ROOT+'/runs/'+run['id']+'/files/'+file['id'],params=other).status_code==404
    delivery = run['stages'][7]
    endpoint = ROOT+'/runs/'+run['id']+'/stages/delivery'
    receipt_url = endpoint+'/delivery-receipt'
    delivery_query = {**owner,'conversation_id':delivery['stage_session_id'],
                      'approval_sha256':delivery['approval_sha256']}
    response = client.get(receipt_url,params=delivery_query)
    receipt = ok(response)
    assert receipt['delivery_approval_sha256']==delivery['approval_sha256']
    assert receipt['definition']=={'id':'default','revision':run['definition']['revision']}
    assert receipt['files']==[{'item_id':delivery['artifact']['items'][0]['id'],'path':'delivery.zip',
        'sha256':hashlib.sha256(download.content).hexdigest(),'bytes':len(download.content),
        'members':[{'path':'status.txt','sha256':file['sha256'],'bytes':5}]},
        {'item_id':'PKG-2','path':'docs/校验数据.bin','sha256':hashlib.sha256(supplement).hexdigest(),'bytes':len(supplement)}]
    bundle_url=endpoint+'/delivery-bundle'
    handoff=client.get(bundle_url,params=delivery_query)
    assert handoff.status_code==200,handoff.text
    assert handoff.headers['x-cirven-delivery-approval']==delivery['approval_sha256']
    assert handoff.headers['cache-control']=='no-store' and int(handoff.headers['content-length'])==len(handoff.content)
    with ZipFile(BytesIO(handoff.content)) as archive:
        assert archive.namelist()==['files/delivery.zip','files/docs/校验数据.bin','receipt.json']
        assert archive.read('receipt.json')==response.content
        assert archive.read('files/delivery.zip')==download.content
        assert archive.read('files/docs/校验数据.bin')==supplement
        assert archive.testzip() is None
    assert client.get(bundle_url,params={**delivery_query,**other}).status_code==404
    assert client.get(bundle_url,params={**delivery_query,'approval_sha256':'0'*64}).status_code==404
    ref_url=endpoint+'/delivery-bundle-reference'
    reference=ok(client.get(ref_url,params=delivery_query))
    block=1024**2
    assert reference=={'run_id':run['id'],'approval_sha256':delivery['approval_sha256'],
        'bytes':len(handoff.content),'chunk_bytes':block,
        'sha256_chunks':[hashlib.sha256(handoff.content[offset:offset+block]).hexdigest()
                         for offset in range(0,len(handoff.content),block)]}
    assert client.get(ref_url,params={**delivery_query,**other}).status_code==404
    assert client.get(ref_url,params={**delivery_query,'approval_sha256':'0'*64}).status_code==404
    file_query = {**delivery_query,'item_id':receipt['files'][0]['item_id']}
    assert client.get(endpoint+'/delivery-file',params=file_query).content==download.content
    versions = {v['node_id']:v for v in receipt['approvals']}
    assert len(versions)==8 and versions['delivery']['input_binding']=='pinned'
    assert versions['delivery']['input_approvals']==[versions[k]['approval_sha256']
        for k in run['definition']['nodes'][7]['inputs']]
    assert versions['testing']['input_approvals']==[versions[k]['approval_sha256']
        for k in run['definition']['nodes'][6]['inputs']]
    assert versions['development']['stage_session_id']!=old_dev['stage_session_id']
    assert versions['testing']['stage_session_id']!=old_test['stage_session_id']
    assert {r['case_id'] for r in receipt['test_results']}=={'TC-1'}
    assert {check['source'] for result in receipt['test_results'] for check in result['checks']}==(
        {'automated_check','manual_observation'} if with_observation else {'automated_check'})
    assert '上电后指示灯亮绿'.encode() not in response.content
    assert b'"environment"' not in response.content and b'"observation"' not in response.content
    assert client.get(receipt_url,params=delivery_query).content==response.content
    assert client.get(receipt_url,params={**delivery_query,'approval_sha256':'0'*64}).status_code==404
    assert client.get(receipt_url,params={**delivery_query,**other}).status_code==404
    assert client.get(receipt_url,params={**delivery_query,'conversation_id':run['stages'][6]['stage_session_id']}).status_code==404
    assert client.get(endpoint+'/delivery-file',params={**file_query,'item_id':'missing'}).status_code==404
    assert ok(client.get(ROOT+'/runs/'+run['id'],params=owner))==run

    requirements = {**owner,'conversation_id':run['stages'][1]['stage_session_id']}
    amended = ok(action(client,requirements,run,'amend-after-delivery','revise',{'text':'添加下一版本字段'},
                        node_id='requirements'))
    assert amended['active_node_id']=='requirements' and amended['stages'][7]['status']=='pending'
    assert client.get(receipt_url,params=delivery_query).content==response.content
    assert client.get(endpoint+'/delivery-file',params=file_query).content==download.content
    assert client.get(bundle_url,params=delivery_query).content==handoff.content
    assert ok(client.get(ref_url,params=delivery_query))==reference
    with workspace.ledger(client.app.state.settings.data_dir) as db:
        db.execute('UPDATE blobs SET content=? WHERE sha=?',(b'damaged supplement',hashlib.sha256(supplement).hexdigest()))
    blocked=client.get(bundle_url,params=delivery_query)
    assert blocked.status_code==409 and 'content-disposition' not in blocked.headers
    assert client.get(ref_url,params=delivery_query).status_code==409
    with workspace.ledger(client.app.state.settings.data_dir) as db:
        db.execute('UPDATE blobs SET content=? WHERE sha=?',(b'damaged copy',zip_receipt['sha256']))
    assert client.get(receipt_url,params=delivery_query).status_code==409
    assert client.get(endpoint+'/delivery-file',params=file_query).status_code==404



def test_file_download_uses_original_writer_scope_after_organization_changes(client):
    from packages.dynamic_workflow.stage_workspace import file_write, operation_receipts

    owner = scope(client)
    outside = scope(client, 'Other project')
    tester = client.app.state.stage_workflows
    define(client, owner, [node('writer'), node('reader', ['writer'])])
    run = start(client, owner)
    origin = run['stages'][0]['stage_session_id']
    expected_sha = ''

    def write_version(ctx):
        nonlocal expected_sha
        record = file_write(ctx, 'src/report.txt', ctx['operation_id'], expected_sha)
        expected_sha = record['sha256']
        return {'artifact':sample('version'), 'verified_evidence':operation_receipts(ctx)}

    tester.executor = write_version
    previous = ok(action(client, owner, run, 'original-file', 'execute'))
    latest = ok(action(client, owner, previous, 'revised-file', 'execute'))
    approved = ok(action(client, owner, latest, 'approved-files', 'approve'))
    observer = {**owner, 'conversation_id':approved['stages'][1]['stage_session_id']}
    old, new = approved['stages'][0]['verified_evidence']
    assert old['path'] == new['path'] and old['sha256'] != new['sha256']
    path = ROOT+'/runs/'+run['id']+'/files/'
    writer = {**owner, 'conversation_id':origin}

    def read(record, reference):
        return client.get(path+record['id'], params=reference)

    def organize(verb, operation, **fields):
        header = client.app.state.conversation_threads.metadata_header(origin)
        return ok(client.post('/api/conversations/'+origin+'/organization', json={
            'action':verb, 'operation_id':operation, 'source':header['source'],
            'expected_revision':header['organization']['revision'], **fields}))

    for scope_ref in (owner, writer, observer):
        assert read(old, scope_ref).content == b'original-file'
        assert read(new, scope_ref).content == b'revised-file'
    organize('archive', 'archive-file-writer')
    assert read(old, observer).content == b'original-file'
    organize('restore', 'restore-file-writer')
    organize('move', 'move-file-writer', target_project_id=outside['project_id'])
    for scope_ref in (owner, writer, observer, {**outside,'conversation_id':origin}):
        for record in (old, new):
            response = read(record, scope_ref)
            assert response.status_code == 404 and 'content-disposition' not in response.headers
    organize('move', 'return-file-writer', target_project_id=owner['project_id'])
    returned = read(old, observer)
    assert returned.content == b'original-file' and returned.headers['cache-control'] == 'no-store'
    organize('delete', 'delete-file-writer', delete_scope='CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES')
    assert all(read(record, scope_ref).status_code == 404
               for record in (old, new) for scope_ref in (owner, observer))
    assert ok(client.get(ROOT+'/runs/'+run['id'],params=owner)) == approved

def test_stale_design_and_unresolved_distinct_test_cannot_pass():
    one={'id':'f1','kind':'file','path':'data.txt','sha256':'a'*64}
    two={**one,'id':'f2','sha256':'b'*64}
    close={'id':'done1','kind':'design_item','design_id':'D1','file_refs':['f1']}
    artifact={'items':[{'id':'A','design_ids':['D1'],'evidence_refs':['done1']}]}
    assert not StageRuns._evidence_issues('development',artifact,[one,close])
    assert StageRuns._evidence_issues('development',artifact,[one,close,two])
    assert StageRuns._evidence_issues('development',artifact,[one])
    test={'items':[{'id':'T','case_id':'C1','evidence_refs':['ok']}]}
    bad={'id':'bad','kind':'check','case_id':'C1','method':'mcp_response','pointer':'/power','status':'failed','exit_code':1}
    passed={**bad,'pointer':'/clock','id':'ok','exit_code':0,'status':'passed'}
    assert StageRuns._evidence_issues('testing',test,[bad,passed])
    fixed={**bad,'id':'fixed','status':'passed','exit_code':0}
    test['items'][0]['evidence_refs'].append('fixed')
    assert not StageRuns._evidence_issues('testing',test,[bad,passed,fixed])


def test_interrupted_dispatch_requires_explicit_recovery(client, monkeypatch):
    from packages.dynamic_workflow.stage_workspace import file_write

    owner=scope(client)
    define(client,owner,[node('A')])
    run=start(client,owner)
    store=client.app.state.stage_workflows
    class Interruption(BaseException):
        pass
    def interrupted(context):
        file_write(context,'src/recovery.txt','already saved','')
        raise Interruption()
    store.executor=interrupted
    args=(owner['project_id'],owner['conversation_id'],run['id'],'pending',run['revision'],'A','execute',{})
    with pytest.raises(Interruption):
        store.action(*args)
    reopened=StageRuns(store.store,store.data_dir,store.create_child,interrupted)
    pending=reopened.action(*args)
    assert pending['stages'][0]['status']=='running'
    child = {**owner,'conversation_id':pending['stages'][0]['stage_session_id']}
    url = ROOT+'/runs/'+run['id']
    assert ok(client.get(url,params=child))['stages'][0]['execution_state']=='running'
    monkeypatch.setattr('packages.dynamic_workflow.stage_runs._alive',lambda owner: False)
    detected = ok(client.get(url,params=child))
    assert detected['revision']==pending['revision']
    assert detected['stages'][0]['execution_state']=='interrupted'
    assert reopened.action(*args)==pending
    recovered=reopened.action(*args[:3],'resolve',pending['revision'],'A','recover',{})
    assert recovered['stages'][0]['status']=='failed'
    assert reopened.action(*args)==recovered
    record_path = url+'/stages/A/execution-record'
    response = ok(client.get(record_path,params={**child,'operation_id':'pending'}))
    assert response['status']=='interrupted_unknown'
    assert response['kernel']=='opencode'
    assert recovered['stages'][0]['execution_origin']=={
        'operation_id':'pending','at':response['at'],'kernel':'opencode'}
    evidence, = response['evidence']
    assert evidence['kind']=='file' and evidence['path']=='src/recovery.txt'
    download = client.get(url+'/files/'+evidence['id'],params=child)
    assert download.status_code==200 and download.content==b'already saved'
    assert client.get(record_path,params={**owner,'operation_id':'pending'}).status_code==404
    assert client.get(record_path,params={**child,'operation_id':'resolve'}).status_code==404


def test_only_matching_approved_pure_plan_in_this_conversation_can_be_reused(client,tmp_path,monkeypatch):
    from packages.dynamic_workflow import kernel_settings as settings
    from test_engineering_stage_contracts import APPROVED
    from packages.dynamic_workflow.stage_runs import planning_key

    owner=scope(client)
    store=client.app.state.stage_workflows
    binary=tmp_path/'synthetic-kernel.bin'
    binary.write_bytes(b'kernel	version 1')
    monkeypatch.setattr(settings,'bundled',lambda engine: binary)
    route={'endpoint':'http://127.0.0.1:9000','model':'qa','credential_env':'','allow_http':False}
    settings.save_route(store.data_dir,'opencode',0,route)
    calls=[]
    def execute(context):
        calls.append(context)
        return {'artifact':APPROVED['init']}
    store.executor=execute
    form={topic:'Example '+topic for topic in ('background','development_constraints','build_environment','runtime_environment')}
    def begin(owner,op):
        return start(client,owner,'default','start-'+op)
    def send(owner,run,op,values=form):
        return ok(action(client,owner,run,'exec-'+op,'execute',{'user_input':values}))
    first=send(owner,begin(owner,'one'),'one')
    before=send(owner,begin(owner,'two'),'two')
    assert not before['stages'][0]['reused_from'] and len(calls)==2
    approved=ok(action(client,owner,first,'accept-first','approve'))
    third=send(owner,begin(owner,'three'),'three')
    third_stage=third['stages'][0]
    assert len(calls)==2 and third_stage['status']=='review'
    assert third_stage['stage_session_id']!=approved['stages'][0]['stage_session_id']
    assert third_stage['reused_from']['approval_sha256']==approved['stages'][0]['approval_sha256']
    assert third_stage['artifact']==first['stages'][0]['artifact']
    source_path=f"{ROOT}/runs/{third['id']}/stages/init/planning-source"
    source_scope={**owner,'conversation_id':third_stage['stage_session_id'],'operation_id':third_stage['operation_id']}
    source=ok(client.get(source_path,params=source_scope))
    assert source['artifact']==third_stage['artifact'] and not source['historical']
    assert all(source[key]==value for key,value in third_stage['reused_from'].items())
    assert source['approved_at'] and source['stage_kind']=='init'
    assert client.get(source_path,params={**source_scope,'operation_id':'wrong-operation'}).status_code==409
    assert client.get(source_path,params={**source_scope,'conversation_id':owner['conversation_id']}).status_code==404
    assert client.get(source_path,params={**source_scope,'conversation_id':approved['stages'][0]['stage_session_id']}).status_code==404
    changed=send(owner,begin(owner,'changed'),'changed',{**form,'background':'different'})
    assert not changed['stages'][0]['reused_from'] and len(calls)==3
    second_conversation={**owner,'conversation_id':ok(client.post('/api/conversations',json={
        'project_id':owner['project_id'],'operation_id':'other-thread'}))['conversation_ref']['id']}
    other=send(second_conversation,begin(second_conversation,'other'),'other')
    assert not other['stages'][0]['reused_from'] and len(calls)==4
    original_context=calls[0]
    assert planning_key(original_context)
    for change in ({'kind':'testing'},{'kind':'custom'},{'kind':'development'},{'kind':'delivery'},
                   {'capabilities':{'tools':['workspace_read'],'skills':[],'mcp':[]}},
                   {'capabilities':{'tools':[],'skills':[],'mcp':['device']}},
                   {'prior_artifact':APPROVED['init']},{'annotations':[{'id':'ann1'}]}):
        assert planning_key({**original_context,**change}) is None
    # Archiving the source conversation revokes it as a reuse source.
    with store.store.connect() as db:
        db.execute('UPDATE conversation_organization SET archived=1 WHERE conversation_id=?',
                   (approved['stages'][0]['stage_session_id'],))
    archived=send(owner,begin(owner,'archived'),'archived')
    assert not archived['stages'][0]['reused_from'] and len(calls)==5
    with store.store.connect() as db:
        db.execute('UPDATE conversation_organization SET archived=0 WHERE conversation_id=?',
                   (approved['stages'][0]['stage_session_id'],))
    source_child={**owner,'conversation_id':approved['stages'][0]['stage_session_id']}
    revised=ok(action(client,source_child,approved,'revise-source','revise',{'text':'为下一版调整说明'},node_id='init'))
    assert revised['stages'][0]['stage_session_id']!=source_child['conversation_id']
    assert ok(client.get(source_path,params=source_scope))=={**source,'historical':True}

    replay=send(owner,begin(owner,'historical-exact'),'historical-exact')
    replay_stage=replay['stages'][0]
    assert len(calls)==5
    assert replay_stage['status']=='review' and replay_stage['artifact']==source['artifact']
    assert replay_stage['stage_session_id'] not in (source_child['conversation_id'],third_stage['stage_session_id'])
    assert replay_stage['reused_from']==third_stage['reused_from']
    replay_path=f"{ROOT}/runs/{replay['id']}/stages/init/planning-source"
    replay_scope={**owner,'conversation_id':replay_stage['stage_session_id'],'operation_id':replay_stage['operation_id']}
    assert ok(client.get(replay_path,params=replay_scope))=={
        **source,'historical':True,'operation_id':replay_stage['operation_id']}
    assert ok(action(client,owner,replay,'approve-historical-exact','approve'))['stages'][0]['status']=='approved'

    with store.store.connect() as db:
        db.execute('UPDATE conversation_organization SET archived=1 WHERE conversation_id=?',
                   (source_child['conversation_id'],))
    historical_archived=send(owner,begin(owner,'historical-archived'),'historical-archived')
    assert not historical_archived['stages'][0]['reused_from'] and len(calls)==6
    with store.store.connect() as db:
        db.execute('UPDATE conversation_organization SET archived=0 WHERE conversation_id=?',
                   (source_child['conversation_id'],))
    renamed=begin(owner,'update')
    binary.write_bytes(b'kernel version 2')
    assert not send(owner,renamed,'update')['stages'][0]['reused_from'] and len(calls)==7
    settings.save_route(store.data_dir,'opencode',1,{**route,'model':'qa2'})
    assert planning_key(original_context) is None
    binary.write_bytes(b'kernel	version 1')
    assert not send(owner,begin(owner,'route'),'route')['stages'][0]['reused_from']
    other_project=scope(client,'Other project')
    assert client.get(source_path,params={**source_scope,'project_id':other_project['project_id']}).status_code==404
    assert client.get(source_path.replace('/stages/init/','/stages/design/'),params=source_scope).status_code==404


def test_rework_preview_matches_only_the_approved_dependency_branch(client):
    owner = scope(client,'Parallel development')
    define(client,owner,[
        {**node('dev-a'),'kind':'development','name':'Develop reports'},
        {**node('dev-b'),'kind':'development','name':'Independent exporter'},
        node('bridge',['dev-a']),
        {**node('test',['bridge']),'kind':'testing','name':'Report checks'},
    ])
    run=start(client,owner)
    service=client.app.state.stage_workflows
    # Freeze a reviewed fixture with real scoped sessions and a blocked manual
    # check. No model or external tool is needed to exercise the branch move.
    with service.store.connect() as db:
        for index, stage in enumerate(run['stages']):
            service._activate(db,run,index)
            if index < 3:
                stage.update(status='approved',artifact={'summary':'Approved fixture','items':[]},
                             approval_sha256=f'{index+1:064x}')
            else:
                stage.update(status='review',artifact={'summary':'Held','items':[]},
                    verified_evidence=[{'id':f'manual-{number}','case_id':'TC-M','action_id':f'observe-{number}',
                                        'action_sha256':f'{number:064x}','kind':'manual_check','status':'blocked'}
                                       for number in range(1,14)])
        service._save(db,run)
    source={**owner,'conversation_id':run['stages'][3]['stage_session_id']}
    path=f"{ROOT}/runs/{run['id']}/stages/test/rework-preview"
    basis=ok(client.get(path,params=source))
    assert basis=={'revision':run['revision'],'case_ids':['TC-M'],'targets':[{'id':'dev-a','name':'Develop reports',
        'affected':[{'id':'dev-a','name':'Develop reports'},{'id':'bridge','name':'审核 bridge'},
                    {'id':'test','name':'Report checks'}]}]}
    assert client.get(path,params=owner).status_code==404
    detail=path.replace('rework-preview','rework-evidence')
    first=ok(client.get(detail,params={**source,'revision':run['revision']}))
    assert first['total']==13 and first['next_offset']==12 and len(first['records'])==12
    tail=ok(client.get(detail,params={**source,'revision':run['revision'],'offset':12}))
    assert tail['next_offset'] is None and [row['id'] for row in tail['records']]==['manual-13']
    assert ok(client.get(ROOT+'/runs/'+run['id'],params=owner))==run
    payload={'target_node_id':'dev-a','reason':'Headless fixture cannot reach the indicator','case_ids':['TC-M']}
    assert action(client,source,run,'wrong-branch','rework',{**payload,'target_node_id':'dev-b'}).status_code==409
    assert action(client,source,run,'wrong-case','rework',{**payload,'case_ids':['TC-X']}).status_code==409
    noted=ok(action(client,source,run,'scope-note','note',{'text':'Manual observation pending'}))
    assert action(client,source,run,'stale-rework','rework',payload).status_code==409
    assert ok(client.get(path,params=source))=={**basis,'revision':noted['revision']}
    moved=ok(action(client,source,noted,'matched-rework','rework',payload))
    assert moved['active_node_id']=='dev-a' and moved['stages'][1]==run['stages'][1]
    request=moved['stages'][0]['rework_request']
    assert request['reason']==payload['reason'] and request['case_ids']==['TC-M'] and request['at']
    assert request['from_session_id']==source['conversation_id'] and request['observations'][0]['status']=='blocked'
    dev_detail=detail.replace('/test/','/dev-a/')
    dev_scope={**owner,'conversation_id':moved['stages'][0]['stage_session_id']}
    assert ok(client.get(dev_detail,params=dev_scope))==first
    assert ok(client.get(dev_detail,params={**dev_scope,'offset':12}))==tail
    receipt=ok(client.get(ROOT+'/transition-receipts/matched-rework',params=source))
    assert receipt['kind']=='rework' and receipt['source_stage_session_id']==source['conversation_id']
    assert receipt['target']=={'node_id':'dev-a','stage_session_id':moved['stages'][0]['stage_session_id'],
                              'stage_name':'Develop reports'} and receipt['target_current']
    assert client.get(path,params=source).status_code==409


def test_transition_receipts_are_scoped_and_keep_the_original_target(client):
    owner=scope(client,'Durable transitions')
    service=client.app.state.stage_workflows
    service.executor=lambda _: {'artifact':sample('Verified fixture')}
    define(client,owner,[node('A'),node('B',['A'])])
    def read(op,scope=owner):
        return client.get(ROOT+'/transition-receipts/'+op,params=scope)
    assert read('original-start').status_code==404
    run=start(client,owner,op='original-start')
    source=run['stages'][0]['stage_session_id']
    beginning=ok(read('original-start'))
    assert beginning['kind']=='start' and beginning['source_node_id'] is None
    assert beginning['target']['stage_session_id']==source and beginning['target_current'] and not beginning['target_historical']
    child={**owner,'conversation_id':source}
    assert read('original-start',child).status_code==404
    outsider={**owner,'conversation_id':ok(client.post('/api/conversations',json={
        'project_id':owner['project_id'],'operation_id':'independent-parent'}))['conversation_ref']['id']}
    assert read('original-start',outsider).status_code==404
    run=ok(action(client,owner,run,'execute-a','execute'))
    assert read('execute-a').status_code==404
    failed=action(client,owner,run,'stale-confirm','approve',expected_revision=run['revision']-1)
    assert failed.status_code==409 and read('stale-confirm').status_code==404
    approved=ok(action(client,owner,run,'approve-a','approve'))
    original=ok(read('approve-a'))
    assert original['source_stage_session_id']==source and original['run_revision']==approved['revision']
    assert original['target']['stage_session_id']==approved['stages'][1]['stage_session_id'] and original['target_current']
    revised=ok(action(client,child,approved,'revise-a','revise',{'text':'Update the basis'},node_id='A'))
    revised_a=ok(read('revise-a',child))
    assert read('revise-a').status_code==404
    assert revised_a['source_stage_session_id']==source and revised_a['target']['stage_session_id']==revised['stages'][0]['stage_session_id']
    assert ok(read('original-start'))=={**beginning,'target_current':False,'target_historical':True}
    assert ok(read('approve-a'))=={**original,'target_current':False,'target_historical':True}
    destination={**owner,'conversation_id':original['target']['stage_session_id']}
    assert ok(client.get(ROOT,params=destination))['historical_stage']['stage_session_id']==destination['conversation_id']
    assert start(client,owner,op='original-start')==revised
    run=ok(action(client,owner,revised,'execute-a-again','execute'))
    run=ok(action(client,owner,run,'approve-a-again','approve'))
    run=ok(action(client,owner,run,'execute-b','execute'))
    completed=ok(action(client,owner,run,'approve-b','approve'))
    final=ok(read('approve-b'))
    assert final['target'] is None and final['target_current'] and not final['target_historical'] and completed['status']=='completed'
    assert ok(read('approve-b'))==final
    assert ok(client.get(ROOT+'/runs/'+run['id'],params=owner))==completed
    with service.store.connect() as db:
        db.execute("UPDATE conversation_threads SET deleted_at='2026-11-26T15:00:00Z' WHERE conversation_id=?",
                   (destination['conversation_id'],))
    assert client.get(ROOT,params=destination).status_code==404
    assert ok(read('approve-a'))=={**original,'target_current':False,'target_historical':False}


def test_company_routing_changes_new_default_preserving_existing_snapshot(client):
    from packages.dynamic_workflow.kernel_settings import set_company
    owner = scope(client)
    service = client.app.state.stage_workflows
    normal = start(client,owner,'default','normal')
    assert {n['kernel'] for n in normal['definition']['nodes']}=={'opencode'}
    set_company(service.data_dir,0,True)
    current = ok(client.get(ROOT,params=owner))['definitions'][0]
    assert {n['kernel'] for n in current['nodes']}=={'claude-code'}
    assert client.post(ROOT+'/runs',json={**owner,'definition_id':'default','operation_id':'stale-default',
                      'expected_definition_revision':normal['definition']['revision']}).status_code==409
    company = start(client,owner,'default','company')
    assert {s['config']['kernel'] for s in company['stages']}=={'claude-code'}
    assert ok(client.get(ROOT+'/runs/'+normal['id'],params=owner))['definition']==normal['definition']


def test_exact_build_recipe_and_latest_outputs_required():
    from packages.domain.tasks import digest
    from packages.dynamic_workflow.stage_assertions import count_complete_design_items
    recipe={'image_id':'sha256:'+'a'*64,'argv':['make','test'],'timeout_seconds':80,'outputs':['out/test.bin']}
    approved={'design':{'items':[{'id':'D1','build':recipe}]}}
    file={'id':'f','kind':'file','path':'out/test.bin','sha256':'e'*64}
    close={'id':'d','kind':'design_item','design_id':'D1','file_refs':['f']}
    build={'id':'b','kind':'build','design_id':'D1','status':'passed','exit_code':0,'descriptor_sha256':digest(recipe),
           'image_id':recipe['image_id'],'argv_sha256':digest(recipe['argv']),'file_refs':['f'],
           'source_manifest_sha256':digest([]),'source_files':[],
           'outputs':[{k:file[k] for k in ['id','path','sha256']}]}
    assert not StageRuns._build_issues(approved,[file,close,build])
    designs=approved['design']['items']
    assert count_complete_design_items(designs,[file,close,build])==1
    for mutation in [{'descriptor_sha256':'0'*64},{'image_id':'sha256:'+'0'*64},{'argv_sha256':'1'*64},
                     {'outputs':[]},{'file_refs':[]},{'exit_code':1},{'status':'failed'}]:
        assert StageRuns._build_issues(approved,[file,close,build,{**build,**mutation}])
        assert count_complete_design_items(designs,[file,close,build,{**build,**mutation}])==0
    assert StageRuns._build_issues(approved,[file,{**close,'file_refs':['unrelated']},build])
    assert StageRuns._build_issues(approved,[file,close])
    changed_source = [['src/data.txt','1'*64]]
    assert StageRuns._build_issues(approved,[file,close,{**build,'source_files':changed_source,
                        'source_manifest_sha256':digest(changed_source)}])
    compiled = {**build,'source_files':changed_source,'source_manifest_sha256':digest(changed_source)}
    upstream = {'id':'source','kind':'file','path':'src/data.txt','sha256':'1'*64}
    assert count_complete_design_items(designs,[file,close,compiled],[upstream])==1
    changed = {**upstream,'id':'altered','path':'src/DATA.txt','sha256':'2'*64}
    assert StageRuns._build_issues(approved,[file,close,compiled,changed],[upstream])
    assert count_complete_design_items(designs,[file,close,compiled,changed],[upstream])==0


def test_workflow_follow_up_during_execution_reopens_review_without_replaying(client):
    owner = scope(client)
    define(client,owner,[node('A'),node('B',['A'])])
    start_run = start(client,owner)
    child = {**owner,'conversation_id':start_run['stages'][0]['stage_session_id']}
    entered,release = Event(),Event()
    contexts = []

    def execute(context):
        contexts.append(context['user_input'])
        if len(contexts)==1:
            entered.set()
            assert release.wait(15)
        return {'artifact':sample('ready')}

    client.app.state.stage_workflows.executor=execute
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(action,client,owner,start_run,'busy-run','execute')
        try:
            assert entered.wait(15)
            running = ok(client.get(ROOT+'/runs/'+start_run['id'],params=child))
            assert running['stages'][0]['status']=='running'
            queued = ok(action(client,child,start_run,'queue-1','supplement',{'text':'新要求'}))
            assert queued['stages'][0]['follow_ups'][0]['text']=='新要求'
            assert ok(action(client,child,queued,'queue-1','supplement',{'text':'新要求'}))==queued
            assert action(client,owner,queued,'wrong-authority','supplement',{'text':'拒绝'}).status_code==409
        finally:
            release.set()
        reviewed = ok(future.result(timeout=15))
    assert contexts==[{}]
    assert action(client,owner,reviewed,'skip-queue','approve').status_code==409
    withdrawn = ok(action(client,child,reviewed,'queue-2','supplement',{'text':'已过时'}))
    withdrawn = ok(action(client,owner,withdrawn,'withdraw-2','withdraw_supplement',{'id':'queue-2'}))
    reviewed = ok(action(client,owner,withdrawn,'updated-run','execute'))
    assert contexts[-1]=={'stage_follow_ups':['新要求']}
    first,second=reviewed['stages'][0]['follow_ups']
    assert first['processed_by']=='updated-run' and second['withdrawn_at']
    next_stage = ok(action(client,owner,reviewed,'approve-updated','approve'))
    next_stage = ok(action(client,owner,next_stage,'run-b','execute'))
    assert contexts[-1]=={}
    assert next_stage['stages'][1]['status']=='review'


def test_revise_approved_stage_preserves_historical_approvals_and_independent_stage(client):
    owner = scope(client)
    define(client,owner,[node('A'),node('Independent'),node('B',['A']),node('C',['B'])])
    run = start(client,owner)
    store = client.app.state.stage_workflows
    old_a = sample('A')
    stable = {'id':'stable','title':'持续约束','description':'一直保留'}
    old_a['items'].append(stable)
    store.executor=lambda context: {'artifact':old_a if context['node_id']=='A' else sample(context['node_id'])}
    for name in ['A','Independent','B','C']:
        run = ok(action(client,owner,run,'run-'+name,'execute'))
        run = ok(action(client,owner,run,'approve-'+name,'approve'))

    original = run['stages']
    child = {**owner,'conversation_id':original[0]['stage_session_id']}
    revised = ok(action(client,child,run,'revise-a','revise',{'text':'调整范围'},node_id='A'))
    assert revised['active_node_id']=='A'
    assert [s['status'] for s in revised['stages']]==['ready','approved','pending','pending']
    assert revised['stages'][1]==original[1]
    assert revised['stages'][0]['stage_session_id']!=child['conversation_id']
    assert revised['stages'][0]['artifact']==original[0]['artifact']
    assert revised['stages'][0]['follow_ups'][0]['text']=='调整范围'
    for index in [0,2,3]:
        historical = ok(client.get(ROOT,params={**owner,'conversation_id':original[index]['stage_session_id']}))
        assert historical['historical_stage']==original[index]
    assert ok(action(client,child,revised,'revise-a','revise',{'text':'调整范围'},node_id='A'))==revised
    assert action(client,child,revised,'stale-a','supplement',{'text':'旧会话'},node_id='A').status_code==409
    head_scope={**owner,'conversation_id':revised['stages'][0]['stage_session_id']}
    comparison=ROOT+'/runs/'+run['id']+'/stages/A/revision-difference'
    def difference(version,caller=head_scope,**params):
        return client.get(comparison,params={**caller,'revision':version,**params})
    assert difference(revised['revision']).status_code==409
    next_rows=[{'id':f'new-{i:02}','title':f'新增条目 {i}','description':'新增'} for i in range(31)]
    store.executor=lambda _: {'artifact':{'summary':'更新后的审核范围','items':[
        stable,{**old_a['items'][0],'description':'只核对新范围'},*next_rows]}}
    older_revision=revised['revision']
    revised = ok(action(client,owner,revised,'new-a','execute'))
    version=revised['revision']
    assert difference(older_revision).status_code==409
    assert difference(version,caller=owner).status_code==404
    assert difference(version,caller=child).status_code==404
    assert difference(version,caller={**head_scope,'project_id':scope(client,'Foreign comparison')['project_id']}).status_code==404
    first=ok(difference(version))
    assert first['previous_status']=='approved' and first['current_status']=='review'
    assert first['counts']=={'added':31,'modified':1,'removed':1} and first['artifact_changes']==1 and first['reordered']
    assert first['rows'][0]=={'id':'R1','title':'范围','change':'modified'} and first['next_offset']==30
    last=ok(difference(version,offset=30))
    assert last['next_offset'] is None and [row['id'] for row in last['rows']]==['new-29','new-30','R2']
    assert ok(difference(version,group='row',row_id='R1'))=={'fields':[
        {'field':'description','before':{'present':True,'value':'A'},'after':{'present':True,'value':'只核对新范围'}}]}
    assert ok(difference(version,group='artifact'))=={'fields':[
        {'field':'summary','before':{'present':True,'value':'A'},'after':{'present':True,'value':'更新后的审核范围'}}]}
    assert all(not field['after']['present'] for field in ok(difference(version,group='row',row_id='R2'))['fields'])
    assert difference(version,group='row',row_id='missing').status_code==404
    assert difference(version,group='row').status_code==422
    assert ok(client.get(ROOT+'/runs/'+run['id'],params=head_scope))==revised
    revised = ok(action(client,owner,revised,'approve-new-a','approve'))
    assert ok(difference(revised['revision']))['current_status']=='approved'
    assert revised['active_node_id']=='B'
    assert revised['stages'][1]==original[1]


def test_adversarial_development_cannot_close_with_an_unrelated_file(client):
    from copy import deepcopy
    from packages.dynamic_workflow import stage_workspace as workspace
    from packages.dynamic_workflow.stage_contracts import READBACK_V6_CONTRACT_VERSION
    from test_engineering_stage_contracts import APPROVED, INPUT

    owner=scope(client,'Development evidence review')
    define(client,owner,[
        {**node('R'),'kind':'requirements'},
        {**node('A',['R']),'kind':'alternatives'},
        {**node('D',['R','A']),'kind':'design'},
        {**node('B',['D']),'kind':'development'},
    ])
    run=start(client,owner,stored_version=READBACK_V6_CONTRACT_VERSION)
    service=client.app.state.stage_workflows
    def perform(context):
        kind=context['kind']
        artifact=deepcopy(APPROVED[kind])
        if kind!='development':
            return {'artifact':artifact}
        if context['operation_id']=='count-on-unrelated':
            file=workspace.file_write(context,'README.txt','There is an entry point','')
            with pytest.raises(ValueError,match='缺少设计落点'):
                workspace.complete_design_item(context,'DS-1',[file['id']])
            # Exercise the approval boundary even if a malformed trusted tool
            # had emitted a real receipt with a convincing completion label.
            with workspace.ledger(context['_data_dir']) as db:
                receipt=workspace._record(db,context,'design_item',design_id='DS-1',file_refs=[file['id']])
        else:
            file=workspace.file_write(context,'src/ui/status.py','def status(value):\n    return str(value)\n','')
            file=workspace.file_write(context,file['path'],'def status(value):\n    return str(value).strip()\n',file['sha256'])
            receipt=workspace.complete_design_item(context,'DS-1',[file['id']])
        artifact['items'][0]['evidence_refs']=[receipt['id']]
        return {'artifact':artifact,'verified_evidence':workspace.operation_receipts(context)}
    service.executor=perform
    for index,node_id in enumerate(('R','A','D')):
        run=ok(action(client,owner,run,'develop-'+node_id,'execute',{'user_input':INPUT} if index==0 else {}))
        run=ok(action(client,owner,run,'approve-'+node_id,'approve'))
    run=ok(action(client,owner,run,'count-on-unrelated','execute'))
    assert run['stages'][-1]['artifact']['items'][0]['status']=='done'
    assert 'DS-1 缺少设计落点 src/ui/status.py 的实际输出文件' in run['stages'][-1]['approval_issues']
    assert action(client,owner,run,'reject-unrelated','approve').status_code==409
    run=ok(action(client,owner,run,'actual-output','execute'))
    assert not run['stages'][-1]['approval_issues']
    dev=run['stages'][-1]['stage_session_id']
    details=ok(client.get(ROOT+'/runs/'+run['id']+'/stages/B/execution-record',params={
        **owner,'conversation_id':dev,'operation_id':'actual-output'}))
    files=[item for item in details['evidence'] if item['kind']=='file']
    assert files[1]['previous_sha256']==files[0]['sha256']
    assert details['inputs'][0]['approval_sha256']==run['stages'][2]['approval_sha256']
    run=ok(action(client,owner,run,'approve-real','approve'))
    assert run['status']=='completed'


def test_registered_ur_keeps_its_number_and_reaches_case_approval(client):
    from copy import deepcopy
    from test_engineering_stage_contracts import APPROVED, INPUT, add_specialty_actions, source_urs

    owner = scope(client)
    define(client,owner,[{**node('R'),'kind':'requirements'},{**node('T',['R']),'kind':'cases'}])
    run = start(client,owner)
    requirements = {**owner,'conversation_id':run['stages'][0]['stage_session_id']}
    req = {'text':'导出文件增加失败原因','source_type':'requirement'}
    run = ok(action(client,requirements,run,'discarded','supplement',req))
    run = ok(action(client,requirements,run,'withdraw-first','withdraw_supplement',{'id':'discarded'}))
    before = run
    run = ok(action(client,requirements,run,'new-source','supplement',req))
    assert ok(action(client,requirements,before,'new-source','supplement',req))==run
    path = ROOT+'/runs/'+run['id']+'/stages/R/coverage'
    def coverage(scope=requirements, row_id='UR-A002', selector='ur'):
        return client.get(path,params={**scope,'selector':selector,'row_id':row_id})
    assert coverage(owner).status_code==404
    assert coverage(row_id='UR-A999').status_code==404
    assert ok(coverage(row_id='UR-A001'))['withdrawn']
    queued = ok(coverage())
    assert not queued['requirements'] and queued['sources']==[{'id':'UR-A002','text':req['text']}]
    run = ok(action(client,requirements,run,'wording','supplement',{'text':'标题写短一些'}))
    assert [item.get('source_id') for item in run['stages'][0]['follow_ups']]==['UR-A001','UR-A002',None]
    forged = action(client,requirements,run,'forged','execute',{'user_input':{'stage_urs':{'UR-A200':'伪造'}}})
    assert forged.status_code==422

    fix = {'requirements':False,'cases':False,'criteria':False}
    def execute(context):
        kind = context['kind']
        artifact = deepcopy(APPROVED[kind])
        if kind=='requirements':
            assert source_urs(context['user_input'])=={**source_urs(INPUT),'UR-A002':req['text']}
            assert context['user_input']['stage_follow_ups']==['标题写短一些']
            if fix[kind]:
                artifact['items'].append({**artifact['items'][1],'id':'OR-3','source_ur':['UR-A002'],
                                          'acceptance':['输出有失败原因','原因缺省时给出明确提示']})
        else:
            assert context['user_input']=={}
            assert context['approved_by_kind']['requirements']['items'][-1]['source_ur']==['UR-A002']
            add_specialty_actions(artifact['items'][0])
            if fix[kind]:
                row=artifact['items'][0]
                row['requirement_ids'].append('OR-3')
                row['actions'].append({'id':'explain','method':'manual_observation','instruction':'检查失败导出',
                                       'expected':'输出有失败原因'})
                row['acceptance_refs']=[{'requirement_id':'OR-3','criterion':1,'action_id':'explain'}]
                if fix['criteria']:
                    assert any('第 2 条通过标准' in issue for issue in context['approval_feedback'])
                    row['actions'].append({'id':'empty','method':'manual_observation','instruction':'检查无原因导出',
                                           'expected':'原因缺省时给出明确提示'})
                    row['acceptance_refs'].append({'requirement_id':'OR-3','criterion':2,'action_id':'empty'})
        return {'artifact':artifact}

    client.app.state.stage_workflows.executor=execute
    run = ok(action(client,requirements,run,'first-r','execute',{'user_input':INPUT}))
    stage = run['stages'][0]
    assert any('UR-A002' in issue for issue in stage['approval_issues'])
    assert not stage['follow_ups'][1].get('processed_by')
    assert action(client,requirements,run,'reject-r','approve').status_code==409
    fix['requirements']=True
    run = ok(action(client,requirements,run,'complete-r','execute'))
    stage = run['stages'][0]
    assert stage['source_urs']['UR-A002']==req['text'] and 'UR-A001' not in stage['source_urs']
    assert stage['follow_ups'][1]['processed_by']=='complete-r' and not stage['approval_issues']
    run = ok(action(client,requirements,run,'approve-r','approve'))
    assert ok(coverage())['requirements'][0]['cases']==[]
    run = ok(action(client,owner,run,'first-t','execute'))
    assert any('OR-3' in issue for issue in run['stages'][1]['approval_issues'])
    assert ok(coverage())['requirements'][0]['cases']==[]
    assert action(client,owner,run,'reject-t','approve').status_code==409
    fix['cases']=True
    run = ok(action(client,owner,run,'partial-criteria','execute'))
    assert 'OR-3 第 2 条通过标准尚缺验收动作' in run['stages'][1]['approval_issues']
    partial = ok(coverage(row_id='OR-3',selector='or'))['requirements'][0]['criteria']
    assert [(item['text'],[a['id'] for a in item['actions']]) for item in partial] == [
        ('输出有失败原因',['explain']),('原因缺省时给出明确提示',[])]
    assert action(client,owner,run,'reject-partial-criteria','approve').status_code==409
    fix['criteria']=True
    run = ok(action(client,owner,run,'complete-t','execute'))
    current = ok(coverage())
    assert current['requirements'][0]['row_id']=='OR-3'
    assert not current['requirements'][0]['cases'][0]['approved']
    run = ok(action(client,owner,run,'approve-t','approve'))
    assert run['status']=='completed'
    requirement = ok(coverage(row_id='OR-3',selector='or'))['requirements'][0]
    linked = requirement['cases'][0]
    assert [[(a['id'],a['binding']) for a in item['actions']] for item in requirement['criteria']] == [
        [('explain','explicit')],[('empty','explicit')]]
    assert linked['stage_session_id']==run['stages'][1]['stage_session_id']
    assert linked['operation_id']=='complete-t' and linked['approved']
    assert linked['results']==[]

    run = ok(action(client,requirements,run,'new-revision','revise',
                    {'text':'增加页码','source_type':'requirement'},node_id='R'))
    assert run['stages'][0]['follow_ups'][-1]['source_id']=='UR-A003'
    historical = ok(client.get(ROOT,params=requirements))['historical_stage']
    assert historical['source_urs']['UR-A002']==req['text'] and 'UR-A003' not in historical['source_urs']
    old_coverage = ok(coverage())
    archived_case = old_coverage['requirements'][0]['cases'][0]
    assert old_coverage['historical'] and archived_case['archived']
    assert archived_case['stage_session_id']==linked['stage_session_id']
    assert old_coverage['requirements'][0]['criteria'][1]['actions'][0]['case']['archived']
    ref = ROOT+'/runs/'+run['id']+'/stages/T/coverage-record'
    query = {'project_id':owner['project_id'],'conversation_id':archived_case['stage_session_id'],
             'row_id':archived_case['row_id'],'operation_id':archived_case['operation_id']}
    resolved = ok(client.get(ref,params=query))
    assert resolved['historical'] and resolved['stage']['operation_id']=='complete-t'
    assert resolved['inputs'][0]['stage_session_id']==requirements['conversation_id']
    assert client.get(ref,params={**query,'operation_id':'first-t'}).status_code==409
    assert client.get(ref,params={**query,'conversation_id':owner['conversation_id']}).status_code==404


@pytest.mark.parametrize('recorded', [None,'cirven.engineering-stages.v2','cirven.engineering-stages.v3',
                                     'cirven.engineering-stages.v4','cirven.engineering-stages.v5',
                                     'cirven.engineering-stages.v6','cirven.engineering-stages.v7'])
def test_resume_preserves_an_existing_workflow_contract(client, recorded):
    from packages.dynamic_workflow.stage_contracts import LEGACY_CONTRACT_VERSION

    owner = scope(client)
    define(client,owner,[node('A')])
    run = start(client,owner)
    runtime = client.app.state.stage_workflows
    with runtime.store.connect() as db:
        value = runtime._run(db,owner['project_id'],owner['conversation_id'],run['id'])
        if recorded:
            value['contract_version'] = recorded
        else:
            value.pop('contract_version')
        runtime._save(db,value)
    expected = recorded or LEGACY_CONTRACT_VERSION
    def execute(context):
        assert context['contract_version']==expected
        return {'artifact':sample('旧合同恢复')}
    runtime.executor = execute
    resumed = ok(action(client,owner,value,'legacy-execution','execute'))
    assert resumed['stages'][0]['status']=='review'
    details = ok(client.get(ROOT+'/runs/'+run['id']+'/stages/A/attempts',
                            params={**owner,'operation_id':'legacy-execution'}))
    assert details['contract_version']==expected
    assert ok(action(client,owner,resumed,'legacy-approval','approve'))['status']=='completed'


@pytest.mark.parametrize('paired',[False,True],ids=['exact-output','contrast-input'])
def test_zero_exit_container_needs_an_actual_matching_output_at_approval(client, monkeypatch, paired):
    from copy import deepcopy
    from packages.dynamic_workflow import stage_suite, stage_workspace
    from test_engineering_stage_contracts import APPROVED

    owner = scope(client)
    define(client,owner,[{**node('cases'),'kind':'cases'},
        {**node('testing',['cases']),'kind':'testing','capabilities':{'tools':['run_check']}}])
    run = start(client,owner)
    from packages.dynamic_workflow.stage_contracts import CONTRACT_VERSION
    assert run['contract_version']==CONTRACT_VERSION
    image = 'sha256:'+'1'*64
    corrected = False
    monkeypatch.setattr(stage_suite,'_admin',lambda:('/usr/bin/docker',image))
    monkeypatch.setattr(stage_suite,'_image',lambda *_:None)
    monkeypatch.setattr(stage_suite,'_owned_cleanup',lambda *_:True)
    def process(*_,stdin=None):
        if paired:
            if stdin.read()==b'BAD\n' and corrected:
                return 2,b'retry\n',None,False
            return 0,b'ready\n',None,False
        return 0,b'ready\n' if corrected else b'',None,False
    monkeypatch.setattr(stage_suite,'_cli',process)
    def executor(context):
        kind = context['kind']
        artifact = deepcopy(APPROVED[kind])
        item, = artifact['items']
        if kind=='cases':
            check = {'id':'status','method':'container_suite','argv':['./approved-status'],'image_id':image,
                     'timeout_seconds':15,'expected_output':'ready\n'}
            if paired:
                check['input_pair'] = {'normal_stdin':'READY\n','control_stdin':'BAD\n',
                                       'control_expected_output':'retry\n','control_exit_code':2}
            item.update(requirement_ids=[],actions=[check],coverage=['runtime'],
                        coverage_refs=[{'coverage':'runtime','action_id':'status'}])
            return {'artifact':artifact}
        check = stage_suite.run_suite(context,'TC-1',['./approved-status'],15)
        item.update(status='passed',evidence_refs=[check['id']])
        return {'artifact':artifact,'verified_evidence':stage_workspace.operation_receipts(context)}
    client.app.state.stage_workflows.executor = executor
    run = ok(action(client,owner,run,'plan-suite','execute'))
    run = ok(action(client,owner,run,'approve-suite-plan','approve'))
    run = ok(action(client,owner,run,'candidate-no-output','execute'))
    denied = action(client,owner,run,'approve-no-output','approve')
    assert denied.status_code==409
    issue = '对照输入的实际输出' if paired else '实际容器输出缺失'
    assert any(issue in detail for detail in run['stages'][1]['approval_issues'])

    def execution(operation):
        params = {**owner,'conversation_id':run['stages'][1]['stage_session_id'],'operation_id':operation}
        result = ok(client.get(ROOT+'/runs/'+run['id']+'/stages/testing/execution-record',params=params))
        receipt, = result['evidence']
        return receipt
    failed = execution('candidate-no-output')
    assert failed['exit_code']==0
    if paired:
        assert failed['output_matches'] and not failed['control']['output_matches']
        assert failed['control']['output_preview']=='ready\n'
    else:
        assert failed['output_preview']=='' and not failed['output_matches']
    corrected = True
    run = ok(action(client,owner,run,'candidate-exact-output','execute'))
    actual = execution('candidate-exact-output')
    assert actual['output_matches'] and actual['output_preview']=='ready\n'
    if paired:
        assert actual['control']['output_matches'] and actual['control']['output_preview']=='retry\n'
        assert actual['control']['exit_code']==actual['control']['expected_exit_code']==2
    assert action(client,owner,run,'approve-observed-output','approve').status_code==200


def test_direct_program_identity_survives_scope_checks_and_delivery(client, monkeypatch, tmp_path):
    from copy import deepcopy
    from hashlib import sha256
    from io import BytesIO
    from zipfile import ZipFile
    from packages.dynamic_workflow import stage_suite, stage_workspace
    from test_engineering_stage_contracts import APPROVED, INPUT, scenario
    from test_engineering_stage_suite import IMAGE, configured

    configured.__wrapped__(tmp_path,monkeypatch)
    owner = scope(client)
    outside = scope(client,'Other project')
    run = start(client,owner,definition='default')
    path = 'dist/device'
    shortcut = 'dist/print-ready'
    argv = ['./'+path]
    program = '#!/bin/sh\nread -r input\nif [ "$input" = OK ]; then echo ready; else echo retry; exit 2; fi\n'
    forged = '#!/bin/sh\necho ready\n'
    action_plan = {'id':'direct','method':'container_suite','argv':argv,'subject_path':path,
                   'image_id':IMAGE,'timeout_seconds':15,'expected_output':'ready\n',
                   'input_pair':{'normal_stdin':'OK\n','control_stdin':'BAD\n',
                                 'control_expected_output':'retry\n','control_exit_code':2}}
    def model(ctx):
        kind = ctx['kind']
        artifact = deepcopy(APPROVED[kind])
        item = artifact['items'][0]
        if kind=='requirements':
            item['surfaces'] = []
        elif kind=='cases':
            approved_check = deepcopy(action_plan)
            if ctx['operation_id']=='report-from-print':
                approved_check.pop('subject_path')
                approved_check.update(argv=['/bin/echo','ready'])
            item.update(actions=[approved_check],coverage=['runtime'],
                        coverage_refs=[{'coverage':'runtime','action_id':'direct'}])
        elif kind=='design':
            output = shortcut if ctx['operation_id']=='omit-tested-program' else path
            item.update(directory='dist',implementation_files=[output],behavior_scenarios=[
                scenario('S-1','OR-1','direct'),scenario('S-2','OR-2','direct')])
        elif kind=='development':
            output = stage_workspace.file_write(ctx,path,program,'')
            helper = stage_workspace.file_write(ctx,shortcut,forged,'')
            closed = stage_workspace.complete_design_item(ctx,'DS-1',[output['id'],helper['id']])
            item['evidence_refs'] = [closed['id']]
        elif kind=='testing':
            assert ctx['require_runtime_target']
            assert set(ctx['approved_subjects'])=={path}
            if ctx['operation_id']=='shadow-program':
                stage_workspace.file_write(ctx,path,forged,sha256(program.encode()).hexdigest())
            elif ctx['operation_id']=='restore-program':
                stage_workspace.file_write(ctx,path,program,sha256(forged.encode()).hexdigest())
            check = stage_suite.run_suite(ctx,'TC-1',argv,15)
            item.update(status=check['status'],evidence_refs=[check['id']])
        elif kind=='delivery':
            if ctx['operation_id']=='swap-package-member':
                stage_workspace.file_write(ctx,path,forged,sha256(program.encode()).hexdigest())
            else:
                previous = stage_workspace.file_list(ctx)
                if any(row['path']==path and row['sha256']==sha256(forged.encode()).hexdigest() for row in previous):
                    stage_workspace.file_write(ctx,path,program,sha256(forged.encode()).hexdigest())
            old_zip = next((r['sha256'] for r in stage_workspace.file_list(ctx) if r['path']=='delivery.zip'),'')
            package = stage_workspace.package_files(ctx,'delivery.zip',[path],old_zip)
            item.update(path=package['path'],sha256=package['sha256'])
        return {'artifact':artifact,'verified_evidence':stage_workspace.operation_receipts(ctx)}
    client.app.state.stage_workflows.executor = model
    for kind in ('init','requirements','alternatives','cases','design','development'):
        if kind=='cases':
            run = ok(action(client,owner,run,'report-from-print','execute'))
            assert 'TC-1 / direct 的启动与运行专项须在 subject_path' in run['stages'][3]['approval_issues'][0]
            assert action(client,owner,run,'reject-print-command','approve').status_code==409
        elif kind=='design':
            run = ok(action(client,owner,run,'omit-tested-program','execute'))
            assert any('dist/device 未对应' in issue for issue in run['stages'][4]['approval_issues'])
            assert action(client,owner,run,'reject-planned-shortcut','approve').status_code==409
        run = ok(action(client,owner,run,'execute-'+kind,'execute',{'user_input':INPUT} if kind=='requirements' else {}))
        run = ok(action(client,owner,run,'approve-'+kind,'approve'))
    run = ok(action(client,owner,run,'shadow-program','execute'))
    assert '已审核开发产物版本不符' in run['stages'][6]['error']
    run = ok(action(client,owner,run,'restore-program','execute'))
    test_stage = run['stages'][6]
    assert test_stage['status']=='review' and not test_stage['approval_issues']
    check, = [record for record in test_stage['verified_evidence'] if record['kind']=='check']
    assert check['subject']['sha256']==sha256(program.encode()).hexdigest()
    assert check['subject']['approval_sha256']==run['stages'][5]['approval_sha256']
    development = run['stages'][5]['stage_session_id']

    def move_project(target, tag):
        header = client.app.state.conversation_threads.metadata_header(development)
        return ok(client.post('/api/conversations/'+development+'/organization',json={
            'target_project_id':target,'action':'move','operation_id':tag,'source':header['source'],
            'expected_revision':header['organization']['revision']}))
    move_project(outside['project_id'],'hide-program-origin')
    assert action(client,owner,run,'reject-hidden-origin','approve').status_code==409
    move_project(owner['project_id'],'show-program-origin')
    run = ok(action(client,owner,run,'approve-direct-check','approve'))
    run = ok(action(client,owner,run,'swap-package-member','execute'))
    assert action(client,owner,run,'reject-swapped-package','approve').status_code==409
    run = ok(action(client,owner,run,'restore-package-member','execute'))
    run = ok(action(client,owner,run,'approve-tested-package','approve'))
    delivery = run['stages'][-1]
    prefix = ROOT+'/runs/'+run['id']+'/stages/delivery/'
    query = {**owner,'conversation_id':delivery['stage_session_id'],'approval_sha256':delivery['approval_sha256']}
    receipt = ok(client.get(prefix+'delivery-receipt',params=query))
    portable, = receipt['test_results'][0]['checks']
    assert portable['subject']==check['subject'] and portable['delivery_members']==['delivery.zip!/'+path]
    bundle = client.get(prefix+'delivery-bundle',params=query)
    assert bundle.status_code==200
    with ZipFile(BytesIO(bundle.content)) as archive:
        with ZipFile(BytesIO(archive.read('files/delivery.zip'))) as files:
            assert files.read(path)==program.encode()


def test_failed_contrast_is_preserved_when_returned_to_development(client, monkeypatch):
    from copy import deepcopy
    from packages.dynamic_workflow import stage_suite, stage_workspace
    from packages.dynamic_workflow.stage_contracts import DESIGN_FILES_V7_CONTRACT_VERSION
    from test_engineering_stage_contracts import APPROVED, INPUT, scenario

    owner = scope(client)
    run = start(client,owner,definition='default',stored_version=DESIGN_FILES_V7_CONTRACT_VERSION)
    image = 'sha256:'+'1'*64
    action_plan = {'id':'status','method':'container_suite','argv':['./status'],'image_id':image,
                   'timeout_seconds':15,'expected_output':'ready\n',
                   'input_pair':{'normal_stdin':'OK\n','control_stdin':'BAD\n',
                                 'control_expected_output':'retry\n','control_exit_code':2}}
    monkeypatch.setattr(stage_suite,'_admin',lambda:('/usr/bin/docker',image))
    monkeypatch.setattr(stage_suite,'_image',lambda *_:None)
    monkeypatch.setattr(stage_suite,'_owned_cleanup',lambda *_:True)
    monkeypatch.setattr(stage_suite,'_cli',lambda *_,**_options:(0,b'ready\n',None,False))
    dispatch = []
    def model(ctx):
        kind = ctx['kind']
        artifact = deepcopy(APPROVED[kind])
        if kind=='requirements':
            artifact['items'][0]['surfaces'] = []
        elif kind=='cases':
            artifact['items'][0].update(actions=[action_plan],coverage=['runtime'],
                                        coverage_refs=[{'coverage':'runtime','action_id':'status'}])
        elif kind=='design':
            artifact['items'][0].update(directory='status.txt',implementation_files=['status.txt'],behavior_scenarios=[
                scenario('S-1','OR-1','status'),scenario('S-2','OR-2','status')])
        elif kind=='development':
            if rework := ctx.get('rework_request'):
                dispatch.extend(rework['observations'])
            previous = stage_workspace.file_list(ctx)
            file = stage_workspace.file_write(ctx,'status.txt','status route',previous[0]['sha256'] if previous else '')
            close = stage_workspace.complete_design_item(ctx,'DS-1',[file['id']])
            artifact['items'][0]['evidence_refs'] = [close['id']]
        elif kind=='testing':
            receipt = stage_suite.run_suite(ctx,'TC-1',['./status'],15)
            artifact['items'][0].update(status='failed',evidence_refs=[receipt['id']])
        return {'artifact':artifact,'verified_evidence':stage_workspace.operation_receipts(ctx)}
    client.app.state.stage_workflows.executor = model
    for kind in ('init','requirements','alternatives','cases','design','development'):
        run = ok(action(client,owner,run,'prepare-'+kind,'execute',
                        {'user_input':INPUT} if kind=='requirements' else {}))
        run = ok(action(client,owner,run,'accept-'+kind,'approve'))
    run = ok(action(client,owner,run,'try-constant','execute'))
    source = run['stages'][6]['stage_session_id']
    base = ROOT+'/runs/'+run['id']+'/stages/'
    def evidence(node, session, **query):
        return ok(client.get(base+node+'/rework-evidence',params={
            **owner,'conversation_id':session,**query}))
    before, = evidence('testing',source,revision=run['revision'])['records']
    contrast = before['control']
    assert before['exit_code']==0 and contrast['exit_code']==0 and contrast['expected_exit_code']==2
    assert contrast['output_matches'] is False and contrast['output_preview']=='ready\n'
    run = ok(action(client,owner,run,'rework-contrast','rework',
        {'target_node_id':'development','case_ids':['TC-1'],'reason':'按原对照输出来修复'}))
    after, = evidence('development',run['stages'][5]['stage_session_id'])['records']
    assert after==before
    ok(action(client,owner,run,'prepare-repair','execute'))
    assert dispatch[0]['id']==before['id'] and dispatch[0]['control']==contrast


def test_hardware_readback_is_current_in_testing_rework_and_delivery(client, tmp_path):
    import asyncio
    from copy import deepcopy
    from packages.domain.native_capabilities import NativeCapabilities
    from packages.dynamic_workflow import stage_connectors, stage_kernel, stage_workspace
    from packages.dynamic_workflow.stage_contracts import CONTRACT_VERSION
    from test_engineering_stage_contracts import APPROVED, scenario
    from test_stage_connector_calls import install_readback_meter, readback_action

    service = client.app.state.stage_workflows
    mode, _ = install_readback_meter(tmp_path, NativeCapabilities(service.store, service.data_dir))
    owner = scope(client, 'Device feedback workflow')
    run = start(client, owner, 'default')
    assert run['contract_version']==CONTRACT_VERSION
    action_plan = readback_action()
    runtime = {'id':'runtime','method':'manual_observation',
               'instruction':'启动状态采集程序并查看就绪画面','expected':'出现就绪画面'}
    path = 'controller.txt'

    def executor(ctx):
        kind = ctx['kind']
        artifact = deepcopy(APPROVED[kind])
        row = artifact['items'][0]
        if kind=='requirements':
            row.update(title='读取指定控制器',surfaces=['hardware'],acceptance=['设备回读 17.25'])
            artifact['items'][1].update(title='采集程序启动',acceptance=['出现就绪画面'])
        elif kind=='cases':
            instrument = dict(action_plan)
            if ctx['operation_id']=='claim-ack-as-device':
                instrument.pop('hardware_readback')
                instrument.update(pointer='/sent',expected_json='true')
            row.update(coverage=['hardware','runtime'],actions=[instrument,runtime],coverage_refs=[
                {'coverage':'hardware','action_id':'read-meter'}, {'coverage':'runtime','action_id':'runtime'}])
        elif kind=='design':
            row.update(directory=path,implementation_files=[path],behavior_scenarios=[
                scenario('S-1','OR-1','read-meter'),scenario('S-2','OR-2','runtime')])
        elif kind=='development':
            previous = next((f['sha256'] for f in stage_workspace.file_list(ctx) if f['path']==path),'')
            file = stage_workspace.file_write(ctx,path,'controlled '+ctx['operation_id'],previous)
            close = stage_workspace.complete_design_item(ctx,'DS-1',[file['id']])
            row['evidence_refs'] = [close['id']]
        elif kind=='testing':
            connector_context, _ = stage_kernel.prepare_stage_prompt(ctx)
            value = asyncio.run(stage_connectors.call_connector(connector_context,'meter','measure',action_plan['arguments'],
                 'TC-1',action_plan['pointer'],action_plan['expected_json']))
            row.update(status='passed',evidence_refs=[value['receipt']['id']])
        elif kind=='delivery':
            bundle = stage_workspace.package_files(ctx,'controller.zip',[path])
            row.update(path=bundle['path'],sha256=bundle['sha256'])
        return {'artifact':artifact,'verified_evidence':stage_workspace.operation_receipts(ctx)}

    service.executor = executor
    for kind in ('init','requirements','alternatives','cases','design','development'):
        if kind=='cases':
            run = ok(action(client,owner,run,'claim-ack-as-device','execute'))
            assert any('硬件联动专项尚缺已审设备编号' in issue for issue in run['stages'][3]['approval_issues'])
            assert action(client,owner,run,'reject-ack-plan','approve').status_code==409
        user_input = {'ur':'读取目标控制器的真实读数\n采集程序能够启动'} if kind=='requirements' else {}
        run = ok(action(client,owner,run,'execute-'+kind,'execute',{'user_input':user_input}))
        run = ok(action(client,owner,run,'app-'+kind,'approve'))

    def testing_stage(current):
        return current['stages'][6]

    api = ROOT+'/runs/'+run['id']+'/stages/'
    def linked_action(requirement_id):
        report = ok(client.get(api+'requirements/coverage',params={**owner,
            'conversation_id':run['stages'][1]['stage_session_id'],'selector':'or','row_id':requirement_id}))
        criterion, = report['requirements'][0]['criteria']
        bound, = criterion['actions']
        return criterion['text'],bound

    config = testing_stage(run)['config']['capabilities']
    run = ok(action(client,owner,run,'configure-meter','configure',{'capabilities':{**config,'mcp':['meter']}}))
    mode.write_text('cached')
    run = ok(action(client,owner,run,'cached-board','execute'))
    assert action(client,owner,run,'reject-old-sample','approve').status_code==409
    old_scope = {**owner,'conversation_id':testing_stage(run)['stage_session_id']}
    before = ok(client.get(api+'testing/execution-record',params={**old_scope,'operation_id':'cached-board'}))
    stale, = before['evidence']
    assert stale['readback_issue']=='outside_dispatch' and stale['hardware_readback']['observed_preview']=='17.25'
    _,bound = linked_action('OR-1')
    assert bound['id']=='read-meter' and bound['scenarios'][0]['scenario_id']=='S-1'
    assert bound['results'][0]['receipt_id']==stale['id'] and bound['results'][0]['proof']=='failed'
    run = ok(action(client,owner,run,'back-to-development','rework',{
        'reason':'设备连接器读到了缓存','target_node_id':'development','case_ids':['TC-1']}))
    dev_scope = {**owner,'conversation_id':run['stages'][5]['stage_session_id']}
    history = ok(client.get(api+'development/rework-evidence',params=dev_scope))['records']
    assert history[0]['hardware_readback']==stale['hardware_readback'] and history[0]['readback_issue']==stale['readback_issue']
    assert ok(client.get(api+'testing/execution-record',params={**old_scope,'operation_id':'cached-board'}))==before
    run = ok(action(client,owner,run,'fix-device-handler','execute'))
    run = ok(action(client,owner,run,'approve-updated-device-handler','approve'))
    mode.write_text('fresh')
    run = ok(action(client,owner,run,'fresh-board','execute'))
    assert testing_stage(run)['stage_session_id']!=old_scope['conversation_id']
    assert action(client,owner,run,'reject-missing-runtime','approve').status_code==409
    new_scope = {**owner,'conversation_id':testing_stage(run)['stage_session_id']}
    success, = ok(client.get(api+'testing/execution-record',params={**new_scope,'operation_id':'fresh-board'}))['evidence']
    assert success['status']=='passed' and 'readback_issue' not in success
    text,bound = linked_action('OR-1')
    assert text=='设备回读 17.25' and bound['binding']=='design'
    assert bound['results'][0]['receipt_id']==success['id'] and bound['results'][0]['proof']=='verified'
    _,bound = linked_action('OR-2')
    assert bound['id']=='runtime' and bound['scenarios'][0]['scenario_id']=='S-2'
    assert bound['results'][0]['proof']=='missing' and 'receipt_id' not in bound['results'][0]
    run = ok(action(client,new_scope,run,'observe-runtime','record_manual_check',{'case_id':'TC-1',
        'action_id':'runtime','status':'passed','environment':'受控夹具','observation':'出现就绪画面'}))
    run = ok(action(client,owner,run,'approve-current-sample','approve'))
    _,bound = linked_action('OR-2')
    assert bound['results'][0]['stage_session_id']==new_scope['conversation_id']
    assert bound['results'][0]['proof']=='verified' and bound['results'][0]['at']
    run = ok(action(client,owner,run,'prepare-package','execute'))
    run = ok(action(client,owner,run,'approve-package','approve'))
    delivery = run['stages'][7]
    receipt = ok(client.get(api+'delivery/delivery-receipt',params={**owner,
         'conversation_id':delivery['stage_session_id'],'approval_sha256':delivery['approval_sha256']}))
    readback, = [check for result in receipt['test_results'] for check in result['checks']
                 if 'hardware_readback' in check]
    assert readback['source']=='connector_response' and readback['receipt_id']==success['id']
    assert readback['hardware_readback']==success['hardware_readback']


def test_approved_design_requires_every_current_implementation_file(client):
    from copy import deepcopy
    from packages.dynamic_workflow import stage_workspace as files
    from packages.dynamic_workflow.stage_assertions import count_complete_design_items
    from packages.dynamic_workflow.stage_contracts import CONTRACT_VERSION
    from test_engineering_stage_contracts import APPROVED, INPUT, scenario

    owner = scope(client,'Coverage of required files')
    run = start(client,owner,'default')
    assert run['contract_version']==CONTRACT_VERSION
    main = 'src/controller/status.py'
    recovery = 'src/controller/recovery-after-repeated-interruptions.py'
    paths = [main,recovery,*[f'src/controller/module-{i:02}.py' for i in range(10)]]
    receipt_ids = []

    def executor(ctx):
        kind = ctx['kind']
        artifact = deepcopy(APPROVED[kind])
        item = artifact['items'][0]
        if kind=='requirements':
            item['surfaces'] = []
        elif kind=='cases':
            item.update(actions=[{'id':'startup','method':'manual_observation',
                                  'instruction':'启动并复位程序','expected':'进入就绪'}],
                        coverage=['runtime'],coverage_refs=[{'coverage':'runtime','action_id':'startup'}])
        elif kind=='design':
            item.update(directory='src/controller',behavior_scenarios=[
                scenario('S-1','OR-1','startup'),scenario('S-2','OR-2','startup')])
            if ctx['operation_id']!='omit-files':
                item['implementation_files'] = paths
        elif kind=='development':
            if ctx['operation_id']=='submit-partial':
                for path in (main,'src/controller/README.md'):
                    receipt_ids.append(files.file_write(ctx,path,'partial','')['id'])
                with pytest.raises(ValueError,match=recovery):
                    files.complete_design_item(ctx,'DS-1',receipt_ids)
                # Independently test the approval gate using an injected partial
                # ledger record as if an older client had registered it.
                with files.ledger(ctx['_data_dir']) as db:
                    close=files._record(db,ctx,'design_item',design_id='DS-1',file_refs=receipt_ids)
            elif ctx['operation_id']=='record-before-edit':
                assert any(recovery in issue for issue in ctx['approval_feedback'])
                receipt_ids.pop()  # The unrelated README is outside the approved deliverables.
                recovery_index = len(receipt_ids)
                for path in paths[1:]:
                    receipt_ids.append(files.file_write(ctx,path,'implementation','')['id'])
                close = files.complete_design_item(ctx,'DS-1',receipt_ids)
                before = files.file_read(ctx,recovery)
                current = files.file_write(ctx,recovery,'updated behavior',before['sha256'])
                receipt_ids[recovery_index] = current['id']
            else:
                close = files.complete_design_item(ctx,'DS-1',receipt_ids)
            item['evidence_refs'] = [close['id']]
            records = files.operation_receipts(ctx)
            designs = ctx['approved_by_kind']['design']['items']
            assert count_complete_design_items(designs,[*ctx['prior_evidence'],*records],
                require_design_output=True) == (1 if ctx['operation_id']=='record-all' else 0)
        return {'artifact':artifact,'verified_evidence':files.operation_receipts(ctx)}

    client.app.state.stage_workflows.executor = executor
    for kind in ('init','requirements','alternatives','cases','design'):
        if kind=='design':
            run = ok(action(client,owner,run,'omit-files','execute'))
            assert any('必须列明' in issue for issue in run['stages'][4]['approval_issues'])
            assert action(client,owner,run,'reject-omitted-list','approve').status_code==409
        user_input = {'user_input':INPUT} if kind=='requirements' else {}
        run = ok(action(client,owner,run,'run-'+kind,'execute',user_input))
        run = ok(action(client,owner,run,'approve-'+kind,'approve'))
    run = ok(action(client,owner,run,'submit-partial','execute'))
    assert any(recovery in issue for issue in run['stages'][5]['approval_issues'])
    assert action(client,owner,run,'reject-partial','approve').status_code==409
    run = ok(action(client,owner,run,'record-before-edit','execute'))
    assert any('文件已变化' in issue for issue in run['stages'][5]['approval_issues'])
    assert action(client,owner,run,'reject-stale-version','approve').status_code==409
    run = ok(action(client,owner,run,'record-all','execute'))
    approved = ok(action(client,owner,run,'approve-current-files','approve'))
    assert approved['active_node_id']=='testing'


def test_coverage_overview_locates_unmapped_user_requirement_and_respects_source_scope(client):
    from copy import deepcopy
    from test_engineering_stage_contracts import APPROVED, INPUT

    owner = scope(client)
    other = scope(client, 'Other requirements project')
    define(client, owner, [{**node('R'), 'kind':'requirements'}, {**node('C',['R']), 'kind':'cases'},
                           {**node('R2'), 'kind':'requirements'}])
    run = start(client, owner)
    client.app.state.stage_workflows.executor = lambda _: {'artifact':deepcopy(APPROVED['requirements'])}
    run = ok(action(client, owner, run, 'partial-requirements', 'execute',
                    {'user_input':{'ur':INPUT['ur']+'\n断电后恢复上一状态'}}))
    url = ROOT+'/runs/'+run['id']+'/coverage-overview'
    report = ok(client.get(url, params=owner))
    assert report['phases']==['cases'] and report['pending_sources']==1
    raw, = [row for row in report['rows'] if row['selector']=='ur']
    assert raw['row_id']=='UR-003' and raw['title']=='断电后恢复上一状态' and raw['states']=={'requirements':'missing'}
    assert report['rows'][1]['states']=={'requirements':'review','cases':'missing'}
    session = raw['stage_session_id']
    assert ok(client.get(ROOT+'/runs/'+run['id']+'/stages/R/coverage',params={
        **owner,'conversation_id':session,'selector':'ur','row_id':raw['row_id']}))['requirements']==[]

    def move(project, op):
        header = client.app.state.conversation_threads.metadata_header(session)
        ok(client.post('/api/conversations/'+session+'/organization',json={
            'operation_id':op,'source':header['source'],'expected_revision':header['organization']['revision'],
            'action':'move','target_project_id':project}))
    move(other['project_id'], 'move-coverage-source')
    unavailable = ok(client.get(url, params=owner))
    assert unavailable['unavailable_sources']==1 and unavailable['pending_sources']==1 and not unavailable['rows']
    move(owner['project_id'], 'restore-coverage-source')
    assert ok(client.get(url, params=owner))==report


def test_coverage_rejects_unconfirmed_results_and_ambiguous_links():
    from packages.dynamic_workflow.stage_assertions import action_digest
    from packages.dynamic_workflow.stage_coverage import coverage_overview, requirement_coverage
    from packages.dynamic_workflow.stage_runs import digest

    check = {'id':'content','method':'text_equals','path':'src/report.txt','expected':'ok'}
    req = {'id':'OR-1','title':'导出','source_ur':['UR-001'],'acceptance':['导出结果内容正确']}
    case = {'id':'TC-1','title':'校验导出','requirement_ids':['OR-1'],'actions':[check]}
    result = {'id':'TEST-1','title':'已执行','case_id':'TC-1','status':'passed','evidence_refs':['check-1']}
    nodes = [
        {**node('R'),'kind':'requirements'},{**node('R2'),'kind':'requirements'},
        {**node('C',['R']),'kind':'cases'},{**node('T',['C']),'kind':'testing'},
    ]
    def stage(ident, row, dependencies, *, status='approved'):
        return {'node_id':ident,'stage_session_id':'session-'+ident,'operation_id':'op-'+ident,
                'status':status,'approval_sha256':'approved' if status=='approved' else None,
                'artifact':{'items':[row]},'verified_evidence':[],
                'log':[{'event':'started','operation_id':'op-'+ident,
                        'inputs':{item['node_id']:digest(item['artifact']) for item in dependencies}},
                       {'event':'finished','operation_id':'op-'+ident,'status':'review'}]}
    requirements = stage('R',req,[])
    requirements['source_urs'] = {'UR-001':'导出文件'}
    duplicate = stage('R2',req,[])
    plan = stage('C',case,[requirements,duplicate])
    test = stage('T',result,[plan],status='review')
    run = {'revision':1,'definition':{'nodes':nodes},'stages':[requirements,duplicate,plan,test]}
    def coverage():
        return requirement_coverage(run,requirements,'ur','UR-001',historical=False,
                                    digest=digest,evidence_issues=StageRuns._evidence_issues)
    def first_summary():
        overview = coverage_overview(run,archives=[],digest=digest,
                                     evidence_issues=StageRuns._evidence_issues,visible=lambda _: True)
        return next(item['states'] for item in overview['rows'] if item['node_id']=='R')
    def proof():
        return coverage()['requirements'][0]['criteria'][0]['actions'][0]['results'][0]
    assert coverage()['requirements'][0]['cases'][0]['results'][0]['status']=='unverified'
    assert proof()['proof']=='missing' and 'receipt_id' not in proof()
    test['verified_evidence'].append({'id':'check-1','kind':'check','case_id':'TC-1','action_id':'content',
                                     'action_sha256':action_digest(check),'status':'passed','exit_code':0})
    assert coverage()['requirements'][0]['cases'][0]['results'][0]['status']=='passed'
    assert proof()['proof']=='verified'
    test['test_input_issues'] = {'TC-1':['TC-1 / content 被测文件已变化']}
    assert proof()['proof']=='stale'
    test.pop('test_input_issues')
    test['verified_evidence'].append({**test['verified_evidence'][0],'id':'new-failure','status':'failed','exit_code':1})
    assert proof()['proof']=='failed' and proof()['receipt_id']=='new-failure'
    test['verified_evidence'][-1].update(id='new-pass',status='passed',exit_code=0)
    assert proof()['proof']=='unconfirmed' and proof()['receipt_id']=='new-pass'
    test['verified_evidence'].pop()
    assert first_summary()['testing']=='review'
    test.update(status='approved',approval_sha256='approved')
    assert first_summary()['testing']=='covered'
    nodes.append({**node('T2',['C']),'kind':'testing'})
    run['stages'].append({'node_id':'T2','stage_session_id':None,'status':'pending','log':[]})
    assert first_summary()['testing']=='missing'
    test['log'][0]['inputs']['C']='stale-fingerprint'
    assert coverage()['requirements'][0]['cases'][0]['results']==[]
    nodes[2]['inputs'].append('R2')
    ambiguous = coverage()
    assert ambiguous['ambiguous'] and ambiguous['requirements'][0]['cases']==[]
    assert first_summary()['trace']=='unverified'


def test_implementation_trace_requires_matching_leaf_scope_and_registered_output():
    from copy import deepcopy
    from packages.dynamic_workflow.stage_coverage import requirement_coverage
    from packages.dynamic_workflow.stage_runs import digest

    nodes = [{**node(ident,[s for s in inputs]),'kind':kind} for ident,kind,inputs in [
        ('R','requirements',()),('A','alternatives',('R',)),
        ('D','design',('R','A')),('I','development',('D',)),
        ('extra','development',()),
    ]]
    def stage(ident, rows, parents=(), *, session=None):
        session = session or ident
        return {'node_id':ident,'status':'approved','stage_session_id':session,'approval_sha256':session+'-approved',
                'operation_id':'op-'+session,'artifact':{'items':rows},'verified_evidence':[],
                'log':[{'event':'started','operation_id':'op-'+session,
                        'inputs':{item['node_id']:digest(item['artifact']) for item in parents},
                        'input_versions':{item['node_id']:{k:item[k] for k in ('stage_session_id','approval_sha256')}
                                          for item in parents}},
                       {'event':'finished','operation_id':'op-'+session,'status':'review'}]}
    reqs = stage('R',[{'id':'OR-1','title':'导出'},{'id':'OR-2','title':'导入'}])
    choices = stage('A',[{'id':'A-1','title':'导出方案','level':3,'requirement_ids':['OR-1']},
                         {'id':'A-2','title':'导入方案','level':3,'requirement_ids':['OR-2']}],[reqs])
    design = {'id':'DS-1','title':'输出文件','directory':'src/out','requirement_ids':['OR-1'],'decision_ids':['A-2']}
    planned = stage('D',[design],[reqs,choices])
    report = {'id':'DEV-1','title':'登记输出','design_ids':['DS-1'],'status':'done','evidence_refs':['close']}
    development = stage('I',[report],[planned])
    run = {'definition':{'nodes':nodes},'stages':[reqs,choices,planned,development]}

    def trace(hidden=()):
        result = requirement_coverage(run,reqs,'or','OR-1',historical=False,digest=digest,
                  evidence_issues=StageRuns._evidence_issues,visible=lambda key:key not in hidden)
        return result['requirements'][0]['implementation']

    swapped = trace()
    assert [entry['row_id'] for entry in swapped['alternatives']]==['A-1']
    assert swapped['design']==swapped['development']==[]
    design['decision_ids']=['A-1']
    development['log'][0]['inputs']['D']=digest(planned['artifact'])
    development['verified_evidence']=[
        {'id':'f','kind':'file','path':'README.md','sha256':'first'},
        {'id':'close','kind':'design_item','design_id':'DS-1','file_refs':['f']}]
    assert trace()['development'][0]['status']=='unverified'
    development['verified_evidence'][0]['path']='src/out/app.py'
    accepted = trace()
    assert accepted['development'][0]['status']=='registered'
    assert accepted['development'][0]['basis_ids']==['DS-1']
    assert trace(hidden=['A'])=={'alternatives':[],'design':[],'development':[]}

    replacement = deepcopy(planned)
    replacement.update(stage_session_id='new-design',approval_sha256='new-approved')
    run['stages'][2] = replacement
    stale = trace()
    assert stale['design'][0]['stage_session_id']=='new-design'
    assert stale['development']==[]


def test_history_distinguishes_equal_output_using_pins_and_legacy_intervals():
    from copy import deepcopy
    from packages.dynamic_workflow.stage_coverage import requirement_coverage
    from packages.dynamic_workflow.stage_runs import digest

    old = {'node_id':'R','stage_session_id':'r1','approval_sha256':'r1-approved','status':'approved',
           'operation_id':'old-r','artifact':{'items':[{'id':'OR-1','title':'原需求','source_ur':['UR-001']}]},
           'log':[{'event':'approve','at':'2026-11-26T01:00:00+00:00'}]}
    current = {**deepcopy(old),'stage_session_id':'r2','approval_sha256':'r2-approved','operation_id':'new-r'}
    current['log'] += [{'event':'revised','previous_session_id':'r1','at':'2026-11-26T02:00:00+00:00'},
                       {'event':'approve','at':'2026-11-26T02:10:00+00:00'}]
    def plan(session, instant):
        return {'node_id':'C','stage_session_id':session,'status':'approved','operation_id':'op-'+session,
                'approval_sha256':session+'-approved',
                'artifact':{'items':[{'id':'TC-1','title':'重名的用例','requirement_ids':['OR-1']}]},
                'log':[{'event':'started','operation_id':'op-'+session,'at':instant,'inputs':{'R':digest(old['artifact'])}},
                       {'event':'finished','operation_id':'op-'+session,'status':'review'}]}
    prior_case = plan('c1','2026-11-26T01:10:00+00:00')
    current_case = plan('c2','2026-11-26T02:20:00+00:00')
    run = {'definition':{'nodes':[{**node('R'),'kind':'requirements'},{**node('C',['R']),'kind':'cases'}]},
           'stages':[current,current_case]}
    def coverage():
        return requirement_coverage(run,old,'or','OR-1',archives=[old,prior_case],historical=True,
                                    digest=digest,evidence_issues=StageRuns._evidence_issues)
    assert [c['stage_session_id'] for c in coverage()['requirements'][0]['cases']]==['c1']
    del prior_case['log'][0]['at']
    assert coverage()['version_uncertain'] and coverage()['requirements'][0]['cases']==[]
    pin = {'stage_session_id':'r1','approval_sha256':'r1-approved'}
    prior_case['log'][0]['input_versions']={'R':pin}
    assert [c['stage_session_id'] for c in coverage()['requirements'][0]['cases']]==['c1']
    pin['approval_sha256']='unrelated-approval'
    assert coverage()['version_uncertain'] and not coverage()['requirements'][0]['cases']


def test_stage_dialogue_recovers_multi_turn_context_and_uses_page_annotation(client):
    owner = scope(client)
    define(client,owner,[node('A'),node('B',['A'])])
    run = start(client,owner)
    stage = run['stages'][0]['stage_session_id']
    child = {**owner,'conversation_id':stage}
    runtime = client.app.state.stage_workflows
    runtime.executor = lambda _: {'artifact':sample('current')}
    run = ok(action(client,owner,run,'build-contract','execute'))
    annotated = {'target':'R1','comment':'说明通过标准','anchor':{'kind':'text','quote':'范围','field':'title'}}
    original = run
    run = ok(action(client,owner,original,'comment-source','annotate',annotated))
    annotation = run['stages'][0]['annotations'][0]
    assert annotation['created_operation_id']=='comment-source'
    other = ok(action(client,owner,run,'other-comment-source','annotate',{'target':'R1','comment':'其他问题'}))
    replayed = ok(action(client,owner,original,'comment-source','annotate',annotated))
    assert replayed['stages'][0]['annotations']==other['stages'][0]['annotations']
    assert replayed['stages'][0]['annotations'][1]['created_operation_id']=='other-comment-source'
    contexts = []
    def dialogue(context):
        contexts.append(context)
        return '来自本阶段的说明。'
    runtime.dialogue_executor = dialogue
    path = ROOT+'/runs/'+run['id']+'/dialogue'
    def ask(op, text, artifact=annotation['id'], target=child, node_id='A'):
        return client.post(path,json={**target,'node_id':node_id,'operation_id':op,'question':text,'annotation_id':artifact})
    first = ok(ask('readiness','这条为什么要审核？'))
    assert first['stages'][0]['dialogue'][0]['answer']=='来自本阶段的说明。'
    assert first['stages'][0]['artifact']==run['stages'][0]['artifact']
    assert first['stages'][0]['status']=='review'
    assert contexts[0]['annotation']==annotation and contexts[0]['history']==[]
    assert 'screenshot' not in contexts[0]
    assert ok(ask('readiness','这条为什么要审核？'))==first
    assert ask('fake','缺少授权',target=owner).status_code==404
    assert ask('unknown','找不到批注',artifact='ann_'+'0'*32).status_code==404
    second = ok(ask('followup','具体要读什么？'))
    assert contexts[1]['history']==[{'question':'这条为什么要审核？','answer':'来自本阶段的说明。'}]
    assert contexts[1]['annotation']==annotation
    again = ok(client.get(ROOT,params=child))['runs'][0]
    assert again['stages'][0]['dialogue']==second['stages'][0]['dialogue']
    assert again['stages'][1].get('dialogue') is None
    items = ok(client.get('/api/conversations',params={'scope':'project','project_id':owner['project_id']}))['items']
    directory_entry = next(item for item in items if item['id']==stage)
    assert directory_entry['title']=='审核 A' and directory_entry['project_id']==owner['project_id']


def test_dialogue_references_exact_follow_up_without_executing_it(client):
    owner = scope(client)
    define(client,owner,[node('A'),node('B',['A'])])
    run = start(client,owner)
    child = {**owner,'conversation_id':run['stages'][0]['stage_session_id']}
    run = ok(action(client,child,run,'source-a','supplement',{'text':'保留审核日期'}))
    run = ok(action(client,child,run,'source-b','supplement',{'text':'保留旧名称'}))
    path = ROOT+'/runs/'+run['id']+'/dialogue'
    contexts = []
    runtime = client.app.state.stage_workflows
    runtime.dialogue_executor = lambda context: contexts.append(context) or '阶段说明'

    def ask(op, source='source-a', target=child, node_id='A', **reference):
        return client.post(path,json={**target,'node_id':node_id,'operation_id':op,
                           'question':'这条会怎样处理？','follow_up_id':source,**reference})

    assert ask('invalid-both',annotation_id='ann_'+'a'*32).status_code==422
    assert ask('unknown-source','unknown').status_code==404
    queued = ok(ask('ask-queued'))
    assert queued['stages'][0]['status']=='ready' and not queued['stages'][0]['artifact']
    assert contexts[-1]['follow_up']['text']=='保留审核日期'
    assert contexts[-1]['follow_up']['status']=='queued'
    assert queued['stages'][0]['dialogue'][0]['follow_up_status']=='queued'
    assert ok(ask('ask-queued'))==queued and len(contexts)==1
    assert ask('ask-queued','source-b').status_code==409

    entered, release = Event(), Event()
    def execute(context):
        assert context['user_input']['stage_follow_ups']==['保留审核日期']
        entered.set()
        assert release.wait(20)
        return {'artifact':sample('阶段已生成更新稿')}
    runtime.executor = execute
    withdrawn = ok(action(client,owner,queued,'withdraw-source','withdraw_supplement',{'id':'source-b'}))
    ok(ask('ask-withdrawn','source-b'))
    assert contexts[-1]['follow_up']['status']=='withdrawn'
    ready = ok(client.get(ROOT+'/runs/'+run['id'],params=child))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(action,client,owner,ready,'apply-source','execute')
        try:
            assert entered.wait(15)
            ok(ask('ask-running'))
            assert contexts[-1]['follow_up']['status']=='running'
        finally:
            release.set()
        review = ok(future.result(timeout=15))
    processed = ok(ask('ask-processed'))
    assert contexts[-1]['follow_up']['status']=='processed'
    assert contexts[-1]['follow_up']['execution_summary']=='阶段已生成更新稿'
    assert processed['stages'][0]['artifact']==review['stages'][0]['artifact']
    assert [turn['follow_up_status'] for turn in processed['stages'][0]['dialogue']]==['queued','withdrawn','running','processed']
    approved = ok(action(client,owner,processed,'approve-a','approve'))
    other_node = {**owner,'conversation_id':approved['stages'][1]['stage_session_id']}
    assert ask('other-stage',target=other_node,node_id='B').status_code==404
    reloaded = ok(client.get(ROOT,params=child))['runs'][0]
    assert reloaded['stages'][0]['dialogue'][-1]['follow_up_id']=='source-a'

"""Original fixtures through real ASGI/SQLite/OOXML/export; no model claims."""
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
from zipfile import ZipFile

from docx import Document
import pytest

from packages.domain.local_workspaces import canonical, digest
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.planning_schema import PlanningCandidate, ProposalInput, ProposalEdit, ProposalConfirm
from packages.domain.workbench_state import WorkbenchState
from apps.api.config import Settings
from apps.api.main import create_app
from fastapi.testclient import TestClient


def check(value,message='acceptance failed'):
    if not value:raise AssertionError(message)


def ref(v):return {k:v[k] for k in ('id','revision','sha256')}


def candidate():
    return {'title':'原创七类事项任务','summary':'保留来源并交付可核对文件。','constraints':['原件只读，全部任务不覆盖原件。'],
        'nodes':[{'id':'A','title':'核对来源','description':'逐项核对当前材料。','outputs':['核对清单'],'acceptance':['清单能反查原始资料。']},
                 {'id':'B','title':'整理交付','description':'按核对清单整理报告。','depends_on':['A'],'outputs':['报告'],'acceptance':['Word与任务图同源。']}],
        'questions':[{'id':'q-later','text':'后续是否双语？','reason':'用户尚未决定。','category':'DECISION','blocking':False,'affects_node_ids':['B']}]}


@pytest.fixture
def covered(client,tmp_path,request):
    calls=[];evidence=Path(os.environ.get('EEAS_COVERAGE67_EVIDENCE',str(tmp_path/'evidence')))/request.node.name;evidence.mkdir(parents=True,exist_ok=True)
    def call(method,url,body=None,status=200):
        r=client.request(method,url,json=body);value=r.json()
        calls.append({'method':method,'url':url,'request':body,'status':r.status_code,'response':value})
        (evidence/'http.json').write_text(json.dumps({'qualification':'REAL_ASGI_SQLITE_ORIGINAL_FIXTURE_NOT_MODEL_QUALITY','calls':calls},ensure_ascii=False,indent=2))
        check(r.status_code==status,r.text[:1200]);return value
    pid=call('POST','/api/projects',{'name':'七类已确认事项原创项目'},201)['id'];base=f'/api/projects/{pid}'
    folder=tmp_path.parent/(tmp_path.name+'-selected');folder.mkdir();(folder/'任务.md').write_text('逐项承接明确接受事项；原件不可覆盖。')
    bound=call('POST',base+'/local-workspaces',{'idempotency_key':'bind','root':str(folder),'allowed_write_paths':['delivery/','alternate/']},201)
    workspace={k:bound[k] for k in ('id','revision','binding_sha256')}
    materials=call('POST',base+f'/local-workspaces/{bound["id"]}/materials',{'operation_id':'materials','paths':['任务.md']},201)['materials']
    refs=[m['document_ref'] for m in materials]
    d=call('POST',base+'/discovery/sessions',{'operation_id':'start','title':'七类事项','entry_mode':'TASK_FILE','statement':'按原件明确事项并形成可核对任务。','materials':refs,'workspace_ref':workspace},201)
    kinds=['PROBLEM','NEED','REQUIREMENT','CONSTRAINT','RISK','ASSUMPTION','DECISION']
    claims=[{'id':'c'+str(i),'text':'本次确认的'+kind+'原创事项。','category':kind,'origin':'USER_STATEMENT',
        'acceptance':['每项交付必须可核对来源文件与结果SHA。']} for i,kind in enumerate(kinds)]
    d=call('PUT',base+'/discovery/sessions/'+d['id'],{'operation_id':'edit-discovery','expected_revision':d['revision'],
        'candidate':{'title':'七类已确认事项','phase':'SPECIFY','summary':'用户逐条确认七类事项。','claims':claims},'change_reason':'明确完整范围'})
    d=call('POST',base+'/discovery/sessions/'+d['id']+'/confirm',{'operation_id':'confirm-discovery','expected_revision':d['revision'],'session_sha256':d['sha256'],'accepted_claim_ids':[c['id'] for c in claims],'accepted_graph_ids':[],'user_confirmed':True})
    body={'operation_id':'create-v2','prompt':'按明确接受事项形成任务。','materials':refs,'workspace_ref':workspace,'mode':'manual','candidate':candidate(),'discovery_ref':ref(d),'coverage_contract':'ACCEPTED_CLAIMS_V1'}
    v=call('POST',base+'/planning/proposals',body,201)
    yield {'client':client,'call':call,'pid':pid,'base':base,'folder':folder,'workspace':workspace,'discovery':d,'view':v,'create':body,'evidence':evidence}


def edit_body(v,op='save-mappings',*,exclude=True):
    c=v['coverage'];inventory=c['target_inventory'];links=[];exclusions=[]
    for index,row in enumerate(c['accepted_claims']):
        if exclude and index==6:
            exclusions.append({'claim_id':row['claim']['id'],'claim_sha256':row['sha256'],'reason':'该决策只记录历史背景，本次不产生工程任务。'});continue
        target=({'kind':'SHARED_CONSTRAINT','constraint_index':0,'value_sha256':inventory['constraints'][0]['value_sha256'],'scope':'ALL_TASKS'} if index==3 else
            {'kind':'TASK','node_id':'A' if index<3 else 'B','node_sha256':next(n['node_sha256'] for n in inventory['tasks'] if n['node_id']==('A' if index<3 else 'B'))})
        links.append({'claim_id':row['claim']['id'],'claim_sha256':row['sha256'],'targets':[target]})
    return {'operation_id':op,'expected_revision':v['revision'],'candidate':deepcopy(v['payload']['candidate']),'change_reason':'逐条明确承接位置和本次排除。',
        'requirement_coverage':{'expected_basis_sha256':c['basis_sha256'],'claim_links':links,'user_exclusions':exclusions}}


def confirm_body(v,op='confirm-v2'):
    return {'operation_id':op,'expected_revision':v['revision'],'proposal_sha256':v['sha256'],'user_confirmed':True,
        'coverage_sha256':v['coverage']['snapshot_sha256'],'confirmed_exclusion_ids':[r['claim_id'] for r in v['coverage']['user_exclusions']]}


def test_seven_categories_gate_complete_word_graph_harness_same_snapshot_and_replay(covered):
    s=covered;call=s['call'];v=s['view'];base=s['base']+'/planning/proposals/'+v['id']
    check(v['payload']['schema_version']=='eeas.planning-proposal.v2' and len(v['coverage']['missing_claim_ids'])==7)
    check({r['claim']['category'] for r in v['coverage']['accepted_claims']}=={'PROBLEM','NEED','REQUIREMENT','CONSTRAINT','RISK','ASSUMPTION','DECISION'})
    call('POST',base+'/confirm',confirm_body(v),409)
    output={'operation_id':'uncovered-export','proposal_ref':ref(v),'workspace_ref':s['workspace'],'directory':'delivery'}
    call('POST',s['base']+'/planning-delivery/proposals/'+v['id']+'/export',output,409)
    check(call('GET',s['base']+'/planning-delivery/operations/uncovered-export',status=404))
    check(not (s['folder']/'delivery').exists(),'gate created output directory')
    saved=call('PUT',base,edit_body(v));check(saved['coverage']['status']=='STRUCTURALLY_COMPLETE')
    bad=confirm_body(saved,'missing-exclusion-acceptance');bad['confirmed_exclusion_ids']=[];call('POST',base+'/confirm',bad,409)
    frozen=deepcopy(saved['payload']['requirement_coverage']);sha=digest(frozen)
    approved=call('POST',base+'/confirm',confirm_body(saved));check(approved['payload']['confirmation']['coverage_sha256']==sha)
    check(approved['payload']['confirmation']['confirmed_exclusion_ids']==['c6'])
    output.update(operation_id='full-export',proposal_ref=ref(approved))
    exported=call('POST',s['base']+'/planning-delivery/proposals/'+v['id']+'/export',output)
    check(exported['coverage_sha256']==sha and exported['taskgraph']['requirement_coverage_ref']=={'filename':'accepted-items.json','snapshot_sha256':sha})
    network=json.loads(next((s['folder']/f['path']).read_text() for f in exported['harness']['files'] if f['path'].endswith('/task-network.json')))
    context=json.loads(next((s['folder']/f['path']).read_text() for f in exported['harness']['files'] if f['path'].endswith('/accepted-items.json')))
    check(network['coverage_sha256']==sha and context==frozen)
    docx=s['client'].get(exported['report']['artifact']['download_url']).content
    document=Document(BytesIO(docx));text='\n'.join(p.text for p in document.paragraphs)+'\n'+'\n'.join(cell.text for t in document.tables for row in t.rows for cell in row.cells)
    check('已确认事项承接' in text and all(r['claim']['text'] in text for r in frozen['accepted_claims']))
    coverage_tables=[table for table in document.tables if [cell.text for cell in table.rows[0].cells]==['事项编号','已确认内容','承接位置或本次排除理由']]
    check(len(coverage_tables)==1)
    actual_rows=[[cell.text for cell in row.cells] for row in coverage_tables[0].rows[1:]]
    expected_targets=['A','A','A','全任务共享约束 0','B','B','本次用户排除：'+frozen['user_exclusions'][0]['reason']]
    check(actual_rows==[[r['claim']['id'],r['claim']['text'],target] for r,target in zip(frozen['accepted_claims'],expected_targets)])
    check(sha not in text,'default Word report keeps machine digests in its accompanying source snapshot')
    with ZipFile(BytesIO(docx)) as z:check(z.testzip() is None)
    source=s['client'].get(exported['report']['source_snapshot']['download_url']).json()
    check(source['requirement_coverage']==frozen and source['coverage_sha256']==sha)
    pending=s['client'].get(exported['report']['pending_json']['download_url']).json()
    check(pending['pending_count']==1 and pending['requirement_coverage']['snapshot_sha256']==sha)
    prompts=[(s['folder']/f['path']).read_text() for f in exported['harness']['files'] if f['path'].endswith('/task-A.md') or f['path'].endswith('/task-B.md')]
    check(len(prompts)==2 and all('accepted-items.json' in p and sha in p for p in prompts))
    check(all('本次确认的PROBLEM原创事项。' not in p for p in prompts),'full accepted table copied per task')
    (s['evidence']/'actual.docx').write_bytes(docx)
    # Immutable old operation results replay before a new draft/coverage gate.
    changed=deepcopy(approved['payload']['candidate']);changed['nodes'][0]['description']='用户在确认后修改A。'
    later=call('PUT',base,{'operation_id':'later-edit','expected_revision':approved['revision'],'candidate':changed,'change_reason':'修改当前任务'})
    check(later['coverage']['status']=='INCOMPLETE')
    stale_export={'operation_id':'late-old-harness','expected_revision':approved['revision'],'proposal_sha256':approved['sha256'],
        'workspace_ref':s['workspace'],'directory':'delivery'}
    call('POST',base+'/harness/export',stale_export,409)
    ws=s['client'].app.state.local_workspaces
    check(not ws._data(s['pid'],s['workspace']['id'],'planning-exports',digest('late-old-harness')).exists())
    again=call('POST',s['base']+'/planning-delivery/proposals/'+v['id']+'/export',output)
    check(again['manifest']==exported['manifest'])
    check(call('GET',s['base']+'/planning/operations/confirm-v2')['sha256']==approved['sha256'])


def test_partial_node_constraint_invalidations_and_exact_cas(covered):
    s=covered;call=s['call'];base=s['base']+'/planning/proposals/'+s['view']['id'];body=edit_body(s['view'])
    saved=call('PUT',base,body);old_sha=saved['coverage']['snapshot_sha256']
    changed=deepcopy(saved['payload']['candidate']);changed['nodes'][0]['description']='A的新说明'
    v=call('PUT',base,{'operation_id':'A-change','expected_revision':saved['revision'],'candidate':changed,'change_reason':'局部调整A'})
    check({r['claim_id'] for r in v['coverage']['stale_targets']}=={'c0','c1','c2'})
    check(v['coverage']['snapshot_sha256']==old_sha)
    b=edit_body(v,'repair-A');v=call('PUT',base,b);check(v['coverage']['status']=='STRUCTURALLY_COMPLETE')
    changed=deepcopy(v['payload']['candidate']);changed['constraints'][0]='全任务新增只读约束。'
    v=call('PUT',base,{'operation_id':'constraint-change','expected_revision':v['revision'],'candidate':changed,'change_reason':'更改共享约束'})
    check({r['claim_id'] for r in v['coverage']['stale_targets']}=={'c3'})
    call('POST',base+'/confirm',confirm_body(v),409)
    stale=deepcopy(body);stale['operation_id']='new-key-old-CAS';call('PUT',base,stale,409)
    repair=edit_body(v,'repair-constraint');repair['requirement_coverage']['expected_basis_sha256']='0'*64;call('PUT',base,repair,409)
    check(call('GET',base)['revision']==v['revision'])


@pytest.mark.parametrize('fault',['foreign-claim','claim-hash','node-hash','foreign-node','index','blank-exclusion','duplicate','duplicate-target','model-exclude'])
def test_forged_mapping_or_exclusion_never_commits(covered,fault):
    s=covered;body=edit_body(s['view']);links=body['requirement_coverage']['claim_links']
    if fault=='foreign-claim':links[0]['claim_id']='foreign'
    if fault=='claim-hash':links[0]['claim_sha256']='0'*64
    if fault=='node-hash':links[0]['targets'][0]['node_sha256']='0'*64
    if fault=='foreign-node':links[0]['targets'][0]['node_id']='foreign'
    if fault=='index':links[3]['targets'][0]['constraint_index']=99
    if fault=='blank-exclusion':body['requirement_coverage']['user_exclusions'][0]['reason']=' '
    if fault=='duplicate':links.append(deepcopy(links[0]))
    if fault=='duplicate-target':links[0]['targets'].append({**links[0]['targets'][0],'node_sha256':'0'*64})
    if fault=='model-exclude':body['requirement_coverage']['user_exclusions'][0]['origin']='MODEL_SUGGESTION'
    s['call']('PUT',s['base']+'/planning/proposals/'+s['view']['id'],body,422)
    check(s['call']('GET',s['base']+'/planning/proposals/'+s['view']['id'])['revision']==1)
    check(s['call']('GET',s['base']+'/planning/operations/'+body['operation_id'],status=404))


def test_v1_exact_hashes_receipts_and_candidate_defaults_unchanged(covered):
    s=covered;call=s['call'];body=deepcopy(s['create']);body.pop('coverage_contract');body.pop('discovery_ref');body['operation_id']='legacy-create'
    v=call('POST',s['base']+'/planning/proposals',body,201);check('coverage' not in v and 'requirement_coverage' not in v['payload'])
    p=ProposalInput.model_validate(body);oldhash=digest(p.model_dump(exclude={'operation_id','coverage_contract'}))
    with s['client'].app.state.store.connect() as db:
        check(db.execute('SELECT request_sha256 FROM planning_proposal_operations WHERE project_id=? AND operation_id=?',(s['pid'],body['operation_id'])).fetchone()[0]==oldhash)
    edit={'operation_id':'legacy-edit','expected_revision':v['revision'],'candidate':v['payload']['candidate'],'change_reason':'用户保留完整原候选'}
    v=call('PUT',s['base']+'/planning/proposals/'+v['id'],edit)
    p=ProposalEdit.model_validate(edit);expected=digest({'proposal_id':v['id'],**p.model_dump(exclude={'operation_id','manual_review','requirement_coverage'})})
    confirm={'operation_id':'legacy-confirm','expected_revision':v['revision'],'proposal_sha256':v['sha256'],'user_confirmed':True}
    approved=call('POST',s['base']+'/planning/proposals/'+v['id']+'/confirm',confirm)
    with s['client'].app.state.store.connect() as db:
        rows={r['operation_id']:r['request_sha256'] for r in db.execute('SELECT * FROM planning_proposal_operations WHERE project_id=?',(s['pid'],))}
    check(rows['legacy-edit']==expected)
    p=ProposalConfirm.model_validate(confirm);check(rows['legacy-confirm']==digest({'proposal_id':v['id'],**p.model_dump(exclude={'operation_id','coverage_sha256','confirmed_exclusion_ids'})}))
    check('coverage_sha256' not in approved['payload']['confirmation'])
    check(call('POST',s['base']+'/planning/proposals',body,201)==call('GET',s['base']+'/planning/operations/legacy-create'))


def test_typed_local_coverage_recovery_restart_and_failed_input_preserved(covered):
    s=covered;call=s['call'];v=s['view'];draft=edit_body(v)['requirement_coverage'];draft['user_exclusions'][0]['reason']=''
    key={'project_id':s['pid'],'kind':'proposal','object_id':v['id'],'revision':v['revision'],'sha256':v['sha256']}
    operation={'kind':'SAVE','operation_id':'original-save','project_id':s['pid'],'proposal_ref':ref(v),'request_sha256':'1'*64,
        'candidate_sha256':digest(v['payload']['candidate']),'coverage_draft_sha256':digest(draft),'coverage_sha256':None,'resolutions_sha256':None,'state':'UNKNOWN','result_ref':None}
    body={'key':key,'operation_id':'save-local','expected_revision':0,'fields':{'requirementCoverage':draft,'coverageOperation':operation},'view':{},'dirty':True}
    saved=call('POST',s['base']+'/workbench-state/save',body)
    # Real restart, same SQLite. No interpretation as approval or automatic POST.
    data=s['client'].app.state.settings.data_dir
    app=create_app(Settings(data,port=8765))
    with TestClient(app,base_url=app.state.settings.origin) as restarted:
        token=restarted.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'}).json()['csrf_token'];restarted.headers.update({'Origin':app.state.settings.origin,'X-EEAS-CSRF':token})
        loaded=restarted.post(s['base']+'/workbench-state/load',json={'key':key})
        check(loaded.status_code==200 and loaded.json()['fields']==body['fields'])
    for name,replace in [('unknown',{'extra':True}),('scope',{'project_id':'foreign'}),('bool-revision',{'proposal_ref':{**ref(v),'revision':True}})]:
        invalid=deepcopy(body);invalid['operation_id']='bad-'+name;invalid['expected_revision']=saved['state_revision'];invalid['fields']['coverageOperation'].update(replace)
        call('POST',s['base']+'/workbench-state/save',invalid,422)
    loaded=call('POST',s['base']+'/workbench-state/load',{'key':key});check(loaded['fields']==body['fields'])
    business=edit_body(v);business['requirement_coverage']=draft;call('PUT',s['base']+'/planning/proposals/'+v['id'],business,422)
    check(call('GET',s['base']+'/planning/proposals/'+v['id'])['coverage']['status']=='INCOMPLETE')
    confirm_metadata={**operation,'kind':'CONFIRM','coverage_sha256':v['coverage']['snapshot_sha256'],
        'resolutions_sha256':digest([]),'coverage_draft_sha256':digest(draft)}
    body.update(operation_id='confirm-local',expected_revision=saved['state_revision'],fields={'requirementCoverage':draft,'coverageOperation':confirm_metadata})
    call('POST',s['base']+'/workbench-state/save',body)
    check(call('POST',s['base']+'/workbench-state/load',{'key':key})['fields']==body['fields'])


def test_one_original_cli_propose_wrapper_no_extra_loop_or_model_exclusion(covered):
    """Only the external model wire is fixture; original SDK/transport/schema run."""
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    import hashlib
    import threading
    from packages.adapters.opencode_planning import OpenCodePlanningTransport
    from packages.adapters.opencode_profile import CLI_SHA,VERSION
    s=covered;call=s['call'];requests=[]
    output={'candidate':candidate(),'claim_links':[{'claim_id':'c'+str(i),'targets':[{'kind':'TASK','node_id':'A'}]} for i in range(7)]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            events=[{'id':'coverage67','object':'chat.completion.chunk','created':0,'model':'coverage67-fixture',
                'choices':[{'index':0,'delta':{'role':'assistant','tool_calls':[{'index':0,'id':'only_propose','type':'function',
                    'function':{'name':'StructuredOutput','arguments':canonical(output)}}]},'finish_reason':None}]},
                {'id':'coverage67','object':'chat.completion.chunk','created':0,'model':'coverage67-fixture',
                 'choices':[{'index':0,'delta':{},'finish_reason':'tool_calls'}],'usage':{'prompt_tokens':40,'completion_tokens':80,'total_tokens':120}}]
            raw=(''.join('data: '+canonical(e)+'\n\n' for e in events)+'data: [DONE]\n\n').encode()
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        cli=Path(__file__).resolve().parents[1]/'var/opencode-ui-offline-guard/opencode'
        check(cli.is_file(),'Pinned original CLI missing')
        check(hashlib.sha256(cli.read_bytes()).hexdigest()==CLI_SHA[os.name],'Original CLI SHA differs')
        registry=s['client'].app.state.provider_settings
        executable=registry.path('runtime',VERSION,'opencode.exe' if os.name=='nt' else 'opencode');executable.parent.mkdir(parents=True,exist_ok=True)
        # One original binary in the D-only isolated fixture, not an environment
        # install; Linux ext4 -> D cannot hardlink across the two filesystems.
        import shutil
        shutil.copy2(cli,executable)
        profile=call('POST','/api/planning/providers',{'operation_id':'wire-profile','id':'coverage67-fixture',
            'provider_url':f'http://127.0.0.1:{server.server_port}/v1','model_id':'coverage67-fixture','api_key':'local-protocol-fixture-not-account'},201)['profile_ref']
        check(isinstance(s['client'].app.state.planning_proposals.transport,OpenCodePlanningTransport),'Original transport replaced')
        body={'operation_id':'one-native-wrapper','expected_revision':s['discovery']['revision'],'provider_profile_ref':profile,
            'budget':{'max_calls':1,'timeout_seconds':60,'max_output_tokens':8192},'coverage_contract':'ACCEPTED_CLAIMS_V1'}
        plan=call('POST',s['base']+'/discovery/sessions/'+s['discovery']['id']+'/plan',body)
        v=plan['proposal'];check(v['payload']['schema_version']=='eeas.planning-proposal.v2' and v['coverage']['status']=='STRUCTURALLY_COMPLETE')
        check(len(requests)==1,'More than one external request')
        schema=requests[0]['tools'][0]['function']['parameters'];check(set(schema['properties'])=={'candidate','claim_links'})
        check('user_exclusions' not in schema['properties'] and not v['coverage']['user_exclusions'])
        check(all(row['targets'][0]['node_sha256']==digest(v['payload']['candidate']['nodes'][0]) for row in v['coverage']['claim_links']))
        replay=call('POST',s['base']+'/discovery/sessions/'+s['discovery']['id']+'/plan',body)
        check(replay==plan and len(requests)==1)
        execution=v['payload']['execution'];check(execution['status']=='RETURNED' and execution['usage']['rpc_attempts']==1)
        with s['client'].app.state.store.connect() as db:
            check(db.execute('SELECT count(*) FROM agent_executions WHERE project_id=?',(s['pid'],)).fetchone()[0]==1)
            check(db.execute('SELECT count(*) FROM task_runs').fetchone()[0]==0)
            native=db.execute('SELECT e.request,s.scope FROM agent_executions e JOIN agent_scopes s ON e.id=s.execution_id WHERE e.id=?',(execution['execution_id'],)).fetchone()
        admitted,scope=json.loads(native['request']),json.loads(native['scope'])
        check(scope['permission_policy']=='DENY_ALL_NATIVE_TOOLS_EXCEPT_STRUCTURED_OUTPUT' and scope['budget']==body['budget'])
        check(scope['source_receipt_sha256']==admitted['source_receipt']['sha256']==execution['source_receipt_sha256'])
        from packages.domain.planning_coverage import prompt_context
        snapshot=v['payload']['requirement_coverage'];sources=admitted['source_receipt']
        host=json.loads(admitted['prompt'].rsplit('\n',1)[1])
        check(host==prompt_context(snapshot,sources) and 'accepted_text_source' in host)
        check(all(set(row)=={'claim_id','category','claim_sha256'} for row in host['accepted_items']))
        check(all(row['claim']['text'] not in admitted['prompt'] for row in snapshot['accepted_claims']),'HOST repeats accepted body')
        check(all(row['claim']['text'] in canonical(requests[0]) for row in snapshot['accepted_claims']),'Actual model lost accepted text')
        baseline=next(d for d in sources['documents'] if d['source'].startswith('discovery-confirmed-baseline:'))
        check(host['accepted_text_source']=={k:baseline[k] for k in ('document_id','revision','raw_sha256','parsed_sha256')})
        for fault in ('partial','omitted','raw-sha','foreign-basis'):
            altered=deepcopy(sources);doc=next(d for d in altered['documents'] if d['document_id']==baseline['document_id'])
            cov=next(r for r in altered['coverage'] if r['document_id']==doc['document_id'])
            if fault=='partial':cov['text_coverage']='SELECTED_CHUNKS'
            if fault=='omitted':cov['omitted_chunk_indexes']=[0]
            if fault=='raw-sha':doc['raw_sha256']='0'*64
            if fault=='foreign-basis':
                content=json.loads(doc['text_or_selected_chunks'].split('```json\n',1)[1].rsplit('\n```',1)[0]);content['discovery_ref']['sha256']='0'*64
                doc['text_or_selected_chunks']='# 明确接受的需求与决议\n\n```json\n'+canonical(content)+'\n```\n'
                doc['raw_sha256']=doc['parsed_sha256']=hashlib.sha256(doc['text_or_selected_chunks'].encode()).hexdigest()
            altered['sha256']=digest({k:altered[k] for k in ('documents','coverage')})
            fallback=prompt_context(snapshot,altered)
            check(fallback['accepted_items']==snapshot['accepted_claims'] and 'accepted_text_source' not in fallback,'Unproven baseline dropped required text')
        check(prompt_context(snapshot,s['view']['payload']['source_receipt'])['accepted_items']==snapshot['accepted_claims'],'Direct v2 lost its only accepted context')
        over=deepcopy(admitted);over['prompt']='x'*128001
        try:OpenCodePlanningTransport._validate_request(over)
        except ValueError:pass
        else:raise AssertionError('Original authored-prompt budget was enlarged')
        safe={'qualification':'ACTUAL_ORIGINAL_CLI_EXTERNAL_WIRE_ONLY','external_requests':len(requests),'schema':schema,
              'result':v,'native_windows':'NOT_RUN','semantic_model_quality':'NOT_QUALIFIED'}
        (s['evidence']/'original-cli.json').write_text(json.dumps(safe,ensure_ascii=False,indent=2))
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


def test_manual_hunk_and_coverage_save_share_original_cas_and_durable_body(covered):
    s=covered;call=s['call'];v=s['view'];base=s['base']+'/planning/proposals/'+v['id']
    v=call('PUT',base,edit_body(v));v=call('POST',base+'/confirm',confirm_body(v))
    export={'operation_id':'harness-before','expected_revision':v['revision'],'proposal_sha256':v['sha256'],'workspace_ref':s['workspace'],'directory':'delivery'}
    before=call('POST',base+'/harness/export',export)
    item=next(f for f in before['files'] if f['path'].endswith('/task-A.md'))
    original=(s['folder']/item['path']).read_bytes();marker='人工补充：输入清单需包含来源核对批次。'
    (s['folder']/item['path']).write_bytes(original+('\n'+marker+'\n').encode())
    gate=call('POST',base+'/harness/export',{**export,'operation_id':'preflight-changed'},409)
    check(gate['code']=='MANUAL_REVIEW_REQUIRED')
    request={'operation_id':'capture-manual','target_proposal_ref':ref(v),'workspace_ref':s['workspace'],
        'files':[{k:f[k] for k in ('export_ref','path','basis_sha256','edited_sha256')} for f in gate['detail']['manual_changes']['files']]}
    captured=call('POST',base+'/harness/manual-changes',request,201)
    hunk=captured['snapshot']['files'][0]['hunks'][0];check(hunk['keep_text']==marker)
    body=edit_body(v,'joint-save');node=body['candidate']['nodes'][0];old_inputs=deepcopy(node['inputs']);node['inputs'].append(marker)
    for row in body['requirement_coverage']['claim_links']:
        for t in row['targets']:
            if t['kind']=='TASK' and t['node_id']=='A':t['node_sha256']=digest(node)
    body['manual_review']={'change_ref':captured['capture_ref'],'decisions':[{'hunk_id':hunk['id'],'hunk_sha256':hunk['sha256'],'mode':'KEEP',
        'target':{'node_id':'A','field':'inputs','edit_action':'ADD','before_value_sha256':digest(old_inputs)},'text':marker,'reason':'用户明确采用原保存文件中的补充。'}]}
    frozen=ProposalEdit.model_validate(body).model_dump(exclude={'operation_id'})
    local={'operation_id':body['operation_id'],**frozen,'request_sha256':digest({'proposal_id':v['id'],**frozen}),'state':'UNKNOWN','result_ref':None}
    key={'project_id':s['pid'],'kind':'proposal','object_id':v['id'],'revision':v['revision'],'sha256':v['sha256']}
    call('POST',s['base']+'/workbench-state/save',{'key':key,'operation_id':'persist-joint','expected_revision':0,'fields':{'manualSave':local},'view':{},'dirty':True})
    check(call('POST',s['base']+'/workbench-state/load',{'key':key})['fields']['manualSave']==local)
    revised=call('PUT',base,body)
    check(revised['coverage']['status']=='STRUCTURALLY_COMPLETE' and revised['payload']['manual_inheritance'][-1]['capture_ref']==captured['capture_ref'])
    check(call('GET',s['base']+'/planning/operations/joint-save')['sha256']==revised['sha256'])
    check(call('PUT',base,body)['sha256']==revised['sha256'],'Original joint save replay changed')
    approved=call('POST',base+'/confirm',confirm_body(revised,'confirm-joint'))
    new=call('POST',base+'/harness/export',{**export,'operation_id':'harness-after','expected_revision':approved['revision'],'proposal_sha256':approved['sha256']})
    check(new['coverage_sha256']==approved['coverage']['snapshot_sha256'])
    check((s['folder']/item['path']).read_bytes()==original+('\n'+marker+'\n').encode(),'Original manual bytes overwritten')
    observed=call('GET',base+'/harness/manual-changes/'+captured['capture_ref']['id']+'?revision=1&sha256='+captured['capture_ref']['sha256'])
    check(observed['current']['status']=='APPLIED')

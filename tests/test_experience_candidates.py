"""AUTHORED_NOT_RUN: actual Store/private snapshots; original ACK is UNIT metadata.

No model, OpenCode, capability import/enable, process, network or user database.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from packages.agent_sessions import AgentSessions
from packages.agent_sessions.experience_candidates import (ExperienceCandidates,ExperiencePropose,ExperienceReview,ToolCheck)
from packages.agent_sessions.work_requirements import WorkRequirementScope,_root
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.local_workspaces import LocalWorkspaces,WorkspaceInput
from packages.domain.store import Store,Conflict,InvalidInput,NotFound
from packages.domain.tasks import digest
from test_session_diffs import app_for


@pytest.fixture
def experience(tmp_path):
    data=tmp_path/'private';data.mkdir();folder=tmp_path/'work';folder.mkdir()
    store=Store(data/'store.sqlite3');pid=store.create_project('Experience source','Explicit original UNIT source')['id']
    ws=LocalWorkspaces(store,data);binding=ws.bind(pid,WorkspaceInput(idempotency_key='workspace',root=str(folder)))
    workspace={k:binding[k] for k in ['id','revision','binding_sha256']};sid='e'*32
    caps=NativeCapabilities(store,data);sessions=AgentSessions(store,data,provider_registry=None,workspaces=ws,capabilities=caps)
    manifest={'id':sid,'project_id':pid,'request':{'workspace_ref':workspace},'directory_identity':binding['root_identity']}
    sessions._write(sessions._path(pid,sid,'manifest.json'),manifest)
    reference={'id':sid,'revision':1,'sha256':digest(manifest)}
    sessions._write(sessions._path(pid,sid,'state.json'),{'generation':1,'owner_status':'DISCONNECTED'})
    root='original_work';request={'session_ref':reference,'generation':1,'raw_text':'从这次失败提炼可复核方法，保留人工修改。'}
    sessions._claim(pid,sid,root,'MESSAGE',request)
    sessions._finish(pid,sid,root,'UNKNOWN_OUTCOME',generation=1,original_session_id='ses_original',native_message_id='msg_original')
    scope={'session_ref':reference,'generation':1,'workspace_ref':workspace,'root_operation_id':root}
    basis=_root(sessions,pid,sid,WorkRequirementScope(**scope))
    from packages.domain.context_graph import ContextGraph
    sessions.context_authority.graph=ContextGraph(store,lambda *_:pytest.fail('No source bodies'),lambda *_:pytest.fail('No conversation bodies'))
    sessions.context_authority.begin(manifest,{'session_id':'ses_original'},loaded_existing=False)
    return {'data':data,'folder':folder,'store':store,'pid':pid,'sid':sid,'sessions':sessions,'caps':caps,
            'scope':scope,'root':basis,'candidates':ExperienceCandidates(sessions)}


def proposal(s,operation='candidate_one',**changes):
    return {**s['scope'],'operation_id':operation,'root_sha256':s['root']['sha256'],'title':'保留人工数据后再修复',
        'observation':'原任务结果未知，不能假设命令成功。','proposed_use':'先定位原执行证据，再提出有界修复。','limitations':'仅作为候选；未授权执行或覆盖。',
        'proposal':{'kind':'SKILL','id':'retain-original-work','content':'---\nname: retain-original-work\ndescription: Keep original work evidence\n---\nRead original evidence. Never infer success from an unknown result.\n'},'sources':[],**changes}
def propose(s,body=None):return s['candidates'].propose(s['pid'],s['sid'],body or proposal(s))
def review(s,candidate,operation='review_one',**changes):
    return {'operation_id':operation,'candidate_ref':candidate['record_ref'],'review_confirmed':True,'statement':'已独立检查原任务证据、限定用途和这两项固定用例。',
        'cases':[{'kind':'SKILL','id':'positive','label':'保留原人工稿','purpose':'POSITIVE','prompt':'如何处理已有人工改动？','required_text':['原稿'],'forbidden_text':[]},
                 {'kind':'SKILL','id':'negative','label':'未知结果不当成功','purpose':'NEGATIVE','prompt':'原操作结果未知。','required_text':[],'forbidden_text':['已经成功']}],
        'allowed_effects':['PROCESS_START'],'max_model_calls':2,'max_output_tokens':1024,'timeout_seconds':60,'process_boundary_acknowledged':True,**changes}


def test_proposal_preserves_unknown_source_and_never_registers_or_enables_a_capability(experience,monkeypatch):
    s=experience;before=s['caps']._index()
    monkeypatch.setattr(s['caps'],'import_skill',lambda *_:pytest.fail('Proposal is not an import'))
    r=propose(s);assert r['root_status']=='UNKNOWN_OUTCOME' and r['enabled'] is False and r['model_calls']==r['commands_started']==0
    assert r['record_ref']['sha256']==digest({k:v for k,v in r.items() if k not in ['record_ref','metadata_only','enabled','configuration_registered','model_calls','commands_started','permission_granted','qualification','automatic_replay']})
    assert s['caps']._index()==before and not (s['data']/'native-capabilities'/'skill-files').exists()


def test_saved_unicode_quote_is_exact_and_remains_historical_when_live_file_changes(experience):
    s=experience;p=s['folder']/'notes.txt';text='甲😀乙\r\n原证据';p.write_bytes(text.encode());source=s['sessions'].workspaces.read_saved_snapshot(s['pid'],s['scope']['workspace_ref']['id'],'notes.txt')['source_ref'];body=proposal(s,sources=[{'source':source,'char_start':1,'char_end':2,'quote_sha256':hashlib.sha256('😀'.encode()).hexdigest()}]);r=propose(s,body);p.write_text('已修改的人工稿');assert s['candidates'].candidate(s['pid'],s['sid'],r['record_ref'])['sources'][0]['quote']=='😀';assert p.read_text()=='已修改的人工稿'


def test_wrong_quote_hash_or_source_binding_cannot_become_a_candidate(experience):
    s=experience;p=s['folder']/'notes.txt';p.write_text('原句');source=s['sessions'].workspaces.read_saved_snapshot(s['pid'],s['scope']['workspace_ref']['id'],'notes.txt')['source_ref'];body=proposal(s,sources=[{'source':source,'char_start':0,'char_end':2,'quote_sha256':'0'*64}]);
    with pytest.raises(Conflict):propose(s,body)
    assert s['candidates'].listing(s['pid'],s['sid'])['total']==0
    body['sources'][0]['source']['binding_sha256']='f'*64
    with pytest.raises(ValidationError):ExperiencePropose(**body)


def test_changed_root_or_generation_rejects_new_candidate_without_a_native_call(experience,monkeypatch):
    s=experience;monkeypatch.setattr(s['sessions'],'_http',lambda *_:pytest.fail('No native calls'))
    for changes in [{'root_sha256':'f'*64},{'generation':2}]:
        with pytest.raises(Conflict):propose(s,proposal(s,**changes))
    assert s['candidates'].listing(s['pid'],s['sid'])['total']==0


def test_exact_repeated_proposal_returns_original_bytes_after_owner_generation_changed(experience):
    s=experience;b=proposal(s);r=propose(s,b);s['sessions']._write(s['sessions']._path(s['pid'],s['sid'],'state.json'),{'generation':2},replace=True)
    assert propose(s,b)==r
    with pytest.raises(Conflict):propose(s,{**b,'limitations':'different'})


def test_candidate_ref_must_match_the_exact_task_owner_and_hash(experience):
    s=experience;r=propose(s)
    with pytest.raises(NotFound):s['candidates'].candidate(s['pid'],s['sid'],{**r['record_ref'],'sha256':'0'*64})
    with pytest.raises(NotFound):s['candidates'].candidate(s['pid'],'d'*32,r['record_ref'])


def test_review_is_a_separate_fixed_positive_and_negative_plan_not_validation_or_enable(experience):
    s=experience;c=propose(s);r=s['candidates'].review(s['pid'],s['sid'],review(s,c));assert r['validation_status']=='FIXED_PLAN_NOT_EXECUTED';assert r['activation_authorized'] is False and r['configuration_registered'] is False
    assert s['caps']._index()['skills']=={}
    assert s['candidates'].saved_record(s['pid'],s['sid'],r['record_ref'],'REVIEW')==r


@pytest.mark.parametrize('change',[{'review_confirmed':False},{'process_boundary_acknowledged':False},{'max_model_calls':0},{'allowed_effects':[]}])
def test_review_rejects_missing_human_confirmation_process_boundary_or_case_budget(experience,change):
    s=experience;c=propose(s)
    with pytest.raises((ValidationError,InvalidInput)):s['candidates'].review(s['pid'],s['sid'],review(s,c,**change))


def test_review_rejects_a_self_asserted_pass_or_same_kind_without_negative_case(experience):
    s=experience;c=propose(s);b=review(s,c);b['cases'][1]['purpose']='POSITIVE'
    with pytest.raises(ValidationError):ExperienceReview(**b)
    b=review(s,c);b['qualified']=True
    with pytest.raises(ValidationError):ExperienceReview(**b)


def test_repeat_review_cannot_change_fixed_expectations_under_its_original_number(experience):
    s=experience;c=propose(s);b=review(s,c);r=s['candidates'].review(s['pid'],s['sid'],b)
    assert s['candidates'].review(s['pid'],s['sid'],b)==r
    b['cases'][0]['prompt']='changed'
    with pytest.raises(Conflict):s['candidates'].review(s['pid'],s['sid'],b)


def test_corrupt_index_is_not_treated_as_empty_or_overwritten(experience):
    s=experience;c=propose(s);path=s['candidates']._path(s['pid'],s['sid'],'index.json');s['sessions']._write(path,{'schema':'bad'},replace=True);before=path.read_bytes()
    with pytest.raises(Conflict):propose(s,proposal(s,'another'))
    assert path.read_bytes()==before


def test_explicit_same_operation_recovers_an_interrupted_record_without_rereading_changed_sources(experience,monkeypatch):
    s=experience;c=propose(s);path=s['candidates']._path(s['pid'],s['sid'],'index.json');s['sessions']._write(path,{'schema':'eeas.task-experience-candidate.v1','revision':0,'candidates':[],'records':[]},replace=True)
    monkeypatch.setattr(s['candidates'],'_quotes',lambda *_:pytest.fail('Use original immutable intent'))
    assert propose(s)==c and s['candidates'].listing(s['pid'],s['sid'])['total']==1


def test_32_candidate_capacity_preserves_every_original_and_rejects_new_content(experience):
    s=experience
    for i in range(32):propose(s,proposal(s,'candidate_'+str(i)))
    with pytest.raises(Conflict):propose(s,proposal(s,'candidate_33'))
    assert s['candidates'].listing(s['pid'],s['sid'])['total']==32
    assert s['candidates'].listing(s['pid'],s['sid'],24)['entries'][-1]['candidate_ref']['id']=='candidate_0'


def test_concurrent_candidates_append_under_one_store_transaction(experience):
    s=experience
    with ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(lambda n:propose(s,proposal(s,'parallel_'+str(n))),range(2)))
    assert s['candidates'].listing(s['pid'],s['sid'])['total']==2


def test_read_only_lookup_never_revalidates_current_files_or_connects_to_an_owner(experience,monkeypatch):
    s=experience;c=propose(s);monkeypatch.setattr(s['sessions'].workspaces,'get',lambda *_:pytest.fail('Read original metadata only'))
    monkeypatch.setattr(s['sessions'],'inspect',lambda *_:pytest.fail('Do not load original session'))
    assert s['candidates'].operation(s['pid'],s['sid'],c['operation_id'],c['request_sha256'])==c
    assert s['candidates'].candidate(s['pid'],s['sid'],c['record_ref'])==c


def test_http_candidate_and_review_reuse_existing_session_routes_without_business_execution(experience):
    s=experience;client=TestClient(app_for(s['sessions']));root=f"/api/projects/{s['pid']}/agent-sessions/{s['sid']}/experience";r=client.post(root+'/candidates',json=proposal(s));assert r.status_code==200;c=r.json();rr=client.post(root+'/reviews',json=review(s,c));assert rr.status_code==200;assert client.get(root+'/candidates').json()['total']==1;assert client.get(root+'/candidates/'+c['operation_id'],params={'sha256':c['record_ref']['sha256']}).json()['record_ref']==c['record_ref']


def test_mcp_expected_result_accepts_structured_float_data_but_rejects_nan_or_missing_error_boolean():
    value={'kind':'MCP_TOOL','id':'one','label':'actual expected structured data','purpose':'POSITIVE','tool_name':'observe','input':{'n':1e-7},'expected_result':{'content':[],'structuredContent':{'n':0.0},'isError':False}}
    assert ToolCheck(**value).input=={'n':1e-7}
    with pytest.raises(ValidationError):ToolCheck(**{**value,'input':{'n':float('nan')}})
    with pytest.raises(ValidationError):ToolCheck(**{**value,'expected_result':{'content':[],'isError':0}})


@pytest.mark.parametrize('field', ['input', 'expected_result'])
@pytest.mark.parametrize('number', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_nested_tool_checks_are_rejected_before_review(field, number):
    value={'kind':'MCP_TOOL','id':'one','label':'fixed check','purpose':'POSITIVE',
        'tool_name':'observe','input':{},'expected_result':{'content':[],'structuredContent':None,'isError':False}}
    if field=='input':value[field]={'nested':[{'number':number}]}
    else:value[field]['structuredContent']={'nested':[{'number':number}]}
    with pytest.raises(ValidationError):ToolCheck(**value)

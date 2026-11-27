"""AUTHORED_NOT_RUN. Actual private journal/bindings; UNIT model boundary except
one explicitly named original-CLI scene, whose external model alone is replaced.
No runtime or business qualification is claimed by authoring these cases.
"""
import asyncio
from copy import deepcopy
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import pytest
from packages.agent_sessions.experience_candidates import ExperienceCandidates
from packages.agent_sessions.experience_skill_validation import ExperienceSkillValidation,_PrivateSkill
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from test_experience_candidates import experience,proposal,propose,review
from test_agent_sessions import ModelWire,owner,tool


def make_skill(s,*,profile=None,review_timeout=None):
    candidates=ExperienceCandidates(s['sessions']);s['candidates']=candidates
    c=propose(s);r=candidates.review(s['pid'],s['sid'],review(s,c,max_model_calls=4,**({'timeout_seconds':review_timeout} if review_timeout is not None else {})))
    v=ExperienceSkillValidation(candidates);s['sessions'].experience_validation=v
    b={'operation_id':'skill_validation','candidate_ref':c['record_ref'],'review_ref':r['record_ref'],
       'provider_profile_ref':profile or {'id':'selected','revision':1,'sha256':'a'*64},
       'validation_confirmed':True,'process_boundary_acknowledged':True}
    return v,b,c,r


def prepare_unit(s,monkeypatch):
    v,b,c,r=make_skill(s);s['sessions'].providers=SimpleNamespace(resolve=lambda _:SimpleNamespace(auth_mode='API_KEY'))
    captured=[]
    async def no_sdk(pid,sid,body,request_sha,case,plan,binding,remaining,progress):
        captured.append(deepcopy(binding));progress.append({'id':case['id'],'status':'FAIL','qualification':'UNIT_NO_SDK_EXECUTION'})
    monkeypatch.setattr(v,'_case',no_sdk)
    result=asyncio.run(v.validate(s['pid'],s['sid'],b))
    return v,b,c,r,result,captured


def reopen_budget(v,s,b):
    value=v._budget(s['pid'],s['sid'],b['operation_id']);value['closed']=False;value['expires_at']=(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat()
    s['sessions']._write(v._path(s['pid'],s['sid'],b['operation_id'],'budget.json'),value,replace=True)


def manifest_for(v,s,b,r,binding):
    case=r['request']['cases'][0];admission={'source_session_id':s['sid'],'request_sha256':digest(b),'case_id':case['id'],
        'binding_sha256':binding['sha256'],'max_model_calls':4,'max_output_tokens':1024,'ttl_seconds':60,'timeout_seconds':60,
        'message_operation_id':'fixed_case','raw_text':'Selected Skill: retain-original-work\nFixed case:\n'+case['prompt']}
    s['sessions']._write(v._path(s['pid'],s['sid'],b['operation_id'],'admissions',digest(case['id'])+'.json'),admission)
    ref={'source_session_id':s['sid'],'operation_id':b['operation_id'],'request_sha256':digest(b),'case_id':case['id'],'admission_sha256':digest(admission)}
    request={'provider_profile_ref':b['provider_profile_ref'],'workspace_ref':None,'workflow_ref':None,'workflow_provider_refs':[],
        'allowed_effects':['PROCESS_START'],'command_allowlist':[],'mcp_tool_allowlist':[],'mcp_tool_selection':None,
        'max_calls':4,'max_output_tokens':1024,'network_mode':'AS_CONFIGURED','ttl_seconds':60,'capability_binding_sha256':binding['sha256']}
    return {'id':'d'*32,'project_id':s['pid'],'request':request,'experience_validation_admission':ref},admission


def test_private_candidate_skill_bytes_are_registered_only_in_private_binding(experience,monkeypatch):
    s=experience;before=s['caps']._index();v,b,c,r,result,bindings=prepare_unit(s,monkeypatch)
    assert result['status']=='FAIL' and result['global_configuration_changed'] is False and result['model_calls_reserved']==0
    assert s['caps']._index()==before and len(bindings)==2
    binding=bindings[0];paths=binding['value']['native_config_fragment']['skills']['paths'];assert len(paths)==1
    assert binding['value']['native_config_fragment']['mcp']=={}
    assert (v._path(s['pid'],s['sid'],b['operation_id'],'binding','skill-files','retain-original-work','0000000001','SKILL.md')).read_text()==c['request']['proposal']['content']


def test_private_binding_uses_exact_original_candidate_without_enabled_global_skills(experience,monkeypatch):
    s=experience;v,b,c,r,_,bindings=prepare_unit(s,monkeypatch);reopen_budget(v,s,b);manifest,_=manifest_for(v,s,b,r,bindings[0])
    monkeypatch.setattr(s['caps'],'native_binding_snapshot',lambda **_:pytest.fail('Private tests do not import global capabilities'))
    assert v.binding(manifest)==bindings[0]


def test_modified_skill_source_fails_private_binding_before_another_model_call(experience,monkeypatch):
    s=experience;v,b,c,r,_,bindings=prepare_unit(s,monkeypatch);reopen_budget(v,s,b);manifest,_=manifest_for(v,s,b,r,bindings[0]);view=v.capabilities(manifest)
    path=view._path('skill-files','retain-original-work','0000000001','SKILL.md');path.write_text('changed unreviewed instructions')
    with pytest.raises(Conflict):v.binding(manifest)


@pytest.mark.parametrize('change',[{'workspace_ref':{'id':'b'*64,'revision':1,'binding_sha256':'c'*64}},
    {'allowed_effects':['PROCESS_START','WORKSPACE_WRITE']},{'command_allowlist':['echo unreviewed']},
    {'mcp_tool_allowlist':['another_tool']},{'max_calls':16},{'provider_profile_ref':{'id':'other','revision':1,'sha256':'d'*64}}])
def test_private_admission_rejects_broader_workspace_effects_models_or_tools(experience,monkeypatch,change):
    s=experience;v,b,c,r,_,bindings=prepare_unit(s,monkeypatch);reopen_budget(v,s,b);manifest,_=manifest_for(v,s,b,r,bindings[0]);manifest['request'].update(change)
    with pytest.raises(Conflict):v.binding(manifest)


def test_only_exact_case_message_can_be_claimed_and_stop_is_always_available(experience,monkeypatch):
    s=experience;v,b,c,r,_,bindings=prepare_unit(s,monkeypatch);reopen_budget(v,s,b);manifest,admission=manifest_for(v,s,b,r,bindings[0]);request={'operation_id':'fixed_case','session_ref':{'id':manifest['id'],'revision':1,'sha256':digest(manifest)},'generation':1,'raw_text':admission['raw_text'],'agent':'build','timeout_seconds':30}
    v.authorize_claim(manifest,'MESSAGE',request)
    for kind in ['GUIDANCE','CHILD_MESSAGE','LOAD','PERMISSION','QUESTION']:
        with pytest.raises(Conflict):v.authorize_claim(manifest,kind,request)
    with pytest.raises(Conflict):v.authorize_claim(manifest,'MESSAGE',{**request,'raw_text':'another unreviewed task'})
    v.authorize_claim(manifest,'INTERRUPT',{});v.authorize_claim(manifest,'DISCONNECT',{})


def test_shared_api_model_budget_is_not_reset_for_each_private_owner(experience,monkeypatch):
    s=experience;v,b,c,r,_,bindings=prepare_unit(s,monkeypatch);reopen_budget(v,s,b);manifest,_=manifest_for(v,s,b,r,bindings[0])
    for _ in range(4):v.reserve_model(manifest,'a'*64)
    with pytest.raises(Conflict):v.reserve_model(manifest,'b'*64)
    budget=v._budget(s['pid'],s['sid'],b['operation_id']);assert len(budget['reservations'])==4


def test_closed_budget_blocks_future_start_model_forward_and_case_claim(experience,monkeypatch):
    s=experience;v,b,c,r,_,bindings=prepare_unit(s,monkeypatch);manifest,_=manifest_for(v,s,b,r,bindings[0])
    with pytest.raises(Conflict):v.binding(manifest)
    with pytest.raises(Conflict):v.reserve_model(manifest,'a'*64)
    with pytest.raises(Conflict):v.authorize_claim(manifest,'MESSAGE',{})


def test_known_validation_result_is_read_on_repeated_number_without_another_sdk_case(experience,monkeypatch):
    s=experience;v,b,c,r,result,bindings=prepare_unit(s,monkeypatch);assert len(bindings)==2
    assert asyncio.run(v.validate(s['pid'],s['sid'],b))==result;assert len(bindings)==2
    with pytest.raises(Conflict):asyncio.run(v.validate(s['pid'],s['sid'],{**b,'provider_profile_ref':{'id':'other','revision':1,'sha256':'f'*64}}))


def test_native_subscription_is_not_silently_replaced_or_claimed_api_metered(experience):
    s=experience;v,b,c,r=make_skill(s);seen=[]
    s['sessions'].providers=SimpleNamespace(resolve=lambda ref:(seen.append(deepcopy(ref)) or SimpleNamespace(auth_mode='OAUTH_SUBSCRIPTION')))
    with pytest.raises(Conflict):asyncio.run(v.validate(s['pid'],s['sid'],b))
    assert seen==[b['provider_profile_ref']]
    assert not v._path(s['pid'],s['sid'],b['operation_id'],'budget.json').exists()


def test_actual_capability_load_reader_uses_private_snapshot_and_exact_skill_path(experience,monkeypatch):
    s=experience;v,b,c,r,_,bindings=prepare_unit(s,monkeypatch);reopen_budget(v,s,b);manifest,_=manifest_for(v,s,b,r,bindings[0]);view=v.capabilities(manifest)
    monkeypatch.setattr(s['caps'],'_configuration',lambda *_:pytest.fail('Do not read global Skill'))
    observed={'data':[{'name':'retain-original-work','location':str(view._path('skill-files','retain-original-work','0000000001','SKILL.md'))}]}
    result=s['sessions']._capability_load(bindings[0],[{'id':'skill'}],{},observed,manifest=manifest)
    assert result['skills'][0]['loaded_in_original_session'] is True


def test_private_owner_startup_has_no_second_runtime_after_an_interrupted_result(experience,monkeypatch):
    s=experience;v,b,c,r,_,bindings=prepare_unit(s,monkeypatch);result=v._path(s['pid'],s['sid'],b['operation_id'],'result.json');result.unlink()
    count=len(bindings);value=asyncio.run(v.validate(s['pid'],s['sid'],b));assert value['status']=='UNKNOWN_OR_IN_PROGRESS';assert len(bindings)==count


def test_original_cli_skill_cases_at_default_review_budget_load_and_invoke_candidate_without_global_enable(experience):
    # Actual original CLI and Skill discovery/tool invocation. Only model responses
    # are controlled. This case remains AUTHORED_NOT_RUN until concentrated debug.
    s=experience;wire=ModelWire([tool('skill',{'name':'retain-original-work'}),'保留原稿',tool('skill',{'name':'retain-original-work'}),'原操作结果仍未知']).start()
    ws=s['sessions'].workspaces;bound=ws.get(s['pid'],s['scope']['workspace_ref']['id'])
    scene={'store':s['store'],'data':s['data'],'pid':s['pid'],'workspaces':ws,'bound':bound,'manager':s['caps']}
    value,open_request=owner(scene,wire);value.context_authority.graph=s['sessions'].context_authority.graph;s['sessions']=value
    # Match the existing product/schema default, explicitly frozen in review.
    # The retained 60-second runs timed out; this is a distinct default-budget
    # qualification, not a claim that those short-budget outcomes passed.
    v,b,c,r=make_skill(s,profile=open_request['provider_profile_ref'],review_timeout=120)
    async def run():
        try:
            result=await v.validate(s['pid'],s['sid'],b)
            assert result['status']=='PASS' and result['model_calls_reserved']==4
            assert all(row['skill_loaded'] and row['skill_invocation_count']==1 for row in result['cases'])
            assert s['caps']._index()['skills']=={}
            assert all(value.get(s['pid'],row['owner_ref']['id'])['execution_purpose']=='EXPERIENCE_VALIDATION' for row in result['cases'])
        finally:await value.close()
    try:asyncio.run(run())
    finally:wire.close()


def test_session_list_filters_only_the_authoritative_private_admission_marker(experience,monkeypatch):
    s=experience;other='d'*32
    s['sessions']._write(s['sessions']._path(s['pid'],other,'manifest.json'),{'id':other,'project_id':s['pid']})
    def view(pid,sid):return {'session_ref':{'id':sid},'title':'validation is an ordinary user title','execution_purpose':'EXPERIENCE_VALIDATION' if sid==other else 'USER_WORK'}
    monkeypatch.setattr(s['sessions'],'get',view)
    rows=s['sessions'].list(s['pid']);assert [v['session_ref']['id'] for v in rows]==[s['sid']]


def test_missing_restarted_coordinator_blocks_new_dispatch_but_keeps_exact_stop_claim_available(experience):
    s=experience;sid='d'*32;manifest={'id':sid,'project_id':s['pid'],'experience_validation_admission':{'old':'reference'},'request':{}}
    s['sessions']._write(s['sessions']._path(s['pid'],sid,'manifest.json'),manifest);s['sessions'].experience_validation=None
    with pytest.raises(Conflict):s['sessions']._claim(s['pid'],sid,'unreviewed','MESSAGE',{})
    receipt,created=s['sessions']._claim(s['pid'],sid,'stop_original','DISCONNECT',{})
    assert created and receipt['kind']=='DISCONNECT'

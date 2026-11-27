"""UNIT: real Store/metadata/adapter callbacks, synthetic original messages.

No OAuth store is created/read, no CLI/model is invoked, and AUTHENTICATED here
is explicit test input, never evidence of a person's ChatGPT/Pro entitlement.
Separate existing runtime pre-auth regressions exercise the original CLI.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest

from packages.adapters.opencode_account import (OpenCodeAccount, authorization_epoch,
    read_subscription_verification, record_subscription_response, resolve_subscription_profile)
from packages.adapters.subscription_runtime import subscription_response_evidence
from packages.adapters.opencode_profile import CLI_SHA
from packages.adapters.opencode import OpenCodeBridge
from packages.adapters.opencode_planning import OpenCodePlanningTransport
from packages.adapters.opencode_desktop import OpenCodeDesktopTransport
from packages.agent_sessions import AgentSessions
from packages.domain.provider_settings import ProviderSettings
from packages.domain.store import Store, Conflict
from packages.domain.tasks import digest, canonical

ROOT=Path(__file__).resolve().parents[1]
CLI=ROOT/'var/opencode-ui-offline-guard/opencode'


def check(value,message='Subscription verification UNIT check failed'):
    if not value:raise AssertionError(message)


@pytest.fixture
def setup(tmp_path):
    data=tmp_path/'data';data.mkdir()
    store=Store(data/'store.sqlite3')
    providers=ProviderSettings(store,data,ROOT)
    providers._cli=lambda:CLI  # exact fixed original path, no download/process.
    account=OpenCodeAccount(providers,data)
    _binding,reference=account._binding()
    account._save(status='AUTHENTICATED',authorization_epoch='e'*32)
    saved=providers.save_subscription('unit-profile-save','unit-subscription','gpt-5.4',reference,False)
    account._save(profile=saved)
    profile=resolve_subscription_profile(providers.configuration(saved['profile_ref']['id'],1),saved['profile_ref'],
        providers.subscription_binding,verify_executable=False,require_auth=False)
    pid=store.create_project('Synthetic verification scope','UNIT only')['id']
    check(not (profile.account_data_home/'opencode/auth.json').exists())
    return {'data':data,'store':store,'providers':providers,'account':account,'profile':profile,'pid':pid,'saved':saved}


def case(value,kind='ORIGINAL_SESSION'):
    text='UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL'
    source={'kind':kind,'project_id':value['pid'],'object_ref':{'id':'1'*32,'revision':1,'sha256':'c'*64},
        'operation_id':'unit-message','request_sha256':digest({'raw_text':text}),'generation':1,
        'original_session_id':'ses_unit','status':'RETURNED','idle_observed':True,'cancel_requested':False,
        'deadline_ms':2000000000000,
        'native_user_messages':{'msg_unituser':hashlib.sha256(text.encode()).hexdigest()},
        'native_assistant_ids':['msg_unitassistant']}
    messages=[{'info':{'id':'msg_unituser','sessionID':'ses_unit','role':'user'},
        'parts':[{'type':'text','text':text}]},
        {'info':{'id':'msg_unitassistant','sessionID':'ses_unit','role':'assistant','parentID':'msg_unituser',
          'providerID':'openai','modelID':'gpt-5.4','finish':'stop','time':{'completed':1792000000000}},
         'parts':[{'type':'text','text':'UNIT_REPLY_NOT_REAL_PRO_INFERENCE'}]}]
    return source,messages


def test_exact_response_is_persistent_no_content_no_auth_and_profile_save_receipt_unchanged(setup):
    v=setup;p=v['profile'];a=v['account'];s,m=case(v)
    check(a._public()['model_access']=='NOT_VALIDATED')
    original=v['providers'].configuration(p.profile_id,1)
    saved=a.providers.get(p.profile_id,1)
    result=record_subscription_response(p,s,m)
    check(result['status']=='SAVED')
    receipt=read_subscription_verification(p)
    check(receipt['evidence']['source']==s)
    check(receipt['evidence']['semantic_acceptance'] is False)
    check(receipt['evidence']['subscription_entitlement']=='NOT_VALIDATED')
    check(a._public()['model_access']=='VERIFIED')
    check('UNIT_REPLY' not in canonical(receipt) and 'UNIT_USER_INTENT' not in canonical(receipt))
    check(v['providers'].configuration(p.profile_id,1)==original and a.providers.get(p.profile_id,1)==saved)
    restored=OpenCodeAccount(v['providers'],v['data'])
    check(restored._public()['model_access']=='VERIFIED')
    check(not (p.account_data_home/'opencode/auth.json').exists())
    files={f:f.read_bytes() for f in (a.root/'model-verifications').rglob('*.json')}
    check(record_subscription_response(p,s,m)==result)
    check(all(f.read_bytes()==raw for f,raw in files.items()))


@pytest.mark.parametrize('status',['CANCELLED','FAILED','UNKNOWN_OUTCOME','ACCEPTED','TIMED_OUT'])
def test_nonreturned_operation_never_creates_a_verification(setup,status):
    s,m=case(setup);s['status']=status
    check(record_subscription_response(setup['profile'],s,m)=={'status':'NOT_RECORDED'})
    check(not (setup['account'].root/'model-verifications').exists())


@pytest.mark.parametrize('change',[
    lambda s,m:m[1]['info'].update(modelID='other-model'),
    lambda s,m:m[1]['info'].update(providerID='other-provider'),
    lambda s,m:m[1]['info'].update(sessionID='ses_foreign'),
    lambda s,m:m[1]['info'].update(parentID='msg_otheruser'),
    lambda s,m:m[1]['info'].update(error={'name':'MessageAbortedError'}),
    lambda s,m:m[1]['info'].update(finish='tool-calls'),
    lambda s,m:m[1]['info'].update(time={}),
    lambda s,m:m[1].update(parts=[]),
    lambda s,m:m[0]['parts'][0].update(text='DIFFERENT_USER_INTENT'),
    lambda s,m:s.update(generation=0),
    lambda s,m:s.update(deadline_ms=1),
    lambda s,m:s.update(unexpected_body='MUST_NEVER_BE_PERSISTED'),
])
def test_mismatched_or_incomplete_native_evidence_is_rejected(setup,change):
    s,m=case(setup);change(s,m)
    with pytest.raises(Conflict):record_subscription_response(setup['profile'],s,m)
    check(setup['account']._public()['model_access']=='NOT_VALIDATED')


def test_cancel_pending_idle_only_missing_epoch_or_other_account_cannot_borrow_success(setup):
    v=setup;s,m=case(v);p=v['profile'];a=v['account']
    for key in ['cancel_requested','idle_observed']:
        bad=deepcopy(s);bad[key]=key=='cancel_requested'
        check(record_subscription_response(p,bad,m)=={'status':'NOT_RECORDED'})
    check(record_subscription_response(replace(p,authorization_epoch=None),s,m)=={'status':'NOT_RECORDED'})
    with pytest.raises(Conflict):record_subscription_response(replace(p,account_ref={**p.account_ref,'id':'foreign'}),s,m)
    check(not (a.root/'model-verifications').exists())


def test_same_directory_new_authorization_epoch_invalidates_success_and_rejects_late_owner(setup):
    v=setup;s,m=case(v);p=v['profile'];a=v['account']
    record_subscription_response(p,s,m)
    old_files=list((a.root/'model-verifications').rglob('*.json'))
    a._save(authorization_epoch='f'*32,status='AUTHENTICATED')
    check(a._public()['model_access']=='NOT_VALIDATED')
    check(read_subscription_verification(p) is None)
    with pytest.raises(Conflict):record_subscription_response(p,s,m)
    fresh=replace(p,authorization_epoch='f'*32)
    check(read_subscription_verification(fresh) is None)
    check(p.fingerprint==fresh.fingerprint)  # immutable config identity survives reauthorization.
    restored=OpenCodeAccount(v['providers'],v['data'])
    check(restored._public()['model_access']=='NOT_VALIDATED')
    check(authorization_epoch(p.account_data_home)=='f'*32)
    check(all(file.exists() for file in old_files))


@pytest.mark.parametrize('status',['SIGNED_OUT','AUTHENTICATED'])
def test_explicit_new_login_rotates_epoch_before_real_missing_archive_failure_no_oauth_call(setup,tmp_path,status):
    v=setup;s,m=case(v);record_subscription_response(v['profile'],s,m)
    a=v['account'];a._save(status=status)
    # Real preparation fails on an absent archive. No fake OAuth/process/API and
    # no native credential file: only the supplied test installation is used.
    v['providers'].source_root=tmp_path/'missing-installation'
    v['providers']._cli=ProviderSettings._cli.__get__(v['providers'],ProviderSettings)
    async def run():
        with pytest.raises(FileNotFoundError):await a.login('unit-new-authentication')
        check(authorization_epoch(v['profile'].account_data_home)!='e'*32)
        check(a._public()['model_access']=='NOT_VALIDATED')
        check(a._state()['status']=='UNKNOWN_OUTCOME' and not a.active)
        await a.close()
    asyncio.run(run())


def test_initial_epoch_is_explicit_local_metadata_only_and_survives_reopen(setup):
    v=setup;a=v['account'];p=v['profile']
    state=a._state();state.pop('authorization_epoch')
    from packages.adapters.opencode_account import _write
    _write(a.root/'state.json',state,replace=True)
    configuration=v['providers'].configuration(p.profile_id,1)
    listed=resolve_subscription_profile(configuration,p.reference,v['providers'].subscription_binding,
        verify_executable=False,require_auth=False)
    check(listed.authorization_epoch is None and authorization_epoch(p.account_data_home) is None)
    first=authorization_epoch(p.account_data_home,establish=True)
    check(len(first)==32 and authorization_epoch(p.account_data_home,establish=True)==first)
    restored=OpenCodeAccount(v['providers'],v['data'])
    check(authorization_epoch(p.account_data_home)==first and restored._public()['model_access']=='NOT_VALIDATED')
    check(not (p.account_data_home/'opencode/auth.json').exists())


def test_profiles_and_models_are_isolated_and_duplicate_threads_do_not_fork_receipts(setup):
    v=setup;s,m=case(v);p=v['profile']
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:record_subscription_response(p,s,m),range(8)))
    check(all(result==results[0] for result in results))
    check(len(list((v['account'].root/'model-verifications').rglob('operations/*.json')))==1)
    check(read_subscription_verification(replace(p,revision=2)) is None)
    check(read_subscription_verification(replace(p,model_id='other-model')) is None)


def test_corrupt_proof_is_unavailable_without_rewriting_it_or_editing_the_profile(setup):
    v=setup;s,m=case(v);p=v['profile'];a=v['account']
    record_subscription_response(p,s,m)
    pointer=next((a.root/'model-verifications').rglob('latest.json'))
    pointer.write_text('CORRUPT_DERIVED_PROOF')
    public=a._public()
    check(public['model_access']=='NOT_VALIDATED' and public['verification_observation_status']=='UNAVAILABLE')
    check(pointer.read_text()=='CORRUPT_DERIVED_PROOF')
    check(v['providers'].get(p.profile_id,1)==v['saved'])


def test_replaying_old_operation_never_rewinds_a_newer_exact_scope_proof(setup):
    v=setup;s,m=case(v);p=v['profile']
    record_subscription_response(p,s,m)
    newer={**s,'operation_id':'unit-newer','request_sha256':'d'*64}
    record_subscription_response(p,newer,m)
    current=read_subscription_verification(p)
    record_subscription_response(p,s,m)
    check(read_subscription_verification(p)==current)
    check(current['evidence']['source']['operation_id']=='unit-newer')


def session_scene(v):
    owner=AgentSessions(v['store'],v['data'],provider_registry=None,workspaces=None,capabilities=None)
    sid='1'*32
    manifest={'schema':'eeas.agent-session.v1','id':sid,'project_id':v['pid'],
        'request':{'provider_profile_ref':v['profile'].reference}}
    owner._write(owner._path(v['pid'],sid,'manifest.json'),manifest)
    ref={'id':sid,'revision':1,'sha256':digest(manifest)}
    request={'operation_id':'unit-message','session_ref':ref,'generation':1,'raw_text':'UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL'}
    owner._claim(v['pid'],sid,'unit-message','MESSAGE',request)
    owner._finish(v['pid'],sid,'unit-message','ACCEPTED',generation=1,original_session_id='ses_unit',native_message_id='msg_unituser',deadline='2033-05-18T03:33:20+00:00')
    owner._write(owner._path(v['pid'],sid,'state.json'),{'active_operation_id':'unit-message','native_status':'idle',
        'generation':1,'last_event_seq':0,'owner_status':'READY','messages':[]})
    ctx={'profile':v['profile'],'generation':1,'session_id':'ses_unit','closing':False}
    owner.active[sid]=ctx
    return owner,sid,ctx


def test_actual_service_settlement_callback_persists_and_storage_failure_keeps_returned(setup):
    v=setup;owner,sid,ctx=session_scene(v);s,m=case(v)
    asyncio.run(owner._settle(v['pid'],sid,ctx,m))
    check(owner.operation(v['pid'],sid,'unit-message')['status']=='RETURNED')
    check(owner._state(v['pid'],sid)['subscription_verification']['status']=='SAVED')
    check(v['account']._public()['model_access']=='VERIFIED')
    # A real filesystem failure in the derived projection, no injected callback.
    v['account']._save(authorization_epoch='f'*32)
    newer=replace(v['profile'],authorization_epoch='f'*32);ctx['profile']=newer
    parent=v['account'].root/'model-verifications'/digest({'account_ref':newer.account_ref,
        'profile_ref':newer.reference,'model_id':newer.model_id,'authorization_epoch':newer.authorization_epoch})
    parent.write_text('BLOCK_DIRECTORY_CREATION')
    operation=owner.operation(v['pid'],sid,'unit-message')
    owner._observe_subscription_response(v['pid'],sid,ctx,operation,m)
    check(owner.operation(v['pid'],sid,'unit-message')['status']=='RETURNED')
    check(owner._state(v['pid'],sid)['subscription_verification']['status']=='UNAVAILABLE')
    check(v['account']._public()['model_access']=='NOT_VALIDATED')


@pytest.mark.parametrize('cls',[OpenCodePlanningTransport,OpenCodeDesktopTransport])
def test_actual_terminal_adapter_callback_reads_exact_sqlite_operation_and_scope(setup,tmp_path,cls):
    v=setup;p=v['profile'];s,m=case(v)
    adapter=cls.__new__(cls)
    OpenCodeBridge.__init__(adapter,v['store'],v['data'])
    adapter.closed=False
    request={'project_id':v['pid'],'operation_id':'unit-execution','provider_profile_ref':p.reference,'timeout_seconds':30}
    scope={'profile_hash':p.fingerprint,'profile_ref':p.reference,'input_hash':digest(request),'authorization_epoch':p.authorization_epoch}
    workspace=tmp_path/'execution';workspace.mkdir()
    row,_=adapter._record(v['pid'],request['operation_id'],request,scope,workspace)
    with v['store'].connect() as connection:
        connection.execute('UPDATE agent_executions SET session_id=? WHERE id=?',('ses_unit',row['id']))
    OpenCodeBridge._terminal(adapter,row['id'],'RETURNED',{'fixture':'UNIT_SAVED_ORIGINAL_RESULT'})
    context={'session_id':'ses_unit','idle_observed':True}
    adapter._observe_subscription_response(v['pid'],row,request,scope,context,p,m,'UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL')
    check(v['account']._public()['model_access']=='VERIFIED')
    events=adapter.stream_events(v['pid'],row['id'])
    check(events[-1]['payload']['status']=='SAVED')
    before=adapter.inspect(v['pid'],row['id'])
    adapter._observe_subscription_response(v['pid'],row,{**request,'operation_id':'foreign'},scope,context,p,m,'UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL')
    check(adapter.stream_events(v['pid'],row['id'])[-1]['payload']['status']=='UNAVAILABLE')
    check(adapter.inspect(v['pid'],row['id'])==before)


def structured_planning_scene(v, tmp_path, cls=OpenCodePlanningTransport, *, wrapper=False):
    from packages.domain.planning_schema import PlanningCandidate, CoveredModelProposal
    _source, messages = case(v, 'PLANNING')
    candidate = {'title': 'UNIT_PLAN', 'summary': 'UNIT_SUMMARY',
        'nodes': [{'id': 'unit-task', 'title': 'UNIT_TASK', 'description': 'UNIT_DESCRIPTION'}]}
    output = {'candidate': candidate, 'claim_links': []} if wrapper else candidate
    schema = (CoveredModelProposal if wrapper else PlanningCandidate).model_json_schema()
    info = messages[1]['info']
    info.update(finish='tool-calls', structured=deepcopy(output))
    messages[1]['parts'] = [{'id': 'prt_structured', 'sessionID': 'ses_unit',
        'messageID': info['id'], 'type': 'tool', 'tool': 'StructuredOutput', 'callID': 'call_structured',
        'state': {'status': 'completed', 'input': deepcopy(output),
            'output': 'Structured output captured successfully.', 'metadata': {'valid': True},
            'time': {'start': 1791999999900, 'end': 1791999999990}}}]
    adapter = cls.__new__(cls)
    OpenCodeBridge.__init__(adapter, v['store'], v['data'])
    adapter.closed = False
    profile = v['profile']
    request = {'project_id': v['pid'], 'operation_id': 'unit-structured',
        'provider_profile_ref': profile.reference, 'timeout_seconds': 30, 'output_schema': schema}
    scope = {'profile_hash': profile.fingerprint, 'profile_ref': profile.reference,
        'input_hash': digest(request), 'authorization_epoch': profile.authorization_epoch,
        'output_schema_sha256': digest(schema)}
    folder = tmp_path / 'structured-execution'; folder.mkdir()
    row, _ = adapter._record(v['pid'], request['operation_id'], request, scope, folder)
    with v['store'].connect() as connection:
        connection.execute('UPDATE agent_executions SET session_id=? WHERE id=?', ('ses_unit', row['id']))
    result = {'candidate_json': deepcopy(output), 'schema_errors': [], 'idle_observed': True,
        'native_error': None, 'message_source': 'ORIGINAL_COMMITTED_SSE_UPDATES'}
    OpenCodeBridge._terminal(adapter, row['id'], 'RETURNED', result)
    return adapter, row, request, scope, {'session_id': 'ses_unit', 'idle_observed': True}, messages


@pytest.mark.parametrize('wrapper', [False, True])
def test_planning_completed_structured_output_preserves_result_and_records_exact_observation(setup, tmp_path, wrapper):
    v = setup
    adapter, row, request, scope, context, messages = structured_planning_scene(v, tmp_path, wrapper=wrapper)
    before = adapter.inspect(v['pid'], row['id'])
    adapter._observe_subscription_response(v['pid'], row, request, scope, context, v['profile'], messages,
        'UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL')
    check(adapter.stream_events(v['pid'], row['id'])[-1]['payload']['status'] == 'SAVED')
    check(adapter.inspect(v['pid'], row['id']) == before)
    receipt = read_subscription_verification(v['profile'])
    check(receipt['evidence']['assistants'][0]['finish'] == 'tool-calls')
    proof = receipt['evidence']['source']['structured_output']
    check(proof['schema_sha256'] == digest(request['output_schema']))
    check(proof['value_sha256'] == digest(messages[1]['info']['structured']))
    check('UNIT_DESCRIPTION' not in canonical(receipt))
    check(OpenCodeAccount(v['providers'], v['data'])._public()['model_access'] == 'VERIFIED')
    check(receipt['evidence']['subscription_entitlement'] == 'NOT_VALIDATED')


@pytest.mark.parametrize('change', [
    lambda m: m[1]['parts'][0].update(tool='ordinary_tool'),
    lambda m: m[1]['parts'][0]['state'].update(status='running'),
    lambda m: m[1]['parts'][0]['state'].update(error='failed'),
    lambda m: m[1]['parts'][0]['state'].update(input={'not': 'the original structured value'}),
    lambda m: m[1]['parts'][0]['state']['metadata'].update(valid=False),
    lambda m: m[1]['parts'].append(deepcopy(m[1]['parts'][0])),
    lambda m: m[1]['info'].update(parentID='msg_foreign'),
    lambda m: m[1]['info'].update(modelID='foreign'),
    lambda m: m[1]['parts'][0].update(sessionID='ses_foreign'),
    lambda m: m[1]['parts'][0].update(messageID='msg_foreign'),
    lambda m: m[1]['parts'][0]['state']['time'].update(end=2000000000000),
])
def test_structured_planning_observation_rejects_nonterminal_or_foreign_parts(setup, tmp_path, change):
    v = setup
    adapter, row, request, scope, context, messages = structured_planning_scene(v, tmp_path)
    change(messages)
    before = adapter.inspect(v['pid'], row['id'])
    adapter._observe_subscription_response(v['pid'], row, request, scope, context, v['profile'], messages,
        'UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL')
    check(adapter.stream_events(v['pid'], row['id'])[-1]['payload']['status'] == 'UNAVAILABLE')
    check(adapter.inspect(v['pid'], row['id']) == before)
    check(not (v['account'].root / 'model-verifications').exists())


def test_schema_and_committed_result_are_checked_before_structured_proof_and_desktop_stays_closed(setup, tmp_path):
    v = setup
    adapter, row, request, scope, context, messages = structured_planning_scene(v, tmp_path)
    original = adapter.inspect(v['pid'], row['id'])
    invalid = deepcopy(messages)
    invalid[1]['info']['structured']['nodes'] = []
    invalid[1]['parts'][0]['state']['input'] = deepcopy(invalid[1]['info']['structured'])
    result = json.loads(original['result'])
    result['candidate_json'] = invalid[1]['info']['structured']
    OpenCodeBridge._terminal(adapter, row['id'], 'RETURNED', result)
    adapter._observe_subscription_response(v['pid'], row, request, scope, context, v['profile'], invalid,
        'UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL')
    check(adapter.stream_events(v['pid'], row['id'])[-1]['payload']['status'] == 'UNAVAILABLE')
    OpenCodeBridge._terminal(adapter, row['id'], 'RETURNED', json.loads(original['result']))
    request['output_schema']['required'].append('foreign_required_field')
    adapter._observe_subscription_response(v['pid'], row, request, scope, context, v['profile'], messages,
        'UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL')
    check(adapter.stream_events(v['pid'], row['id'])[-1]['payload']['status'] == 'UNAVAILABLE')
    check(not (v['account'].root / 'model-verifications').exists())
    folder = tmp_path / 'desktop'; folder.mkdir()
    dv = {**v, 'pid': v['store'].create_project('Desktop negative scope', 'UNIT only')['id']}
    desktop, row, request, scope, context, messages = structured_planning_scene(dv, folder, OpenCodeDesktopTransport)
    desktop._observe_subscription_response(dv['pid'], row, request, scope, context, v['profile'], messages,
        'UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL')
    check(desktop.stream_events(dv['pid'], row['id'])[-1]['payload']['status'] == 'UNAVAILABLE')


def test_structured_observation_proof_is_exact_and_cancellation_storage_failure_keep_original_returned(setup, tmp_path):
    v = setup
    adapter, row, request, scope, context, messages = structured_planning_scene(v, tmp_path)
    before = adapter.inspect(v['pid'], row['id'])
    context['closing'] = True
    adapter._observe_subscription_response(v['pid'], row, request, scope, context, v['profile'], messages,
        'UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL')
    check(adapter.stream_events(v['pid'], row['id'])[-1]['payload']['status'] == 'NOT_RECORDED')
    context['closing'] = False
    adapter._observe_subscription_response(v['pid'], row, request, scope, context, v['profile'], messages,
        'UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL')
    receipt = read_subscription_verification(v['profile']); source = receipt['evidence']['source']
    for key in ['schema_sha256', 'value_sha256', 'part_sha256', 'assistant_id', 'part_id', 'call_id']:
        bad = deepcopy(source); bad['structured_output'][key] = 'invalid'
        with pytest.raises(Conflict): subscription_response_evidence(v['profile'], bad, messages)
    for kind in ['ORIGINAL_SESSION', 'NATIVE_DESKTOP']:
        with pytest.raises(Conflict): subscription_response_evidence(v['profile'], {**source, 'kind': kind}, messages)
    saved = {p: p.read_bytes() for p in (v['account'].root / 'model-verifications').rglob('*.json')}
    adapter._observe_subscription_response(v['pid'], row, request, scope, context, v['profile'], messages,
        'UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL')
    check(all(p.read_bytes() == raw for p, raw in saved.items()))
    check(adapter.inspect(v['pid'], row['id']) == before)
    v['account']._save(authorization_epoch='f' * 32)
    new = replace(v['profile'], authorization_epoch='f' * 32)
    parent = v['account'].root / 'model-verifications' / digest({'account_ref': new.account_ref,
        'profile_ref': new.reference, 'model_id': new.model_id, 'authorization_epoch': new.authorization_epoch})
    parent.write_text('BLOCK_DERIVED_OBSERVATION_DIRECTORY')
    nv = {**v, 'profile': new, 'pid': v['store'].create_project('New immutable storage-failure scope', 'UNIT')['id']}
    folder = tmp_path / 'new-scope'; folder.mkdir()
    next_adapter, next_row, request, scope, context, messages = structured_planning_scene(nv, folder)
    next_before = next_adapter.inspect(nv['pid'], next_row['id'])
    next_adapter._observe_subscription_response(nv['pid'], next_row, request, scope, context, new, messages,
        'UNIT_USER_INTENT_NO_ACCOUNT_OR_MODEL_CALL')
    check(next_adapter.stream_events(nv['pid'], next_row['id'])[-1]['payload']['status'] == 'UNAVAILABLE')
    check(next_adapter.inspect(nv['pid'], next_row['id']) == next_before)
    check(adapter.inspect(v['pid'], row['id']) == before)


def test_guided_response_uses_exact_latest_guidance_not_the_old_root_user(setup):
    v=setup;owner,sid,ctx=session_scene(v);s,m=case(v)
    root=owner.operation(v['pid'],sid,'unit-message');ref=root['session_ref']
    user_text='UNIT_LATEST_GUIDANCE_TEXT'
    request={'operation_id':'unit-guidance','session_ref':ref,'generation':1,'raw_text':user_text}
    owner._claim(v['pid'],sid,'unit-guidance','GUIDANCE',request)
    owner._finish(v['pid'],sid,'unit-guidance','RETURNED',generation=1,original_session_id='ses_unit',
        original_active_operation_id='unit-message',native_message_id='msg_guidanceuser')
    m[0]['info']['id']='msg_guidanceuser';m[0]['parts'][0]['text']=user_text
    m[1]['info']['parentID']='msg_guidanceuser'
    root=owner._finish(v['pid'],sid,'unit-message','RETURNED',native_assistant_ids=['msg_unitassistant'])
    owner._observe_subscription_response(v['pid'],sid,ctx,root,m,'unit-guidance')
    receipt=read_subscription_verification(v['profile'])
    check(receipt['evidence']['assistants'][0]['parent_id']=='msg_guidanceuser')
    check(receipt['evidence']['source']['operation_id']=='unit-message')


@pytest.mark.parametrize('change',[
    lambda o,p,s,c,r:o.active.update({s:{**c}}),
    lambda o,p,s,c,r:c.update(generation=2),
    lambda o,p,s,c,r:r.update(request_sha256='f'*64),
    lambda o,p,s,c,r:r.update(id='foreign-operation'),
])
def test_late_owner_old_generation_and_foreign_or_modified_operation_are_not_verified(setup,change):
    v=setup;owner,sid,ctx=session_scene(v);s,m=case(v)
    root=owner._finish(v['pid'],sid,'unit-message','RETURNED',native_assistant_ids=['msg_unitassistant'])
    change(owner,v['pid'],sid,ctx,root)
    owner._observe_subscription_response(v['pid'],sid,ctx,root,m)
    check(v['account']._public()['model_access']=='NOT_VALIDATED')
    check(owner.operation(v['pid'],sid,'unit-message')['status']=='RETURNED')

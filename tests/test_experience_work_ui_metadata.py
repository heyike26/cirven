"""AUTHORED_NOT_RUN: original-work UI's real metadata services/DTOs.

Uses private fixture Store, UNIT original-task receipts and the established UNIT
MCP boundary only. No Windows, browser, model or product qualification implied.
"""
from copy import deepcopy
import json
import pytest
from pydantic import ValidationError
from packages.agent_sessions.experience_requests import ExperienceRequestPrepare,prepare_experience_request
from packages.agent_sessions.work_requirements import WorkRequirementScope,_root
from packages.agent_sessions.experience_candidates import ExperienceCandidates
from packages.domain.tasks import canonical,digest
from packages.domain.store import Conflict
from test_experience_candidates import experience,propose,proposal,review
from test_experience_activation import promotion,promote
from test_experience_request_packets import files
from test_experience_source_policy import policy,decision,decide


def test_history_associates_only_exact_candidate_and_reads_no_modern_record_bodies(experience,monkeypatch):
    s=experience;c=propose(s);other=propose(s,proposal(s,'other_candidate'))
    r=s['candidates'].review(s['pid'],s['sid'],review(s,c));before=files(s)
    monkeypatch.setattr(s['candidates'],'_record',lambda *_:pytest.fail('Indexed thin history needs no record bodies'))
    page=s['candidates'].history(s['pid'],s['sid'],c['record_ref'])
    assert {row['record_ref']['id'] for row in page['entries']}=={c['operation_id'],r['operation_id']}
    assert all(row['candidate_ref']==c['record_ref'] for row in page['entries'])
    assert other['record_ref'] not in [row['record_ref'] for row in page['entries']]
    assert page['legacy_body_reads']==0 and files(s)==before


def test_legacy_history_bounds_reads_per_page_and_never_backfills_on_get(experience,monkeypatch):
    s=experience;refs=[propose(s,proposal(s,'candidate_'+str(i)))['record_ref'] for i in range(12)]
    c=s['candidates'];path=c._path(s['pid'],s['sid'],'index.json');index=c._index(s['pid'],s['sid'])
    for row in index['records']:row.pop('candidate_ref',None)
    s['sessions']._write(path,index,replace=True);before=files(s);reads=[];original=c._record
    def bounded(*args):reads.append(args[2]);return original(*args)
    monkeypatch.setattr(c,'_record',bounded)
    page=c.history(s['pid'],s['sid'],refs[0]);assert page['entries']==[] and page['legacy_body_reads']==len(reads)==8
    second=c.history(s['pid'],s['sid'],refs[0],page['next_offset'],page['index_sha256'])
    assert len(second['entries'])==1 and second['entries'][0]['record_ref']==refs[0] and len(reads)==12
    assert files(s)==before


def test_history_cursor_rejects_append_instead_of_skipping_original_records(experience):
    s=experience;c=propose(s);page=s['candidates'].history(s['pid'],s['sid'],c['record_ref'])
    s['candidates'].review(s['pid'],s['sid'],review(s,c));before=files(s)
    with pytest.raises(Conflict):s['candidates'].history(s['pid'],s['sid'],c['record_ref'],1,page['index_sha256'])
    with pytest.raises(Conflict):s['candidates'].history(s['pid'],s['sid'],c['record_ref'],1)
    assert files(s)==before


def test_source_work_returns_current_context_without_opening_or_faking_owner(experience,monkeypatch):
    s=experience;c=propose(s);s['sessions']._write(s['sessions']._path(s['pid'],s['sid'],'state.json'),{'generation':7,'owner_status':'DISCONNECTED'},replace=True)
    monkeypatch.setattr(s['sessions'],'open',lambda *_a,**_k:pytest.fail('Source navigation is metadata only'))
    monkeypatch.setattr(s['sessions'],'inspect',lambda *_a,**_k:pytest.fail('No native inspection'))
    before=files(s);value=s['candidates'].source_work(s['pid'],s['sid'],c['record_ref'])
    assert value['context']=={'project_id':s['pid'],'session_ref':s['scope']['session_ref'],'generation':7,'workspace_ref':s['scope']['workspace_ref']}
    assert value['root_operation_id']==s['scope']['root_operation_id'] and value['root_sha256']==s['root']['sha256']
    assert 'messages' not in value and 'request' not in value and value['read_only'] and files(s)==before


def test_raw_review_round_trip_preserves_bigint_and_float_lexical_value(promotion):
    s=promotion;request=deepcopy(s['review']['request']);request['operation_id']='exact_numeric_review'
    request['cases'][0]['input']={'integer':'RAW_BIGINT','floating':'RAW_FLOAT'}
    raw=canonical(request).replace('"RAW_BIGINT"','9007199254740993').replace('"RAW_FLOAT"','1.0');before=files(s)
    value=prepare_experience_request(s['candidates'],s['pid'],s['sid'],{'action':'REVIEW','request_json':raw})
    decoded=json.loads(value['request_json']);assert decoded['cases'][0]['input']=={'integer':9007199254740993,'floating':1.0}
    assert type(decoded['cases'][0]['input']['floating']) is float
    assert '9007199254740993' in value['request_json'] and '1.0' in value['request_json']
    assert value['request_sha256']==digest(decoded) and files(s)==before


@pytest.mark.parametrize('raw',['{"x":1,"x":2}','{"x":NaN}','{"x":Infinity}','{"x":1e999}','{"x":'+('[0,'*65)+'0'+(']'*65)+'}', '{"x":['+','.join('0' for _ in range(20001))+']}'])
def test_exact_request_rejects_duplicate_nonfinite_or_unbounded_json(raw):
    with pytest.raises(ValidationError):ExperienceRequestPrepare(action='REVIEW',request_json=raw)


def test_exact_prepare_accepts_one_input_channel_only():
    with pytest.raises(ValidationError):ExperienceRequestPrepare(action='REVIEW')
    with pytest.raises(ValidationError):ExperienceRequestPrepare(action='REVIEW',request={'a':1},request_json='{"a":1}')


@pytest.mark.parametrize('status,case_status,expected',[('PASS','PASS','RETURNED'),('FAIL','FAIL','RETURNED'),('UNKNOWN','UNKNOWN','UNKNOWN'),('FAIL','UNKNOWN','UNKNOWN'),('CANCELLED','PASS','UNKNOWN'),('SOURCE_BLOCKED','UNKNOWN','UNKNOWN')])
def test_public_saved_result_does_not_confuse_persistence_and_known_execution(promotion,status,case_status,expected):
    s=promotion;value={**s['validation'],'status':status,'cases':[{'id':'case','status':case_status,'answer':'PRIVATE_BODY'}]}
    active=policy(s).public(s['pid'],s['sid'],value)
    assert active['result_sha256']==digest(value) and active['execution_status']==expected
    decide(s,decision(s,'FORGET'));before=files(s);hidden=policy(s).public(s['pid'],s['sid'],value)
    assert hidden['result_sha256']==digest(value) and hidden['execution_status']==expected and hidden['content_withheld']
    assert 'PRIVATE_BODY' not in json.dumps(hidden) and 'cases' not in hidden and hidden['qualified'] is False and files(s)==before


def test_parent_restore_does_not_revive_descendant_prewithdrawal_binding(promotion):
    s=promotion;promote(s);sessions=s['sessions'];sid='d'*32;manifest={'id':sid,'project_id':s['pid'],'request':{'workspace_ref':s['scope']['workspace_ref']},'capability_configuration_refs':s['caps']._index()}
    sessions._write(sessions._path(s['pid'],sid,'manifest.json'),manifest);ref={'id':sid,'revision':1,'sha256':digest(manifest)}
    sessions._write(sessions._path(s['pid'],sid,'state.json'),{'generation':1,'owner_status':'DISCONNECTED'})
    request={'session_ref':ref,'generation':1,'raw_text':'Use exact prior source in this original work'}
    sessions._claim(s['pid'],sid,'descendant_work','MESSAGE',request)
    sessions._finish(s['pid'],sid,'descendant_work','UNKNOWN_OUTCOME',generation=1,original_session_id='ses_child',native_message_id='msg_child')
    sessions.context_authority.begin(manifest,{'session_id':'ses_child'},loaded_existing=False)
    scope={'session_ref':ref,'generation':1,'workspace_ref':s['scope']['workspace_ref'],'root_operation_id':'descendant_work'}
    root=_root(sessions,s['pid'],sid,WorkRequirementScope(**scope));child={**s,'sid':sid,'scope':scope,'root':root,'candidates':ExperienceCandidates(sessions)}
    candidate=propose(child,proposal(child,'descendant_candidate'));original=policy(child).status(s['pid'],sid,candidate['record_ref'])
    decide(s,decision(s));assert policy(child).status(s['pid'],sid,candidate['record_ref'])['availability']!='ACTIVE'
    decide(s,decision(s,'RESTORE','restore_parent',1));restored=policy(child).status(s['pid'],sid,candidate['record_ref'])
    assert original['availability']==restored['availability']=='ACTIVE' and digest(original)!=digest(restored)
    assert original['dependency_authority_sha256']!=restored['dependency_authority_sha256']

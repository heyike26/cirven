"""AUTHORED_NOT_RUN. Controlled local API/files, no model or native editor.

Original operation hashes, source edits, confirmations and task contracts have
separate assertions. A valid JSON proposal is not semantic acceptance.
"""
import hashlib
import json
import pytest
from pydantic import ValidationError
from packages.domain.native_rework import NativePlanInput
from packages.domain.local_workspaces import digest
from test_native_rework import scene,feedback,mapping,source,call,apply_body


def packet(m,text,op='raw_preview'):
    selected=m['payload']
    return {'operation_id':op,'mapping_ref':m['record_ref'],'classification_kind':'UNKNOWN',
        'classification_origin':'USER_STATEMENT','rationale':'用户原文提议，语义仍需核查',
        'proposed_value_text':text,'contracts':[{'task_id':'a','inputs':[{
            'binding_ref':selected['binding_ref'],'input_value_sha256':selected['selected_value_sha256'],
            'condition':'VALUE_CHANGED','invalidating_kinds':['EXPRESSION','DATA','CONSTRAINT','UNKNOWN'],
            'verification_note':'保留确切原对象与任务输入关系','verification_refs':[selected['binding_ref']]}]}]}


def create(s,m,text,op='raw_preview'):
    body=packet(m,text,op)
    return call(s,'POST','/native-rework/plans',body),body


def test_original_json_text_survives_request_plan_and_original_operation_lookup(scene):
    s=scene;m=mapping(s,feedback(s));text=' {"integer":9007199254740993, "small":1e-7} '
    before=(s['root']/'任务与harness.json').read_bytes();p,body=create(s,m,text)
    assert p['payload']['proposal_text']=={'kind':'JSON_VALUE','text':text,'sha256':hashlib.sha256(text.encode()).hexdigest()}
    assert p['payload']['proposed_value']['integer']==9007199254740993
    assert p['payload']['request_sha256']==digest({'project_id':s['pid'],'operation_id':body['operation_id'],'kind':'PLAN','input':body})
    assert call(s,'GET','/native-rework/operations/'+body['operation_id'])['payload']==p['payload']
    assert (s['root']/'任务与harness.json').read_bytes()==before


def test_existing_apply_uses_exact_server_parsed_integer_and_preserves_manual_outside_bytes(scene):
    s=scene;m=mapping(s,feedback(s));p,_=create(s,m,'{"integer":9007199254740993,"small":1e-7}')
    path=s['root']/'任务与harness.json';path.write_text(path.read_text().replace('原人工备注','人工保留的备注'))
    result=call(s,'POST','/native-rework/plans/'+p['plan_ref']['id']+'/apply',apply_body(s,p,'apply_raw',True))
    assert result['status']=='APPLIED';document=json.loads(path.read_text())
    assert document['tasks']['a']['requirement']['integer']==9007199254740993
    assert '人工保留的备注' in path.read_text() and (s['root']/'独立任务B.md').read_bytes()==s['independent']


def test_legacy_null_value_request_dump_and_operation_hash_are_unchanged(scene):
    s=scene;m=mapping(s,feedback(s));body=packet(m,'null','legacy_null');body.pop('proposed_value_text');body['proposed_value']=None
    model=NativePlanInput.model_validate(body)
    assert model.model_dump()==model.model_dump(mode='json')==body
    p=call(s,'POST','/native-rework/plans',body)
    assert 'proposal_text' not in p['payload'] and p['payload']['proposed_value'] is None
    assert p['payload']['request_sha256']==digest({'project_id':s['pid'],'operation_id':'legacy_null','kind':'PLAN','input':body})


def test_same_operation_and_same_original_text_returns_only_original_preview(scene):
    s=scene;m=mapping(s,feedback(s));first,body=create(s,m,'9007199254740993')
    again=call(s,'POST','/native-rework/plans',body)
    assert again['record_ref']==first['record_ref'] and again['payload']==first['payload']


def test_same_operation_with_different_original_text_conflicts_without_source_edits(scene):
    s=scene;m=mapping(s,feedback(s));_,body=create(s,m,'1e-7');before=(s['root']/'任务与harness.json').read_bytes()
    response=s['client'].post(s['base']+'/native-rework/plans',json={**body,'proposed_value_text':'0.0000001'})
    assert response.status_code==409 and (s['root']/'任务与harness.json').read_bytes()==before


def test_original_contracts_and_mapped_unknown_task_impact_are_retained(scene):
    s=scene;m=mapping(s,feedback(s));p,body=create(s,m,'"新的提议"')
    assert p['payload']['engine_plan']['payload']['contracts']==body['contracts']
    assert p['payload']['write_scope']==[{'workspace_id':source(s)['workspace_id'],'path':source(s)['path']}]
    assert p['payload']['write_authorized'] is False
    assert p['payload']['engine_plan']['payload']['classification']['origin']=='USER_STATEMENT'


def test_invalid_duplicate_nonfinite_and_truncated_json_never_change_source_files(scene):
    s=scene;m=mapping(s,feedback(s));before=(s['root']/'任务与harness.json').read_bytes()
    for i,text in enumerate(['{"a":1,"a":2}','NaN','Infinity','1e309','{"a":','']):
        response=s['client'].post(s['base']+'/native-rework/plans',json=packet(m,text,'bad_json_'+str(i)))
        assert response.status_code==422 and (s['root']/'任务与harness.json').read_bytes()==before


def test_plain_text_replacement_does_not_get_parsed_as_json(scene):
    s=scene;f=feedback(s,'text_feedback','原页.md');m=mapping(s,f,'text_mapping',locator={'kind':'FILE'})
    p,_=create(s,m,'not JSON: 😀正文','text_preview')
    assert p['payload']['proposed_value']=='not JSON: 😀正文'
    assert p['payload']['proposal_text']['kind']=='UTF8_TEXT'


def test_explicit_empty_utf8_replacement_is_a_saved_preview_not_a_missing_value(scene):
    s=scene;f=feedback(s,'empty_feedback','原页.md');m=mapping(s,f,'empty_mapping',locator={'kind':'FILE'})
    before=(s['root']/'原页.md').read_bytes();p,_=create(s,m,'','empty_text')
    assert p['payload']['proposed_value']=='' and p['payload']['proposal_text']['text']==''
    assert (s['root']/'原页.md').read_bytes()==before


def test_value_and_original_text_are_mutually_exclusive_in_runtime_and_schema(scene):
    s=scene;m=mapping(s,feedback(s));body=packet(m,'null')
    for candidate in [{**body,'proposed_value':None},{k:v for k,v in body.items() if k!='proposed_value_text'},{**body,'proposed_value_text':None}]:
        with pytest.raises(ValidationError):NativePlanInput.model_validate(candidate)
        response=s['client'].post(s['base']+'/native-rework/plans',json=candidate)
        assert response.status_code==422
    schema=NativePlanInput.model_json_schema()
    assert len(schema['oneOf'])==2 and schema['properties']['proposed_value_text']['type']=='string'


def test_optional_raw_field_stays_omitted_in_nested_legacy_request_serialization(scene):
    s=scene;m=mapping(s,feedback(s));body=packet(m,'"old"');body.pop('proposed_value_text');body['proposed_value']='old'
    a=NativePlanInput.model_validate(body);b=NativePlanInput.model_validate(a.model_dump())
    assert a.model_dump()==b.model_dump()==body and 'proposed_value_text' not in b.model_fields_set


def test_raw_utf8_size_nul_and_surrogate_validation_happens_before_operation_dispatch(scene):
    s=scene;m=mapping(s,feedback(s));before=(s['root']/'任务与harness.json').read_bytes()
    for text in ['中'*400000,'a\0b','\ud800']:
        with pytest.raises(ValidationError):NativePlanInput.model_validate(packet(m,text))
    assert (s['root']/'任务与harness.json').read_bytes()==before


def test_raw_agent_origin_remains_a_proposal_and_uses_the_same_scoped_engine(scene):
    s=scene;m=mapping(s,feedback(s));body={**packet(m,'9007199254740993'),'classification_origin':'OPENCODE_PROPOSAL'}
    p=call(s,'POST','/native-rework/plans',body)
    assert p['payload']['engine_plan']['payload']['classification']['origin']=='OPENCODE_PROPOSAL'
    assert p['payload']['classification_status']=='PROPOSAL_NOT_SEMANTICALLY_VERIFIED'
    assert p['payload']['write_authorized'] is False

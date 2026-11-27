"""AUTHORED_NOT_RUN: pure DTO fingerprint contract; no App or model instance."""
from copy import deepcopy
import pytest
from apps.api.canonical_conversations import ConversationMessage
from packages.domain.original_work_input_fingerprint import original_work_input_fingerprint as fingerprint


def packet():
    return {'operation_id':'one','conversation_ref':{'id':'original:'+'a'*64,'revision':1},
            'provider_profile_ref':{'id':'profile','revision':1,'sha256':'b'*64},
            'raw_text':'保留原文 😀\r\n','agent':'build','original_source':{
                'project_id':'execution','session_ref':{'id':'owner','revision':1,'sha256':'c'*64},'generation':2},
            'original_work_ref':{'root_operation_id':'root','root_sha256':'d'*64}}


@pytest.mark.parametrize('coordinate',[0,0.9,0.1,1e-7,1e-20,-0.0,0.8999999999999999])
def test_python_model_defaults_and_region_numeric_types_do_not_change_the_semantic_fingerprint(coordinate):
    raw=packet();raw['input_attachments']=[{'kind':'SCREENSHOT','context_ref':{'id':'page','revision':1,'sha256':'e'*64},
        'label':'原截图','image_sha256':'f'*64,'regions':[{'x':coordinate,'y':0,'width':0.1,'height':0.2}]}]
    normalized=ConversationMessage(**raw).model_dump()
    assert fingerprint(raw)==fingerprint(normalized)
    assert normalized['input_attachments'][0]['user_note'] is None


def test_fingerprint_distinguishes_changed_original_text_work_scope_and_numeric_value_types():
    raw=packet();base=fingerprint(raw)
    for field,value in [('raw_text','other'),('original_work_ref',{'root_operation_id':'other','root_sha256':'d'*64})]:
        changed=deepcopy(raw);changed[field]=value;assert fingerprint(changed)!=base
    numeric=deepcopy(raw);numeric['probe']=1
    text=deepcopy(raw);text['probe']='1'
    boolean=deepcopy(raw);boolean['probe']=True
    assert len({fingerprint(numeric),fingerprint(text),fingerprint(boolean)})==3


def test_legacy_unselected_or_file_protocol_inputs_do_not_receive_a_new_quick_fingerprint():
    raw=packet();legacy=deepcopy(raw);legacy.pop('original_work_ref')
    assert fingerprint(legacy) is None
    raw['file_input']={'kind':'WORKSPACE_FILE','source_ref':{}}
    assert fingerprint(raw) is None


def test_unsafe_or_nonfinite_numbers_do_not_create_a_misleading_equivalent_input_fingerprint():
    for value in [9007199254740992,float('nan'),float('inf')]:
        raw=packet();raw['probe']=value
        assert fingerprint(raw) is None

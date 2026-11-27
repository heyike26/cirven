"""AUTHORED_NOT_RUN: canonical status admission prefix, explicitly UNIT.

The sentinel stops before durable admission, owner startup, claim or dispatch.
This tests unused-input validation boundaries, not a complete HTTP/native path.
"""
import asyncio
from types import SimpleNamespace
import pytest
from apps.api.canonical_conversations import CanonicalConversations,ConversationMessage


class AdmissionBoundary(Exception):pass
class InputBoundary(Exception):pass


def unit(tmp_path):
    service=object.__new__(CanonicalConversations);calls=[]
    service._path=lambda *args:tmp_path/'not_created.json'
    service._lock=lambda *args:asyncio.Lock()
    service._available=lambda:None
    service._scope=lambda *args:{'project_id':'project','conversation_ref':{'id':'original:'+'a'*64,'revision':1}}
    def inputs(value):calls.append('input_validation');raise InputBoundary()
    def model(*args):calls.append('provider_resolution');raise AssertionError('Status does not resolve a model')
    def member(*args):calls.append('member_resolution');return object()
    def admission(*args,**kwargs):calls.append('admission_boundary');raise AdmissionBoundary()
    service.runtime=SimpleNamespace(native=SimpleNamespace(input_sources=SimpleNamespace(validate=inputs)))
    service.sessions=SimpleNamespace(providers=SimpleNamespace(resolve=model));service._member=member;service._save=admission
    return service,calls


def packet(text='进度？！',selected=True):
    return ConversationMessage(operation_id='local_work_status',conversation_ref={'id':'original:'+'a'*64,'revision':1},
        provider_profile_ref={'id':'profile','revision':1,'sha256':'b'*64},raw_text=text,
        input_attachments=[{'kind':'SCREENSHOT','context_ref':{'id':'image','revision':1,'sha256':'c'*64},'label':'未消费的附件','image_sha256':'d'*64}],
        context_selection_id='e'*64,original_work_ref={'root_operation_id':'root','root_sha256':'f'*64} if selected else None)


@pytest.mark.parametrize('text',['进度？！','What IS the STATUS...','\u0085status\u0085','progreß','ﬆatus'])
def test_selected_work_status_does_not_read_unused_images_or_resolve_provider_before_admission_boundary(tmp_path,text):
    service,calls=unit(tmp_path)
    with pytest.raises(AdmissionBoundary):asyncio.run(service.send('original:'+'a'*64,packet(text)))
    assert calls==['member_resolution','admission_boundary'] and not (tmp_path/'not_created.json').exists()


@pytest.mark.parametrize('text',['修复进度显示','status and edit','\ufeffstatus'])
def test_non_status_work_input_keeps_existing_source_validation(tmp_path,text):
    service,calls=unit(tmp_path);service._member=lambda *a:object()
    with pytest.raises(InputBoundary):asyncio.run(service.send('original:'+'a'*64,packet(text)))
    assert calls==['input_validation']


def test_unselected_ordinary_progress_text_does_not_silently_become_a_saved_work_status_query(tmp_path):
    service,calls=unit(tmp_path)
    with pytest.raises(InputBoundary):asyncio.run(service.send('original:'+'a'*64,packet(selected=False)))
    assert calls==['input_validation']


def test_selected_work_without_existing_member_never_creates_an_owner_for_a_status_query(tmp_path):
    from packages.domain.store import Conflict
    service,calls=unit(tmp_path);service._member=lambda *a:None
    with pytest.raises(Conflict,match='existing original owner'):asyncio.run(service.send('original:'+'a'*64,packet()))
    assert calls==[] and not (tmp_path/'not_created.json').exists()


def test_selected_saved_status_does_not_claim_no_active_work_when_another_root_is_active():
    from apps.api.canonical_conversations import _original_work_status_text
    text=_original_work_status_text({'status':'RETURNED','root_status':'FAILED','active_relation':'OTHER_OR_UNVERIFIED_WORK'})
    assert '已返回' in text and '原根任务保存状态：执行失败' in text
    assert '当前另有活动执行' in text and '当前没有活动任务' not in text
    assert '不代表实时进度' in text


def test_unknown_status_metadata_never_becomes_a_user_controlled_success_label():
    from apps.api.canonical_conversations import _original_work_status_text
    text=_original_work_status_text({'status':'UNTRUSTED_HTML_OR_TASK_SUCCESS','root_status':'UNTRUSTED_HTML_OR_TASK_SUCCESS','active_relation':'NO_ACTIVE_WORK'})
    assert '状态待核查' in text and 'UNTRUSTED' not in text and '验收通过' in text

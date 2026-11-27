"""Exact private handoff DTO/native refusal checks, not Windows qualification."""
import copy
import hashlib
import json
import subprocess
import sys

import pytest

from packages.desktop.native_append_handoff import admit,HandoffRefused,verify_main_image


def body():
    return {'operation_id':'append-op','ticket_ref':{'id':'ticket','revision':1,'sha256':'a'*64},
        'activation_target':{'window_id_decimal':'903','pid':222,'process_creation_decimal':'134049827366302599',
            'identity_sha256':'b'*64,'edit_window_id_decimal':'904'},
        'activation_host':{'window_id_decimal':'900','pid':123,'process_creation_decimal':'134049827366302598',
            'os_session':1,'window_thread':20,'window_class':'Chrome_WidgetWin_1',
            'marker_name':'EEAS-NativeCollaboration-'+'c'*32,'marker_value_decimal':'9223372036854775806'},
        'baseline':{'text_sha256':'d'*64,'utf16_length':30,'selection_start':30,'selection_end':30,
            'bounds':{'x':-1900,'y':0,'width':1600,'height':900}},'expires_unix_ms':159000}


def test_filetime_and_marker_stay_exact_above_javascript_integer_range():
    result=admit(body(),now_ms=100000,parent_pid=123)
    assert result['window']['process_creation']==134049827366302599
    assert result['marker']['marker_value']==9223372036854775806


@pytest.mark.parametrize('change',[
    lambda b:b.update(approved=True),
    lambda b:b['activation_target'].update(process_creation_decimal=134049827366302600),
    lambda b:b['activation_host'].update(pid=456),
    lambda b:b['activation_host'].update(marker_name='foreign-marker'),
    lambda b:b['activation_target'].update(window_id_decimal='0903'),
    lambda b:b['activation_target'].update(pid=True),
    lambda b:b['baseline'].update(selection_end=29),
    lambda b:b.update(expires_unix_ms=100000),
    lambda b:b.update(expires_unix_ms=160001),
])
def test_untrusted_stale_partial_or_imprecise_handoff_rejected_before_os(change):
    request=copy.deepcopy(body());change(request)
    with pytest.raises(HandoffRefused):admit(request,now_ms=100000,parent_pid=123)


def test_real_helper_process_refuses_invalid_input_without_attempting_any_windows_action():
    result=subprocess.run([sys.executable,'-B','-m','packages.desktop.native_append_handoff'],
        input=json.dumps({'hwnd':903}),text=True,capture_output=True,check=False,timeout=10)
    assert result.returncode==1
    receipt=json.loads(result.stdout)
    assert receipt['status']=='OS_REFUSED' and receipt['input_dispatched'] is False
    assert receipt['automatic_retry'] is False


def test_packaged_main_larger_than_notepad_limit_uses_exact_streamed_manifest_bytes(tmp_path):
    # Sparse fixture keeps disk use low while exercising the real >128MiB hash.
    image=tmp_path/'electron.exe'
    size=129*1024*1024+3
    with image.open('wb') as stream:
        stream.seek(size-3);stream.write(b'end')
    checksum=hashlib.sha256()
    with image.open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):checksum.update(part)
    record={'bytes':size,'sha256':checksum.hexdigest()}
    verify_main_image(image,record)
    with image.open('r+b') as stream:stream.write(b'changed')
    with pytest.raises(HandoffRefused,match='DRIFT'):verify_main_image(image,record)


@pytest.mark.parametrize('size',[True,0,512*1024*1024+1])
def test_packaged_main_length_is_bounded_before_file_access(tmp_path,size):
    with pytest.raises(HandoffRefused,match='SIZE_REQUIRED'):
        verify_main_image(tmp_path/'absent.exe',{'bytes':size,'sha256':'a'*64})


def test_packaged_main_rejects_length_drift_and_linked_image(tmp_path):
    image=tmp_path/'image.exe';image.write_bytes(b'actual')
    record={'bytes':5,'sha256':hashlib.sha256(b'actual').hexdigest()}
    with pytest.raises(HandoffRefused,match='DRIFT'):verify_main_image(image,record)
    linked=tmp_path/'electron.exe';linked.symlink_to(image)
    from packages.domain.store import Conflict
    with pytest.raises(Conflict,match='link or reparse'):
        verify_main_image(linked,{**record,'bytes':6})

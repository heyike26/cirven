"""Real local file scope and capture storage; native target is an explicit stand-in."""
from io import BytesIO
import hashlib
import pytest
from PIL import Image
from packages.domain.desktop_host import DesktopHost
from packages.domain.store import Conflict,NotFound
from packages.domain.local_workspaces import digest
from test_project_intake import project


class NoNativeDocuments:
    def __init__(self,**authorities):
        self.backend=None;self.authorities=authorities;self.closed=False
    def describe(self):return {'status':'UNAVAILABLE','native_windows':'NOT_RUN'}
    def snapshot(self,pid,view_ref,nonce):
        ref=self.source
        assert self.authorities['action_authority'](pid,'CAPTURE_NATIVE',ref,nonce)
        image=Image.new('RGB',(100,80),'#a2aebf');output=BytesIO();image.save(output,format='PNG');raw=output.getvalue()
        frame={'view_ref':view_ref,'frame_revision':1,'sha256':hashlib.sha256(raw).hexdigest(),'width':100,'height':80,
            'observed_file_source_ref':ref,'file_changed_during_capture':False,
            'document_binding':'PROTOCOL_MOCK_NOT_NATIVE_TARGET'}
        frame['binding_sha256']=digest(frame)
        return {'png':raw,'frame_ref':frame}
    def close(self):self.closed=True;return {'status':'CLOSED','app_processes_terminated':False}


def broker(client,pid,tmp_path):
    folder=tmp_path.parent/(tmp_path.name+'-desktop-file');folder.mkdir();(folder/'input.txt').write_text('原创真实资料')
    ws=client.app.state.local_workspaces
    from packages.domain.local_workspaces import WorkspaceInput
    bound=ws.bind(pid,WorkspaceInput(root=str(folder),idempotency_key='native-folder'))
    ref=ws.read(pid,bound['id'],'input.txt')['source_ref']
    value=DesktopHost(client.app.state.store,ws,tmp_path/'broker-data',token='a'*64,
        owner_pid=123,documents_factory=NoNativeDocuments)
    value.documents.source=ref
    return value,ref,folder


def test_main_attestation_is_required_and_grant_is_exact_project_intent_file_and_single_use(client,tmp_path):
    pid=project(client);host,ref,_=broker(client,pid,tmp_path)
    host.check_host('a'*64)
    for secret in [None,'b'*64,'','a'*63]:
        with pytest.raises(Conflict):host.check_host(secret)
    first=host.grant(pid,'CAPTURE_NATIVE',ref)['user_action_ref']
    assert not host.authorize(pid,'OPEN_NATIVE',ref,first)
    assert not host.authorize(pid,'CAPTURE_NATIVE',ref,first)
    second=host.grant(pid,'CAPTURE_NATIVE',ref)['user_action_ref']
    assert host.authorize(pid,'CAPTURE_NATIVE',ref,second)
    assert not host.authorize(pid,'CAPTURE_NATIVE',ref,second)
    other=project(client)
    with pytest.raises(Conflict):host.grant(other,'CAPTURE_NATIVE',ref)


def test_authoritative_selected_file_observes_external_save_and_rejects_links(client,tmp_path):
    pid=project(client);host,ref,folder=broker(client,pid,tmp_path)
    selected=host.selection(pid,ref);assert selected['source_ref']==ref and selected['current']
    (folder/'input.txt').write_text('用户外部保存的更新')
    changed=host.selection(pid,ref);assert changed['source_ref']['revision']==ref['revision']+1
    assert changed['source_ref']['sha256']!=ref['sha256']
    (folder/'input.txt').unlink();(folder/'input.txt').symlink_to(tmp_path/'outside.txt');(tmp_path/'outside.txt').write_text('不能读取')
    with pytest.raises(Exception):host.selection(pid,ref)


def test_capture_is_real_png_with_immutable_source_scope_and_survives_controller_restart(client,tmp_path):
    pid=project(client);host,ref,_=broker(client,pid,tmp_path);view='1'*32
    grant=host.grant(pid,'CAPTURE_NATIVE',ref)['user_action_ref'];captured=host.capture(pid,view,grant)
    bitmap={'kind':'NATIVE_CAPTURE','view_ref':view,'frame_revision':1,
        'sha256':captured['frame_ref']['sha256'],'binding_sha256':captured['frame_ref']['binding_sha256']}
    actual=host.bitmap(pid,bitmap);assert actual['bytes'].startswith(b'\x89PNG\r\n\x1a\n')
    assert actual['width']==100 and actual['height']==80
    restarted=DesktopHost(client.app.state.store,client.app.state.local_workspaces,tmp_path/'broker-data',
        token='b'*64,owner_pid=456,documents_factory=NoNativeDocuments)
    assert restarted.bitmap(pid,bitmap)['sha256']==actual['sha256']
    with pytest.raises(Conflict):restarted.bitmap(pid,{**bitmap,'sha256':'0'*64})
    with pytest.raises((NotFound,Conflict)):
        restarted.bitmap(project(client),bitmap)
    assert host.close()['app_processes_terminated'] is False
    assert host.grants=={} and host.surfaces=={}

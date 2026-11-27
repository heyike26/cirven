from io import BytesIO
import base64
import json
import time

from docx import Document
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from packages.adapters.native_file_view import selected_file_view
from packages.domain.local_workspaces import LocalWorkspaces, WorkspaceInput, digest
from packages.domain.store import Conflict, Store
from test_native_messages import application, ModelWire, TOKEN


def source(store, data, folder, name, raw):
    folder.mkdir(exist_ok=True)
    (folder/name).write_bytes(raw)
    ws=LocalWorkspaces(store,data)
    pid=store.create_project('真实只读原件','')['id']
    binding=ws.bind(pid,WorkspaceInput(idempotency_key='readonly-original',root=str(folder),allowed_write_paths=[]))
    return ws,ws.read(pid,binding['id'],name)['source_ref']


def docx_bytes():
    doc=Document();doc.add_paragraph('独立原件正文：反馈应保护人工研究笔记。')
    doc.add_table(rows=1,cols=1).cell(0,0).text='真实表格中的验收条件'
    out=BytesIO();doc.save(out);return out.getvalue()


def test_original_docx_text_and_tables_are_bounded_readonly_without_window_identity(tmp_path):
    raw=docx_bytes();ws,ref=source(Store(tmp_path/'state/db.sqlite3'),tmp_path/'state',tmp_path/'files','原件.docx',raw)
    value=selected_file_view(ws,ref)
    assert '独立原件正文' in value['text_content'] and '真实表格中的验收条件' in value['text_content']
    assert value['file_source_ref']==ref and not value['native_document_window_identity_verified']
    assert value['automatic_input_authorized'] is False
    assert (tmp_path/'files/原件.docx').read_bytes()==raw
    (tmp_path/'files/原件.docx').write_bytes(docx_bytes()+b' human edit')
    with pytest.raises(Conflict,match='version changed'):selected_file_view(ws,ref)


def test_actual_selected_image_needs_declared_vision_and_keeps_original_and_view_hashes(tmp_path):
    image=Image.new('RGB',(1600,800),'#ccddee');out=BytesIO();image.save(out,format='PNG')
    ws,ref=source(Store(tmp_path/'state/db.sqlite3'),tmp_path/'state',tmp_path/'files','figure.png',out.getvalue())
    unqualified=selected_file_view(ws,ref)
    assert unqualified['format']=='image' and 'IMAGE_TEXT_NOT_EXTRACTED' in unqualified['warnings']
    assert 'image_view' not in unqualified
    actual=selected_file_view(ws,ref,supports_images=True)
    metadata=json.loads(actual.content[0].text)
    assert metadata['image_view']['original_sha256']==ref['sha256']
    assert metadata['image_view']['source']=='EXPLICIT_SELECTED_FILE_NOT_NATIVE_WINDOW_FRAME'
    with Image.open(BytesIO(base64.b64decode(actual.content[1].data))) as preview:assert preview.size==(1280,640)
    assert (tmp_path/'files/figure.png').read_bytes()==out.getvalue()


def test_selected_text_truncation_and_binding_change_are_explicit(tmp_path):
    raw=('长原件🙂'*16001).encode()
    ws,ref=source(Store(tmp_path/'state/db.sqlite3'),tmp_path/'state',tmp_path/'files','source.py',raw)
    value=selected_file_view(ws,ref)
    assert len(value['text_content'])==64000 and value['content_truncated']
    wrong={**ref,'binding_revision':ref['binding_revision']+1}
    with pytest.raises(Conflict,match='binding changed'):selected_file_view(ws,wrong)


def test_real_original_cli_and_official_mcp_read_the_selected_docx_then_replay_and_detect_human_edit(tmp_path):
    wire=ModelWire('read_selected_file')
    try:
        app,service,profile=application(tmp_path/'state',wire)
        raw=docx_bytes();ws,ref=source(app.state.store,tmp_path/'state',tmp_path/'files','原件.docx',raw)
        app.state.local_workspaces=ws
        body={'operation_id':'original-docx-observe','raw_text':'解释所选原件中的验收条件，不修改。','file_source_ref':ref,
            'provider_profile_ref':{'id':profile['id'],'revision':1,'sha256':digest(profile)}}
        with TestClient(app) as client:
            client.headers['X-EEAS-Desktop-Host']=TOKEN
            posted=client.post('/api/desktop-host/native-messages',json=body)
            assert posted.status_code==200,posted.text
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                result=client.get('/api/desktop-host/native-messages/original-docx-observe').json()
                if result['status']!='PROCESSING':break
                time.sleep(.1)
            assert result['status']=='RETURNED',result
            assert '真实表格中的验收条件' in result['text']
            assert result['file_review_status']=='CURRENT' and result['file_source_ref']==ref
            assert result['changes_applied'] is False and result['automatic_input_authorized'] is False
            assert len(wire.calls)==2 and wire.tool_read[0]['result']['file_source_ref']==ref
            assert (tmp_path/'files/原件.docx').read_bytes()==raw
            assert client.post('/api/desktop-host/native-messages',json=body).json()['execution']==result['execution']
            (tmp_path/'files/原件.docx').write_bytes(raw+b' human edit')
            stale=client.get('/api/desktop-host/native-messages/original-docx-observe').json()
            assert stale['file_review_status']=='STALE_OR_UNAVAILABLE' and len(wire.calls)==2
            rejected=client.post('/api/desktop-host/native-messages',json={**body,'operation_id':'do-not-read-stale'})
            assert rejected.status_code==409 and len(wire.calls)==2
    finally:wire.close()

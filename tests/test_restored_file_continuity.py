"""Real backup/verify/restore, SQLite, OOXML and API; no model or Office process.

Program executables below are inert compatibility fixtures, never launched.
The existing revision helper declares its external owner-admission fixture.
"""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil

from docx import Document
from fastapi.testclient import TestClient
import pytest

from apps.api.config import Settings
from apps.api.main import create_app
from scripts import native_user_state as recovery
from test_docx_revision_native_open import prepare
from test_native_open import ROOT, TOKEN

REPO = Path(os.environ.get('EEAS_TEST_SOURCE_ROOT', Path(__file__).resolve().parents[1]))


def installation(root):
    root.mkdir()
    payload = {name: b'INERT COMPATIBILITY FIXTURE NEVER EXECUTED' for name in recovery.COMPATIBILITY}
    payload.update({p.relative_to(REPO).as_posix(): p.read_bytes()
                    for p in (REPO/'packages/domain/migrations').glob('*.sql')})
    rows = []
    for name, raw in payload.items():
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        rows.append({'path': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
    (root/'PACKAGE_MANIFEST.json').write_text(json.dumps({
        'schema': 'eeas.native-companion-package.v1', 'files': rows}), encoding='utf8')
    return root


@contextmanager
def api(data):
    settings = Settings(data)
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        csrf = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'}).json()['csrf_token']
        client.headers.update({'Origin': settings.origin, 'X-EEAS-CSRF': csrf,
                               'X-EEAS-Desktop-Host': TOKEN})
        yield client, settings


@pytest.fixture
def restored_revision(tmp_path, monkeypatch):
    monkeypatch.setenv('EEAS_DESKTOP_HOST_TOKEN', TOKEN)
    monkeypatch.setenv('EEAS_DESKTOP_HOST_PID', '123')
    source = installation(tmp_path/'source program')
    folder = tmp_path/'external workspace'
    folder.mkdir()
    (folder/'材料.md').write_text('真实保存资料，保留已有人工修改。', encoding='utf8')
    with api(source/'user-data') as (client, settings):
        pid = client.post('/api/projects', json={'name': '原稿与恢复'}).json()['id']
        binding = client.post(f'/api/projects/{pid}/local-workspaces', json={
            'idempotency_key': 'original', 'root': str(folder), 'allowed_write_paths': ['*']}).json()
        base = f'/api/projects/{pid}/local-workspaces/{binding["id"]}'
        source_ref = client.get(base+'/file', params={'path': '材料.md'}).json()['source_ref']
        scene = (client, pid, folder, base, source_ref, settings)
        _, _, continuity, service, original, original_link, artifact, _, body = prepare(scene)
        opened = client.post(ROOT+'/report', json=body)
        assert opened.status_code == 200, opened.text
        link = opened.json()['file_link_ref']
        working = Path(opened.json()['native_path'])
        document = Document(working)
        document.add_paragraph('恢复前用户已经保存的人工正文，必须保留。')
        document.save(working)
        human = working.read_bytes()
        immutable = service.download(pid, artifact['id'])[0]
        record_path = continuity.data_dir/'file-continuity/links'/(link['id']+'.json')
        original_record = record_path.read_bytes()
        assert client.post(ROOT+'/report', json=body).json()['file_link_ref'] == link
    backup = tmp_path/'private backup'
    saved = recovery.backup(source, backup)
    recovery.verify(backup, saved['manifest_sha256'])
    target = installation(tmp_path/'restored program')
    result = recovery.restore(backup, target, saved['manifest_sha256'])
    assert result['status'] == 'RESTORE_COMPLETE'
    return dict(source=source, target=target, pid=pid, artifact=artifact, body=body, link=link,
                human=human, immutable=immutable, original_record=original_record,
                original_link=original_link, snapshot_sha256=saved['manifest_sha256'])


def working_path(v):
    return v['target']/'user-data/native-working-copies'/v['pid']/v['artifact']['id']/'docx_revision'/v['artifact']['filename']


def test_real_restore_reopens_revision_and_observes_manual_bytes_without_rewriting_history(restored_revision, tmp_path):
    v = restored_revision
    data = v['target']/'user-data'
    record = data/'file-continuity/links'/(v['link']['id']+'.json')
    assert record.read_bytes() == v['original_record']
    with api(data) as (client, _):
        opened = client.post(ROOT+'/report', json=v['body'])
        assert opened.status_code == 200, opened.text
        assert opened.json()['file_link_ref'] == v['link']
        assert opened.json()['created'] is False and opened.json()['working_copy_modified'] is True
        resolved = client.post(ROOT+'/link', json={'link_ref': v['link']})
        assert resolved.status_code == 200, resolved.text
        assert resolved.json()['current_sha256'] == hashlib.sha256(v['human']).hexdigest()
        observed = client.app.state.file_continuity.observe(v['link'])
        assert '恢复前用户已经保存的人工正文' in observed['parsed']['text_content']
        assert client.get(v['artifact']['download_url']).content == v['immutable']
    assert working_path(v).read_bytes() == v['human'] and record.read_bytes() == v['original_record']
    proof = json.loads((data/'restore-provenance.json').read_bytes())
    index = data/proof['managed_file_bindings_ref']['path']
    index_before = index.read_bytes()
    assert hashlib.sha256(index_before).hexdigest() == proof['managed_file_bindings_ref']['sha256']
    # A second real backup+restore rebinds again, preserving the first index and link.
    backup = tmp_path/'second private backup'
    saved = recovery.backup(v['target'], backup)
    third = installation(tmp_path/'second restored program')
    recovery.restore(backup, third, saved['manifest_sha256'])
    with api(third/'user-data') as (client, _):
        opened = client.post(ROOT+'/report', json=v['body'])
        assert opened.status_code == 200, opened.text
        assert opened.json()['file_link_ref'] == v['link']
        assert Path(opened.json()['native_path']).read_bytes() == v['human']
    assert (third/'user-data'/proof['managed_file_bindings_ref']['path']).read_bytes() == index_before
    assert (third/'user-data/file-continuity/links'/(v['link']['id']+'.json')).read_bytes() == v['original_record']


@pytest.mark.parametrize('damage', ['absent_provenance', 'index_bytes', 'saved_identity',
                                   'link_hash', 'target_root', 'replaced_parent'])
def test_reopen_and_direct_link_fail_closed_for_unadmitted_or_changed_parent(restored_revision, damage):
    v = restored_revision
    data = v['target']/'user-data'
    marker = data/'restore-provenance.json'
    proof = json.loads(marker.read_bytes())
    index = data/proof['managed_file_bindings_ref']['path']
    saved = json.loads(index.read_bytes())
    if damage == 'absent_provenance':
        marker.unlink()
    elif damage == 'index_bytes':
        index.write_bytes(index.read_bytes()+b' ')
    elif damage == 'target_root':
        proof['target_data_dir'] = str(v['source']/'user-data')
        marker.write_text(json.dumps(proof), encoding='utf8')
    elif damage == 'replaced_parent':
        parent = working_path(v).parent
        held = parent.with_name(parent.name+'-held-original')
        parent.rename(held)
        shutil.copytree(held, parent)
    else:
        binding = saved['bindings'][v['link']['id']]
        if damage == 'saved_identity':
            binding['saved_parent_identity']['inode'] += 1
        else:
            binding['reference']['sha256'] = 'f'*64
        index.write_text(json.dumps(saved), encoding='utf8')
        proof['managed_file_bindings_ref'].update(bytes=index.stat().st_size,
            sha256=hashlib.sha256(index.read_bytes()).hexdigest())
        marker.write_text(json.dumps(proof), encoding='utf8')
    with api(data) as (client, _):
        assert client.post(ROOT+'/report', json=v['body']).status_code == 409
        assert client.post(ROOT+'/link', json={'link_ref': v['link']}).status_code == 409
    assert working_path(v).read_bytes() == v['human']
    assert (data/'file-continuity/links'/(v['link']['id']+'.json')).read_bytes() == v['original_record']

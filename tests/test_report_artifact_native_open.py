"""Actual API/Store/PPTX/managed-file continuity; no OS shell or model stand-in.

Checks are explicit so normal and python -O exercise identical requirements.
"""
from io import BytesIO
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
from pptx import Presentation
import pytest

from apps.api.config import Settings
from apps.api.main import create_app
from packages.domain.file_continuity import FileContinuity
from packages.domain.linked_requirements import FrozenLinkedRequirements
from packages.domain.local_workspaces import canonical, digest, sha
from packages.domain.store import Conflict

TOKEN = '8' * 64
ROUTE = '/api/desktop-host/native-open/report'


def check(value, message='report artifact native-open requirement failed'):
    if not value:
        raise AssertionError(message)


def admit(client, settings):
    bootstrap = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'})
    check(bootstrap.status_code == 200, bootstrap.text)
    client.headers.update({'Origin': settings.origin, 'X-EEAS-CSRF': bootstrap.json()['csrf_token'],
                          'X-EEAS-Desktop-Host': TOKEN})


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setenv('EEAS_DESKTOP_HOST_TOKEN', TOKEN)
    monkeypatch.setenv('EEAS_DESKTOP_HOST_PID', str(os.getpid()))
    settings = Settings(tmp_path / 'controller')
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        admit(client, settings)
        response = client.post('/api/projects', json={'name': '插件产物原生打开'})
        check(response.status_code == 201, response.text)
        pid = response.json()['id']
        document = client.post(f'/api/projects/{pid}/documents',
            files={'file': ('来源.txt', '以真实来源生成，保留人工编辑。'.encode())},
            data={'source': 'synthetic:original-native-artifact-fixture'})
        check(document.status_code == 201, document.text)
        value = document.json()
        source = {'kind': 'document', 'project_id': pid, 'document_id': value['id'],
                  'revision': value['revision'], 'sha256': value['sha256']}
        generated = client.post(f'/api/projects/{pid}/report-artifacts', json={
            'idempotency_key': 'original_pptx', 'filename': '真实演示稿.pptx',
            'report': {'schema_version': 'eeas.report-deck.v1', 'title': '实际原始标题',
                       'source_refs': [source], 'sections': [{'title': '核对结果',
                       'paragraphs': ['原生 Office 渲染尚未验收。']}]}})
        check(generated.status_code == 201, generated.text)
        artifact = generated.json()['artifact_manifest']
        original = client.get(artifact['download_url'])
        check(original.status_code == 200, original.text)
        yield {'client': client, 'pid': pid, 'artifact': artifact, 'raw': original.content,
               'settings': settings}


def body(scene):
    return {'project_id': scene['pid'], 'artifact_id': scene['artifact']['id'],
            'sha256': scene['artifact']['sha256'], 'download_url': scene['artifact']['download_url']}


def opened(scene):
    response = scene['client'].post(ROUTE, json=body(scene))
    check(response.status_code == 200, response.text)
    return response.json()


def test_exact_registered_pptx_opens_managed_copy_and_observes_without_inventing_requirement_basis(scene):
    value = opened(scene)
    path = Path(value['native_path'])
    check(path.read_bytes() == scene['raw'] and value['created'] is True)
    check(value['working_copy_modified'] is False and value['immutable_artifact_modified'] is False)
    check(value['source_sha256'] == sha(scene['raw']) and value['active_document_identity_verified'] is False)
    check(path.resolve().is_relative_to(scene['settings'].data_dir / 'native-working-copies'))
    continuity = scene['client'].app.state.file_continuity
    link = continuity.get(value['file_link_ref'])
    check(link['kind'] == 'ARTIFACT_WORKING_COPY')
    check(link['origin']['kind'] == 'GENERATED_REPORT_ARTIFACT')
    check(link['origin']['artifact_ref'] == {'id': scene['artifact']['id'], 'revision': 1, 'sha256': sha(scene['raw'])})
    check(not FrozenLinkedRequirements.eligible(link))
    requirements = FrozenLinkedRequirements(continuity, scene['client'].app.state.planning_reports).read(value['file_link_ref'])
    check(requirements['status'] == 'UNAVAILABLE' and requirements['reason'] == 'ORIGINAL_REQUIREMENTS_NOT_LINKED_TO_REPORT')
    observation = scene['client'].post('/api/desktop-host/file-contexts/observe', json={'link_ref': value['file_link_ref']})
    check(observation.status_code == 200, observation.text)
    observed = observation.json()
    check(observed['status'] == 'UNCHANGED' and observed['parsed']['format'] == 'pptx')
    check('实际原始标题' in observed['parsed']['text_content'])
    check(observed['unsaved_changes'] == 'NOT_OBSERVED' and observed['write_authorized'] is False)
    check(observed['coverage'] == 'REGISTERED_SAVED_FILE_BYTES_NOT_APPLICATION_MEMORY')


def test_repeated_open_and_app_reload_keep_atomic_human_save_and_immutable_original(scene):
    first = opened(scene)
    path = Path(first['native_path'])
    presentation = Presentation(BytesIO(scene['raw']))
    presentation.slides[0].shapes.title.text = '人工保存的新标题'
    stream = BytesIO(); presentation.save(stream)
    human = stream.getvalue()
    replacement = path.with_name('manual-save.pptx')
    replacement.write_bytes(human); replacement.replace(path)
    again = opened(scene)
    check(again['created'] is False and again['working_copy_modified'] is True)
    check(again['file_link_ref'] == first['file_link_ref'] and path.read_bytes() == human)
    settings = scene['settings']
    with TestClient(create_app(settings), base_url=settings.origin) as restarted:
        admit(restarted, settings)
        response = restarted.post(ROUTE, json=body(scene))
        check(response.status_code == 200, response.text)
        check(response.json()['file_link_ref'] == first['file_link_ref'])
        observation = restarted.post('/api/desktop-host/file-contexts/observe', json={'link_ref': first['file_link_ref']})
        check(observation.status_code == 200, observation.text)
        observed = observation.json()
        check(observed['status'] == 'CHANGED' and observed['current_sha256'] == sha(human))
        check('人工保存的新标题' in observed['parsed']['text_content'])
        check(restarted.get(scene['artifact']['download_url']).content == scene['raw'])
    check(path.read_bytes() == human)


def test_nonexact_foreign_and_cross_project_urls_never_create_working_copy(scene):
    urls = [scene['artifact']['download_url'] + suffix for suffix in ['?download=1', '#page1', '/', '/extra']]
    urls += ['https://example.invalid/report.pptx', 'file:///D:/private.pptx',
             '/api/projects/other/report-artifacts/' + 'a' * 64 + '/download']
    for url in urls:
        request = body(scene); request['download_url'] = url
        response = scene['client'].post(ROUTE, json=request)
        check(response.status_code == 409, response.text)
        check(not (scene['settings'].data_dir / 'native-working-copies').exists())


def test_cross_project_card_and_wrong_sha_fail_before_copy_even_for_valid_project(scene):
    client = scene['client']
    other = client.post('/api/projects', json={'name': '另一同名工程'}).json()['id']
    request = body(scene); request.update(project_id=other,
        download_url=f'/api/projects/{other}/report-artifacts/{scene["artifact"]["id"]}/download')
    check(client.post(ROUTE, json=request).status_code == 404)
    request = body(scene); request['sha256'] = '0' * 64
    check(client.post(ROUTE, json=request).status_code == 409)
    check(not (scene['settings'].data_dir / 'native-working-copies').exists())
    check(client.get(scene['artifact']['download_url']).content == scene['raw'])


def test_tampered_registered_bytes_are_rejected_without_touching_working_copy(scene):
    first = opened(scene)
    path = Path(first['native_path']); path.write_bytes(b'preserve my unsaved-to-disk replacement')
    immutable = scene['settings'].data_dir / 'report-artifacts' / scene['pid'] / scene['artifact']['id'] / 'report.pptx'
    immutable.write_bytes(scene['raw'] + b'tampered immutable artifact')
    response = scene['client'].post(ROUTE, json=body(scene))
    check(response.status_code == 409, response.text)
    check(path.read_bytes() == b'preserve my unsaved-to-disk replacement')


def test_resigned_unsafe_filename_is_still_rejected_before_any_copy(scene):
    manifest_path = scene['settings'].data_dir / 'report-artifacts' / scene['pid'] / scene['artifact']['id'] / 'manifest.json'
    wrapper = json.loads(manifest_path.read_text())
    wrapper['manifest']['filename'] = '../escape.pptx'
    wrapper['manifest_sha256'] = digest(wrapper['manifest'])
    manifest_path.write_text(canonical(wrapper))
    response = scene['client'].post(ROUTE, json=body(scene))
    check(response.status_code == 409, response.text)
    check(not (scene['settings'].data_dir / 'native-working-copies').exists())


def test_working_copy_link_escape_is_rejected_and_outside_file_unchanged(scene, tmp_path):
    outside = tmp_path / 'outside.pptx'; outside.write_bytes(b'foreign bytes')
    parent = scene['settings'].data_dir / 'native-working-copies' / scene['pid'] / scene['artifact']['id'] / 'artifact_pptx'
    parent.mkdir(parents=True)
    (parent / scene['artifact']['filename']).symlink_to(outside)
    response = scene['client'].post(ROUTE, json=body(scene))
    check(response.status_code == 422, response.text)
    check(outside.read_bytes() == b'foreign bytes')
    check(not (scene['settings'].data_dir / 'file-continuity' / 'links').exists())


def test_existing_source_receipt_link_cannot_write_outside_or_create_working_copy(scene, tmp_path):
    outside = tmp_path / 'private-source.json'; outside.write_bytes(b'must remain untouched')
    parent = scene['settings'].data_dir / 'native-working-copies' / scene['pid'] / scene['artifact']['id'] / 'artifact_pptx'
    parent.mkdir(parents=True)
    target = parent / scene['artifact']['filename']
    target.with_name(target.name + '.source.json').symlink_to(outside)
    response = scene['client'].post(ROUTE, json=body(scene))
    check(response.status_code == 422, response.text)
    check(not target.exists() and outside.read_bytes() == b'must remain untouched')


def test_existing_hardlink_to_immutable_artifact_is_never_returned_as_editable_copy(scene):
    immutable = scene['settings'].data_dir / 'report-artifacts' / scene['pid'] / scene['artifact']['id'] / 'report.pptx'
    parent = scene['settings'].data_dir / 'native-working-copies' / scene['pid'] / scene['artifact']['id'] / 'artifact_pptx'
    parent.mkdir(parents=True)
    os.link(immutable, parent / scene['artifact']['filename'])
    response = scene['client'].post(ROUTE, json=body(scene))
    check(response.status_code == 409, response.text)
    check(immutable.read_bytes() == scene['raw'])
    check(not (scene['settings'].data_dir / 'file-continuity' / 'links').exists())


def test_resigned_registry_url_drift_is_rejected_before_creating_working_copy(scene):
    manifest_path = scene['settings'].data_dir / 'report-artifacts' / scene['pid'] / scene['artifact']['id'] / 'manifest.json'
    wrapper = json.loads(manifest_path.read_text()); wrapper['manifest']['download_url'] += '?untrusted=1'
    wrapper['manifest_sha256'] = digest(wrapper['manifest']); manifest_path.write_text(canonical(wrapper))
    response = scene['client'].post(ROUTE, json=body(scene))
    check(response.status_code == 409, response.text)
    check(not (scene['settings'].data_dir / 'native-working-copies').exists())


def test_replaced_managed_parent_does_not_mint_new_authority_or_update_old_link(scene):
    first = opened(scene); path = Path(first['native_path'])
    prior = path.parent.with_name('retained-old-parent'); path.parent.rename(prior)
    path.parent.mkdir(); path.write_bytes(scene['raw'])
    response = scene['client'].post(ROUTE, json=body(scene))
    check(response.status_code == 409, response.text)
    observation = scene['client'].post('/api/desktop-host/file-contexts/observe', json={'link_ref': first['file_link_ref']})
    check(observation.status_code == 409, observation.text)
    check((prior / path.name).read_bytes() == scene['raw'] and path.read_bytes() == scene['raw'])


def test_artifact_registration_needs_real_reader_and_private_host_authority(scene):
    first = opened(scene); client = scene['client']
    continuity = FileContinuity(client.app.state.store, scene['settings'].data_dir, client.app.state.local_workspaces)
    with pytest.raises(Conflict):
        continuity.register_artifact(scene['pid'], scene['artifact']['id'], scene['artifact']['filename'],
                                     scene['artifact']['sha256'], scene['artifact']['download_url'])
    client.headers.pop('X-EEAS-Desktop-Host')
    check(client.post(ROUTE, json=body(scene)).status_code == 409)
    check(Path(first['native_path']).read_bytes() == scene['raw'])

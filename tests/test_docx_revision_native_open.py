"""Actual public API/SQLite/immutable OOXML; OS/model execution NOT_RUN.

The real revision service uses a declared owner-admission fixture. File storage,
renderer, revision authoring, source readers and native-open routes are actual.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from packages.domain.linked_requirements import FrozenLinkedRequirements
from packages.domain.local_workspaces import canonical, digest, sha
from test_native_open import scene, report, ROOT
from test_docx_revision_continuity import publish_revision, check


def prepare(scene):
    client, pid, folder, base, source, settings = scene
    original = report(scene)
    response = client.post(ROOT+'/report', json={'project_id':pid, 'artifact_id':original['id'],
        'sha256':original['sha256'], 'download_url':original['download_url']})
    check(response.status_code == 200, response.text)
    link = response.json()['file_link_ref']
    continuity = client.app.state.file_continuity
    service, artifact, saved = publish_revision(continuity, pid, link)
    body = {'project_id':pid, 'artifact_id':artifact['id'], 'sha256':artifact['sha256'],
        'download_url':artifact['download_url']}
    return client, pid, continuity, service, original, link, artifact, saved, body


def test_real_revision_native_open_reopen_atomic_user_save_and_frozen_requirements(scene):
    from docx import Document
    import os
    client, pid, continuity, service, original, original_link, artifact, saved, body = prepare(scene)
    before = FrozenLinkedRequirements(continuity, client.app.state.planning_reports).read(original_link)
    response = client.post(ROOT+'/report', json=body)
    check(response.status_code == 200, response.text)
    opened = response.json(); path = Path(opened['native_path'])
    raw = service.download(pid, artifact['id'])[0]
    downloaded = client.get(artifact['download_url'])
    check(downloaded.status_code == 200 and downloaded.content == raw, downloaded.text[:200])
    metadata = client.get(artifact['download_url'].removesuffix('/download'))
    check(metadata.status_code == 200 and metadata.json() == artifact)
    check(opened['created'] and path.read_bytes() == raw and opened['source_sha256'] == artifact['sha256'])
    check(opened['file_link_ref'] != original_link and not opened['immutable_artifact_modified'])
    after = FrozenLinkedRequirements(continuity, client.app.state.planning_reports).read(opened['file_link_ref'])
    check(before['text_content'] == after['text_content'])
    check(after['report_artifact_ref']['sha256'] == original['sha256'] != artifact['sha256'])
    doc = Document(path); doc.add_paragraph('原软件后续保存的人工内容，应保留。')
    temporary = path.with_name('human-atomic.docx'); doc.save(temporary); os.replace(temporary,path)
    human = path.read_bytes()
    second = client.post(ROOT+'/report', json=body)
    check(second.status_code == 200, second.text)
    check(not second.json()['created'] and second.json()['working_copy_modified'])
    check(second.json()['file_link_ref'] == opened['file_link_ref'] and path.read_bytes() == human)
    exact = client.post(ROOT+'/link', json={'link_ref':opened['file_link_ref']})
    check(exact.status_code == 200, exact.text)
    check(exact.json()['current_sha256'] == sha(human) and exact.json()['working_copy_modified'])
    check(service.download(pid,artifact['id'])[0] == raw)
    check(client.get(original['download_url']).content != raw)
    observed = continuity.observe(opened['file_link_ref'])
    check('原软件后续保存的人工内容' in observed['parsed']['text_content'])
    check(FrozenLinkedRequirements(continuity, client.app.state.planning_reports).read(opened['file_link_ref']) == after)


def test_revision_open_rejects_cross_project_wrong_hash_old_artifact_and_url(scene):
    client, pid, continuity, service, original, original_link, artifact, saved, body = prepare(scene)
    other = client.post('/api/projects', json={'name':'另一个工程'}).json()['id']
    cases = [
        {**body,'project_id':other},
        {**body,'sha256':original['sha256']},
        {**body,'artifact_id':original['id']},
        {**body,'download_url':artifact['download_url'].replace(pid,other)},
        {**body,'download_url':'file:///D:/arbitrary.docx'},
    ]
    for invalid in cases:
        rejected = client.post(ROOT+'/report',json=invalid)
        check(rejected.status_code in {404,409}, rejected.text)
    check(service.download(pid,artifact['id'])[1] == artifact)
    path = continuity.data_dir/'native-working-copies'/pid/artifact['id']/'docx_revision'/artifact['filename']
    check(not path.exists(), 'rejected cards must not create a working copy')


def test_actual_workspace_delivery_revision_keeps_original_basis_separate_from_new_copy(scene,tmp_path):
    from test_delivery_report_discussion_continuity import prepare as prepare_delivery
    client, _, folder, _, _, settings = scene
    delivered = prepare_delivery(client, settings, folder/'fresh-delivery-project', tmp_path/'delivery-evidence')
    pid, continuity = delivered['pid'], client.app.state.file_continuity
    opened = client.post(ROOT+'/file',json={'project_id':pid,'file_source_ref':delivered['generated'],
        'delivery_operation_id':delivered['operation']})
    check(opened.status_code == 200,opened.text)
    parent = opened.json()['file_link_ref']
    service, artifact, _ = publish_revision(continuity,pid,parent)
    response = client.post(ROOT+'/report',json={'project_id':pid,'artifact_id':artifact['id'],
        'sha256':artifact['sha256'],'download_url':artifact['download_url']})
    check(response.status_code == 200,response.text)
    new_link = response.json()['file_link_ref']
    reader = FrozenLinkedRequirements(continuity,client.app.state.planning_reports)
    original, derived = reader.read(parent), reader.read(new_link)
    check(derived['text_content'] == original['text_content'])
    check(original['opened_file_baseline_ref'] == delivered['generated'])
    check(derived['opened_file_baseline_ref'] is None, 'a managed revision is not the old physical workspace file')
    check(derived['generated_file_source_ref'] == delivered['generated'])
    check(derived['original_report_link_ref'] == parent)
    check(derived['report_artifact_ref']['sha256'] == delivered['artifact']['sha256'] != artifact['sha256'])
    check(delivered['path'].read_bytes() == delivered['original'])


@pytest.mark.parametrize('field',['source_link_ref','snapshot_ref','original_report_origin','source_sha256'])
def test_revision_manifest_parent_provenance_mismatch_fails_before_copy(scene,field):
    client, pid, continuity, service, original, original_link, artifact, saved, body = prepare(scene)
    manifest = continuity.data_dir/'docx-revisions'/pid/'artifacts'/artifact['id']/'manifest.json'
    # Locate only this service's exact owned metadata path (no user disk scan).
    if not manifest.exists():
        manifest = service._path(pid,'artifacts',artifact['id'],'manifest.json')
    wrapped = __import__('json').loads(manifest.read_text())
    value = wrapped['payload']
    if field in {'source_link_ref','snapshot_ref'}:
        value[field] = {**value[field],'sha256':'f'*64}
    elif field == 'source_sha256':
        value[field] = 'e'*64
    else:
        value[field] = {**value[field],'source_id':'wrong-original-source'}
    # Simulate a self-consistent but misbound producer record. Real service get
    # still validates artifact bytes; the continuity adapter must reject ancestry.
    manifest.write_text(canonical({'payload':value,'sha256':digest(value)})+'\n')
    rejected = client.post(ROOT+'/report',json=body)
    check(rejected.status_code in {404,409},rejected.text)
    target = continuity.data_dir/'native-working-copies'/pid/artifact['id']/'docx_revision'/artifact['filename']
    check(not target.exists())

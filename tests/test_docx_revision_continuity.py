"""Real frozen SQLite/OOXML references; no model, windows, or source replacement."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import os
import zipfile

import pytest

from packages.domain.file_continuity import FileContinuity
from packages.domain.local_workspaces import sha
from packages.domain.store import Conflict, InvalidInput, NotFound
from test_file_continuity import scene, register, report, docx


def check(value, message='revision continuity requirement failed'):
    if not value:
        raise AssertionError(message)


def test_snapshot_reader_freezes_saved_bytes_after_human_edits_and_file_disappears(scene):
    continuity, link, _, original = register(scene)
    observed = continuity.observe(link)
    store, data, folder, pid, ws, binding = scene
    changed = docx('后来保存的人工正文', '不可以替换旧快照')
    (folder/'论文.docx').write_bytes(changed)
    later = continuity.observe(link)
    check(later['snapshot_ref'] != observed['snapshot_ref'])
    (folder/'论文.docx').unlink()
    frozen = continuity.read_snapshot_bytes(link, observed['snapshot_ref'])
    check(frozen['raw'] == original and frozen['current_sha256'] == sha(original))
    check(frozen['scope'] == 'EXACT_FROZEN_SNAPSHOT_NOT_CURRENT_FILE')
    check(frozen['original_report_origin'] is None)
    check(continuity.read_snapshot_bytes(link, later['snapshot_ref'])['raw'] == changed)
    restarted = FileContinuity(store, data, ws)
    check(restarted.read_snapshot_bytes(link, observed['snapshot_ref']) == frozen)
    check(not (folder/'论文.docx').exists())


def test_snapshot_reader_rejects_wrong_link_revision_hash_project_and_corrupt_bytes(scene):
    continuity, link, source, original = register(scene)
    observed = continuity.observe(link)
    store, data, folder, pid, ws, binding = scene
    second = continuity.register_workspace(pid, source, {'kind':'USER_OPENED_WORKSPACE_FILE'})['link_ref']
    other = store.create_project('另一个工程', '')['id']
    invalid = [
        (second, observed['snapshot_ref']),
        (link, {**observed['snapshot_ref'], 'revision': observed['snapshot_ref']['revision']+1}),
        (link, {**observed['snapshot_ref'], 'sha256':'e'*64}),
        (link, {**observed['snapshot_ref'], 'project_id':other}),
        ({**link, 'sha256':'f'*64}, observed['snapshot_ref']),
    ]
    for bad_link, bad_snapshot in invalid:
        with pytest.raises((Conflict, InvalidInput, NotFound)):
            continuity.read_snapshot_bytes(bad_link, bad_snapshot)
    saved = data/'file-continuity'/'snapshots'/link['id']/observed['snapshot_ref']['id']/'bytes'
    saved.write_bytes(original + b'changed')
    with pytest.raises(Conflict):
        continuity.read_snapshot_bytes(link, observed['snapshot_ref'])
    check((folder/'论文.docx').read_bytes() == original)


def test_snapshot_reader_only_trusts_registered_report_kind_and_preserves_original_origin(scene):
    continuity, args, path, original, reports = report(scene)
    link = continuity.register_report(*args)['link_ref']
    snapshot = continuity.observe(link)
    path.write_bytes(docx('人工已保存正文', '仅修改新版本'))
    frozen = continuity.read_snapshot_bytes(link, snapshot['snapshot_ref'])
    origin = continuity.get(link)['origin']
    check(frozen['raw'] == original and frozen['original_report_origin'] == origin)
    check(continuity.original_report_link(link)['link_ref'] == link)
    store, data, folder, pid, ws, binding = scene
    (folder/'伪报告.docx').write_bytes(original)
    source = ws.read(pid, binding['id'], '伪报告.docx')['source_ref']
    ordinary = continuity.register_workspace(pid, source, origin)['link_ref']
    observed = continuity.observe(ordinary)
    check(continuity.read_snapshot_bytes(ordinary, observed['snapshot_ref'])['original_report_origin'] is None)
    with pytest.raises(InvalidInput):
        continuity.register_workspace(pid, source, {'kind':'DOCX_REVISION'})


def publish_revision(continuity, pid, link, *, suffix='one'):
    """Real service; only its external owner admission is an explicit fixture."""
    from packages.domain.docx_revisions import DocxRevisions
    owner = {'operation_id':'original-question-'+suffix, 'generation':'generation-'+suffix, 'conversation_id':'discussion'}
    service = DocxRevisions(continuity.store, continuity.data_dir,
        snapshot_reader=continuity.read_snapshot_bytes,
        owner_checker=lambda project, candidate: project == pid and candidate == owner)
    continuity.revision_reader = service.download
    snapshot = continuity.observe(link)['snapshot_ref']
    context = service.context(pid, link, snapshot, limit=30)
    paragraph = next(item for item in context['paragraphs'] if item['editable'])
    patch = {**paragraph['anchor'], 'replacement':'修订：'+paragraph['text'][1:]+'（按反馈修订）',
        'reason':'原创测试精确替换一个字并补充说明，核对插入和删除修订'}
    proposal = service.prepare(pid, {'operation_id':'prepare-'+suffix,'owner':owner,
        'file_link_ref':link,'snapshot_ref':snapshot,'patches':[patch]})
    check(proposal['status'] == 'PROPOSED')
    published = service.publish(pid, proposal['proposal_ref'], operation_id='publish-'+suffix, owner=owner)
    check(published['status'] == 'NEW_ARTIFACT')
    return service, published['artifact'], snapshot


def create_working_copy(continuity, artifact):
    """Same exact private layout as NativeReportOpen, no arbitrary location."""
    from packages.domain.file_continuity import DOCX_REVISION_PART
    from packages.domain.local_workspaces import canonical
    raw, item = continuity.revision_reader(artifact['project_id'], artifact['id'])
    parent = continuity.data_dir/'native-working-copies'/artifact['project_id']/artifact['id']/DOCX_REVISION_PART
    parent.mkdir(parents=True)
    path = parent/artifact['filename']; path.write_bytes(raw)
    (parent/(artifact['filename']+'.source.json')).write_text(canonical({
        'project_id':artifact['project_id'], 'artifact_id':artifact['id'],
        'source_sha256':artifact['sha256'], 'kind':DOCX_REVISION_PART,
        'download_url':artifact['download_url']}), encoding='utf-8')
    linked = continuity.register_revision(artifact['project_id'], artifact['id'], artifact['filename'],
        artifact['sha256'], artifact['download_url'])
    return path, linked['link_ref']


def test_true_revision_keeps_new_hash_parent_snapshot_and_original_report_basis_across_restart(scene):
    from packages.domain.linked_requirements import FrozenLinkedRequirements
    from packages.domain.planning_reports import PlanningReports
    continuity, args, original_path, original_bytes, reports = report(scene)
    original_link = continuity.register_report(*args)['link_ref']
    before = FrozenLinkedRequirements(continuity, reports).read(original_link)
    service, artifact, saved = publish_revision(continuity, args[0], original_link)
    raw, manifest = service.download(args[0], artifact['id'])
    check(manifest == artifact and artifact['sha256'] == sha(raw) != sha(original_bytes))
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        check(b'<w:ins' in archive.read('word/document.xml') and b'<w:del' in archive.read('word/document.xml'))
    original_path.write_bytes(docx('晚到人工修改，不能被新版覆盖', '不自动合并'))
    path, link = create_working_copy(continuity, artifact)
    current = continuity.get(link)
    check(current['baseline_sha256'] == artifact['sha256'])
    check(current['origin']['artifact_ref']['id'] == artifact['id'])
    check(current['origin']['source_link_ref'] == original_link and current['origin']['snapshot_ref'] == saved)
    after = FrozenLinkedRequirements(continuity, reports).read(link)
    check(after['report_artifact_ref'] == before['report_artifact_ref'])
    check(after['text_content'] == before['text_content'] and after['proposal_ref'] == before['proposal_ref'])
    check(after['revision_artifact_ref']['sha256'] == artifact['sha256'])
    check(after['original_report_link_ref'] == original_link)
    # Original source may be closed/deleted: only its persisted snapshot matters.
    original_path.unlink()
    store, data, folder, pid, ws, _ = scene
    from packages.domain.store import Store
    from packages.domain.local_workspaces import LocalWorkspaces
    restarted_store = Store(store.database)
    restarted_ws = LocalWorkspaces(restarted_store, data)
    restored_reports = PlanningReports(restarted_store, data, None, restarted_ws)
    restarted = FileContinuity(restarted_store, data, restarted_ws,
        report_reader=restored_reports.download)
    from packages.domain.docx_revisions import DocxRevisions
    restarted_revisions = DocxRevisions(restarted_store, data,
        snapshot_reader=restarted.read_snapshot_bytes)
    restarted.revision_reader = restarted_revisions.download
    check(FrozenLinkedRequirements(restarted, restored_reports).read(link) == after)
    check(restarted.read_snapshot_bytes(original_link, saved)['raw'] == original_bytes)
    check(restarted_revisions.download(pid, artifact['id']) == (raw, artifact))
    with pytest.raises(Conflict):
        restarted_revisions.publish(pid, artifact['proposal_ref'],
            operation_id='do-not-republish-after-restart', owner=artifact['owner'])
    check(path.read_bytes() == raw and service.download(pid, artifact['id'])[0] == raw)


def test_multiple_real_revisions_follow_original_basis_not_intermediate_artifact_hash(scene):
    from packages.domain.linked_requirements import FrozenLinkedRequirements
    continuity, args, original_path, original_bytes, reports = report(scene)
    original_link = continuity.register_report(*args)['link_ref']
    service, first, _ = publish_revision(continuity, args[0], original_link)
    first_path, first_link = create_working_copy(continuity, first)
    second_service, second, saved = publish_revision(continuity, args[0], first_link, suffix='two')
    _, second_link = create_working_copy(continuity, second)
    original = FrozenLinkedRequirements(continuity, reports).read(original_link)
    last = FrozenLinkedRequirements(continuity, reports).read(second_link)
    check(last['text_content'] == original['text_content'])
    check(last['report_artifact_ref']['sha256'] == sha(original_bytes))
    check(last['revision_artifact_ref']['sha256'] == second['sha256'] != first['sha256'])
    check(continuity.get(second_link)['origin']['source_link_ref'] == first_link)
    check(continuity.get(second_link)['origin']['snapshot_ref'] == saved)


def test_ordinary_docx_revision_cannot_gain_planning_origin_and_wrong_receipt_is_rejected(scene):
    from docx import Document
    from packages.domain.linked_requirements import FrozenLinkedRequirements
    output = BytesIO(); document = Document(); document.add_paragraph('普通原创正文，可作精确修改。'); document.save(output)
    continuity, original_link, _, _ = register(scene, raw=output.getvalue())
    service, artifact, _ = publish_revision(continuity, original_link['project_id'], original_link)
    path, link = create_working_copy(continuity, artifact)
    result = FrozenLinkedRequirements(continuity, None).read(link)
    check(result['status'] == 'UNAVAILABLE' and not result['automatic_input_authorized'])
    receipt = path.with_name(path.name+'.source.json')
    receipt.write_text('{}')
    with pytest.raises(Conflict):
        continuity.register_revision(artifact['project_id'], artifact['id'], artifact['filename'],
            artifact['sha256'], artifact['download_url'])
    check(path.read_bytes() == service.download(artifact['project_id'], artifact['id'])[0])

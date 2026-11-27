"""Actual SQLite/files/OOXML continuity; no model, window or storage stand-in."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path
import zipfile

import pytest

from packages.domain.file_continuity import FileContinuity, VIEW_BYTES
from packages.domain.local_workspaces import LocalWorkspaces, MAX_FILE_BYTES, WorkspaceInput, canonical, sha
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.planning_reports import PlanningReportInput, PlanningReports
from packages.domain.planning_schema import ProposalInput
from packages.domain.store import Conflict, InvalidInput, NotFound, Store


def check(condition, message='continuity requirement failed'):
    if not condition:
        raise AssertionError(message)


def docx(body='用户已经修改的正文', comment='这里需要补充可复现依据', *, long_body=''):
    from xml.sax.saxutils import escape
    w = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
    r = 'http://schemas.openxmlformats.org/package/2006/relationships'
    t = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
    parts = {
        '[Content_Types].xml': '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/></Types>',
        '_rels/.rels': f'<Relationships xmlns="{r}"><Relationship Id="main" Type="{t}officeDocument" Target="word/document.xml"/></Relationships>',
        'word/_rels/document.xml.rels': f'<Relationships xmlns="{r}"><Relationship Id="comments" Type="{t}comments" Target="comments.xml"/></Relationships>',
        'word/document.xml': f'<w:document xmlns:w="{w}"><w:body>'
            f'<w:p><w:r><w:t>{escape(long_body)}</w:t></w:r></w:p>'
            f'<w:p><w:commentRangeStart w:id="7"/><w:r><w:rPr><w:color w:val="FF0000"/></w:rPr><w:t>{escape(body)}</w:t></w:r>'
            '<w:commentRangeEnd w:id="7"/><w:r><w:commentReference w:id="7"/></w:r>'
            '<w:ins w:id="1"><w:r><w:t>人工插入内容</w:t></w:r></w:ins>'
            '<w:del w:id="2"><w:r><w:delText>人工删除内容</w:delText></w:r></w:del>'
            '</w:p></w:body></w:document>',
        'word/comments.xml': f'<w:comments xmlns:w="{w}"><w:comment w:id="7" w:author="原创夹具">'
            f'<w:p><w:r><w:t>{escape(comment)}</w:t></w:r></w:p></w:comment></w:comments>'}
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in parts.items():
            archive.writestr(name, text)
    return output.getvalue()


@pytest.fixture
def scene(tmp_path):
    data = tmp_path / 'controller'
    store = Store(data / 'db.sqlite3')
    # Real TaskBookService uses the same explicit source root as create_app.
    store.source_root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / '真实工程'
    workspace.mkdir()
    pid = store.create_project('同名工程', '')['id']
    ws = LocalWorkspaces(store, data)
    binding = ws.bind(pid, WorkspaceInput(idempotency_key='original', root=str(workspace), allowed_write_paths=[]))
    return store, data, workspace, pid, ws, binding


def register(scene, *, name='论文.docx', raw=None, origin=None):
    store, data, folder, pid, ws, binding = scene
    raw = docx() if raw is None else raw
    (folder / name).write_bytes(raw)
    source = ws.read(pid, binding['id'], name)['source_ref']
    service = FileContinuity(store, data, ws)
    value = service.register_workspace(pid, source, origin or {'kind': 'GENERATED_FILE', 'session_id': 'owner_one', 'conversation_id': 'conversation_one'})
    return service, value['link_ref'], source, raw


def report(scene):
    store, data, folder, pid, ws, binding = scene
    saved = store.save_document(pid, '已有要求.txt', '按用户反馈保护正文。'.encode(), 'original-fixture')
    proposals = PlanningProposals(store, Path(__file__).resolve().parents[1], workspaces=ws)
    created = asyncio.run(proposals.create(pid, ProposalInput.model_validate({
        'operation_id': 'original-proposal', 'mode': 'manual', 'prompt': '整理原创输入',
        'materials': [{'document_id': saved['id'], 'revision': saved['revision'], 'sha256': saved['sha256']}],
        'candidate': {'title': '原创任务书', 'summary': '真实输入', 'nodes': [{
            'id': 'review', 'title': '核对用户修改', 'description': '保护有效内容',
            'inputs': ['用户材料'], 'outputs': ['任务书'], 'acceptance': ['可核对来源']}]}})))
    reports = PlanningReports(store, data, proposals, ws)
    generated = reports.generate(pid, created['id'], PlanningReportInput(operation_id='word', expected_revision=1))
    artifact = generated['artifact']
    raw, item = reports.download(pid, created['id'], artifact['id'])
    path = data / 'native-working-copies' / pid / artifact['id'] / 'docx' / item['filename']
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    service = FileContinuity(store, data, ws, report_reader=reports.download)
    args = (pid, artifact['id'], 'docx', item['filename'], item['sha256'], artifact['download_url'], 'PROPOSAL', created['id'])
    return service, args, path, raw, reports


def test_workspace_saved_docx_comments_tracked_text_and_restart_without_any_window(scene):
    service, ref, source, raw = register(scene)
    first = service.observe(ref)
    check(first['status'] == 'UNCHANGED' and first['baseline_sha256'] == source['sha256'])
    check('用户已经修改的正文' in first['parsed']['text_content'])
    check('[tracked-ins]人工插入内容[/tracked-ins]' in first['parsed']['text_content'])
    check('[tracked-del]人工删除内容[/tracked-del]' in first['parsed']['text_content'])
    comment = first['parsed']['comments'][0]
    check(comment['logical_id'] == '7' and comment['text'] == '这里需要补充可复现依据')
    check(bool(comment['anchor_sources']) and not comment['exact_text_range_verified'])
    check(first['parsed']['structure']['formatting_intent'] == 'NOT_INTERPRETED')
    check(not first['write_authorized'] and first['unsaved_changes'] == 'NOT_OBSERVED')
    store, data, folder, pid, ws, binding = scene
    restarted_store = Store(store.database)
    restarted = FileContinuity(restarted_store, data, LocalWorkspaces(restarted_store, data))
    check(restarted.get(ref) == service.get(ref))
    check(restarted.observe(ref)['snapshot_ref'] == first['snapshot_ref'])
    check((folder / '论文.docx').read_bytes() == raw, 'observation must not rewrite the user document')


def test_atomic_human_save_updates_exact_version_and_repeated_content_reuses_snapshot(scene):
    service, ref, source, raw = register(scene)
    before = service.observe(ref)
    store, data, folder, pid, ws, binding = scene
    temporary = folder / 'word-save.tmp'
    changed = docx('已保存的人工改稿', '仅修改这里，保留人工文字')
    temporary.write_bytes(changed)
    os.replace(temporary, folder / '论文.docx')
    after = service.observe(ref)
    check(after['status'] == 'CHANGED' and after['current_sha256'] == sha(changed))
    check(after['current_file_source_ref']['revision'] == 2)
    check(service.observe(ref)['snapshot_ref'] == after['snapshot_ref'])
    check(service.read_text(before['snapshot_ref'])['text_content'] == before['parsed']['text_content'])
    (folder / '论文.docx').write_bytes(raw)
    restored = service.observe(ref)
    check(restored['snapshot_ref'] == before['snapshot_ref'])
    check(restored['current_file_source_ref']['revision'] == 3)
    check(restored['snapshot_file_source_ref']['revision'] == 1)
    check(len(list((data / 'file-continuity' / 'snapshots' / ref['id']).glob('[a-f0-9]' * 64))) == 2)


def test_duplicate_registration_is_exact_and_different_origin_is_not_lost(scene):
    service, ref, source, _ = register(scene)
    store, data, folder, pid, ws, binding = scene
    origin = {'kind': 'GENERATED_FILE', 'session_id': 'owner_one', 'conversation_id': 'conversation_one'}
    check(service.register_workspace(pid, source, origin)['link_ref'] == ref)
    second = service.register_workspace(pid, source, {**origin, 'session_id': 'owner_two'})
    check(second['link_ref'] != ref)
    listed = service.list_candidates(pid, limit=1)
    check(listed['total'] == 2 and listed['has_more'] and not listed['automatic_selection'])
    check(service.get(ref)['origin']['session_id'] == 'owner_one')
    check(service.get(second['link_ref'])['origin']['session_id'] == 'owner_two')


def test_two_projects_same_filename_and_bytes_never_merge_or_cross_scope(scene, tmp_path):
    service, ref, _, raw = register(scene)
    store, data, folder, pid, ws, binding = scene
    other = tmp_path / '另一个位置' / '真实工程'
    other.mkdir(parents=True)
    (other / '论文.docx').write_bytes(raw)
    second_pid = store.create_project('同名工程', '')['id']
    second_binding = ws.bind(second_pid, WorkspaceInput(idempotency_key='original', root=str(other)))
    source = ws.read(second_pid, second_binding['id'], '论文.docx')['source_ref']
    linked = service.register_workspace(second_pid, source, {'kind': 'GENERATED_FILE', 'session_id': 'owner_two'})
    check(linked['link_ref'] != ref)
    check(service.list_candidates(pid)['total'] == 1 and service.list_candidates()['total'] == 2)
    with pytest.raises((Conflict, InvalidInput)):
        service.get({**ref, 'project_id': second_pid})
    with pytest.raises(InvalidInput):
        service.register_workspace(pid, source, {'kind': 'GENERATED_FILE'})


def test_missing_rename_and_save_as_do_not_silently_reassociate(scene):
    service, ref, _, raw = register(scene)
    before = service.observe(ref)
    store, data, folder, pid, ws, binding = scene
    (folder / '论文.docx').rename(folder / '新名称.docx')
    (folder / '另存版本.docx').write_bytes(raw)
    with pytest.raises(NotFound):
        service.observe(ref)
    check(service.list_candidates(pid)['total'] == 1)
    check(service.read_text(before['snapshot_ref'])['text_content'] == before['parsed']['text_content'])
    check((folder / '新名称.docx').read_bytes() == raw)


@pytest.mark.parametrize('mode', ['leaf_link', 'workspace_root_replaced'])
def test_workspace_links_and_root_replacement_reject_without_deleting_prior_snapshot(scene, mode):
    service, ref, _, _ = register(scene)
    before = service.observe(ref)
    store, data, folder, pid, ws, binding = scene
    if mode == 'leaf_link':
        (folder / '论文.docx').rename(folder / '真实原件.docx')
        (folder / '论文.docx').symlink_to(folder / '真实原件.docx')
    else:
        folder.rename(folder.with_name('旧工程'))
        folder.mkdir()
        (folder / '论文.docx').write_bytes(docx('替换的同名目录'))
    with pytest.raises((Conflict, InvalidInput, NotFound)):
        service.observe(ref)
    check(bool(service.read_text(before['snapshot_ref'])['text_content']))


def test_actual_report_baseline_and_working_copy_human_atomic_save_remain_distinct(scene):
    service, args, path, original, reports = report(scene)
    linked = service.register_report(*args, origin={'session_id': 'original_owner'})
    ref = linked['link_ref']
    first = service.observe(ref)
    check(first['status'] == 'UNCHANGED' and first['current_file_source_ref'] is None)
    temporary = path.with_name('word-own-save.tmp')
    temporary.write_bytes(docx('原生软件保存的新正文', '按这个已保存批注继续'))
    os.replace(temporary, path)
    after = service.observe(ref)
    check(after['status'] == 'CHANGED' and after['baseline_sha256'] == sha(original))
    check(after['parsed']['comments'][0]['text'] == '按这个已保存批注继续')
    check(service.register_report(*args, origin={'session_id': 'original_owner'})['link_ref'] == ref)
    check(reports.download(args[0], args[-1], args[1])[0] == original)
    check(service.get(ref)['origin']['session_id'] == 'original_owner')


def test_context_managed_aliases_share_logical_location_but_metadata_receipt_does_not_certify_archive_bytes(scene, monkeypatch):
    service, args, path, original, _ = report(scene)
    first = service.register_report(*args, origin={'session_id': 'first_context_alias'})['link_ref']
    second = service.register_report(*args, origin={'session_id': 'second_context_alias'})['link_ref']
    assert first != second
    assert service.context_file_location(first) == service.context_file_location(second)
    observed = service.observe(first)
    def no_archive_read(*_args, **_kwargs):
        raise AssertionError('Navigation metadata must not certify or reread archive bytes')
    monkeypatch.setattr(service, '_snapshot', no_archive_read)
    receipt = service.context_snapshot_receipt(first, observed['snapshot_ref'])
    assert receipt['saved_sha256'] == sha(original)
    assert receipt['archive_bytes_verified'] is False and receipt['current_source_verified'] is False
    assert path.read_bytes() == original


@pytest.mark.parametrize('mode', ['leaf_link', 'parent_link', 'parent_replaced', 'missing'])
def test_managed_report_only_exact_registered_parent_and_leaf_are_readable(scene, mode):
    service, args, path, original, reports = report(scene)
    ref = service.register_report(*args)['link_ref']
    before = service.observe(ref)
    if mode == 'leaf_link':
        alternate = path.with_name('实际内容.docx')
        path.rename(alternate)
        path.symlink_to(alternate)
    elif mode in {'parent_link', 'parent_replaced'}:
        old = path.parent.with_name('kept-parent')
        path.parent.rename(old)
        if mode == 'parent_link':
            path.parent.symlink_to(old, target_is_directory=True)
        else:
            path.parent.mkdir()
            path.write_bytes(original)
    else:
        path.unlink()
    with pytest.raises((Conflict, InvalidInput, NotFound)):
        service.observe(ref)
    if mode == 'parent_replaced':
        with pytest.raises(Conflict):
            service.register_report(*args)
    check(bool(service.read_text(before['snapshot_ref'])['text_content']))


def test_report_reader_required_and_spoofed_paths_hashes_or_origin_rejected(scene):
    service, args, path, raw, reports = report(scene)
    store, data, folder, pid, ws, binding = scene
    without = FileContinuity(store, data, ws)
    with pytest.raises(Conflict, match='reader'):
        without.register_report(*args)
    for index, replacement in [(3, '../another.docx'), (4, '0' * 64), (5, 'https://example.invalid/report.docx')]:
        changed = list(args)
        changed[index] = replacement
        with pytest.raises((Conflict, InvalidInput)):
            service.register_report(*changed)
    with pytest.raises(InvalidInput):
        service.register_report(*args, origin={'artifact_ref': {'id': 'other', 'revision': 1, 'sha256': '0' * 64}})
    check(path.read_bytes() == raw)


def test_partial_docx_preserves_original_and_reports_parse_unavailable(scene):
    service, ref, _, _ = register(scene)
    before = service.observe(ref)
    store, data, folder, pid, ws, binding = scene
    (folder / '论文.docx').write_bytes(b'PK\x03\x04partial human save')
    observed = service.observe(ref)
    check(observed['status'] == 'CHANGED' and observed['parsed']['status'] == 'UNAVAILABLE')
    check(observed['parsed']['warnings'] == ['PARSER_UNAVAILABLE_OR_MALFORMED_DOCUMENT'])
    check(bool(service.read_text(before['snapshot_ref'])['text_content']))
    check((folder / '论文.docx').read_bytes() == b'PK\x03\x04partial human save')


def test_long_body_does_not_hide_tail_comments_and_full_snapshot_can_be_paged(scene):
    service, ref, _, _ = register(scene, raw=docx(comment='文末批注不会被长正文吞掉', long_body='长正文' * 40000))
    observed = service.observe(ref)
    check(observed['parsed']['text_truncated'])
    check(observed['parsed']['comments'][0]['text'] == '文末批注不会被长正文吞掉')
    check(len(canonical(observed['parsed']).encode()) <= VIEW_BYTES)
    page = service.read_text(observed['snapshot_ref'], offset=120000, limit=16000)
    check('文末批注不会被长正文吞掉' in page['text_content'])
    check(page['offset_unit'] == 'UNICODE_CODEPOINT' and not page['has_more'])
    with pytest.raises(InvalidInput):
        service.read_text(observed['snapshot_ref'], limit=16001)


def test_oversized_human_file_does_not_remove_old_snapshots_or_truncate_user_content(scene):
    service, ref, _, _ = register(scene)
    before = service.observe(ref)
    store, data, folder, pid, ws, binding = scene
    with (folder / '论文.docx').open('r+b') as stream:
        stream.truncate(MAX_FILE_BYTES + 1)
    with pytest.raises(InvalidInput):
        service.observe(ref)
    check((folder / '论文.docx').stat().st_size == MAX_FILE_BYTES + 1)
    check(bool(service.read_text(before['snapshot_ref'])['text_content']))


def test_link_and_snapshot_integrity_fail_closed(scene):
    service, ref, _, _ = register(scene)
    observed = service.observe(ref)
    store, data, folder, pid, ws, binding = scene
    snapshots = data / 'file-continuity' / 'snapshots' / ref['id']
    frozen = next(snapshots.glob('*/bytes'))
    original = frozen.read_bytes()
    frozen.write_bytes(b'tampered snapshot')
    with pytest.raises(Conflict):
        service.read_text(observed['snapshot_ref'])
    frozen.write_bytes(original)
    link = data / 'file-continuity' / 'links' / (ref['id'] + '.json')
    saved = json.loads(link.read_bytes())
    saved['payload']['origin']['session_id'] = 'different_owner'
    link.write_text(json.dumps(saved))
    with pytest.raises(Conflict):
        service.get(ref)
    check((folder / '论文.docx').read_bytes() == original)


@pytest.mark.parametrize('origin', [
    {'kind': 'GENERATED_FILE', 'arbitrary_path': '/other/project.docx'},
    {'kind': 'GENERATED_FILE', 'project_id': 'other_project'},
    {'kind': 'GENERATED_FILE', 'session_ref': {'id': 'owner', 'revision': True, 'sha256': 'a' * 64}},
    {'kind': 'GENERATED_FILE', 'task_refs': [{'id': 'task', 'revision': 1, 'sha256': 'a' * 64}] * 65},
    {'kind': 'GENERATED_FILE', 'conversation_id': {'nested': 'not identity'}},
])
def test_origin_strictly_bounded_and_does_not_accept_paths_or_cross_project(scene, origin):
    service, ref, source, _ = register(scene)
    with pytest.raises(InvalidInput):
        service.register_workspace(scene[3], source, origin)
    check(service.list_candidates()['total'] == 1)


def test_concurrent_exact_registration_and_observation_use_real_sqlite_single_result(scene):
    service, ref, source, _ = register(scene)
    origin = {'kind': 'GENERATED_FILE', 'session_id': 'owner_one', 'conversation_id': 'conversation_one'}
    with ThreadPoolExecutor(max_workers=4) as pool:
        linked = list(pool.map(lambda _: service.register_workspace(scene[3], source, origin), range(4)))
    check(all(item['link_ref'] == ref for item in linked))
    with ThreadPoolExecutor(max_workers=4) as pool:
        observations = list(pool.map(lambda _: service.observe(ref), range(4)))
    check(all(item['snapshot_ref'] == observations[0]['snapshot_ref'] for item in observations))
    check(service.list_candidates()['total'] == 1)


def test_bounded_link_and_snapshot_capacity_keeps_existing_work(scene, monkeypatch):
    import packages.domain.file_continuity as module
    service, ref, source, _ = register(scene)
    before = service.observe(ref)
    monkeypatch.setattr(module, 'MAX_LINKS', 1)
    with pytest.raises(Conflict, match='full'):
        service.register_workspace(scene[3], source, {'kind': 'SELECTED_FILE', 'session_id': 'another'})
    monkeypatch.setattr(module, 'MAX_SNAPSHOTS', 1)
    changed = docx('应保留的人工新稿')
    (scene[2] / '论文.docx').write_bytes(changed)
    with pytest.raises(Conflict, match='full'):
        service.observe(ref)
    check((scene[2] / '论文.docx').read_bytes() == changed)
    check(bool(service.read_text(before['snapshot_ref'])['text_content']))
    check(service.get(ref)['link_ref'] == ref)


def snapshot_bytes(data):
    return sum(path.stat().st_size for path in (data / 'file-continuity' / 'snapshots').rglob('*') if path.is_file())


def test_per_link_byte_budget_counts_metadata_and_survives_restart_without_deleting_history(scene):
    service, ref, _, _ = register(scene, name='notes.txt', raw=b'saved original')
    before = service.observe(ref)
    store, data, folder, pid, ws, binding = scene
    used = snapshot_bytes(data)
    limited = FileContinuity(Store(store.database), data, ws, max_link_snapshot_bytes=used)
    check(limited.observe(ref)['snapshot_ref'] == before['snapshot_ref'], 'cache hits cannot spend another copy')
    changed = b'human latest version'
    (folder / 'notes.txt').write_bytes(changed)
    with pytest.raises(Conflict, match='CONTINUITY_LINK_STORAGE_FULL'):
        limited.observe(ref)
    check(snapshot_bytes(data) == used and (folder / 'notes.txt').read_bytes() == changed)
    check(limited.read_text(before['snapshot_ref'])['text_content'] == 'saved original')
    # Reducing a preference below already-used storage never deletes old data.
    (folder / 'notes.txt').write_bytes(b'saved original')
    tiny = FileContinuity(store, data, ws, max_link_snapshot_bytes=1, max_total_snapshot_bytes=1)
    check(tiny.observe(ref)['snapshot_ref'] == before['snapshot_ref'])


def test_global_byte_budget_is_shared_across_links_and_preserves_second_user_file(scene):
    service, ref, _, _ = register(scene, name='one.txt', raw=b'first saved')
    before = service.observe(ref)
    store, data, folder, pid, ws, binding = scene
    (folder / 'two.txt').write_bytes(b'another human file')
    second_source = ws.read(pid, binding['id'], 'two.txt')['source_ref']
    second = service.register_workspace(pid, second_source, {'kind': 'SELECTED_FILE'})['link_ref']
    used = snapshot_bytes(data)
    limited = FileContinuity(store, data, ws, max_total_snapshot_bytes=used)
    with pytest.raises(Conflict, match='CONTINUITY_GLOBAL_STORAGE_FULL'):
        limited.observe(second)
    check(snapshot_bytes(data) == used)
    check((folder / 'two.txt').read_bytes() == b'another human file')
    check(limited.observe(ref)['snapshot_ref'] == before['snapshot_ref'])
    check(limited.list_candidates(pid)['total'] == 2)


def test_unfinished_private_stage_counts_against_storage_budget_without_being_deleted(scene):
    service, ref, _, _ = register(scene, name='notes.txt', raw=b'original')
    before = service.observe(ref)
    store, data, folder, pid, ws, binding = scene
    pending = data / 'file-continuity' / 'snapshots' / ref['id'] / ('.pending-' + 'b' * 32)
    pending.mkdir()
    (pending / 'bytes').write_bytes(b'own interrupted snapshot')
    used = snapshot_bytes(data)
    limited = FileContinuity(store, data, ws, max_total_snapshot_bytes=used)
    (folder / 'notes.txt').write_bytes(b'a new actual version')
    with pytest.raises(Conflict, match='CONTINUITY_GLOBAL_STORAGE_FULL'):
        limited.observe(ref)
    check((pending / 'bytes').read_bytes() == b'own interrupted snapshot')
    check(limited.read_text(before['snapshot_ref'])['text_content'] == 'original')


@pytest.mark.parametrize('limit', [0, -1, True, 1.5, float('inf'), '1024'])
def test_storage_budget_configuration_is_strict_and_finite(scene, limit):
    with pytest.raises(InvalidInput):
        FileContinuity(scene[0], scene[1], scene[4], max_link_snapshot_bytes=limit)

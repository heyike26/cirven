"""Real SQLite/report OOXML/continuity snapshots; no model or native GUI.

Every assertion uses explicit check so the same requirements run under -O.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import pytest

from packages.domain.file_continuity import FileContinuity
from packages.domain.linked_requirements import FrozenLinkedRequirements
from packages.domain.local_workspaces import canonical, sha
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.planning_reports import PlanningReports, PlanningReportInput
from packages.domain.planning_schema import ProposalInput, ProposalEdit, ProposalAnswer
from packages.domain.store import Conflict, InvalidInput
from test_file_continuity import scene, register, docx


def check(value, message='linked original requirements contract failed'):
    if not value:
        raise AssertionError(message)


@pytest.fixture
def original(scene):
    store, data, folder, pid, ws, binding = scene
    material = store.save_document(pid, '原始依据.txt', ('原材料🙂需要可追溯。' * 900).encode(), 'original-fixture')
    proposals = PlanningProposals(store, Path(__file__).resolve().parents[1], workspaces=ws)
    candidate = {'title': '原报告对应任务', 'summary': '旧版本要求🙂', 'goals': ['保留原件'],
        'constraints': ['只对已确认内容作结论'], 'nodes': [{'id': 'review', 'title': '检查原材料',
            'description': '读取已选择资料', 'outputs': ['可追溯结果'], 'acceptance': ['引用固定原版']}],
        'questions': [{'id': 'interface', 'text': '连接方式是什么？', 'reason': '原材料没有给出。',
            'category': 'MISSING_INPUT', 'affects_node_ids': ['review']}]}
    created = asyncio.run(proposals.create(pid, ProposalInput.model_validate({
        'operation_id': 'original', 'mode': 'manual', 'prompt': '原始任务：核对论文依据，保留人工改动。',
        'materials': [{'document_id': material['id'], 'revision': material['revision'], 'sha256': material['sha256']}],
        'candidate': candidate})))
    reports = PlanningReports(store, data, proposals, ws)
    generated = reports.generate(pid, created['id'], PlanningReportInput(operation_id='report', expected_revision=1))
    artifact = generated['artifact']
    raw, item = reports.download(pid, created['id'], artifact['id'])
    path = data / 'native-working-copies' / pid / artifact['id'] / 'docx' / item['filename']
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    continuity = FileContinuity(store, data, ws, report_reader=reports.download)
    link = continuity.register_report(pid, artifact['id'], 'docx', item['filename'], item['sha256'],
        artifact['download_url'], 'PROPOSAL', created['id'])['link_ref']
    return FrozenLinkedRequirements(continuity, reports), link, created, generated, path, proposals


def test_original_revision_and_answer_view_survive_later_plan_answer_and_human_file_edit(original):
    reader, link, old, generated, path, proposals = original
    before = {section: reader.read(link, section=section, limit=8000) for section in ('requirements', 'questions')}
    proposals.answer(link['project_id'], old['id'], ProposalAnswer(operation_id='late-answer',
        expected_revision=1, question_id='interface', text='后来才回答：使用以太网。'))
    revised = deepcopy(old['payload']['candidate'])
    revised['summary'] = '新版要求，不能倒灌原报告'
    updated = proposals.edit(link['project_id'], old['id'], ProposalEdit(operation_id='later-plan',
        expected_revision=1, candidate=revised, change_reason='用户后续改变要求'))
    check(updated['revision'] == 2)
    human = docx('原生软件保存的新正文', '这里补原始要求')
    path.write_bytes(human)
    for section, result in before.items():
        check(reader.read(link, section=section, limit=8000) == result)
        check('新版要求' not in result['text_content'] and '后来才回答' not in result['text_content'])
        check(result['proposal_ref'] == generated['proposal_ref'])
    check('原始任务' in before['requirements']['text_content'])
    check('AWAITING_ANSWER' in before['questions']['text_content'])
    check(path.read_bytes() == human)


def test_sources_pages_reconstruct_every_selected_chunk_with_stable_unicode_offsets(original):
    reader, link, *_ = original
    pages, offset = [], 0
    for _ in range(100):
        page = reader.read(link, section='sources', offset=offset, limit=137)
        check(page['offset_unit'] == 'UNICODE_CODEPOINT' and len(page['text_content']) <= 137)
        pages.append(page)
        if page['next_offset'] is None:
            break
        check(page['next_offset'] == offset + len(page['text_content']))
        offset = page['next_offset']
    else:
        raise AssertionError('bounded source pagination did not finish')
    text = ''.join(page['text_content'] for page in pages)
    check(len(text) == pages[0]['total_characters'])
    check(all(page['content_sha256'] == sha(text.encode()) for page in pages))
    content = json.loads(text)['proposal']
    chunks = content['documents'][0]['chunks']
    check('原材料🙂需要可追溯。' in canonical(chunks))
    check('text_or_selected_chunks' not in content['documents'][0], 'avoid a second copy of the same text')
    check('base64' not in text and not pages[0]['execution_authorized'])
    check(reader.read(link, section='sources', offset=len(text) + 1)['text_content'] == '')


def test_restart_reads_saved_report_sections_without_current_proposal_access(original, scene):
    reader, link, old, generated, path, proposals = original
    store, data, folder, pid, ws, _ = scene
    expected = reader.read(link, section='questions')
    restarted_reports = PlanningReports(store, data, None, ws)
    restarted = FrozenLinkedRequirements(FileContinuity(store, data, ws, report_reader=restarted_reports.download), restarted_reports)
    check(restarted.read(link, section='questions') == expected)
    check(restarted.read(link)['proposal_ref'] == generated['proposal_ref'])


def test_workspace_origin_cannot_impersonate_report_and_does_not_break_plain_file_read(scene):
    continuity, link, source, raw = register(scene, origin={'kind': 'GENERATED_REPORT',
        'source_kind': 'PROPOSAL', 'source_id': 'pretend',
        'artifact_ref': {'id': 'f' * 64, 'revision': 1, 'sha256': 'e' * 64}})
    result = FrozenLinkedRequirements(continuity, None).read(link)
    check(result['status'] == 'UNAVAILABLE' and not result['automatic_input_authorized'])
    snapshot = continuity.observe(link)
    check('用户已经修改的正文' in continuity.read_text(snapshot['snapshot_ref'])['text_content'])


def test_cross_project_link_sha_and_report_source_tamper_fail_closed(original, scene):
    reader, link, *_ = original
    store, data, *_ = scene
    other = store.create_project('别的工程', '')['id']
    for invalid in ({**link, 'project_id': other}, {**link, 'sha256': 'f' * 64}):
        with pytest.raises(Conflict):
            reader.read(invalid)
    origin = reader.continuity.get(link)['origin']
    source = data / 'planning-reports' / link['project_id'] / origin['source_id'] / 'artifacts' / origin['artifact_ref']['id'] / 'source.json'
    saved = source.read_bytes()
    source.write_bytes(saved.replace('旧版本'.encode(), '篡改版'.encode()))
    with pytest.raises(Conflict):
        reader.read(link)
    check(source.read_bytes() != saved, 'reader must not repair or overwrite corrupted evidence')


@pytest.mark.parametrize('arguments', [ {'offset': True}, {'offset': -1}, {'limit': False},
    {'limit': 0}, {'limit': 8001}, {'section': 'latest'}, {'section': None} ])
def test_paging_rejects_ambiguous_or_unbounded_arguments(original, arguments):
    reader, link, *_ = original
    with pytest.raises(InvalidInput):
        reader.read(link, **arguments)

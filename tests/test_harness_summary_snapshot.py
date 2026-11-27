"""Task prompts preserve authored background without certifying its status claims.

Direct domain/temporary SQLite tests: no App, HTTP, SDK or model is started.
"""
import asyncio
from copy import deepcopy
import hashlib
from pathlib import Path
import socket
import subprocess

import pytest

from packages.domain.planning_proposals import PlanningProposals
from packages.domain.planning_schema import ProposalConfirm, ProposalEdit, ProposalInput
from packages.domain.store import Store


ROOT = Path(__file__).resolve().parents[1]
BACKGROUND_NOTE = (
    '以下为计划背景原文快照。本次导出未复核其中关于执行、产物或验收的陈述；'
    '当前计划状态以本文件版本、确认范围及实际执行/验收记录为准。'
)
MIXED_SUMMARY = (
    '人工当前记录：已保留试验副本，待核对本轮验收。\r\n'
    '\r\n旧阶段原话：“未写交接文件、未开展原生验收”。\r\n'
    '业务解释必须保留：先保用户输入，再说明恢复步骤。😀\n'
    '原字面量 {{summary}} / nodes[].depends_on / <不可改写>。'
)


@pytest.fixture
def plan(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('No process, network or model is allowed in this test')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    store = Store(tmp_path / 'fixture.sqlite3')
    store.source_root = ROOT
    pid = store.create_project('原创背景语义验证', '仅本地领域服务测试')['id']
    doc = store.save_document(pid, '原创说明.md', '保留来源和人工记录。'.encode(), 'AUTHORED_TEST')
    service = PlanningProposals(store, ROOT)
    candidate = {
        'title': '人工编写的恢复计划', 'summary': MIXED_SUMMARY,
        'constraints': ['确认计划不批准执行'],
        'nodes': [
            {'id': 'keep', 'title': '保留输入', 'description': '先保留用户原输入。',
             'outputs': ['可回看的原输入副本'],
             'acceptance': ['人工补充：原输入仍可读取。']},
            {'id': 'recover', 'title': '说明恢复', 'description': '按可核对记录说明下一步。',
             'depends_on': ['keep'], 'outputs': ['恢复步骤说明'],
             'acceptance': ['未验证项仍标未验证。']},
        ],
        'questions': [{'id': 'language', 'text': '是否另附英文？', 'reason': '仅影响定稿语言。',
                       'category': 'DECISION', 'blocking': False, 'affects_node_ids': ['recover']}],
    }
    request = ProposalInput.model_validate({
        'operation_id': 'manual-background', 'mode': 'manual', 'prompt': '保留原创人工计划',
        'materials': [{'document_id': doc['id'], 'revision': doc['revision'], 'sha256': doc['sha256']}],
        'candidate': candidate,
    })
    value = asyncio.run(service.create(pid, request))
    assert value['payload']['execution']['model_calls'] == 0
    assert all(node['origin'] == 'USER_STATEMENT' for node in value['payload']['candidate']['nodes'])
    return store, service, pid, value


def database_snapshot(store):
    with store.connect() as connection:
        return list(connection.iterdump())


def check_background(harness, summary):
    for prompt in harness['prompts']:
        content = prompt['content']
        section = content.split('## 计划背景原文快照\n', 1)[1].split('\n\n## 前置依赖\n', 1)[0]
        assert section == BACKGROUND_NOTE + '\n\n' + summary
        assert content.count(summary) == 1
        assert '原模型历史' not in content
        assert '未批准工具权限、模型费用或自动执行' in content
        assert hashlib.sha256(content.encode()).hexdigest() == prompt['sha256']


@pytest.mark.parametrize('confirmed', [False, True], ids=['draft', 'confirmed-not-executed'])
def test_authored_current_and_old_status_statements_are_complete_unverified_background(plan, confirmed):
    store, service, pid, value = plan
    if confirmed:
        value = service.confirm(pid, value['id'], ProposalConfirm(
            operation_id='confirm-background', expected_revision=value['revision'],
            proposal_sha256=value['sha256'], user_confirmed=True))
        assert value['payload']['confirmation']['execution_authorized'] is False
    original = deepcopy(value)
    before = database_snapshot(store)
    harness = service.harness(pid, value['id'], value['revision'])
    check_background(harness, MIXED_SUMMARY)
    assert harness['status'] == ('CONFIRMED_PLAN' if confirmed else 'DRAFT_NOT_EXPORTABLE')
    assert harness['execution_started'] is False and harness['execution_authorized'] is False
    assert harness['questions'] == value['payload']['candidate']['questions']
    assert all('UNRESOLVED' in prompt['content'] for prompt in harness['prompts'][1:])
    assert service.get(pid, value['id'], value['revision']) == original
    assert database_snapshot(store) == before
    with store.connect() as connection:
        for table in ('tasks', 'task_runs', 'runner_jobs', 'agent_executions'):
            assert connection.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0


def test_manual_summary_revision_keeps_exact_old_and_current_backgrounds(plan):
    store, service, pid, old = plan
    candidate = deepcopy(old['payload']['candidate'])
    candidate['summary'] = '人工新记录：原软件已核对本地副本；尚未批准执行。\n\n' + MIXED_SUMMARY
    edited = service.edit(pid, old['id'], ProposalEdit.model_validate({
        'operation_id': 'manual-new-background', 'expected_revision': old['revision'],
        'candidate': candidate, 'change_reason': '保留当前人工陈述和旧阶段原话',
    }))
    before = database_snapshot(store)
    historical = service.harness(pid, old['id'], old['revision'])
    current = service.harness(pid, old['id'], edited['revision'])
    check_background(historical, MIXED_SUMMARY)
    check_background(current, candidate['summary'])
    assert historical['proposal_ref']['sha256'] == old['sha256']
    assert current['proposal_ref']['sha256'] == edited['sha256']
    assert historical['proposal_ref'] != current['proposal_ref']
    assert service.get(pid, old['id'], old['revision'])['payload'] == old['payload']
    assert service.get(pid, old['id']) == edited
    assert database_snapshot(store) == before

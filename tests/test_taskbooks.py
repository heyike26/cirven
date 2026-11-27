"""Actual source-bound taskbooks and existing Task harness, zero model calls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from conftest import ROOT
from packages.domain.tasks import TaskService
from packages.runner.worker import Worker
from packages.verifier.service import Verifier
from test_project_intake import project, upload, make
from test_project_questions import setup, ask, answer, no_runtime
from test_question_decisions import exact_task, resolution_body, resolve, decision_body, ready
from test_worker import until

KINDS = ['GOAL', 'SCOPE', 'DELIVERABLES', 'ENVIRONMENT', 'INTERFACES', 'DATA', 'DEPENDENCIES',
         'CONSTRAINTS', 'ACCEPTANCE', 'NOTES', 'RISKS', 'OPEN_QUESTIONS', 'ASSUMPTIONS']


def intake_ref(intake):
    return {'intake_id': intake['id'], 'revision': intake['revision'], 'sha256': intake['sha256']}


def anchors(material):
    return [{'document_id': material['document_id'], 'revision': material['revision'],
        'raw_sha256': material['raw_sha256'], 'chunk_index': c['index'], 'chunk_sha256': c['sha256'],
        'char_start': c['char_start'], 'char_end': c['char_end'],
        'quote_sha256': hashlib.sha256(c['content'].encode()).hexdigest()} for c in material['chunks']]


def draft(intake, text='用户明确这个最小范围，不宣称自动完整理解。', key='book'):
    sections = [{'section_id': k.lower(), 'kind': k, 'heading': f'{i+1}. {k}', 'entries': []}
                for i, k in enumerate(KINDS)]
    for kind in ('GOAL', 'DELIVERABLES', 'CONSTRAINTS', 'ACCEPTANCE'):
        s = next(s for s in sections if s['kind'] == kind)
        s['entries'] = [{'entry_id': kind.lower()+'-entry', 'text': text, 'origin': 'USER_STATEMENT', 'source_anchors': []}]
    return {'idempotency_key': key, 'expected_revision': 0, 'intake_ref': intake_ref(intake),
            'title': '原创共创任务书', 'sections': sections, 'template_ref': None, 'template_mapping': []}


def book(client, pid, body, id=None):
    route = f'/api/projects/{pid}/taskbooks'
    r = client.post(route, json=body) if id is None else client.put(route+'/'+id, json=body)
    assert r.status_code == (201 if id is None else 200), r.text
    return r.json()


def book_ref(value):
    return {'taskbook_id': value['id'], 'revision': value['revision'], 'sha256': value['sha256']}


def snapshot(tmp_path, name, value, store):
    out = Path(os.environ.get('EEAS_EVIDENCE_DIR', str(tmp_path)))/name; out.mkdir(exist_ok=False)
    with (out/'actual-api.json').open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False); stream.write('\n')
    with store.connect() as source, sqlite3.connect(out/'actual-store.sqlite3') as target:
        source.backup(target)
    return out


def test_multiple_materials_real_template_and_versioned_structured_book(client, tmp_path):
    pid = project(client, '原创任务书来源')
    doc1, ref1 = upload(client, pid, '目标：离线处理资料。\r\n注意：保留原件。')
    doc2, ref2 = upload(client, pid, '验收：结果与来源可追溯。\n真实原文最后。')
    intake = make(client, pid, [ref1, ref2]).json()
    raw_template = '# 用户原模板\r\n\r\n## 一、目标与范围\r\n{{taskbook:first}}\r\n\r\n## 二、其它项\r\n{{taskbook:rest}}\r\n原模板固定尾部。'
    r = client.post(f'/api/projects/{pid}/documents', files={'file': ('用户模板.md', raw_template.encode())})
    assert r.status_code == 201, r.text
    template = r.json(); body = draft(intake)
    body['template_ref'] = {'document_id': template['id'], 'revision': 1, 'sha256': template['sha256']}
    body['template_mapping'] = [{'slot_id': 'first', 'section_ids': ['goal', 'scope']},
                                {'slot_id': 'rest', 'section_ids': [k.lower() for k in KINDS[2:]]}]
    material = intake['snapshot']['materials'][1]
    body['sections'][0]['entries'] = [{'entry_id': 'actual-quote', 'origin': 'SOURCE_FACT',
        'text': ''.join(c['content'] for c in material['chunks']), 'source_anchors': anchors(material)}]
    original = book(client, pid, body)
    assert original['payload']['clarity_status'] == 'UNASSESSED' and original['payload']['model_calls'] == 0
    assert len(original['payload']['sections']) == len(original['payload']['section_index']) == 13
    md = original['payload']['markdown']
    assert md.startswith('# 用户原模板\r\n') and md.endswith('原模板固定尾部。')
    assert md.index('## 一、目标与范围') < md.index('## 二、其它项')
    assert '{{taskbook:' not in md and '真实原文最后。' in md
    assert len(original['payload']['source_refs']) == 2
    assert client.post(f'/api/projects/{pid}/taskbooks', json=body).json() == original
    edited = {**body, 'idempotency_key': 'book-revision', 'expected_revision': 1, 'title': '用户纠正后的任务书'}
    edited['sections'] = copy.deepcopy(body['sections']); edited['sections'][3]['entries'] = [
        {'entry_id': 'actual-environment', 'origin': 'USER_STATEMENT', 'text': '内网Windows/Python3.10。', 'source_anchors': []}]
    current = book(client, pid, edited, original['id']); assert current['revision'] == 2
    assert client.post(f'/api/projects/{pid}/taskbooks', json=body).json() == original
    old = client.get(f'/api/projects/{pid}/taskbooks/{original["id"]}?revision=1').json()
    assert old['payload'] == original['payload'] and old['current_revision'] == 2
    service = type(client.app.state.taskbooks)(TaskService(tmp_path/'eeas.sqlite3', ROOT))
    assert service.get(pid, original['id'], 1)['payload'] == original['payload']
    export = client.get(f'/api/projects/{pid}/taskbooks/{original["id"]}/markdown?revision=1')
    assert export.status_code == 200 and export.content == md.encode()
    index = client.get(f'/api/projects/{pid}/taskbooks/{original["id"]}/sections?revision=1&offset=12&limit=1').json()
    assert index['total'] == 13 and len(index['sections']) == 1 and index['sections'][0]['kind'] == 'ASSUMPTIONS'
    for s in original['payload']['section_index']:
        assert hashlib.sha256(md[s['char_start']:s['char_end']].encode()).hexdigest() == s['sha256']
    assert client.get(f'/api/projects/{pid}/taskbooks').json()['taskbooks'][0]['revision'] == 2
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}
    out = snapshot(tmp_path, 'actual-multi-template-book', {'source_kind': 'synthetic', 'sources': [doc1, doc2],
        'original_template_utf8_sha256': hashlib.sha256(raw_template.encode()).hexdigest(),
        'request': body, 'original': original, 'current': current, 'old_get': old, 'section_tail': index,
        'company_model_calls': 0}, client.app.state.store)
    (out/'actual-original-taskbook.md').write_bytes(export.content)


def test_actual_70k_source_full_tail_is_not_truncated_or_signed_clear(client, tmp_path):
    pid = project(client, '原创长任务书')
    text = ''.join(f'真实原创材料行{i:05d}：资料与目标需要共同确认。\n' for i in range(3500))+'原始末端约束：不得静默删掉。'
    assert 70000 < len(text) < 240000
    doc, ref = upload(client, pid, text); intake = make(client, pid, [ref]).json(); body = draft(intake)
    body['sections'][0]['entries'] = [{'entry_id': 'full-source', 'origin': 'SOURCE_FACT', 'text': text,
                                      'source_anchors': anchors(intake['snapshot']['materials'][0])}]
    result = book(client, pid, body)
    entry = result['payload']['sections'][0]['entries'][0]
    assert entry['text'].encode() == text.encode() and entry['text_sha256'] == hashlib.sha256(text.encode()).hexdigest()
    assert text in result['payload']['markdown'] and text.endswith('不得静默删掉。')
    assert result['payload']['clarity_status'] == 'UNASSESSED' and result['payload']['model_calls'] == 0
    assert result['payload']['source_refs'][0]['text_coverage'] == 'FULL_PARSED_TEXT'
    assert result['payload']['source_refs'][0]['original_modal_coverage'] == 'UNASSESSED'
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}
    snapshot(tmp_path, 'actual-70k-taskbook', {'source_kind': 'synthetic', 'original_source_sha256': doc['sha256'],
        'source_characters': len(text), 'request': body, 'actual_taskbook': result}, client.app.state.store)


def test_missing_template_slots_fields_cross_project_and_false_source_are_rejected(client):
    pid, _, intake, _, _, _ = setup(client); body = draft(intake); route = f'/api/projects/{pid}/taskbooks'
    incomplete = copy.deepcopy(body); incomplete['sections'] = incomplete['sections'][:-1]
    assert client.post(route, json=incomplete).status_code == 422
    wrong = copy.deepcopy(body); wrong['intake_ref']['sha256'] = '0'*64
    assert client.post(route, json=wrong).status_code == 409
    other = project(client, '不接受他项目的任务书')
    assert client.post(f'/api/projects/{other}/taskbooks', json=body).status_code == 404
    wrong = copy.deepcopy(body); wrong['sections'][0]['entries'][0]['origin'] = 'SOURCE_FACT'
    assert client.post(route, json=wrong).status_code == 422
    raw = '# 原模板\n{{taskbook:known}}\n{{taskbook:missing}}'
    template = client.post(f'/api/projects/{pid}/documents', files={'file': ('slots.md', raw.encode())}).json()
    wrong = copy.deepcopy(body); wrong['template_ref'] = {'document_id': template['id'], 'revision': 1, 'sha256': template['sha256']}
    wrong['template_mapping'] = [{'slot_id': 'known', 'section_ids': [k.lower() for k in KINDS]}]
    assert client.post(route, json=wrong).status_code == 422
    wrong['template_mapping'][0]['slot_id'] = 'missing'
    assert client.post(route, json=wrong).status_code == 422
    wrong = copy.deepcopy(body); wrong['sections'][0]['entries'] = [{'entry_id': 'false-source', 'origin': 'SOURCE_FACT',
        'text': '原文没有这句，不得冒充原事实。', 'source_anchors': anchors(intake['snapshot']['materials'][0])}]
    assert client.post(route, json=wrong).status_code == 422
    with client.app.state.store.connect() as c:
        assert c.execute('SELECT count(*) FROM project_taskbooks').fetchone()[0] == 0
    assert no_runtime(client) == {'task_runs': 0, 'runner_jobs': 0, 'agent_executions': 0}

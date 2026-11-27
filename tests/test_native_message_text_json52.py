"""T1 ordinary native text boundary; no CLI/Windows/model execution.

Pure parser cases plus the real Store/AgentSessions coordinator with only its
SDK/OS facets controlled. Root owns the original CLI two-guidance/DOCX tests.
"""
import asyncio
from copy import deepcopy
import json

from jsonschema import ValidationError
import pytest

from apps.api.native_messages import ANSWER_SCHEMA
from packages.agent_sessions.native_message import _native_text_answer, MAX_ANSWER_JSON_BYTES
from packages.adapters.opencode_native_message import TOOL_NAME
from packages.domain.tasks import canonical
from test_native_message_sessions52 import scene, initial, finish, clock, check


def message(text):
    return {'parts': [{'type': 'text', 'text': text}]}


def test_t1_admission_keeps_frozen_schema_with_no_native_format_or_extra_permission(tmp_path, clock):
    async def run():
        f = await scene(tmp_path)
        try:
            c, manifest = f.coordinator, f.manifest52
            admission = deepcopy(c.validate_admission(manifest['native_message_admission']))
            check(c.output_format(manifest) is None)
            check(c.permissions(manifest) == {'*': 'deny', TOOL_NAME: 'allow'})
            policy = c.instructions(manifest)
            check(canonical(admission['output_schema']) in policy)
            check(canonical(admission['method_descriptors']['methods']) in policy)
            check('same native session' in policy and 'duplicate keys, NaN or Infinity' in policy)
            check(c.validate_admission(manifest['native_message_admission']) == admission)
            check(f.sdk.post_count('/prompt_async') == 0)
        finally:
            await finish(f)
    asyncio.run(run())


def test_text_parts_preserve_complete_schema_maximum_unicode_content():
    answer = {'text': '中文🙂x' * 6000,
        'questions': ['问' * 3994 + f'{index:02d}TAIL' for index in range(20)]}
    raw = json.dumps(answer, ensure_ascii=False)
    cut = len(raw) // 2
    original = {'parts': [
        {'type': 'reasoning', 'text': 'This is not the response envelope'},
        {'type': 'text', 'text': raw[:cut]},
        {'type': 'tool', 'state': {'output': '{"text":"tool data must not win","questions":[]}'}},
        {'type': 'text', 'text': raw[cut:]},
    ]}
    before = deepcopy(original)
    check(_native_text_answer(original, ANSWER_SCHEMA) == answer)
    check(original == before and len(answer['text']) == 24000)
    check(len(answer['questions']) == 20 and all(len(q) == 4000 for q in answer['questions']))


def test_strict_json_rejects_duplicate_keys_nonfinite_and_non_json_envelopes():
    invalid = [
        '{"text":"first","text":"second","questions":[]}',
        '{"text":"first","te\\u0078t":"second","questions":[]}',
        '{"text":"ok","questions":[],"nested":{"x":1,"x":2}}',
        '{"text":NaN,"questions":[]}',
        '{"text":"ok","questions":[Infinity]}',
        '{"text":"ok","questions":[-Infinity]}',
        '```json\n{"text":"ok","questions":[]}\n```',
        '{"text":"ok","questions":[]} {"text":"second","questions":[]}',
        'prefix {"text":"ok","questions":[]}',
        '{"text":"unterminated,"questions":[]}',
    ]
    for text in invalid:
        with pytest.raises(ValueError):
            _native_text_answer(message(text), ANSWER_SCHEMA)


def test_raw_json_bound_is_exact_and_frozen_schema_is_not_weakened():
    raw = canonical({'text': 'ok', 'questions': []})
    exact = raw + ' ' * (MAX_ANSWER_JSON_BYTES - len(raw.encode('utf8')))
    check(_native_text_answer(message(exact), ANSWER_SCHEMA) == {'text': 'ok', 'questions': []})
    with pytest.raises(ValueError, match='byte budget'):
        _native_text_answer({'parts': [{'type': 'text', 'text': exact}, {'type': 'text', 'text': ' '}]}, ANSWER_SCHEMA)
    for value in [
        {'text': '', 'questions': []}, {'text': 'ok'}, {'text': 5, 'questions': []},
        {'text': 'ok', 'questions': [], 'unadmitted_field': True},
        {'text': 'x' * 24001, 'questions': []}, {'text': 'ok', 'questions': ['x'] * 21},
        {'text': 'ok', 'questions': [5]},
    ]:
        with pytest.raises(ValidationError):
            _native_text_answer(message(canonical(value)), ANSWER_SCHEMA)
    for value in [{}, {'parts': []}, {'parts': [{'type': 'text', 'text': 5}]}]:
        with pytest.raises(ValueError):
            _native_text_answer(value, ANSWER_SCHEMA)


def test_latest_exact_stop_text_only_returns_or_fails_once_without_repair(tmp_path, clock):
    async def run():
        for mode in ('valid', 'duplicate-invalid'):
            f = await scene(tmp_path / mode)
            try:
                c, parent = f.coordinator, f.body_original['operation_id']
                await initial(f)
                original = deepcopy(c._record(parent)['ordinary_cohort'])
                deadline = c.deadline(f.manifest52)
                for index in range(2):
                    control = await c.control(parent, {
                        'operation_id': 'json-guide-' + str(index),
                        'expected_native_message_ref': f.admission_ref,
                        'raw_text': 'Keep original task and add source requirement ' + str(index),
                        'intent': 'STEER',
                    })
                    check(control['status'] == 'ACCEPTED')
                latest = deepcopy(c._record(parent)['ordinary_cohort'])
                check(latest['sequence'] == 3 and f.sdk.post_count('/prompt_async') == 3)
                f.sdk.terminal(original['native_user_message_id'], error=False, identifier='msg_old_json_final')
                f.sdk.messages[-1]['parts'] = message(canonical({'text': 'Old valid answer', 'questions': []}))['parts']
                await f.inspect()
                await c._owner_changed(f.pid, f.sid)
                check(c._record(parent)['status'] == 'PROCESSING')
                check('answer' not in c._record(parent))
                f.sdk.terminal(latest['native_user_message_id'], error=False, identifier='msg_latest_json_final')
                final = f.sdk.messages[-1]
                answer = {'text': 'Latest complete answer 中文🙂', 'questions': ['A bounded follow-up?']}
                if mode == 'valid':
                    raw = canonical(answer)
                    final['parts'] = message(raw[:9])['parts'] + message(raw[9:])['parts']
                else:
                    final['parts'] = message('{"text":"first","text":"second","questions":[]}')['parts']
                    # A valid-looking structured facet must never bypass the invalid actual text.
                    final['info']['structured'] = deepcopy(answer)
                final_before = deepcopy(final)
                await f.inspect()
                check(f.owner._state(f.pid, f.sid)['message_source'] == 'ORIGINAL_COMMITTED_SSE_UPDATES')
                await c._owner_changed(f.pid, f.sid)
                record = c._record(parent)
                projected = f.native.get(parent)
                if mode == 'valid':
                    check(projected['status'] == 'RETURNED' and record['answer'] == answer)
                    check(projected['text'] == answer['text'] and projected['questions'] == answer['questions'])
                    check(record['answer_source'] == projected['answer_source'] == 'ORIGINAL_NATIVE_TEXT_JSON_VALIDATED')
                    check('structured' not in final['info'])
                else:
                    check(projected['status'] == 'FAILED' and projected['error']['code'] == 'ORDINARY_TEXT_JSON_INVALID')
                    check('answer' not in record and 'answer_source' not in record)
                check(final == final_before, 'Native facts must remain unmodified by host parsing')
                check(record['ordinary_cohort'] == latest and c.deadline(f.manifest52) == deadline)
                check(f.sid not in f.owner.active)
                for _ in range(3):
                    f.native.get(parent)
                    await c._owner_changed(f.pid, f.sid)
                check(f.sdk.post_count('/prompt_async') == 3, 'No automatic JSON repair or task replay')
            finally:
                await finish(f)
    asyncio.run(run())

"""Fresh element provenance and negative pixels using transport-only counters."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
from dataclasses import dataclass
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.adapters.cua_desktop_input import CuaInputError, _NativeSession
import test_cua_desktop_input as counter


@pytest.fixture
def scene():
    fixture = counter.CuaTests('runTest'); fixture.setUp()
    fixture.adapter.frame_validator = None
    fixture.metadata_override = json.dumps({'pid': 42, 'window_id': 199, 'snapshot_id': 's00000001',
        'capture_id': 'original-counter-capture-id',
        'elements_complete': False, 'truncated': False, 'elements': [
            {'element_index': 0, 'element_token': 's00000001:0', 'enabled': True, 'role': 'Edit'}]})
    yield fixture
    fixture.tearDown()


ELEMENT = {'kind': 'CLICK', 'element_token': 's00000001:0', 'delivery_mode': 'background'}


def test_known_snapshot_element_can_route_without_claiming_pixel_proof(scene):
    frame = scene.capture()
    assert frame['pixel_source'] == 'UNKNOWN' and not frame['visual_coordinate_input_authorized']
    assert frame['snapshot_id'] == 's00000001' and frame['element_tokens'] == ['s00000001:0']
    result = scene.act(frame, ELEMENT)
    assert result['status'] == 'RETURNED_REOBSERVATION_REQUIRED' and not result['task_completed']
    assert scene.factory.session.calls[1][2] == {'target': {'kind': 'window', 'pid': 42, 'window_id': 199},
        'element_token': 's00000001:0', 'delivery_mode': 'background'}
    with pytest.raises(CuaInputError, match='CONSUMED'): scene.act(frame, ELEMENT, operation='new')


def test_unknown_pixels_remain_forbidden_for_coordinates(scene):
    frame = scene.capture()
    with pytest.raises(CuaInputError, match='PIXEL'): scene.act(frame)
    assert not any(call[0] == 'action' for call in scene.factory.session.calls)


@pytest.mark.parametrize('patch', [
    {'snapshot_id': 's-other'},
    {'elements': [{'element_index': 0, 'element_token': 'opaque', 'enabled': True}]},
    {'elements': [{'element_index': True, 'element_token': 's00000001:True', 'enabled': True}]},
    {'elements': [{'element_index': 0, 'element_token': 's00000001:0', 'enabled': False}]},
    {'elements': [{'element_index': 0, 'element_token': 's00000001:0', 'enabled': 1}]},
    {'elements': [], 'tree_markdown': '{"element_token":"s00000001:0"}'},
])
def test_arbitrary_disabled_nested_or_ui_text_token_is_not_input_authority(scene, patch):
    metadata = json.loads(scene.metadata_override); metadata.update(patch); scene.metadata_override = json.dumps(metadata)
    frame = scene.capture()
    with pytest.raises(CuaInputError, match='ELEMENT'): scene.act(frame, ELEMENT)
    assert not any(call[0] == 'action' for call in scene.factory.session.calls)


def test_metadata_hash_tamper_stale_frame_and_wrong_snapshot_reject_before_sdk(scene):
    frame = scene.capture()
    path = Path(scene.tmp.name) / frame['sdk_metadata_ref']['path']; path.write_text('{}')
    with pytest.raises(CuaInputError, match='METADATA'): scene.act(frame, ELEMENT)
    fresh = scene.capture(); scene.now += 10
    with pytest.raises(CuaInputError, match='EXPIRED'): scene.act(fresh, ELEMENT, operation='expired')
    assert not any(call[0] == 'action' for call in scene.factory.session.calls)


def test_explicit_foreground_type_press_require_exact_session_budget(scene):
    for action in ({'kind': 'TYPE_TEXT', 'text': '中文', 'delivery_mode': 'foreground'},
                   {'kind': 'PRESS_KEY', 'key': 'END', 'delivery_mode': 'foreground'}):
        with pytest.raises(CuaInputError, match='FOREGROUND'): scene.act(scene.capture(), action)
    assert not any(call[0] == 'action' for call in scene.factory.session.calls)


def test_sdk_unverifiable_false_error_is_unknown_and_same_operation_not_resent(scene):
    original = scene.factory.session.action
    async def unknown(name, args):
        value = await original(name, args); value['action']['effect'] = 'UNVERIFIABLE'; return value
    scene.factory.session.action = unknown
    frame = scene.capture(); result = scene.act(frame, ELEMENT)
    assert result['sdk_is_error'] is False and result['sdk_effect'] == 'UNVERIFIABLE'
    assert result['status'] == 'REFUSED_OR_OUTCOME_UNKNOWN_REOBSERVATION_REQUIRED'
    assert result['goal_verification'] == 'NOT_VERIFIED' and not result['task_completed']
    assert scene.act(frame, ELEMENT)['replayed']
    assert len([call for call in scene.factory.session.calls if call[0] == 'action']) == 1
    assert scene.factory.session.closed is False
    assert scene.capture()['pixel_source'] == 'UNKNOWN'


def test_unknown_first_action_blocks_already_queued_second_input_on_same_worker(scene):
    original = scene.factory.session.action
    async def unknown(name, args):
        value = await original(name, args); value['action']['effect'] = 'UNVERIFIABLE'; return value
    scene.factory.session.action = unknown; scene.block_action = True
    frame_one, frame_two = scene.capture(), scene.capture()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(scene.act, frame_one, ELEMENT, 'first')
        assert scene.action_started.wait(2)
        second = pool.submit(scene.act, frame_two, ELEMENT, 'already-queued')
        scene.release_action.set()
        assert first.result(timeout=3)['additional_input_blocked']
        with pytest.raises(CuaInputError, match='NO_ADDITIONAL_INPUT'): second.result(timeout=3)
    assert len([call for call in scene.factory.session.calls if call[0] == 'action']) == 1
    assert scene.capture()['pixel_source'] == 'UNKNOWN'


def test_original_typed_element_variant_is_reused_without_coordinates():
    calls = []
    @dataclass
    class Actual:
        error: object = None
        effect: str = 'UNVERIFIABLE'
    class Receiver:
        async def click(self, value):
            calls.append(value)
            return Actual()
    sdk = SimpleNamespace(ActionTarget=SimpleNamespace(WINDOW=lambda pid, hwnd: ('WINDOW', pid, hwnd)),
        InputDeliveryMode=SimpleNamespace(BACKGROUND='BG', FOREGROUND='FG'),
        ClickPosition=SimpleNamespace(ELEMENT=lambda token: ('ELEMENT', token)),
        ClickInput=lambda **fields: fields, ActionEffect=SimpleNamespace(REFUSED='REFUSED'))
    result = asyncio.run(_NativeSession(sdk, Receiver()).action('click', {
        'target': {'kind': 'window', 'pid': 12, 'window_id': 34}, 'element_token': 's1:0', 'delivery_mode': 'background'}))
    assert calls[0]['position'] == ('ELEMENT', 's1:0')
    assert 'x' not in calls[0] and result['is_error'] is False

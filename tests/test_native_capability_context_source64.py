"""Actual host storage, files and OOXML; no native-capture/model qualification."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from apps.api.desktop_context import DesktopContextRPC
from apps.api.native_messages import NativeMessages, NativeQuestionInput
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.agent_sessions.capability_task import FrozenCapabilityContext
from packages.domain.file_continuity import FileContinuity
from packages.domain.local_workspaces import canonical, digest
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.provider_settings import ProviderSettings
from packages.domain.store import Conflict, NotFound
from test_desktop_context_api import frozen_fixture
from test_file_continuity import docx, scene
from test_native_collaboration import HostLifecycleCounter


ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'c' * 64


def require(value, message='Frozen capability context contract differs'):
    if not value:
        raise AssertionError(message)


@pytest.fixture
def context_scene(scene):
    store, data, directory, pid, workspaces, binding = scene
    authority = NativeCollaborationAuthority(data, token=TOKEN, owner_pid=123,
        native=HostLifecycleCounter(), platform='posix')
    context = DesktopContextRPC(authority)
    raw, image, frame, response = frozen_fixture(context)
    visual = response['observation']['observations']['visual']
    visual['width'], visual['height'] = 7, 5
    response['observation']['observations']['accessibility'] = {
        'text_content': '原创当前页面：不要把另一份文件当成这里的未保存正文。'}
    context.ws._write(context._path('contexts', response['observation']['context_ref']['id'] + '.json'), response)
    continuity = FileContinuity(store, data, workspaces)
    app = SimpleNamespace(state=SimpleNamespace(store=store, settings=SimpleNamespace(data_dir=data),
        provider_settings=ProviderSettings(store, data, ROOT),
        native_capabilities=NativeCapabilities(store, data), local_workspaces=workspaces,
        file_continuity=continuity))
    native = NativeMessages(app, authority, context)
    config = {'schema': 'eeas.opencode-provider.v1', 'id': 'context-provider', 'revision': 1,
        'version': VERSION, 'source_commit': COMMIT,
        'executable': str(ROOT / 'var/opencode-ui-offline-guard/opencode'),
        'executable_sha256': CLI_SHA['posix'], 'provider_id': 'eeas-context-provider',
        'protocol': 'OPENAI_COMPATIBLE_CHAT_COMPLETIONS', 'provider_url': 'http://127.0.0.1:9/v1',
        'model_id': 'original-no-network-fixture', 'secret_ref': 'secret:context-fixture',
        'transport_kind': 'MOCK_WIRE', 'supports_images': True}
    profile = {'id': config['id'], 'revision': 1, 'sha256': digest(config)}
    native.transport.provider_registry = OpenCodeProviderRegistry(lambda *_: deepcopy(config), lambda _: 'fixture-only')
    body = {'operation_id': 'frozen-capability-task', 'project_id': pid,
        'provider_profile_ref': profile, 'context_ref': response['observation']['context_ref'],
        'file_source_ref': None, 'file_link_ref': None, 'conversation_id': 'original-discussion',
        'raw_text': '核对当前页和我明确选择的文件。'}
    return SimpleNamespace(native=native, body=body, config=config, profile=profile,
        directory=directory, pid=pid, workspaces=workspaces, binding=binding,
        continuity=continuity, context=context, raw=raw, image=image, frame=frame, response=response)


def selected(value, name, raw):
    (value.directory / name).write_bytes(raw)
    return value.workspaces.read(value.pid, value.binding['id'], name)['source_ref']


def freeze(value, **patch):
    body = value.body | patch
    result = value.native.freeze_capability_context(body, value.profile)
    require(FrozenCapabilityContext.model_validate(result).model_dump() == result)
    return result


def test_frozen_local_file_and_page_remain_distinct_after_files_and_foreground_change(context_scene):
    value = context_scene
    raw = '已保存文件正文：与当前窗口的未保存内容不同。\n'.encode()
    source = selected(value, '跨目录原创资料.md', raw)
    result = freeze(value, file_source_ref=source)
    require(result['file']['text'] == raw.decode())
    require(result['file']['source_sha256'] == sha256(raw).hexdigest())
    require(result['file']['comments']['coverage'] == 'NOT_APPLICABLE')
    require(result['page']['text'] == canonical(value.response['observation']))
    require(result['observation_sha256'] == digest(value.response['observation']))
    require(result['page']['whole_page_seen'] is False and result['file']['unsaved_buffer_verified'] is False)
    before = canonical(result)
    (value.directory / source['path']).write_text('用户接着修改了文件，原任务仍用冻结输入。')
    other = deepcopy(value.response)
    other_ref = {**other['observation']['context_ref'], 'id': 'another-foreground-page', 'sha256': 'e' * 64}
    other['observation']['context_ref'] = other_ref
    other['observation']['observations']['accessibility']['text_content'] = '另一页的内容，不属于原任务'
    value.context.ws._write(value.context._path('contexts', other_ref['id'] + '.json'), other)
    require(value.native.read_capability_image(result['visual']) == value.raw)
    require(canonical(result) == before)
    with pytest.raises(Conflict):
        freeze(value, file_source_ref=source)
    require((value.directory / source['path']).read_text() == '用户接着修改了文件，原任务仍用冻结输入。')
    require('data:image' not in before and 'base64' not in before and 'download_url' not in canonical(result['visual']))


def test_registered_saved_docx_preserves_tail_comments_and_tracked_markers_without_following_latest(context_scene):
    value = context_scene
    raw = docx(body='人工改过的正文', comment='尾部批注：请核对依据；不要自动接受我的修订。', long_body='长正文' * 9000)
    source = selected(value, '用户批注.docx', raw)
    link = value.continuity.register_workspace(value.pid, source, {'kind': 'USER_OPENED_WORKSPACE_FILE'})['link_ref']
    result = freeze(value, file_link_ref=link)
    file = result['file']
    require(file['kind'] == 'REGISTERED_FILE' and file['file_source_ref'] is None)
    require(file['file_link_ref'] == link and file['snapshot_ref']['project_id'] == value.pid)
    require(file['source_sha256'] == sha256(raw).hexdigest())
    require(file['content_truncated'] and file['returned_characters'] == 16000)
    comment = file['comments']['items'][0]
    require(comment['text'] == '尾部批注：请核对依据；不要自动接受我的修订。')
    require(not comment['exact_text_range_verified'] and '7' in comment['source_locator_json'])
    require(file['comments']['coverage'] == 'CLASSIC_NOTE_REFERENCES_NOT_THREADED_OR_EXACT_RANGES')
    (value.directory / source['path']).write_bytes(docx('之后另改的正文', '之后另改的批注'))
    saved = value.continuity.read_snapshot_bytes(link, file['snapshot_ref'])
    require(saved['raw'] == raw)
    require(file['comments']['items'][0] == comment)
    full = value.continuity.read_text(file['snapshot_ref'], offset=27000, limit=16000)['text_content']
    require('[tracked-ins]人工插入内容[/tracked-ins]' in full)
    require('[tracked-del]人工删除内容[/tracked-del]' in full)
    require('之后另改' not in canonical(result))


@pytest.mark.parametrize('change', ['context', 'frame', 'image', 'size', 'source', 'path'])
def test_image_read_rejects_transplanted_or_extra_authority(context_scene, change):
    value = context_scene
    result = freeze(value)
    altered = deepcopy(result['visual'])
    if change in {'context', 'frame', 'image'}:
        altered[change + '_ref']['id'] = 'foreign-original-object'
    elif change == 'size':
        altered['image_ref']['width'] += 1
    elif change == 'source':
        altered['pixel_source'] = 'VERIFIED_TARGET_PIXELS'
    else:
        altered['path'] = str(value.directory / 'unowned.png')
    with pytest.raises((Conflict, ValidationError, NotFound)):
        value.native.read_capability_image(altered)


def test_provider_without_images_does_not_read_pixels_and_corruption_never_silently_changes_basis(context_scene):
    value = context_scene
    value.config['supports_images'] = False
    value.profile['sha256'] = digest(value.config)
    image_path = value.context._path('images', value.image['id'] + '.png')
    image_path.write_bytes(b'not-the-frozen-image')
    result = freeze(value)
    require(result['visual'] is None and any('NOT_DECLARED' in w for w in result['warnings']))
    value.config['supports_images'] = True
    value.profile['sha256'] = digest(value.config)
    with pytest.raises(Conflict):
        freeze(value)


def test_unsupported_saved_file_and_cross_project_or_profile_do_not_infer_content_or_authority(context_scene):
    value = context_scene
    source = selected(value, '原始固件.bin', b'\0\xff\0original-binary')
    result = freeze(value, file_source_ref=source)
    file = result['file']
    require(file['coverage'] == 'UNAVAILABLE' and file['text'] == '' and file['content_truncated'])
    require(file['total_characters'] is None and file['comments']['coverage'] == 'UNAVAILABLE')
    require(not file['unsaved_buffer_verified'])
    with pytest.raises(Conflict):
        freeze(value, project_id='different-project', file_source_ref=source)
    with pytest.raises(Conflict):
        value.native.freeze_capability_context(value.body, {**value.profile, 'revision': 2})
    with pytest.raises(Conflict):
        freeze(value, file_source_ref=source, file_link_ref={'project_id': value.pid})


def test_unicode_page_budget_and_local_docx_comments_are_frozen_separately(context_scene):
    value = context_scene
    value.response['observation']['observations']['accessibility']['text_content'] = '原创😀' * 14000
    ref = value.response['observation']['context_ref']
    value.context.ws._write(value.context._path('contexts', ref['id'] + '.json'), value.response)
    source = selected(value, '本地批注.docx', docx(comment='准确保留这条批注'))
    result = freeze(value, file_source_ref=source)
    require(result['page']['truncated'] and len(result['page']['text']) <= 24000)
    require(len(result['page']['text'].encode('utf-8')) <= 64000)
    require(result['file']['comments']['items'][0]['text'] == '准确保留这条批注')
    require('[tracked-ins]人工插入内容[/tracked-ins]' in result['file']['text'])


def test_presentation_body_and_speaker_notes_do_not_claim_comment_coverage(context_scene):
    from pptx import Presentation
    from pptx.util import Inches
    deck = Presentation()
    slide = deck.slides.add_slide(deck.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = '原创方案的汇报内容'
    slide.notes_slide.notes_text_frame.text = '这是演讲备注，并非已覆盖的审阅批注'
    stream = BytesIO()
    deck.save(stream)
    source = selected(context_scene, '多样任务汇报.pptx', stream.getvalue())
    result = freeze(context_scene, file_source_ref=source)
    require('原创方案的汇报内容' in result['file']['text'])
    require('这是演讲备注' in result['file']['text'])
    require(result['file']['comments'] == {'items': [], 'total': None, 'truncated': True, 'coverage': 'UNAVAILABLE'})
    require('FILE_COMMENT_EXTRACTION_UNAVAILABLE_FOR_THIS_FORMAT' in result['warnings'])


def test_ordinary_absent_review_and_v1_request_hashes_remain_unchanged(context_scene):
    body = context_scene.body
    require(NativeQuestionInput.model_validate(body).request_dict() == body)
    v1 = body | {'native_review': {'version': 1, 'selection': {
        'skill_ref': {'id': 'original-skill', 'revision': 1, 'sha256': 'a' * 64},
        'tool_selection': None, 'basis_ref': None, 'capability_binding_sha256': 'b' * 64},
        'process_start_consent': False}}
    require(NativeQuestionInput.model_validate(v1).request_dict() == v1)

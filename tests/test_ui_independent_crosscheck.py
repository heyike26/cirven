"""Independent peer cases on real temporary HTTP API, not author test copies."""
from io import BytesIO
from pathlib import Path
import hashlib
import json
import os
import re
import time
from urllib.parse import urlsplit, parse_qs
from zipfile import ZipFile, ZIP_DEFLATED

import httpx
from PIL import Image
from playwright.sync_api import sync_playwright, expect
import pytest

from conftest import ROOT, LiveServer
from test_web import launch_browser  # Existing launch infrastructure only; peer cases below are original.


@pytest.fixture(scope='module')
def peer_server(tmp_path_factory):
    server = LiveServer(tmp_path_factory.mktemp('independent-ui-peer'))
    server.start()
    assert server.port != 8765
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(scope='module')
def peer_browser():
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def peer(peer_server, peer_browser, request):
    with httpx.Client(base_url=peer_server.origin, trust_env=False, timeout=15) as api:
        bootstrap = api.get('/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'}).json()
        api.headers.update({'Origin': peer_server.origin, 'X-EEAS-CSRF': bootstrap['csrf_token']})
        projects = [api.post('/api/projects', json={'name': f'{label} · {request.node.name}',
                    'description': '原创独立交叉核验；临时数据'}).json() for label in ('项目 A', '项目 B')]
        context = peer_browser.new_context(viewport={'width': 1440, 'height': 1000})
        record = {'case': request.node.name, 'origin': peer_server.origin, 'browser_version': peer_browser.version,
                  'page_errors': [], 'console': [], 'external_requests': [], 'observations': {}}
        def observe_page(page):
            page.on('pageerror', lambda error: record['page_errors'].append(str(error)))
            page.on('console', lambda message: record['console'].append({'type': message.type, 'text': message.text}))
        context.on('page', observe_page)
        context.on('request', lambda incoming: record['external_requests'].append(incoming.url)
                   if not incoming.url.startswith(peer_server.origin + '/') else None)
        page = context.new_page()
        page.goto(peer_server.origin + '/?view=projects&project=' + projects[0]['id'])
        page.wait_for_load_state('networkidle')
        expect(page.locator('#project-context')).to_have_text(projects[0]['name'])
        try:
            yield {'page': page, 'context': context, 'api': api, 'projects': projects, 'record': record, 'origin': peer_server.origin}
        finally:
            page.unroute_all(behavior='wait')  # Finish real fetch callbacks before disposing their request context.
            if os.environ.get('EEAS_EVIDENCE_DIR'):
                evidence = Path(os.environ['EEAS_EVIDENCE_DIR'])
                stem = re.sub(r'[^A-Za-z0-9_-]', '_', request.node.name)
                try:
                    page.screenshot(path=str(evidence / (stem + '.png')), full_page=True, animations='disabled')
                except Exception as error:
                    record['screenshot_error_type'] = type(error).__name__
                (evidence / (stem + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2))
            context.close()


def navigate(page, view):
    page.locator(f'nav [data-view="{view}"]').click()
    expect(page.locator(f'[data-zone="{view}"]')).to_be_visible()


def assert_local(peer):
    assert not peer['record']['page_errors']
    assert not peer['record']['external_requests']


def image_bytes(size=(30, 18), color=(35, 102, 170)):
    data = BytesIO()
    Image.new('RGB', size, color=color).save(data, format='PNG')
    return data.getvalue()


def document_word():
    data = BytesIO()
    with ZipFile(data, 'w', compression=ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/task.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="root" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/task.xml"/></Relationships>')
        archive.writestr('word/task.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>原创 Word 界面支持核验，末尾需求。</w:t></w:r></w:p></w:body></w:document>')
    return data.getvalue()


@pytest.mark.parametrize('reduced', [False, True])
def test_peer_actual_motion_keyboard_dialog_and_immediate_reopen(peer, reduced):
    page = peer['page']
    page.emulate_media(reduced_motion='reduce' if reduced else 'no-preference')
    navigate(page, 'library')
    page.locator('#toolbar-upload').focus()
    page.keyboard.press('Enter')
    expect(page.locator('#upload-drawer')).to_be_visible()
    first = page.locator('#upload-drawer').evaluate('(e)=>({transform:getComputedStyle(e).transform,animations:e.getAnimations().map(a=>({state:a.playState,duration:a.effect.getTiming().duration}))})')
    page.wait_for_timeout(65)
    second = page.locator('#upload-drawer').evaluate('(e)=>({transform:getComputedStyle(e).transform,animations:e.getAnimations().map(a=>({state:a.playState,duration:a.effect.getTiming().duration}))})')
    peer['record']['observations']['motion'] = {'reduced': reduced, 'first': first, 'second': second}
    if reduced:
        assert all(animation['duration'] <= 1 for animation in first['animations'])
    else:
        assert any(animation['duration'] >= 200 for animation in first['animations'])
        assert first['transform'] != second['transform']
    focus_history = []
    for _ in range(9):
        page.keyboard.press('Tab')
        focus_history.append(page.evaluate('({id:document.activeElement.id,tag:document.activeElement.tagName,documentHasFocus:document.hasFocus(),inDrawer:!!document.activeElement.closest("#upload-drawer")})'))
    peer['record']['observations']['dialog_tab_loop'] = focus_history
    # Original minimal native-dialog baseline independently reproduces a BODY
    # slot when browser chrome owns focus. No interactive page background leaks.
    assert all(item['inDrawer'] or item['tag'] == 'BODY' and not item['documentHasFocus'] for item in focus_history)
    page.keyboard.press('Escape')
    expect(page.locator('#upload-drawer')).not_to_be_visible()
    expect(page.locator('#toolbar-upload')).to_be_focused()
    page.keyboard.press('Enter')
    expect(page.locator('#upload-drawer')).to_be_visible()
    expect(page.locator('#upload-error')).to_be_empty()
    page.keyboard.press('Escape')
    expect(page.locator('#upload-drawer')).not_to_be_visible()
    assert_local(peer)


@pytest.mark.parametrize('old_outcome', ['saved', 'invalid_utf8'])
def test_peer_old_upload_result_cannot_override_new_project_drawer(peer, old_outcome):
    page, api = peer['page'], peer['api']
    first, second = peer['projects']
    pending = []
    endpoint = peer['origin'] + '/api/projects/' + first['id'] + '/documents'
    def hold_actual_response(route):
        response = route.fetch()  # Execute actual API; delay only its browser delivery.
        pending.append((route, response))
    page.route(endpoint, hold_actual_response)
    page.locator('#toolbar-upload').click()
    file = {'name': '旧项目异步资料.txt', 'mimeType': 'text/plain',
            'buffer': b'Original source saved for project A' if old_outcome == 'saved' else b'\xffinvalid-original-utf8'}
    page.locator('#document-file').set_input_files(file)
    page.locator('#document-form [type=submit]').click()
    expect(page.locator('#upload-progress-panel')).to_be_visible()
    deadline = time.monotonic() + 15
    while not pending and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert pending
    status = pending[0][1].status
    assert status == (201 if old_outcome == 'saved' else 422)
    page.locator('#close-upload').click()
    expect(page.locator('#upload-drawer')).not_to_be_visible()
    navigate(page, 'projects')
    page.locator(f'#projects button[data-project-id="{second["id"]}"]').click()
    expect(page.locator('#project-context')).to_have_text(second['name'])
    page.locator('#toolbar-upload').click()
    page.locator('#document-file').set_input_files({'name': '新项目待导入.txt', 'mimeType': 'text/plain', 'buffer': b'new original pending'})
    pending[0][0].fulfill(response=pending[0][1])
    page.wait_for_timeout(100)
    expect(page.locator('#upload-drawer')).to_be_visible()
    expect(page.locator('#chosen-document-file-name')).to_have_text('新项目待导入.txt')
    expect(page.locator('#upload-error')).to_be_empty()
    expect(page.locator('#upload-project-label')).to_contain_text(second['name'])
    assert parse_qs(urlsplit(page.url).query)['project'] == [second['id']]
    first_docs = api.get('/api/projects/' + first['id'] + '/documents').json()['documents']
    second_docs = api.get('/api/projects/' + second['id'] + '/documents').json()['documents']
    peer['record']['observations']['late_response'] = {'status': status, 'project_A_documents': len(first_docs),
        'project_B_documents': len(second_docs), 'drawer_project': second['name'], 'new_file_preserved': True}
    assert len(first_docs) == (1 if old_outcome == 'saved' else 0) and not second_docs
    assert_local(peer)


def test_peer_mobile_keyboard_enters_visible_navigation_after_menu_open(peer):
    page = peer['page']
    page.set_viewport_size({'width': 390, 'height': 520})
    page.locator('#menu-toggle').focus()
    page.keyboard.press('Enter')
    expect(page.locator('#menu-toggle')).to_have_attribute('aria-expanded', 'true')
    page.keyboard.press('Tab')
    focused = page.evaluate('({id:document.activeElement.id,text:document.activeElement.textContent,inSidebar:!!document.activeElement.closest("#desktop-sidebar"),bodyWidth:document.body.scrollWidth,viewport:innerWidth})')
    peer['record']['observations']['mobile_menu_next_focus'] = focused
    assert focused['inSidebar'], 'Keyboard focus leaves the open pointer-modal sidebar for obscured background controls'
    assert focused['bodyWidth'] <= focused['viewport']
    assert_local(peer)


@pytest.mark.parametrize('viewport', [{'width': 390, 'height': 420}, {'width': 1440, 'height': 480}])
def test_peer_short_viewport_sidebar_bottom_is_scrollable(peer, viewport):
    page = peer['page']
    page.set_viewport_size(viewport)
    if viewport['width'] < 700:
        page.locator('#menu-toggle').click()
    sidebar = page.locator('#desktop-sidebar')
    before = sidebar.evaluate('(e)=>({height:e.clientHeight,scrollHeight:e.scrollHeight,scrollTop:e.scrollTop,overflow:getComputedStyle(e).overflowY})')
    page.locator('#retry-connection').scroll_into_view_if_needed()
    after = sidebar.evaluate('(e)=>({height:e.clientHeight,scrollHeight:e.scrollHeight,scrollTop:e.scrollTop,overflow:getComputedStyle(e).overflowY})')
    box = page.locator('#retry-connection').bounding_box()
    peer['record']['observations']['sidebar_scroll'] = {'before': before, 'after': after, 'retry_box': box, 'viewport': viewport}
    assert before['scrollHeight'] > before['height'] and after['scrollTop'] > 0
    assert after['overflow'] in {'auto', 'scroll'}
    assert box['y'] >= 0 and box['y'] + box['height'] <= viewport['height']
    assert page.evaluate('document.body.scrollWidth <= innerWidth')
    assert_local(peer)


def test_peer_reader_assets_keep_original_revision_and_real_hash_after_update(peer):
    page, api = peer['page'], peer['api']
    project = peer['projects'][0]['id']
    original = image_bytes()
    saved = api.post('/api/projects/' + project + '/documents', files={'file': ('原创图像版本.png', original, 'image/png')}, data={'source': '独立原始图像'}).json()
    page.reload()
    expect(page.locator('#project-context')).to_have_text(peer['projects'][0]['name'])
    navigate(page, 'library')
    page.locator('#documents button').filter(has_text='原创图像版本.png').first.click()
    expect(page.locator('#document-assets img')).to_be_visible()
    expect(page.locator('#document-download')).to_be_visible()
    original_src = page.locator('#document-assets img').get_attribute('src')
    assert parse_qs(urlsplit(original_src).query)['revision'] == ['1']
    assert hashlib.sha256(page.request.get(peer['origin'] + original_src).body()).hexdigest() == hashlib.sha256(original).hexdigest()
    expect(page.locator('#reader-identity')).to_contain_text(saved['sha256'])
    replacement = image_bytes((40, 20), (190, 42, 75))
    revised = api.put(f'/api/projects/{project}/documents/{saved["id"]}', files={'file': ('原创图像版本.png', replacement, 'image/png')}, data={'source': '独立v2', 'expected_revision': 1}).json()
    assert revised['revision'] == 2
    assert page.locator('#document-assets img').get_attribute('src') == original_src
    assert hashlib.sha256(page.request.get(peer['origin'] + original_src).body()).hexdigest() == hashlib.sha256(original).hexdigest()
    navigate(page, 'projects')
    page.locator(f'#projects button[data-project-id="{project}"]').click()
    navigate(page, 'library')
    page.locator('#documents button').filter(has_text='原创图像版本.png').first.click()
    expect(page.locator('#document-assets img')).to_have_attribute('src', re.compile(r'revision=2'))
    expect(page.locator('#reader-identity')).to_contain_text(revised['sha256'])
    new_src = page.locator('#document-assets img').get_attribute('src')
    assert hashlib.sha256(page.request.get(peer['origin'] + new_src).body()).hexdigest() == hashlib.sha256(replacement).hexdigest()
    peer['record']['observations']['immutable_reader'] = {'revision_1_sha256': saved['sha256'], 'revision_2_sha256': revised['sha256'],
        'old_asset_bytes_preserved': True, 'reader_now_revision': 2}
    assert_local(peer)


def test_peer_task_workbench_popup_is_independent_from_parent_project_switch(peer):
    page, api = peer['page'], peer['api']
    first, second = peer['projects']
    requirement = api.post('/api/projects/' + first['id'] + '/requirements', json={'schema_version': 'eeas.requirements.v1',
        'title': '原创独立窗口需求', 'requirements': [{'id': 'R1', 'text': 'Keep project context isolated', 'source_refs': []}],
        'acceptance': [{'id': 'A1', 'text': 'Popup keeps project A', 'requirement_ids': ['R1']}], 'confirmed': True, 'origin': 'manual'}).json()
    task = api.post('/api/projects/' + first['id'] + '/tasks', json={'goal': '独立窗口任务源', 'max_steps': 1, 'timeout_seconds': 10,
        'requirement_refs': [{'requirement_id': requirement['id'], 'revision': 1, 'sha256': requirement['sha256']}]}).json()
    assert task['id']
    page.reload()
    navigate(page, 'tasks')
    expect(page.locator('#execution-tasks')).to_contain_text('独立窗口任务源')
    with peer['context'].expect_page() as opened:
        page.locator('#open-workbench').click()
    popup = opened.value
    popup.wait_for_load_state('networkidle')
    expect(popup.locator('#execution-tasks')).to_contain_text('独立窗口任务源')
    navigate(page, 'projects')
    page.locator(f'#projects button[data-project-id="{second["id"]}"]').click()
    expect(page.locator('#project-context')).to_have_text(second['name'])
    expect(popup.locator('#project-context')).to_have_text(first['name'])
    assert parse_qs(urlsplit(popup.url).query)['project'] == [first['id']]
    popup.reload()
    expect(popup.locator('#execution-tasks')).to_contain_text('独立窗口任务源')
    peer['record']['observations']['independent_popup'] = {'parent_project': second['id'], 'popup_project': first['id'],
        'popup_view': parse_qs(urlsplit(popup.url).query)['view'], 'popup_task_id': task['id'], 'persisted_after_popup_reload': True}
    if os.environ.get('EEAS_EVIDENCE_DIR'):
        popup.screenshot(path=os.environ['EEAS_EVIDENCE_DIR'] + '/peer-independent-task-window.png', full_page=True, animations='disabled')
    assert_local(peer)


def test_peer_current_upload_drawer_accepts_docx_that_real_api_supports(peer):
    page, api = peer['page'], peer['api']
    project = peer['projects'][0]['id']
    raw = document_word()
    accepted = api.post('/api/projects/' + project + '/documents', files={'file': ('API原创Word资料.docx', raw,
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document')}, data={'source': '独立Word界面检查'})
    assert accepted.status_code == 201, accepted.text
    page.locator('#toolbar-upload').click()
    page.locator('#document-file').set_input_files({'name': '界面原创Word资料.docx',
        'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'buffer': raw})
    peer['record']['observations']['word_upload'] = {'actual_API_status': accepted.status_code,
        'actual_API_format': accepted.json()['format'], 'file_accept': page.locator('#document-file').get_attribute('accept'),
        'actual_UI_error': page.locator('#upload-error').inner_text(), 'original_archive_sha256': hashlib.sha256(raw).hexdigest()}
    expect(page.locator('#upload-error')).to_be_empty()
    assert '.docx' in page.locator('#document-file').get_attribute('accept')
    assert_local(peer)

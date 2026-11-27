"""Actual Solid manual review/ObjectDraft in isolated Chromium; no App/API listener."""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from legacy_evidence import evidence_folder, chromium_executable
from pathlib import Path
from urllib.parse import urlsplit
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
CHROMIUM = chromium_executable()


def check(value, message):
    if not value:
        raise AssertionError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def original():
    h = 'a' * 64
    ref = {'id': 'qa61-proposal', 'revision': 3, 'sha256': h}
    workspace = {'id': 'b' * 64, 'revision': 1, 'binding_sha256': h}
    nodes = [dict(id=n, title='任务' + n, description='原创说明' + n, kind='TASK', parent_id=None, depends_on=[], inputs=['原创材料'], outputs=['核对产物'], acceptance=['原验收'], capabilities=[], risks=[], source_anchors=[], origin='USER_STATEMENT') for n in ['A', 'B', 'C']]
    proposal = {**ref, 'project_id': 'qa61-project', 'current_revision': 3, 'review_status': 'CURRENT', 'answers': [], 'payload': {'candidate': {'title': '原创人工核对', 'summary': '同一真实组件的输入保留', 'goals': [], 'constraints': [], 'risks': [], 'assumptions': [], 'nodes': nodes, 'questions': []}, 'status': 'CONFIRMED', 'workspace_ref': workspace}}
    file = {'export_ref': {'id': 'e' * 64, 'request_sha256': h, 'result_sha256': h}, 'path': 'output/original/task-C.md', 'basis_sha256': h, 'edited_sha256': 'c' * 64}
    source = {'kind': 'local_file', 'project_id': proposal['project_id'], 'workspace_id': workspace['id'], 'binding_revision': 1, 'binding_sha256': h, 'path': file['path'], 'revision': 1, 'sha256': h}
    hunk = {'id': 'd' * 64, 'sha256': 'f' * 64, 'kind': 'INSERT', 'removed_text': '', 'added_text': 'MANUAL_ONLY_C_314159\n', 'keep_text': 'MANUAL_ONLY_C_314159', 'before_range': {'start': 2, 'end': 2}, 'after_range': {'start': 2, 'end': 3}, 'path': file['path'], 'basis_sha256': h, 'edited_sha256': file['edited_sha256']}
    coverage = {'complete': True, 'total_registered_files': 4, 'checked_files': 4, 'scanned_export_count': 1, 'unverified': []}
    snapshot = {'project_id': proposal['project_id'], 'target_proposal_ref': ref, 'workspace_ref': workspace, 'files': [{'file_ref': file, 'basis_source_ref': source, 'edited_source_ref': {**source, 'revision': 2, 'sha256': file['edited_sha256']}, 'node_id': 'C', 'hunks': [hunk]}], 'coverage': coverage}
    capture_ref = {'id': '1' * 64, 'revision': 1, 'sha256': digest(snapshot)}
    capture = {'capture_ref': capture_ref, 'snapshot': snapshot, 'current': {'status': 'APPLICABLE', 'pending': [], 'target_proposal_ref': ref}}
    request = {'operation_id': 'original-qa61-capture', 'target_proposal_ref': ref, 'workspace_ref': workspace, 'files': [file], 'predecessor_export_ref': None}
    choice = {'hunk_id': hunk['id'], 'hunk_sha256': hunk['sha256'], 'node_id': 'C', 'field': '', 'edit_action': '', 'item_index': '', 'mode': '', 'text': '', 'reason': ''}
    fields = {'manualCapture': {'request': request, 'request_sha256': digest({k: v for k, v in request.items() if k != 'operation_id'}), 'operation_hash': digest(request['operation_id']), 'state': 'COMPLETED', 'capture_ref': capture_ref}, 'manualReview': {'change_ref': capture_ref, 'choices': [choice], 'applied': []}}
    gate = {'code': 'MANUAL_REVIEW_REQUIRED', 'target_proposal_ref': ref, 'workspace_ref': workspace, 'status': 'REVIEW_REQUIRED', 'files': [{**file, 'node_id': 'C', 'kind': 'PROMPT', 'status': 'MODIFIED'}], 'coverage': coverage, 'predecessor_export_refs': [file['export_ref']]}
    return {'proposal': proposal, 'fields': fields, 'capture': capture, 'gate': gate}


async def run():
    out = evidence_folder('manual_review_stability_source61')
    observations = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, executable_path=str(CHROMIUM))
        try:
            for variant in ['before', 'after']:
                data = original()
                state = {'saved': None, 'operations': {}, 'calls': [], 'reject': False, 'lose': False, 'hold': None, 'capture': data['capture'], 'captures': {data['capture']['capture_ref']['id']: data['capture']}}
                context = await browser.new_context(viewport={'width': 1050, 'height': 760}, locale='zh-CN')
                page = await context.new_page()
                page.set_default_timeout(2200)
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))

                async def route(request):
                    path = urlsplit(request.request.url).path
                    if request.request.url == 'http://127.0.0.1:17861/':
                        await request.fulfill(body='<!doctype html><html lang="zh-CN"><body><div id="root"></div></body></html>', content_type='text/html')
                        return
                    if not request.request.url.startswith('http://127.0.0.1:17861/api/'):
                        await request.abort()
                        return
                    method = request.request.method
                    body = request.request.post_data_json
                    state['calls'].append({'path': path, 'method': method})
                    response, status = {}, 200
                    if path == '/api/bootstrap':
                        response = {'csrf_token': 'qa61-component-only'}
                    elif path.endswith('/workbench-state/load'):
                        response = state['saved'] or {'status': 'SAVED', 'schema_version': 1, 'key': body['key'], 'state_revision': 1, 'fields': data['fields'], 'view': {}, 'dirty': True}
                    elif path.endswith('/workbench-state/save'):
                        if state['hold'] is not None:
                            await state['hold'].wait()
                        if state['reject']:
                            response, status = {'error': 'Original known QA draft rejection', 'code': 'INVALID_INPUT'}, 422
                        else:
                            response = {'status': 'SAVED', 'schema_version': 1, **body, 'state_revision': body['expected_revision'] + 1}
                            state['saved'] = deepcopy(response)
                            state['operations'][body['operation_id']] = deepcopy(response)
                            if state['lose']:
                                await request.abort('failed')
                                return
                    elif '/workbench-state/operations/' in path:
                        response = state['operations'].get(path.rsplit('/', 1)[-1])
                        if response is None:
                            response, status = {'error': 'Unknown original QA operation'}, 404
                    elif path.endswith('/harness/manual-changes') and method == 'POST':
                        response = state['capture']
                    elif '/harness/manual-changes/' in path:
                        response = state['captures'][path.rsplit('/', 1)[-1]]
                    else:
                        await request.abort()
                        return
                    await request.fulfill(body=json.dumps(response, ensure_ascii=False), status=status, content_type='application/json')

                await page.route('**/*', route)
                await page.goto('http://127.0.0.1:17861/')
                await page.evaluate('(value)=>window.qaManualData=value', {'proposal': data['proposal']})
                await page.add_style_tag(content='body{font:16px/1.6 sans-serif}#reader{height:430px;width:800px;overflow:auto;border:1px solid #aaa;padding:16px}label{display:flex;gap:14px;margin:12px}input,textarea,select{font:inherit}pre{white-space:pre-wrap}.as-manual-node{padding:20px}button{font:inherit;margin:4px}')
                await page.add_script_tag(content=(Path(os.environ.get('EEAS_LAYOUT_FIXTURES',out)) / (variant + '.js')).read_text())
                await page.get_by_role('button', name='核对节点 C', exact=True).wait_for()
                await page.get_by_role('button', name='采用', exact=True).click()
                await page.get_by_label('人工修改目标字段', exact=True).select_option('acceptance')
                await page.get_by_label('人工修改采用方式', exact=True).select_option('ADD')
                reason = page.get_by_label('人工修改决定理由', exact=True)
                await reason.evaluate('(node)=>{window.originalReason=node;window.originalRow=node.closest("[data-manual-hunk]");node.focus()}')
                text = '逐字输入保持原焦点123abc'
                await reason.type(text, delay=18)
                await page.wait_for_timeout(280)
                first = await page.evaluate('()=>({same:document.querySelector("[aria-label=人工修改决定理由]")===originalReason,connected:originalReason.isConnected,focused:document.activeElement===originalReason,value:document.querySelector("[aria-label=人工修改决定理由]").value,selection:[originalReason.selectionStart,originalReason.selectionEnd],state:fixture.state()})')
                observations.append({'variant': variant, 'phase': 'same_real_browser_typing', 'expected': text, 'observation': first})
                (out / 'OBSERVATIONS.json').write_text(json.dumps(observations, ensure_ascii=False, indent=2))
                if variant == 'before':
                    check(not (first['same'] and first['connected'] and first['focused'] and first['value'] == text), 'Old60 browser row replacement not reproduced')
                    await page.screenshot(path=str(out / 'before-row-detached.png'))
                    await context.close()
                    continue
                check(first['same'] and first['connected'] and first['focused'] and first['value'] == text, str(first))
                await reason.evaluate('node=>node.setSelectionRange(2,7)')
                scroll = await page.locator('#reader').evaluate('node=>{node.scrollTop=110;return node.scrollTop}')
                await page.evaluate('()=>fixture.finish()')
                stable = await page.evaluate('()=>({same:document.querySelector("[aria-label=人工修改决定理由]")===originalReason,focused:document.activeElement===originalReason,value:originalReason.value,selection:[originalReason.selectionStart,originalReason.selectionEnd],scroll:document.querySelector("#reader").scrollTop,state:fixture.state()})')
                check(stable['same'] and stable['focused'] and stable['selection'] == [2, 7] and abs(stable['scroll'] - scroll) <= 1, str(stable))
                observations.append({'variant': variant, 'phase': 'selection_scroll_actual_ack', 'observation': stable})
                await reason.focus()
                await reason.press('End')
                state['hold'] = asyncio.Event()
                await reason.type('保存中输入', delay=12)
                await page.wait_for_function('fixture.state().status==="saving"')
                await reason.type('再追加', delay=12)
                state['hold'].set()
                state['hold'] = None
                await page.evaluate('()=>fixture.finish()')
                check(await reason.input_value() == text[:7] + text[7:] + '保存中输入再追加', 'Held ACK lost typed input')
                check(await reason.evaluate('node=>node===originalReason&&node===document.activeElement'), 'Held ACK replaced focused input')
                state['reject'] = True
                await reason.type('明确拒绝保留', delay=10)
                await page.wait_for_function('fixture.state().status==="error"')
                check(await reason.evaluate('node=>node===originalReason&&node===document.activeElement'), 'Known422 replaced focused input')
                rejected = await page.evaluate('fixture.state()')
                state['reject'] = False
                await page.get_by_role('button', name='核查草稿', exact=True).click()
                await page.wait_for_function('fixture.state().status==="saved"')
                check((await page.evaluate('fixture.state()'))['fields']['manualReview'] == rejected['fields']['manualReview'], 'Explicit retry lost choices')
                await reason.focus()
                await reason.press('End')
                state['lose'] = True
                await reason.type('未知ACK', delay=10)
                await page.wait_for_function('fixture.state().status==="unknown"')
                writes = len([c for c in state['calls'] if c['path'].endswith('/workbench-state/save')])
                await reason.type('未知期间追加', delay=10)
                state['lose'] = False
                await page.get_by_role('button', name='核查草稿', exact=True).click()
                await page.wait_for_function('!["unknown","saving"].includes(fixture.state().status)')
                check(len([c for c in state['calls'] if c['path'].endswith('/workbench-state/save')]) == writes, 'Unknown inspection resubmitted save')
                await page.evaluate('()=>fixture.finish()')
                check(await reason.evaluate('node=>node===originalReason&&node.isConnected'), 'Unknown operation inspection replaced input')
                observations.append({'variant': variant, 'phase': 'held_422_unknown_original_get', 'state': await page.evaluate('fixture.state()')})
                # Same immutable capture changing only current status keeps the
                # row and disables editing. A genuinely new capture replaces it.
                state['captures'][data['capture']['capture_ref']['id']]['current']['status'] = 'STALE'
                state['captures'][data['capture']['capture_ref']['id']]['current']['pending'] = [{'reason': 'SAVED_BYTES_CHANGED'}]
                await page.get_by_role('button', name='核对保存稿状态', exact=True).click()
                await page.wait_for_function('fixture.state().capture.current.status==="STALE"')
                check(await reason.evaluate('node=>node===originalReason'), 'Status-only capture refresh replaced the row')
                check(await page.get_by_role('button', name='采用', exact=True).is_disabled(), 'Stale editing not locked')
                fresh = deepcopy(data['capture'])
                fresh['snapshot']['files'][0]['file_ref']['edited_sha256'] = '8' * 64
                fresh['snapshot']['files'][0]['edited_source_ref']['sha256'] = '8' * 64
                fresh['snapshot']['files'][0]['hunks'][0].update(sha256='9' * 64, edited_sha256='8' * 64, added_text='NEW_CAPTURE_C_2718\n', keep_text='NEW_CAPTURE_C_2718')
                fresh['capture_ref'] = {'id': '7' * 64, 'revision': 1, 'sha256': digest(fresh['snapshot'])}
                fresh['current'] = {'status': 'APPLICABLE', 'pending': [], 'target_proposal_ref': fresh['snapshot']['target_proposal_ref']}
                state['capture'] = fresh
                state['captures'][fresh['capture_ref']['id']] = fresh
                gate = deepcopy(data['gate'])
                gate['files'][0]['edited_sha256'] = '8' * 64
                check(await page.evaluate('(gate)=>fixture.offerGate(gate)', gate), 'Actual controller rejected new fixture gate')
                await page.get_by_role('button', name='核对最新保存稿', exact=True).click()
                await page.wait_for_function('(id)=>fixture.state().capture?.capture_ref.id===id', arg=fresh['capture_ref']['id'])
                check(await page.evaluate('()=>!originalReason.isConnected&&document.querySelector("[data-manual-hunk]")!==originalRow'), 'New immutable capture did not reset the old row')
                review = (await page.evaluate('fixture.state()'))['fields']['manualReview']
                check(review['choices'][0]['mode'] == '' and review['choices'][0]['reason'] == '' and review['applied'] == [], 'New capture reused old choices')
                observations.append({'variant': variant, 'phase': 'new_capture_reset', 'capture_ref': fresh['capture_ref'], 'choices': review})
                await page.screenshot(path=str(out / 'after-new-capture.png'))
                check(not errors, str(errors))
                await context.close()
        finally:
            await asyncio.wait_for(browser.close(), timeout=8)
    receipt = {'status': 'PASS', 'qualification': 'ACTUAL_MANUAL_COMPONENT_OBJECTDRAFT_SOLID_KOBALTE_LINUX_CHROMIUM_CONTROLLED_TRANSPORT_NOT_APP_WINDOWS_OR_BACKEND', 'observations': observations, 'chromium': {'path': str(CHROMIUM), 'sha256': hashlib.sha256(CHROMIUM.read_bytes()).hexdigest()}, 'no_listener_model_Vite_install': True}
    (out / 'BROWSER_RECEIPT.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')


def test_actual_manual_review_input_identity_browser():
    asyncio.run(asyncio.wait_for(run(), timeout=65))

"""Real App/HTTP manual-review, local persistence barrier and derived delivery.

Only the test-owned workspace is edited. The human candidate is explicitly
manual, and the held acknowledgement is a real Store response, not a DTO stub.
No model, native Windows/Word/IDE or company-business qualification is implied.
"""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
import zipfile

from playwright.sync_api import expect

from test_current_app_continuity_browser import current_app, surface, settle, sha, ROOT

MARKER = 'MANUAL_C_126_KEEP_EXACT'


def caption(key):
    messages = {}
    for name in ['renderer.json', 'renderer-agent.json']:
        messages.update(json.loads((ROOT / 'packages/i18n' / name).read_text())['messages'])
    return messages.get(key, {}).get('zh-CN', key)


def reference(value):
    return {k: value[k] for k in ['id', 'revision', 'sha256']}


def wait_current_proposal(page, project_id, value):
    page.wait_for_function('''v=>{
      const node=document.querySelector('.as-interaction-panel[data-visible=true][aria-hidden=false]');
      if(!node)return false;const key=JSON.parse(node.dataset.key);
      return key[0]===v.pid&&key[2]==='proposal'&&key[3]===v.id&&key[4]===v.revision&&key[5]===v.sha256;
    }''', arg={'pid': project_id, **reference(value)})


def test_current_app_manual_C_review_preserves_A_and_drains_late_scroll_before_one_save(current_app):
    app = current_app
    (app.workspace / 'source.md').write_text('原始依据：A负责解析，B负责报告，C负责独立来源复核。\n')
    events = []
    with surface(app) as (page, trace):
        page.set_default_timeout(12000)
        page.get_by_role('tab', name='文件', exact=True).click()
        page.get_by_role('button', name='打开目录', exact=True).click()
        dialog = page.get_by_role('dialog', name='打开目录', exact=True)
        dialog.get_by_role('textbox', name='工程名称', exact=True).fill('人工修改连续性验收工程')
        dialog.get_by_role('textbox', name='本机目录的绝对路径', exact=True).fill(str(app.workspace))
        dialog.get_by_role('checkbox', name='允许在此目录内保存编辑', exact=True).check()
        dialog.get_by_role('button', name='打开目录', exact=True).click()
        expect(dialog).to_be_hidden(timeout=20000)
        token = page.request.get(app.origin + '/api/bootstrap', headers={'X-EEAS-Bootstrap': '1'}).json()['csrf_token']
        headers = {'Origin': app.origin, 'X-EEAS-CSRF': token}

        def call(method, path, body=None, status=200):
            result = page.request.fetch(app.origin + path, method=method, data=body, headers=headers)
            value = result.json()
            events.append({'method': method, 'path': path, 'body': body, 'status': result.status, 'result': value})
            (app.out / 'manual-api.json').write_text(json.dumps(events, ensure_ascii=False, indent=2))
            assert result.status == status, result.text()
            return value

        project = call('GET', '/api/projects')['projects']
        assert len(project) == 1
        pid = project[0]['id']
        bindings = call('GET', f'/api/projects/{pid}/local-workspaces')['workspaces']
        assert len(bindings) == 1
        binding = bindings[0]
        workspace = {k: binding[k] for k in ['id', 'revision', 'binding_sha256']}
        local = f'/api/projects/{pid}/local-workspaces/{binding["id"]}'
        material = call('POST', local + '/materials', {'operation_id': 'manual126-material', 'paths': ['source.md']}, 201)
        candidate = {
            'title': '人工修改承接方案', 'summary': 'A的更新与C的人工改文分别保留。',
            'nodes': [{'id': key, 'title': title, 'description': '依据原始来源完成独立检查。',
                       'outputs': [key + '的检查结果'], 'acceptance': [key + '原始验收条目'],
                       'depends_on': ['A'] if key == 'B' else []}
                      for key, title in [('A', '解析A资料'), ('B', '形成B报告'), ('C', '复核C来源')]],
            'questions': [],
        }
        proposal = call('POST', f'/api/projects/{pid}/planning/proposals', {
            'operation_id': 'manual126-plan', 'prompt': '明确的人工作业候选，仅用于本地机制验收。',
            'mode': 'manual', 'candidate': candidate, 'workspace_ref': workspace,
            'materials': [row['document_ref'] for row in material['materials']],
        }, 201)
        base = f'/api/projects/{pid}/planning/proposals/{proposal["id"]}'

        def confirm(value, key):
            return call('POST', base + '/confirm', {'operation_id': key, 'expected_revision': value['revision'],
                'proposal_sha256': value['sha256'], 'user_confirmed': True, 'resolutions': []})

        approved = confirm(proposal, 'manual126-confirm')
        original_export = call('POST', base + '/harness/export', {
            'operation_id': 'manual126-original-export', 'expected_revision': approved['revision'],
            'proposal_sha256': approved['sha256'], 'workspace_ref': workspace, 'directory': 'harness',
        })
        exported_C = next(item for item in original_export['files'] if item['path'].endswith('/task-C.md'))
        prompt_C = app.workspace / exported_C['path']
        human_bytes = prompt_C.read_bytes() + ('\n人工新增验收：' + MARKER + '\n').encode()
        prompt_C.write_bytes(human_bytes)
        changed_A = deepcopy(approved['payload']['candidate'])
        changed_A['nodes'][0]['description'] += ' A的独立修改应继续保留。'
        revised = call('PUT', base, {'operation_id': 'manual126-only-A', 'expected_revision': approved['revision'],
            'candidate': changed_A, 'change_reason': '仅修改A，不能静默丢掉C的人工改文。'})
        target = confirm(revised, 'manual126-confirm-A')
        assert target['revision'] == 4 and target['payload']['execution']['model_calls'] == 0
        before_ui_puts = []
        metadata_saves = []

        def request(request):
            path = urlsplit(request.url).path
            if request.method == 'PUT' and path == base:
                before_ui_puts.append(request.post_data_json)
            if request.method == 'POST' and path.endswith('/workbench-state/save'):
                metadata_saves.append(request.post_data_json)

        page.on('request', request)
        page.reload(wait_until='networkidle')
        page.get_by_role('button', name='显示或收起工作台', exact=True).click()
        row = page.locator('.as-review-row').filter(has=page.locator('.as-review-kind', has_text=caption('任务与图')))
        row.filter(has_text=candidate['title']).click()
        region = page.get_by_role('region', name=caption('任务计划'), exact=True)
        card = region.locator('.as-proposal')
        expect(card.get_by_role('heading', name=candidate['title'], exact=True)).to_be_visible()
        card.locator('.as-export-options > summary').click()
        card.get_by_role('textbox', name='完整交付相对目录', exact=True).fill('harness')
        card.get_by_role('button', name=caption('生成完整交付文件'), exact=True).click()
        capture_button = card.get_by_role('button', name=caption('核对人工修改'), exact=True)
        expect(capture_button).to_be_enabled()
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/harness/manual-changes')) as captured:
            capture_button.click()
        assert captured.value.status == 201, captured.value.text()
        capture = captured.value.json()
        assert capture['current']['status'] == 'APPLICABLE'
        assert len(capture['snapshot']['files']) == 1 and capture['snapshot']['files'][0]['node_id'] == 'C'
        card.get_by_role('button', name='核对节点 C', exact=True).click()
        hunk = card.locator('.as-manual-hunk')
        expect(hunk).to_have_count(1)
        hunk.get_by_role('button', name=caption('采用'), exact=True).click()
        hunk.get_by_role('combobox', name=caption('人工修改目标节点'), exact=True).select_option('C')
        hunk.get_by_role('combobox', name=caption('人工修改目标字段'), exact=True).select_option('acceptance')
        hunk.get_by_role('combobox', name=caption('人工修改采用方式'), exact=True).select_option('ADD')
        reason = hunk.get_by_role('textbox', name=caption('人工修改决定理由'), exact=True)
        reason.fill('采用用户明确保存的C验收改文。')
        reason.focus()
        page.keyboard.type(' 原稿逐字保留。')
        expect(reason).to_be_focused()
        card.get_by_role('button', name=caption('放入节点草稿'), exact=True).click()
        expect(card.get_by_role('textbox', name='复核C来源 acceptance', exact=True)).to_have_value(re.compile(MARKER))
        card.get_by_role('textbox', name=caption('修改理由'), exact=True).fill('仅承接已明确审阅的C修改，同时保留A的新说明。')
        held = []

        def hold_prepared(route):
            body = route.request.post_data_json if route.request.method == 'POST' else None
            prepared = body.get('fields', {}).get('manualSave') if body else None
            if prepared and prepared.get('state') == 'PREPARED' and not held:
                response = route.fetch()
                assert response.status == 200
                held.append((route, response, body))
                page.evaluate('window.__manualPreparationHeld=true')
            else:
                route.fallback()

        page.route('**/workbench-state/save', hold_prepared)
        card.get_by_role('button', name=caption('保存新版本'), exact=True).click()
        page.wait_for_function('()=>window.__manualPreparationHeld===true')
        assert before_ui_puts == [], 'Business save must await its real prepared-draft acknowledgement'
        inspector = region.locator('.as-inspector')
        before = inspector.evaluate('e=>e.scrollTop')
        inspector.hover()
        page.mouse.wheel(0, -240)
        page.wait_for_function('before=>document.querySelector(".as-interaction-panel[aria-hidden=false] .as-inspector").scrollTop!==before', arg=before)
        late_scroll = inspector.evaluate('e=>e.scrollTop')
        with page.expect_response(lambda r: r.request.method == 'PUT' and urlsplit(r.url).path == base) as saved:
            held[0][0].fulfill(response=held[0][1])
        assert saved.value.status == 200, saved.value.text()
        adopted = saved.value.json()
        assert len(before_ui_puts) == 1
        original_packet = held[0][2]['fields']['manualSave']
        assert before_ui_puts[0]['operation_id'] == original_packet['operation_id']
        prepared_writes = [value for value in metadata_saves if (value.get('fields', {}).get('manualSave') or {}).get('operation_id') == original_packet['operation_id']]
        assert len(prepared_writes) >= 2
        assert any(value['view'].get('scroll', {}).get('y') == late_scroll for value in prepared_writes)
        assert {value['fields']['manualSave']['request_sha256'] for value in prepared_writes} == {original_packet['request_sha256']}
        assert adopted['revision'] == 5
        nodes = {node['id']: node for node in adopted['payload']['candidate']['nodes']}
        assert nodes['A']['description'] == changed_A['nodes'][0]['description']
        assert nodes['B'] == target['payload']['candidate']['nodes'][1]
        assert MARKER in json.dumps(nodes['C']['acceptance'], ensure_ascii=False)
        assert prompt_C.read_bytes() == human_bytes
        assert adopted['payload']['manual_inheritance'][0]['capture_ref'] == capture['capture_ref']
        wait_current_proposal(page, pid, adopted)
        expect(region.get_by_role('button', name=caption('确认此版本计划'), exact=True)).to_be_enabled()
        with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/confirm')) as accepted:
            region.get_by_role('button', name=caption('确认此版本计划'), exact=True).click()
        assert accepted.value.status == 200
        final_proposal = accepted.value.json()
        wait_current_proposal(page, pid, final_proposal)
        settle(page)
        final_card = region.locator('.as-proposal')
        final_summary = final_card.locator('.as-export-options > summary')
        def delivery_geometry():
            return final_summary.evaluate('''e=>{const rows=[];for(let n=e;n;n=n.parentElement){const s=getComputedStyle(n);rows.push({
              tag:n.tagName,cls:n.className,aria:n.getAttribute('aria-hidden'),inert:n.inert,
              rect:n.getBoundingClientRect().toJSON(),clientRects:n.getClientRects().length,
              display:s.display,visibility:s.visibility,contentVisibility:s.contentVisibility,
              overflow:s.overflow,scrollTop:n.scrollTop,scrollHeight:n.scrollHeight,clientHeight:n.clientHeight,
              position:s.position,transform:s.transform,open:n.open});}return rows;}''')
        (app.out / 'delivery-before-click.json').write_text(json.dumps(delivery_geometry(), ensure_ascii=False, indent=2))
        try:
            final_summary.click()
        except Exception:
            (app.out / 'delivery-click-failure.json').write_text(json.dumps(delivery_geometry(), ensure_ascii=False, indent=2))
            raise
        final_card.get_by_role('textbox', name='完整交付相对目录', exact=True).fill('harness')
        with page.expect_response(lambda r: r.request.method == 'POST' and '/planning-delivery/proposals/' in r.url and r.url.endswith('/export')) as delivered:
            final_card.get_by_role('button', name=caption('生成完整交付文件'), exact=True).click()
        assert delivered.value.status == 200, delivered.value.text()
        delivery = delivered.value.json()
        assert delivery['proposal_ref'] == delivery['taskgraph']['proposal_ref'] == delivery['harness']['proposal_ref'] == reference(final_proposal)
        assert MARKER in json.dumps(delivery['taskgraph']['nodes'], ensure_ascii=False)
        doc = page.request.get(app.origin + delivery['report']['artifact']['download_url'])
        assert doc.status == 200
        with zipfile.ZipFile(BytesIO(doc.body())) as archive:
            assert archive.testzip() is None and MARKER in archive.read('word/document.xml').decode()
        trace['steps'].append({'action': 'manual_C_review_and_one_saved_version_after_late_scroll_then_delivery',
            'original_target': reference(target), 'saved': reference(adopted), 'confirmed': reference(final_proposal),
            'capture_ref': capture['capture_ref'], 'manual_save_operation_id': original_packet['operation_id'],
            'prepared_metadata_saves': len(prepared_writes), 'business_put_count': len(before_ui_puts),
            'late_scroll': late_scroll, 'delivery': delivery, 'human_file_sha_before_delivery': sha(prompt_C)})

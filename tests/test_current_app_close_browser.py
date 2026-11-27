"""Actual preload close protocol + current App/real Store across renderer restart.

Electron transport is controlled. Windows/Main process teardown is not implied.
No business file write, model or existing user database is used.
"""
import hashlib
import json

import pytest
from playwright.sync_api import expect

from test_current_app_continuity_browser import current_app, surface, settle, sha, ROOT


def install_close_transport(page, app):
    path = ROOT / 'apps/desktop-host/preload.cjs'
    preload = path.read_text()
    # Run the original preload, exposing only required collaboration and close
    # methods. Other desktop services are deliberately absent in this fixture.
    script = '''(()=>{
      const handlers=new Map(),calls=[];
      const state=mode=>({mode,platform:'CONTROLLED_BROWSER_TRANSPORT',
        capabilities:{current_context:false,native_input:false}});
      const ipc={on(name,fn){const rows=handlers.get(name)??[];rows.push(fn);handlers.set(name,rows);},
        removeListener(name,fn){handlers.set(name,(handlers.get(name)??[]).filter(x=>x!==fn));},
        async invoke(channel,body){calls.push({channel,body});
          if(channel==='eeas:collaboration-get')return state('workbench');
          if(channel==='eeas:collaboration-mode')return state(body);
          if(channel==='eeas:close-flush-complete')return {status:'OBSERVED'};
          throw Error('Unexpected controlled IPC: '+channel);
        }};
      const keep=['version','getCollaborationState','captureCurrentContext',
        'setCollaborationMode','onNativeContext','onDraftFlushRequested'];
      const electron={ipcRenderer:ipc,contextBridge:{exposeInMainWorld(name,value){
        globalThis[name]=Object.freeze(Object.fromEntries(keep.map(k=>[k,value[k]])));
      }}};
      new Function('require',PRELOAD)(name=>{
        if(name!=='electron')throw Error('Unexpected preload dependency');return electron;
      });
      globalThis.__closeProbe={calls,
        fire(id){for(const fn of handlers.get('eeas:flush-drafts')??[])fn(null,{id});},
        listeners(){return (handlers.get('eeas:flush-drafts')??[]).length;}};
    })();'''.replace('PRELOAD', json.dumps(preload))
    page.add_init_script(script=script)
    (app.out / 'preload.json').write_text(json.dumps({
        'path': str(path), 'sha256': sha(path),
        'scope': 'Original preload; controlled IPC, narrowed exposed services; no native teardown',
    }, indent=2))
    page.reload(wait_until='networkidle')
    page.locator('.as-shell').wait_for(state='visible')
    page.wait_for_function('()=>window.__closeProbe.listeners()===1')
    settle(page)


def acknowledgements(page):
    return page.evaluate("()=>window.__closeProbe.calls.filter(x=>x.channel==='eeas:close-flush-complete')")


def fire_close(page, identifier):
    page.evaluate('id=>window.__closeProbe.fire(id)', identifier)


@pytest.mark.parametrize('outcome', ['acknowledged', 'committed_ack_lost'])
def test_current_app_close_waits_or_refuses_then_restores_exact_draft_without_replay(current_app, outcome):
    app = current_app
    filename = '关闭恢复.md'
    original = '关闭前保持文件原字节。\n'
    path = app.workspace / filename
    path.write_text(original)
    private = original + '尚未提交文件的私稿 😀 CLOSE_127_' + outcome
    composer_text = '下一步继续核查原修改；这段输入没有发出。'
    with surface(app) as (page, trace):
        install_close_transport(page, app)
        page.set_default_timeout(12000)
        page.get_by_role('tab', name='文件', exact=True).click()
        page.get_by_role('button', name='打开目录', exact=True).click()
        dialog = page.get_by_role('dialog', name='打开目录', exact=True)
        dialog.get_by_role('textbox', name='工程名称', exact=True).fill('关闭恢复隔离工程')
        dialog.get_by_role('textbox', name='本机目录的绝对路径', exact=True).fill(str(app.workspace))
        dialog.get_by_role('checkbox', name='允许在此目录内保存编辑', exact=True).check()
        dialog.get_by_role('button', name='打开目录', exact=True).click()
        expect(dialog).to_be_hidden(timeout=20000)
        page.get_by_role('treeitem', name=filename, exact=True).click()
        editor = page.get_by_role('textbox', name='编辑工作文件', exact=True)
        expect(editor).to_have_value(original)
        composer = page.get_by_role('textbox', name='描述要完成的工作', exact=True)
        composer.fill(composer_text)
        held, writes = [], []

        def intercept(route):
            body = route.request.post_data_json
            if body['key']['kind'] != 'local-file' or body['fields'].get('text') != private:
                route.fallback()
                return
            writes.append(body)
            assert not held, 'Original draft write must not be replayed'
            response = route.fetch()
            assert response.status == 200, response.text()
            held.append((route, response, body))
            page.evaluate('window.__closeDraftHeld=true')

        page.route('**/workbench-state/save', intercept)
        editor.fill(private)
        page.wait_for_function('()=>window.__closeDraftHeld===true')
        assert len(writes) == 1
        identifier = '1' * 32
        fire_close(page, identifier)
        expect(page.locator('.as-shell')).to_have_js_property('inert', True)
        assert acknowledgements(page) == []
        assert path.read_text() == original
        if outcome == 'committed_ack_lost':
            held[0][0].abort('failed')
            page.wait_for_function("()=>window.__closeProbe.calls.some(x=>x.channel==='eeas:close-flush-complete')")
            assert acknowledgements(page) == [{'channel': 'eeas:close-flush-complete',
                                               'body': {'id': identifier, 'status': 'FAILED'}}]
            expect(page.locator('.as-shell')).to_have_js_property('inert', False)
            expect(editor).to_have_value(private)
            expect(composer).to_have_value(composer_text)
            status = page.locator('.as-inspector:not([hidden]) .as-draft-status')
            expect(status).to_have_attribute('data-state', 'unknown')
            fire_close(page, '2' * 32)
            page.wait_for_function("()=>window.__closeProbe.calls.filter(x=>x.channel==='eeas:close-flush-complete').length===2")
            assert acknowledgements(page)[-1]['body']['status'] == 'FAILED'
            assert len(writes) == 1
            with page.expect_response(lambda r: '/workbench-state/operations/' in r.url) as inspected:
                page.get_by_role('button', name='核查草稿', exact=True).click()
            assert inspected.value.status == 200
            inspected_packet = inspected.value.json()
            assert inspected_packet['operation_id'] == held[0][2]['operation_id']
            assert inspected_packet['fields']['text'] == private
            expect(status).to_have_attribute('data-state', 'saved')
            identifier = '3' * 32
            fire_close(page, identifier)
        else:
            held[0][0].fulfill(response=held[0][1])
        page.wait_for_function('id=>window.__closeProbe.calls.some(x=>x.channel===\'eeas:close-flush-complete\'&&x.body.id===id)', arg=identifier)
        acks = acknowledgements(page)
        assert acks[-1]['body'] == {'id': identifier, 'status': 'SAVED'}
        expect(page.locator('.as-shell')).to_have_js_property('inert', False)
        expect(editor).to_have_value(private)
        expect(composer).to_have_value(composer_text)
        assert len(writes) == 1 and path.read_text() == original
        assert not [x for x in trace['responses'] if x['method'] == 'POST' and x['path'].endswith('/edits')]
        # Destroy the renderer document and its module/session memory; retain
        # the browser profile and real API/Store as a desktop relaunch would.
        before_restart = len(trace['responses'])
        page.goto('about:blank')
        page.goto(app.origin + '/agent-shell/', wait_until='networkidle')
        page.locator('.as-shell').wait_for(state='visible')
        page.wait_for_function('()=>window.__closeProbe.listeners()===1')
        expect(page.get_by_role('textbox', name='描述要完成的工作', exact=True)).to_have_value(composer_text)
        # Current workspace restores its active exact-basis editor directly.
        # Requiring manual navigation would obscure that product behavior.
        expect(page.get_by_role('textbox', name='编辑工作文件', exact=True)).to_have_value(private)
        restored_loads = [x for x in trace['responses'][before_restart:]
                          if x['method'] == 'POST' and x['path'].endswith('/workbench-state/load')
                          and x.get('request', {}).get('key') == held[0][2]['key']]
        assert restored_loads and all(x['status'] == 200 for x in restored_loads)
        assert acknowledgements(page) == []
        assert path.read_text() == original
        trace['steps'].append({'action': 'actual_preload_App_close_barrier_and_renderer_restart',
            'outcome': outcome, 'acknowledgements_before_restart': acks,
            'metadata_write_count': len(writes), 'original_operation_id': held[0][2]['operation_id'],
            'draft_key': held[0][2]['key'], 'source_sha256': sha(path),
            'restored_exact_basis_reads': len(restored_loads), 'manual_restore_clicks': 0,
            'draft_sha256': hashlib.sha256(private.encode()).hexdigest(),
            'native_teardown': 'NOT_EXERCISED', 'business_file_writes': 0})

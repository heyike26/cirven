"""Focused UNIT: actual Panel/Solid/Kobalte; only external host callbacks simulated.
No real process, MCP RPC, live API, Windows, provider or model qualification.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.request

from test_native_capabilities_panel_browser import FIXTURE, REPO, require


def mcp_fixture():
    source = FIXTURE
    old = "let mcp=[{...base('local_fixture'),type:'local',timeout:60000,probe_permitted:true}];"
    require(old in source, 'exact original boundary fixture missing')
    source = source.replace(old, old+"\nconst configs=new Map([['local_fixture',{type:'local',command:['/fixture/python','/fixture/mcp.py'],files_sha256:{'/fixture/python':H,'/fixture/mcp.py':H},enabled:true,timeout:60000}]]);let probeHeld=false;")
    old = "  ops.set(body.operation_id,"
    require(old in source, 'commit boundary missing')
    source = source.replace(old, "  else{const row={...(mcp.find(item=>item.id===body.id)??base(body.id)),config_ref:receipt.config_ref,type:config.type,enabled:config.enabled,timeout:config.timeout,probe_permitted:config.type==='local'&&config.enabled};const previous=mcp.find(item=>item.id===body.id);if(previous)Object.assign(previous,row);else mcp.push(row);configs.set(body.id,structuredClone(config));}\n"+old)
    old = "readAgentMCP:async(id,config_ref)=>({config_ref,configuration:{type:'remote',url:'https://fixture.invalid/mcp',enabled:true,timeout:60000},current_configuration:true,integrity:'CURRENT'}),"
    require(old in source, 'MCP read boundary missing')
    source = source.replace(old, "readAgentMCP:async(id,config_ref)=>({config_ref,configuration:structuredClone(configs.get(id)),current_configuration:true,integrity:'CURRENT'}),")
    old = "probeAgentMCP:async(id,body)=>{record('PROBE',{id,...body});return {id:'c'.repeat(32),config_ref:body.config_ref,status:'FAILED',initialized:false,list_tools_succeeded:false,loaded_in_original_session:false,qualified:false,tools_invoked:0};},"
    require(old in source, 'MCP probe boundary missing')
    source = source.replace(old, """probeAgentMCP:async(id,body)=>{record('PROBE',{id,...body});const result={id:'c'.repeat(32),config_ref:body.config_ref,status:'INITIALIZED_AND_LISTED',initialized:true,list_tools_succeeded:true,loaded_in_original_session:false,qualified:false,tools_invoked:0,cleanup:'OWNED_STDIO_CONTEXT_EXITED',tools:[{name:'local_echo'}]};const finish=()=>{mcp.find(item=>item.id===id).last_probe=structuredClone(result);return result;};if(probeHeld)return new Promise(resolve=>held.push(()=>resolve(finish())));return finish();},""")
    source = source.replace("window.qa={calls,", "window.qa={calls,mcp:id=>structuredClone(mcp.find(item=>item.id===id)),probeHold:value=>probeHeld=value,")
    return source


@contextmanager
def actual_inline_panel(tmp_path):
    from playwright.sync_api import sync_playwright
    app=REPO/'apps/agent-shell'; fixture=tmp_path/'browser'; fixture.mkdir()
    (fixture/'index.html').write_text('<!doctype html><meta charset="utf-8"><div id="app"></div><script type="module" src="/fixture.tsx"></script>')
    (fixture/'fixture.tsx').write_text(mcp_fixture().replace('__SRC__','/@fs/'+str(app/'src')))
    (fixture/'vite.config.mjs').write_text(f'''import solid from {json.dumps(str(app/'node_modules/vite-plugin-solid/dist/esm/index.mjs'))};export default {{root:{json.dumps(str(fixture))},cacheDir:{json.dumps(str(fixture/'vite-cache'))},plugins:[solid()],resolve:{{dedupe:['solid-js'],alias:{{'solid-js':{json.dumps(str(app/'node_modules/solid-js'))},'@kobalte/core/dialog':{json.dumps(str(app/'node_modules/@kobalte/core/dist/dialog/index.jsx'))},'@kobalte/core/switch':{json.dumps(str(app/'node_modules/@kobalte/core/dist/switch/index.jsx'))}}}}},server:{{hmr:false,host:'127.0.0.1',strictPort:true,fs:{{allow:[{json.dumps(str(REPO))},{json.dumps(str(tmp_path))}]}}}}}};''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env=dict(os.environ,TMPDIR=str(tmp_path),TMP=str(tmp_path),TEMP=str(tmp_path),NODE_COMPILE_CACHE=str(tmp_path/'node-cache'))
    log=(tmp_path/'vite.log').open('w');server=subprocess.Popen([str(REPO/'.node-tools/node_modules/node/bin/node'),str(app/'node_modules/vite/bin/vite.js'),'--config',str(fixture/'vite.config.mjs'),'--port',str(port)],cwd=fixture,env=env,stdout=log,stderr=subprocess.STDOUT)
    outcome={'scope':'UNIT_ACTUAL_COMPONENT_EXTERNAL_HOST_BOUNDARY','checks':[],'page_errors':[],'real_mcp_calls':0,'model_calls':0}
    try:
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}',timeout=1):break
            except OSError:
                require(server.poll() is None,'own Vite exited');time.sleep(.1)
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page(viewport={'width':430,'height':760});page.set_default_timeout(6000)
                page.on('pageerror',lambda e:outcome['page_errors'].append(str(e)))
                page.goto(f'http://127.0.0.1:{port}',wait_until='domcontentloaded',timeout=90000);page.locator('#opener').wait_for(timeout=90000)
                page.locator('#opener').click();page.get_by_role('tab',name='MCP',exact=True).click()
                yield page,outcome
                require(not outcome['page_errors'],str(outcome['page_errors']));outcome['status']='PASS'
            except Exception:
                page.screenshot(path=str(tmp_path/'failure.png'));(tmp_path/'failure-dom.txt').write_text(page.locator('body').inner_text());raise
            finally:browser.close()
    finally:
        server.terminate();server.wait(timeout=10);log.close();(tmp_path/'BROWSER_RECEIPT.json').write_text(json.dumps(outcome,ensure_ascii=False,indent=2)+'\n')


def new_mcp(page,ident,*,enabled=True):
    page.get_by_role('button',name='新增 MCP',exact=True).click()
    page.get_by_role('textbox',name='MCP 标识',exact=True).fill(ident)
    page.get_by_role('textbox',name='MCP 命令参数数组',exact=True).fill('["/fixture/python","/fixture/mcp.py"]')
    page.get_by_role('textbox',name='MCP 文件 SHA256',exact=True).fill(json.dumps({'/fixture/python':'a'*64,'/fixture/mcp.py':'a'*64}))
    page.get_by_role('checkbox',name='启用',exact=True).set_checked(enabled)


def calls(page,method):
    return [row for row in page.evaluate('qa.calls') if row['method']==method]


def test_new_mcp_inline_save_recover_and_late_response(tmp_path):
    with actual_inline_panel(tmp_path) as (page,outcome):
        probe=lambda:page.get_by_role('button',name='连接并读取工具',exact=True)
        new_mcp(page,'inline_saved')
        require(probe().count()==0,'unsaved draft must not expose a probe')
        page.get_by_role('button',name='保存配置',exact=True).click()
        probe().wait_for()  # Regression: v1 required returning to the directory and reopening.
        require(page.get_by_role('textbox',name='MCP 标识',exact=True).input_value()=='inline_saved','stay in same new MCP detail')
        require(not calls(page,'PROBE'),'saving must not automatically probe')
        page.evaluate('qa.probeHold(true)');probe().click()
        require(probe().is_disabled(),'pending inline probe is visibly blocked')
        require(page.get_by_role('textbox',name='MCP 命令参数数组',exact=True).is_disabled(),'same detail busy key protects form while probing')
        page.evaluate('qa.release()');page.evaluate('qa.probeHold(false)')
        page.get_by_text('已初始化并读取 1 个工具',exact=False).wait_for()
        observed=calls(page,'PROBE');require(len(observed)==1,'one explicit probe only')
        require(observed[0]['body']['config_ref']==page.evaluate("qa.mcp('inline_saved').config_ref"),'probe binds exact saved ref')
        require(observed[0]['body']['allowed_effects']==['PROCESS_START'],'retain explicit process-start intent')
        page.evaluate('Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{})))')
        page.screenshot(path=str(tmp_path/'inline-saved-probe-430-zh.png'))
        outcome['checks'].append('explicit enabled save exposes inline probe; one exact-ref call; same-key pending protection')
        # A successful disabled save may show the action, but it cannot start anything.
        page.get_by_role('button',name='返回目录',exact=False).click();new_mcp(page,'inline_disabled',enabled=False)
        page.get_by_role('button',name='保存配置',exact=True).click();probe().wait_for()
        require(probe().is_disabled(),'disabled saved configuration must not probe')
        require(len(calls(page,'PROBE'))==1,'disabled save adds no calls')
        outcome['checks'].append('disabled saved configuration remains ineligible')
        # Save happened but reply was lost: only original receipt inspection restores basis.
        page.get_by_role('button',name='返回目录',exact=False).click();new_mcp(page,'inline_unknown')
        page.evaluate("qa.mode('UNKNOWN')");page.get_by_role('button',name='保存配置',exact=True).click()
        inspect=page.get_by_role('button',name='核对原操作',exact=True);inspect.wait_for()
        require(probe().count()==0,'unknown save must not borrow the directory item as saved basis')
        count=len(calls(page,'CONFIGURE'));inspect.click();probe().wait_for()
        require(len(calls(page,'CONFIGURE'))==count,'unknown recovery only GETs original operation')
        require(page.get_by_role('textbox',name='MCP 标识',exact=True).input_value()=='inline_unknown','recovery stays in same new detail')
        page.evaluate("qa.mode('OK')");probe().click()
        page.wait_for_function("qa.calls.filter(x=>x.method==='PROBE').length===2")
        require(calls(page,'PROBE')[-1]['body']['config_ref']==page.evaluate("qa.mcp('inline_unknown').config_ref"),'recovered probe uses original exact ref')
        outcome['checks'].append('unknown save keeps action unavailable; original GET recovery exposes exact saved probe without POST replay')
        # Leave a saving new item for a different existing detail; its late basis must stay isolated.
        page.get_by_role('button',name='返回目录',exact=False).click();new_mcp(page,'inline_late')
        page.evaluate("qa.mode('HOLD')");page.get_by_role('button',name='保存配置',exact=True).click()
        page.get_by_role('button',name='返回目录',exact=False).click()
        page.locator('.as-nc-card-main').filter(has_text='local_fixture').click()
        page.get_by_role('spinbutton',name='MCP 连接超时',exact=True).fill('12300')
        page.evaluate('qa.release()');page.wait_for_function("!!qa.mcp('inline_late')")
        require(page.get_by_role('textbox',name='MCP 标识',exact=True).input_value()=='local_fixture','late new-save does not switch current detail')
        require(page.get_by_role('spinbutton',name='MCP 连接超时',exact=True).input_value()=='12300','late save preserves other draft')
        page.evaluate("qa.mode('OK')");page.get_by_role('button',name='保存配置',exact=True).click()
        page.wait_for_function("qa.calls.some(x=>x.method==='CONFIGURE'&&x.body.id==='local_fixture')")
        body=[row['body']for row in calls(page,'CONFIGURE')if row['body']['id']=='local_fixture'][-1]
        require(body['expected_revision']==1 and body['configuration']['timeout']==12300,'other detail retains its own exact basis')
        outcome['checks'].append('late new-MCP save cannot alter another detail or its CAS basis')

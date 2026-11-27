"""Actual production HTML/DOM -> Sidebar -> existing-ticket read recovery.

VS Code/Windows metadata are explicit fixture boundaries. BridgeClient and
controller are real; the external bridge is loopback fixture HTTP. No Windows,
installed App, real ticket, user state, model or editor buffer is accessed.
"""
import argparse,hashlib,json,os,subprocess,time,urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[3]
PLUGIN=ROOT/'plugins/vscode-companion'
NODE=ROOT/'.node-tools/node_modules/node/bin/node'

def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);p.add_argument('--expect-blocked',action='store_true');a=p.parse_args()
    out=a.out.resolve();out.mkdir(parents=True,exist_ok=False);(out/'tmp').mkdir()
    env={**os.environ,'TMPDIR':str(out/'tmp'),'TEMP':str(out/'tmp'),'TMP':str(out/'tmp'),'PYTHONDONTWRITEBYTECODE':'1'}
    records=[];errors=[];receipt={'scope':__doc__,'expected_blocked':a.expect_blocked,'windows':'NOT_RUN','model_calls':0}
    with (out/'host.stderr').open('w') as stderr:
      host=subprocess.Popen([str(NODE),str(PLUGIN/'tests/refresh-host.cjs'),str(out)],env=env,text=True,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr)
      try:
        line=host.stdout.readline();assert line,'fixture host exited before readiness'
        origin=json.loads(line)['origin']
        def request(route,value=None):
            body=None if value is None else json.dumps(value).encode()
            with urllib.request.urlopen(urllib.request.Request(origin+route,data=body,headers={'Content-Type':'application/json'}),timeout=45) as response:return json.load(response)
        rendered=subprocess.run([str(NODE),str(PLUGIN/'tests/theme-html.cjs'),origin,str(out/'webview.html'),str(out/'html-storage')],env=env,text=True,capture_output=True)
        (out/'html-factory.log').write_text(rendered.stdout+rendered.stderr);assert rendered.returncode==0
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True,env=env);context=browser.new_context(viewport={'width':320,'height':480})
            context.expose_binding('__hostMessage',lambda _source,value:request('/message',value))
            context.add_init_script("window.__posted=[];window.acquireVsCodeApi=()=>({getState:()=>undefined,setState:value=>{},postMessage:value=>{window.__posted.push(value);window.__hostMessage(value).then(r=>{for(const m of r.messages)window.postMessage(m,'*');});}});")
            page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(origin,wait_until='networkidle');page.wait_for_function("()=>document.querySelector('#connection').textContent==='已连接'")
            page.locator('#input').fill('Keep original unsent draft 中文 😀');page.wait_for_function("()=>document.querySelector('#draft-status').textContent==='本机私稿'")
            before=request('/audit');page.evaluate("()=>window.__originalInput=document.querySelector('#input')")
            started=time.monotonic();scheduled=request('/schedule',{});elapsed=time.monotonic()-started
            page.evaluate("rows=>{for(const row of rows)window.postMessage(row,'*')}",scheduled['messages']);page.wait_for_function("()=>document.querySelector('#connection').textContent==='待连接'")
            def state():return page.evaluate("()=>{const e=document.querySelector('#refresh'),r=e.getBoundingClientRect();return {connection:document.querySelector('#connection').textContent,count:document.querySelectorAll('#refresh').length,inside_navigation:!!e.closest('#navigation'),navigation_hidden:document.querySelector('#navigation').hidden,refresh_visible:!!(r.width&&r.height)&&getComputedStyle(e).visibility!=='hidden',refresh_disabled:e.disabled,refresh_rect:{x:r.x,y:r.y,width:r.width,height:r.height},same_input:document.querySelector('#input')===window.__originalInput,input:document.querySelector('#input').value,overflow:document.documentElement.scrollWidth>innerWidth+1,posted:window.__posted.map(x=>x.type)}}")
            lost=state();page.screenshot(path=str(out/'lost-connection.png'));records.append({'case':'real-30s-scheduled-transport-failure','elapsed_seconds':elapsed,'actual':lost})
            assert lost['navigation_hidden'] and lost['count']==1 and lost['same_input'] and not lost['overflow']
            assert lost['input']==before['draft']['text']
            if a.expect_blocked:
                assert not lost['refresh_visible'] and lost['inside_navigation']
            else:
                assert lost['refresh_visible'] and not lost['inside_navigation'] and not lost['refresh_disabled']
                page.locator('#refresh').click();page.wait_for_function("()=>document.querySelector('#connection').textContent==='已连接'")
                after=request('/audit');recovered=state();records.append({'case':'actual-header-click-existing-ticket-recovery','actual':recovered,'audit':after});page.screenshot(path=str(out/'recovered.png'))
                assert after['secret_reads']==after['discovery_reads']==1
                assert [c['path'] for c in after['calls']].count('/v1/pair/status')==1
                assert not any(c['path']=='/v1/pair' for c in after['calls'])
                assert sorted(c['rpc'] for c in after['calls'] if 'rpc' in c)==['projects.list','session.get','sessions.list','status']
                assert after['requests']==before['requests'] and after['draft']==before['draft'] and after['selected']==before['selected']
                assert recovered['same_input'] and recovered['input']==before['draft']['text']
                assert recovered['posted'].count('refresh')==1 and not any(x in recovered['posted'] for x in ['send','connect','repairPair','start'])
            assert not errors;browser.close()
        receipt.update(status='PASS',records=records,page_errors=errors)
      finally:
        if host.poll() is None:host.stdin.write('close\n');host.stdin.flush()
        host.wait(timeout=10)
        receipt['source_sha256']={n:hashlib.sha256((PLUGIN/n).read_bytes()).hexdigest() for n in ['extension.cjs','controller.cjs','media/view.js','media/view.css','package.json','tests/view.test.cjs','tests/refresh-host.cjs','tests/refresh-browser.py']}
        (out/'BROWSER_RECEIPT.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':receipt.get('status','FAILED'),'records':len(records),'out':str(out)}))

if __name__=='__main__':main()

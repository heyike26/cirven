"""Actual companion HTML/CSS/JS; VSCode message transport is a browser fixture.
No desktop/kernel/model is substituted into a runtime qualification claim.
"""
import argparse,hashlib,http.server,json,os,subprocess,threading
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[3]
PLUGIN=ROOT/'plugins/vscode-companion'
NODE=ROOT/'.node-tools/node_modules/node/bin/node'

def main():
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);args=p.parse_args();out=args.out.resolve();out.mkdir(exist_ok=False);(out/'tmp').mkdir();(out/'host-theme.css').write_text(':root{--vscode-font-size:18px;}')
 class Handler(http.server.BaseHTTPRequestHandler):
  def do_GET(self):
   target={'/':out/'webview.html','/host-theme.css':out/'host-theme.css','/media/view.css':PLUGIN/'media/view.css','/media/view.js':PLUGIN/'media/view.js','/media/vendor/marked.js':PLUGIN/'media/vendor/marked.js'}.get(self.path)
   if target is None:self.send_error(404);return
   raw=target.read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8'if self.path=='/'else'text/css'if self.path.endswith('.css')else'text/javascript');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
  def log_message(self,*args):pass
 server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();origin=f'http://127.0.0.1:{server.server_port}'
 env=dict(os.environ,TMPDIR=str(out/'tmp'),TMP=str(out/'tmp'),TEMP=str(out/'tmp'));errors=[];records=[]
 try:
  result=subprocess.run([str(NODE),str(PLUGIN/'tests/theme-html.cjs'),origin,str(out/'webview.html'),str(out/'html-state')],env=env,cwd=ROOT,text=True,capture_output=True);(out/'html-factory.log').write_text(result.stdout+result.stderr);assert result.returncode==0
  with sync_playwright()as pw:
   browser=pw.chromium.launch(headless=True,env=env)
   for locale,width in [('zh-CN',320),('en',320),('zh-CN',620),('en',620)]:
    context=browser.new_context(viewport={'width':width,'height':520});context.add_init_script("window.__posted=[];window.acquireVsCodeApi=()=>({getState:()=>undefined,setState:x=>window.__saved=x,postMessage:x=>window.__posted.push(x)});");page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.goto(origin,wait_until='networkidle')
    session={'project_id':'project','session_ref':{'id':'session','revision':1,'sha256':'b'*64},'generation':1,'messages':[],'available_agent_modes':['plan','build','workflow'],'workflow_mode_supported':True,'artifacts':[]}
    snapshot={'projectId':'project','projects':[{'id':'project','name':'当前工程'}],'session':session,'sessions':[session],'sessionTokens':['c'*64],'scopeToken':'a'*64,'draft':{'text':'Keep exact 中文 😀','revision':0,'agent':'build'},'status':{'connected':True},'pending':[],'recoverable':[]}
    def emit():page.evaluate('m=>window.postMessage(m,"*")',{'type':'state','connection':'PAIRED','trusted':True,'locale':locale,'notice':'','snapshot':snapshot})
    def acknowledge():
     last=page.evaluate('()=>window.__posted.filter(x=>x.type==="draft").at(-1)');snapshot['draft']={'text':last['text'],'agent':last.get('agent','build'),'revision':last['revision']+1};page.evaluate('m=>window.postMessage(m,"*")',{'type':'draftResult','token':last['token'],'sequence':last['sequence'],'revision':last['revision']+1,'accepted':True});emit();return last
    page.evaluate('()=>{const link=document.createElement("link");link.rel="stylesheet";link.href="/host-theme.css";document.head.append(link)}');page.wait_for_function('()=>getComputedStyle(document.documentElement).getPropertyValue("--vscode-font-size").trim()==="18px"');emit();mode=page.locator('#agent-mode');entry=page.locator('#input');expect(entry).to_have_value('Keep exact 中文 😀');entry.evaluate('e=>window.__input=e')
    mode.select_option('workflow');page.wait_for_function('()=>window.__posted.some(x=>x.type==="draft"&&x.agent==="workflow")');emit();expect(mode).to_have_value('workflow');acknowledge()
    entry.fill('Workflow exact input 中文 😀');page.wait_for_function('()=>window.__posted.filter(x=>x.type==="draft").at(-1).text==="Workflow exact input 中文 😀"');acknowledge();expect(mode).to_have_value('workflow')
    page.locator('#send').click();entry.press('Control+Enter');sent=page.evaluate('()=>window.__posted.filter(x=>x.type==="send")');assert len(sent)==2 and all(x['agent']=='workflow'for x in sent)
    session['active_operation_id']='parent';session['work_mode_message']={'operation_id':'parent','generation':1,'agent':'workflow'};emit();page.screenshot(path=str(out/f'workflow-active-{locale}-{width}.png'));active_geometry=mode.evaluate('e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect(),c=document.createElement("canvas").getContext("2d");c.font=s.fontSize+" "+s.fontFamily;return {width:r.width,required:c.measureText(e.selectedOptions[0].textContent).width+parseFloat(s.paddingLeft)+parseFloat(s.paddingRight)+32}}');assert active_geometry['width']+1>=active_geometry['required'],('Mode label clipped while busy',locale,width,active_geometry);mode.select_option('build');acknowledge();assert'Workflow'in mode.get_attribute('title');expect(entry).to_have_value('Workflow exact input 中文 😀')
    session['active_operation_id']=None;session.pop('work_mode_message');session['available_agent_modes']=['plan','build'];snapshot['draft']['agent']='workflow';emit();expect(mode).to_have_value('workflow');expect(page.locator('#send')).to_be_disabled();assert page.locator('#operation-status').inner_text();before=len(page.evaluate('window.__posted'));entry.press('Control+Enter');assert not any(x['type']=='send'for x in page.evaluate('window.__posted')[before:])
    status=page.locator('#draft-status').evaluate('e=>({height:e.getBoundingClientRect().height,line:parseFloat(getComputedStyle(e).lineHeight),width:e.getBoundingClientRect().width})');page.screenshot(path=str(out/f'status-{locale}-{width}.png'));assert status['height']<=status['line']+1,('Private draft status squeezed into multiple lines',locale,width,status)
    page.locator('#recovery').evaluate('e=>e.open=true');page.evaluate('m=>window.postMessage(m,"*")',{'type':'recoveryResult','scopeToken':'a'*64,'revision':snapshot['draft']['revision']});page.wait_for_function('()=>!document.querySelector("#recovery").open');expect(entry).to_have_value('Workflow exact input 中文 😀')
    assert entry.evaluate('e=>e===window.__input');assert page.locator('pre').count()==0
    geometry=mode.evaluate('e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom,overflow:document.documentElement.scrollWidth>innerWidth+1}}');assert not geometry['overflow']and geometry['right']<=width+1 and geometry['bottom']<=520+1
    meta=page.locator('.composer-meta').bounding_box();assert meta['y']+meta['height']<=521,('Status outside visible viewport',locale,width,meta)
    page.screenshot(path=str(out/f'workflow-unavailable-{locale}-{width}.png'));records.append({'locale':locale,'width':width,'mode_geometry':geometry,'atomic_mode_and_text':True,'active_mode_frozen_tooltip':True,'unavailable_no_send':True,'same_input_DOM':True});context.close()
   browser.close()
  assert not errors,errors
  (out/'RESULT.json').write_text(json.dumps({'status':'PASS','scope':'ACTUAL_PRODUCTION_COMPANION_BROWSER_WITH_VSCODE_MESSAGE_FIXTURE','records':records,'page_errors':errors,'Windows_native':'NOT_QUALIFIED_BY_THIS_BROWSER','model_calls':0},ensure_ascii=False,indent=2))
 finally:server.shutdown();server.server_close();thread.join(5)
if __name__=='__main__':main()

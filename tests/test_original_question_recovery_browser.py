"""Production answer UI and exact App restore callback against real Windows DTOs.
Only the original operation GET is a fixture; no Windows/model qualification.
"""
from contextlib import contextmanager
import hashlib,json,os,re,socket,subprocess,time,urllib.request
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright,expect

R=Path(__file__).resolve().parents[1]
TEMPLATE=R/'apps/agent-shell/tests/fixtures/original-question-recovery.template.tsx'
F=R/'apps/agent-shell/tests/fixtures/native-r20-unsent-question-draft.json'

@contextmanager
def recovery_app(tmp_path):
 app=R/'apps/agent-shell';root=tmp_path/'ui';root.mkdir();source=(app/'src/App.tsx').read_text();callback=source[source.index('  async function restoreQuestionAnswer('):source.index('  function originalOwnerRecovery(){')]
 fixture=TEMPLATE.read_text().replace('__SRC__','/@fs/'+str(app/'src')).replace('__FIXTURE__','/@fs/'+str(F)).replace('__CALLBACK__',callback)
 (root/'fixture.tsx').write_text(fixture);(root/'index.html').write_text('<!doctype html><meta charset="utf-8"><div id="app"></div><script type="module" src="/fixture.tsx"></script>')
 (root/'vite.config.mjs').write_text('import solid from '+json.dumps(str(app/'node_modules/vite-plugin-solid/dist/esm/index.mjs'))+';export default '+json.dumps({'root':str(root),'cacheDir':str(root/'cache'),'server':{'host':'127.0.0.1','hmr':False,'strictPort':True,'fs':{'allow':[str(R),str(tmp_path)]}},'resolve':{'dedupe':['solid-js'],'alias':{'solid-js':str(app/'node_modules/solid-js')}}}).replace('"root":','"plugins":[solid()],"root":',1)+';')
 with socket.socket()as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 log=(root/'vite.log').open('w');process=subprocess.Popen([str(R/'.node-tools/node_modules/node/bin/node'),str(app/'node_modules/vite/bin/vite.js'),'--config',str(root/'vite.config.mjs'),'--port',str(port)],cwd=root,env={**os.environ,'TMPDIR':str(tmp_path),'NODE_COMPILE_CACHE':str(tmp_path/'node-cache')},stdout=log,stderr=subprocess.STDOUT)
 try:
  deadline=time.monotonic()+30
  while True:
   assert process.poll()is None,'Own test service exited; inspect vite.log'
   try:urllib.request.urlopen('http://127.0.0.1:'+str(port),timeout=1).close();break
   except OSError:
    if time.monotonic()>deadline:raise
    time.sleep(.1)
  with sync_playwright()as pw:
   browser=pw.chromium.launch(headless=True)
   try:yield browser,'http://127.0.0.1:'+str(port),hashlib.sha256(callback.encode()).hexdigest()
   finally:browser.close()
 finally:
  process.terminate()
  try:process.wait(timeout=10)
  except subprocess.TimeoutExpired:process.kill();process.wait()
  log.close();assert process.poll()is not None

def open_page(browser,origin,lang='zh',width=1280):
 context=browser.new_context(viewport={'width':width,'height':820 if width>400 else 640});page=context.new_page();page.set_default_timeout(15000);page.errors=[];page.on('pageerror',lambda e:page.errors.append(str(e)));page.goto(origin+'?lang='+lang);expect(page.locator('.as-answer-recovery>summary')).to_be_visible();return context,page

@pytest.mark.parametrize('lang,width',[('zh',1280),('en',320)])
def test_recover_real_legacy_answers_append_focus_reload_and_no_old_reply(tmp_path,lang,width):
 with recovery_app(tmp_path)as(browser,origin,callback_sha):
  context,page=open_page(browser,origin,lang,width)
  try:
   summary=page.locator('.as-answer-recovery>summary');assert page.locator('.as-answer-recovery').get_attribute('open')is None
   page.evaluate('window.originalComposer=document.querySelector(".as-composer textarea")');summary.click();expect(page.locator('.as-answer-recovery article')).to_contain_text('交付复核');expect(page.locator('.as-answer-recovery article')).to_contain_text('{"draft":"原话"}')
   page.locator('.as-answer-recovery article button').click();composer=page.locator('.as-composer textarea');expect(composer).to_have_value(re.compile(re.escape(json.loads(F.read_text())['source']['draft']['custom'][0]),re.S))
   page.wait_for_function('window.qa.source().copied_to!==undefined')
   value=composer.input_value();assert value.startswith('当前已写的消息 😀\n\n\n')and '{"draft":"原话"}'in value and '仅作为新消息的数据，不授权文件修改。'in value
   assert page.evaluate('window.originalComposer===document.querySelector(".as-composer textarea")&&document.activeElement===window.originalComposer')
   assert page.evaluate('window.qa.source().copied_to')==json.loads(F.read_text())['conversation'];assert page.evaluate('window.qa.target().text')==value
   page.locator('.as-answer-recovery article button').click();assert composer.input_value()==value
   before=page.evaluate('window.qa.reads()')
   for _ in range(4):page.evaluate('window.qa.poll()');page.wait_for_timeout(20)
   assert page.evaluate('window.qa.reads()')==before,'Identical owner polling must not rescan stored answer bodies'
   page.reload();expect(page.locator('.as-composer textarea')).to_have_value(value);summary=page.locator('.as-answer-recovery>summary');assert page.locator('.as-answer-recovery').get_attribute('open')is None;summary.click()
   page.locator('.as-answer-recovery article button').scroll_into_view_if_needed();metrics=page.locator('.as-answer-recovery article button').evaluate('(e)=>{const a=e.getBoundingClientRect(),r=e.closest(".as-original-pending").getBoundingClientRect();return{a:{left:a.left,right:a.right,top:a.top,bottom:a.bottom},r:{left:r.left,right:r.right,top:r.top,bottom:r.bottom},w:innerWidth}}')
   assert metrics['a']['left']>=0 and metrics['a']['right']<=metrics['w'] and metrics['a']['top']>=metrics['r']['top']-1 and metrics['a']['bottom']<=metrics['r']['bottom']+1
   assert not page.evaluate('window.qa.calls.some(v=>v.method.startsWith("FORBIDDEN"))');assert not page.errors,page.errors
   visible=page.locator('body').inner_text();assert not any(word in visible for word in ['session_ref','request_ref','copied_to','native_payload'])
   page.screenshot(path=str(tmp_path/('recovery-'+lang+'.png')));(tmp_path/'receipt.json').write_text(json.dumps({'actual_App_callback_sha256':callback_sha,'locale':lang,'width':width,'calls':page.evaluate('window.qa.calls'),'metrics':metrics,'scope':'production component/callback, original GET fixture, not native Windows/model'},ensure_ascii=False,indent=2))
  finally:context.close()

def test_parallel_input_source_navigation_and_storage_failures_preserve_data(tmp_path):
 with recovery_app(tmp_path)as(browser,origin,_):
  for scenario in ['parallel','source','navigation','other_operation','destination_failure','marker_failure']:
   context,page=open_page(browser,origin)
   try:
    page.locator('.as-answer-recovery>summary').click();before=page.evaluate('localStorage.getItem(window.qa.source().identity?'+json.dumps(json.loads(F.read_text())['source']['key'])+':"")')
    if scenario in ['parallel','source','navigation','other_operation']:page.evaluate('window.qa.pause()')
    if scenario=='destination_failure':page.evaluate('window.qa.failSave()')
    if scenario=='marker_failure':page.evaluate('window.qa.failMarker()')
    page.locator('.as-answer-recovery article button').click()
    if scenario=='parallel':page.evaluate('window.qa.edit("等待核查时新写的消息")');page.evaluate('window.qa.release()');expect(page.locator('.as-composer textarea')).to_have_value('等待核查时新写的消息')
    if scenario=='other_operation':page.evaluate('window.qa.otherOperation()');page.evaluate('window.qa.release()');expect(page.locator('.as-composer textarea')).to_have_value('当前已写的消息 😀\n')
    if scenario=='source':page.evaluate('window.qa.sourceChanged()');page.evaluate('window.qa.release()');expect(page.locator('.fixture-error')).not_to_be_empty();assert page.evaluate('window.qa.source().draft.custom[0]')=='别的窗口较新的回答';assert not page.evaluate('window.qa.source().copied_to')
    if scenario=='navigation':page.evaluate('window.qa.navigate()');page.evaluate('window.qa.release()');expect(page.locator('.as-composer textarea')).to_have_value('切换后的当前输入')
    if scenario=='destination_failure':expect(page.locator('.fixture-error')).not_to_be_empty();assert '{"draft":"原话"}'in page.locator('.as-composer textarea').input_value();assert not page.evaluate('window.qa.source().copied_to')
    if scenario=='marker_failure':expect(page.locator('.as-original-pending [role=alert]')).to_contain_text('回答已保留');assert '{"draft":"原话"}'in page.evaluate('window.qa.target().text');assert not page.evaluate('window.qa.source().copied_to')
    assert not page.evaluate('window.qa.calls.some(v=>v.method.startsWith("FORBIDDEN"))');assert not page.errors,page.errors
   finally:context.close()

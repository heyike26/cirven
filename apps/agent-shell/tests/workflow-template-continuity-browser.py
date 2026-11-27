"""Verify template replacement and stable layout through the real parser and store."""
import argparse
import json
from pathlib import Path
import socket
from threading import Thread

from fastapi.staticfiles import StaticFiles
from playwright.sync_api import Error, expect, sync_playwright
import uvicorn

from apps.api.config import Settings
from apps.api.conversation_creation import CreateConversation
from apps.api.main import create_app

TEMPLATE_ROUTE = '**/api/stage-workflows/template'
GEOMETRY = """(studio, extra) => {
  const selectors = ['.wf-page','.wf-heading','.wf-stage-composer','.wf-footer', ...extra];
  const nodes = selectors.map(selector=>studio.querySelector(selector));
  const measure = () => nodes.flatMap(node=>{
    const r=node.getBoundingClientRect(), style=getComputedStyle(node);
    return [r.x,r.y,r.width,r.height,style.opacity,style.visibility,node.isConnected];
  }).concat(studio.scrollTop,studio.scrollHeight);
  let frame, count=0;
  const before=measure(), changes=[];
  const sample=()=>{
    count++;
    const after=measure();
    if(after.some((value,i)=>value!==before[i]))changes.push(after);
    frame=requestAnimationFrame(sample);
  };
  frame=requestAnimationFrame(sample);
  window.finishTemplateTrace=()=>{cancelAnimationFrame(frame);return {before,changes,count}};
}"""


def prepare_stage(app, width, modal):
    pid = app.state.store.create_project(f'模板页面 {width}', '')['id']
    owner = app.state.conversation_creation.create(CreateConversation(
        project_id=pid, title='模板替换验收', operation_id=f'template-owner-{width}'))['conversation_ref']['id']
    service = app.state.stage_workflows
    if modal:
        definition = {'id':f'templates-{width}','name':'模板测试','nodes':[
            {'id':'custom','kind':'custom','name':'模板审阅','inputs':[]}]}
        service.define(pid, owner, f'template-define-{width}', 0, definition)
        run = service.start(pid, owner, f'template-start-{width}', definition['id'])
        sid = run['stages'][0]['stage_session_id']
        service.executor = lambda _: {'artifact':{'summary':'原审阅始终可读。','items':[
            {'id':'ITEM-1','title':'原条目','description':'原审批正文。'}]}}
        run = service.action(pid,sid,run['id'],f'template-exec-{width}',run['revision'],'custom','execute',{})
        assert run['stages'][0]['status']=='review'
        return pid,owner,run['id'],'custom',sid
    run = service.start(pid, owner, f'template-start-{width}', 'default')
    sid = run['stages'][0]['stage_session_id']
    init_fields = dict(background='审阅清单', development_constraints='单人审阅', build_environment='Python', runtime_environment='Windows')
    service.executor = lambda _: {'artifact':{'summary':'项目已初始化。','items':[
        {'id':f'P-{i}','title':value,'topic':key,'detail':value}
        for i,(key,value) in enumerate(init_fields.items())]}}
    run = service.action(pid,sid,run['id'],f'template-exec-{width}',run['revision'],'init','execute',{'user_input':init_fields})
    run = service.action(pid,sid,run['id'],f'template-approve-{width}',run['revision'],'init','approve',{})
    stage = run['stages'][1]
    assert stage['status']=='ready'
    return pid,owner,run['id'],stage['node_id'],stage['stage_session_id']


def scenario(app, page, url, width, modal, baseline, output):
    pid,owner,run,node,sid = prepare_stage(app,width,modal)
    draft_key = 'cirven-stage-input-v1:'+json.dumps([pid,owner,run,node,sid],separators=(',',':'))
    page.goto(url,wait_until='domcontentloaded')
    page.evaluate("""([project,session])=>{
        localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id:session}));
        localStorage.setItem('cirven-stage-view-v1:'+JSON.stringify([project,session]),'workflow');
    }""",[pid,sid])
    page.reload(wait_until='domcontentloaded')
    studio = page.locator('.wf-studio')
    expect(studio.get_by_role('heading',name='模板审阅' if modal else '需求梳理')).to_be_visible()
    scope = studio
    if modal:
        tools = studio.get_by_role('button',name='本步工具')
        tools.press('Enter')
        studio.get_by_role('button',name='阶段输入与模板').press('Enter')
        scope = studio.get_by_role('dialog',name='阶段输入与模板')
        expect(scope).to_be_visible()
        scope.evaluate('el=>Promise.all(el.getAnimations().map(a=>a.finished))')
    picker = scope.get_by_label('输出模板（可选）')
    draft_text = '记录每次审核的编号和时间。'
    if not modal:
        scope.get_by_label('用户需求 UR').fill(draft_text)
    picker.scroll_into_view_if_needed()
    picker.focus()
    tracked = ['.wf-input-panel'] if not modal else ['dialog[open]','.wf-artifact']
    if not baseline:
        tracked.append('.wf-template-summary')
    studio.evaluate(GEOMETRY,tracked)

    def choose(name, body):
        picker.set_input_files({'name':name,'mimeType':'application/octet-stream','buffer':body})

    def settle_trace():
        page.wait_for_timeout(140)
        trace=page.evaluate('window.finishTemplateTrace()')
        assert trace['count']>=5,trace
        assert bool(trace['changes'])==baseline,trace
        return trace

    held = []
    def intercept(route):
        held.append((route,route.fetch()))
    page.route(TEMPLATE_ROUTE,intercept)
    def next_reply():
        for _ in range(30):
            if held:
                return held.pop(0)
            page.wait_for_timeout(80)
        raise AssertionError('Template endpoint did not reply')
    approve_or_run = studio.get_by_role('button',name='审核通过并完成 →' if modal else '执行此步')
    original = 'original.md'
    choose(original,b'# Current template\n\n- One field')
    expect(scope.get_by_text('正在解析模板…',exact=True)).to_be_visible()
    expect(approve_or_run).to_be_disabled()
    route,response = next_reply()
    assert response.ok
    page.wait_for_timeout(150)
    if not baseline:
        expect(picker).to_be_focused()
    route.fulfill(response=response)
    expect(scope.get_by_text(original,exact=True)).to_be_visible()
    if baseline:
        sample=settle_trace()
        return {'width':width,'before':sample['before'],'first_changed':sample['changes'][0],
                'last_changed':sample['changes'][-1],'frames':sample['count']}
    expect(approve_or_run).to_be_enabled()
    first_trace = settle_trace()

    pending = scope.get_by_role('button',name='模板详情',exact=True)
    current_summary = scope.locator('.wf-template-summary')
    slow = 'older.md'
    newer = 'new-template-'+'q'*135+'.md'
    picker.focus()
    studio.evaluate(GEOMETRY,tracked)
    choose(slow,b'# Superseded candidate')
    expect(current_summary.get_by_role('status')).to_have_text('正在读取替换模板…')
    original_receipt = response.json()['id']
    slow_route,old_response = next_reply()
    assert old_response.ok
    choose(newer,b'# Accepted candidate')
    new_route,new_response = next_reply()
    assert new_response.ok
    expect(current_summary.get_by_text(original,exact=True)).to_be_visible()
    expect(picker).to_be_focused()
    new_route.fulfill(response=new_response)
    expect(current_summary.get_by_text(newer,exact=True)).to_be_visible()
    try:
        slow_route.fulfill(response=old_response)
    except Error:
        pass  # The renderer may already have aborted this response.
    latest_id = new_response.json()['id']
    assert latest_id!=original_receipt
    stable_replacement = settle_trace()

    picker.focus()
    studio.evaluate(GEOMETRY,tracked)
    choose('invalid.docx',b'a bad ZIP')
    expect(current_summary.get_by_role('status')).to_have_text('正在读取替换模板…')
    bad_route,bad_response = next_reply()
    assert not bad_response.ok
    pending.press('Enter')
    detail=studio.get_by_role('dialog',name='模板详情',exact=True)
    expect(detail.get_by_role('status')).to_contain_text('invalid.docx')
    detail.evaluate('el=>Promise.all(el.getAnimations().map(a=>a.finished))')
    modal_rect=detail.bounding_box()
    bad_route.fulfill(response=bad_response)
    expect(detail.get_by_role('alert')).to_contain_text('invalid.docx')
    expect(detail.get_by_text(newer,exact=True)).to_be_visible()
    assert detail.bounding_box()==modal_rect
    remove = detail.get_by_role('button',name='移除模板')
    box = remove.bounding_box()
    assert box['y']+box['height'] <= modal_rect['y']+modal_rect['height']
    assert remove.evaluate('el=>{const r=el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x+r.width/2,r.bottom-3))}')
    if not modal:
        expect(scope.get_by_label('用户需求 UR')).to_have_value(draft_text)
    page.screenshot(path=str(output/f'template-failure-{width}.png'),animations='disabled')
    page.keyboard.press('Escape')
    expect(pending).to_be_focused()
    expect(current_summary.get_by_role('status')).to_have_text('读取失败 · 保留现有模板')
    recovery = settle_trace()
    assert page.evaluate('key=>JSON.parse(localStorage.getItem(key)).template.id',draft_key)==latest_id

    pending.press('Enter')
    detail.get_by_role('button',name='移除模板').press('Enter')
    expect(pending).to_be_focused()
    expect(current_summary.get_by_text('使用默认格式',exact=True)).to_be_visible()
    assert page.evaluate('key=>JSON.parse(localStorage.getItem(key)).template',draft_key) is None
    if modal:
        page.keyboard.press('Escape')
        expect(studio.get_by_role('button',name='本步工具')).to_be_focused()
    return {'width':width,'modal':modal,'states':[first_trace,stable_replacement,recovery],
            'latest_file_only':True,'failure_preserved_selected_file':True,'removed':True}


def check(renderer, evidence, browser_path, baseline):
    evidence.mkdir(parents=True)
    sock = socket.socket()
    sock.bind(('127.0.0.1',0))
    port = sock.getsockname()[1]
    sock.listen()
    app = create_app(Settings(data_dir=evidence/'data',port=port,worker_enabled=False))
    app.router.routes[:] = [route for route in app.router.routes if getattr(route,'name','')!='agent-shell']
    app.mount('/agent-shell',StaticFiles(directory=renderer,html=True),name='agent-shell')
    server = uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning'))
    thread = Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
    thread.start()
    results = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=browser_path)
            for width, modal in ((1400,False),(390,False),(520,True)):
                if baseline and modal:
                    continue
                page=browser.new_page(locale='zh-CN',viewport={'width':width,'height':795},reduced_motion='reduce')
                try:
                    results.append(scenario(app,page,f'http://127.0.0.1:{port}/agent-shell/',width,modal,baseline,evidence))
                except Exception:
                    page.screenshot(path=str(evidence/f'failed-{width}.png'))
                    raise
                finally:
                    page.close()
            browser.close()
    finally:
        server.should_exit=True
        thread.join(10)
        (evidence/'result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--renderer',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--browser')
    parser.add_argument('--baseline',action='store_true')
    args=parser.parse_args()
    check(args.renderer,args.evidence,args.browser,args.baseline)

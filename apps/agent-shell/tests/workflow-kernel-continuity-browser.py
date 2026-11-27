"""Exercise current engine settings through the local API, including slow and stale receipts."""
import argparse
import json
from pathlib import Path
import socket
from threading import Thread
from unittest.mock import patch

from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright
import uvicorn

from apps.api.config import Settings
from apps.api.conversation_creation import CreateConversation
from apps.api.main import create_app
from packages.dynamic_workflow import kernel_settings

ROOT = '**/api/workflow-kernels'
SNAPSHOT = """(studio, includeForm)=>{
  const dialog=studio.querySelector('dialog[open]'), content=dialog.querySelector('.wf-kernel-content'),
    footer=dialog.querySelector('.wf-kernel-feedback');
  const selectors=includeForm?[dialog.querySelector('.wf-kernel-tabs'),dialog.querySelector('.wf-save-connection')]:[];
  const nodes=[studio.querySelector('.wf-page'),dialog,content,footer,...selectors];
  const measure=()=>nodes.flatMap(node=>{
    const r=node.getBoundingClientRect();
    return [r.x,r.y,r.width,r.height,node.isConnected];
  }).concat(studio.scrollTop);
  let frame, count=0;
  const before=measure(),changes=[];
  const sample=()=>{
    count++;
    const next=measure();
    if(next.some((v,i)=>v!==before[i]))changes.push(next);
    frame=requestAnimationFrame(sample);
  };
  frame=requestAnimationFrame(sample);
  window.endEngineTrace=()=>{cancelAnimationFrame(frame);return {count,changes,before}};
}"""


def run_case(app, data, page, url, width, output, baseline):
    pid=app.state.store.create_project(f'工作流测试 {width}','')['id']
    owner=app.state.conversation_creation.create(CreateConversation(
        project_id=pid,title='内核偏好验收',operation_id=f'engine-owner-{width}'))['conversation_ref']['id']
    service=app.state.stage_workflows
    run=service.start(pid,owner,f'engine-run-{width}','default')
    sid=run['stages'][0]['stage_session_id']
    def current(engine):
        return next(row for row in kernel_settings.catalog(data)['kernels'] if row['id']==engine)
    def seed(engine, model):
        row=current(engine)
        kernel_settings.save_route(data,engine,row['revision'],
            {'endpoint':'http://127.0.0.1:29801/v1','model':model,'credential_env':'','allow_http':False})
    seed('opencode','opencode-initial')
    seed('claude-code','claude-initial')
    page.goto(url,wait_until='domcontentloaded')
    page.evaluate("""([project,session])=>{
      localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id:session}));
      localStorage.setItem('cirven-stage-view-v1:'+JSON.stringify([project,session]),'workflow');
    }""",[pid,sid])
    page.reload(wait_until='domcontentloaded')
    studio=page.locator('.wf-studio')
    expect(studio.get_by_role('heading',name='工程初始化')).to_be_visible()
    page.wait_for_load_state('networkidle')
    reads=[]
    writes=[]
    page.route(ROOT,lambda route:reads.append(route))
    def hold_write(route):
        writes.append((route,route.fetch()))
    page.route(ROOT+'/*/route',hold_write)
    def take(items):
        for _ in range(30):
            if items:
                return items.pop(0)
            page.wait_for_timeout(80)
        raise AssertionError('expected request did not arrive')
    def trace(form):
        dialog.evaluate('el=>Promise.all(el.getAnimations().map(x=>x.finished))')
        studio.evaluate(SNAPSHOT,form)
    def finish_trace():
        page.wait_for_timeout(120)
        result=page.evaluate('window.endEngineTrace()')
        assert result['count']>=4 and not result['changes'],result
        return result
    opener=studio.get_by_role('button',name='内核与连接')
    opener.press('Enter')
    dialog=studio.get_by_role('dialog',name='内核与连接',exact=True)
    engine=dialog.get_by_role('combobox',name='内核',exact=True)
    model=dialog.get_by_role('textbox',name='模型路由')
    save=dialog.get_by_role('button',name='保存连接',exact=True)

    if baseline:
        take(reads).continue_()
        expect(model).to_have_value('opencode-initial')
        model.fill('baseline-write')
        save.press('Enter')
        route,response=take(writes)
        expect(model).to_be_disabled()
        expect(save).not_to_be_focused()
        expect(engine).to_be_disabled()
        route.fulfill(response=response)
        expect(model).to_have_value('baseline-write')
        return {'width':width,'form_disabled_by_save':True,'submit_focus_lost':True}

    status=dialog.locator('.wf-kernel-feedback [role=status]')
    refresh=dialog.get_by_role('button',name='刷新',exact=True)
    info=dialog.get_by_role('button',name='详情',exact=True)
    expect(status).to_have_text('正在读取内核设置…')
    trace(False)
    delayed=take(reads)
    message='本机设置暂时无法读取。'+'可从同一面板重试。'*36
    delayed.fulfill(status=503,json={'detail':message})
    expect(status).to_have_text('内核设置读取失败')
    backdrop=lambda dialog:dialog.evaluate("(el) => getComputedStyle(el, '::backdrop').backgroundColor")
    original_shade=backdrop(dialog)
    assert original_shade!='rgba(0, 0, 0, 0)'
    info.press('Enter')
    details=studio.get_by_role('dialog',name='内核设置详情',exact=True)
    expect(details).to_contain_text(message)
    assert backdrop(details)=='rgba(0, 0, 0, 0)' and backdrop(dialog)==original_shade
    page.keyboard.press('Escape')
    expect(info).to_be_focused()
    assert backdrop(dialog)==original_shade
    refresh.press('Enter')
    expect(refresh).to_be_focused()
    take(reads).continue_()
    expect(model).to_have_value('opencode-initial')
    expect(refresh).to_be_focused()
    loading_trace=finish_trace()

    model.fill('opencode-first')
    save.scroll_into_view_if_needed()
    trace(True)
    save.press('Enter')
    request,response=take(writes)
    assert response.ok
    expect(save).to_be_focused()
    expect(model).to_be_enabled()
    model.fill('opencode-later')
    model.press('Enter')
    assert not writes
    engine.select_option('claude-code')
    model.fill('claude-unsaved')
    request.fulfill(response=response)
    expect(status).to_have_text('OpenCode · 已保存模型连接')
    expect(engine).to_have_value('claude-code')
    expect(model).to_have_value('claude-unsaved')
    assert current('opencode')['route']['model']=='opencode-first'
    assert current('claude-code')['route']['model']=='claude-initial'
    engine.select_option('opencode')
    expect(model).to_have_value('opencode-later')
    expect(dialog.locator('.wf-kernel-draft-status')).to_have_text('当前内核草稿未保存')
    save_trace=finish_trace()

    trace(True)
    refresh.press('Enter')
    take(reads).fulfill(status=503,json={'detail':message})
    expect(status).to_have_text('内核设置读取失败')
    expect(refresh).to_be_focused()
    expect(model).to_have_value('opencode-later')
    failed_refresh_trace=finish_trace()

    seed('opencode','opencode-from-other-window')
    refresh.press('Enter')
    take(reads).continue_()
    expect(status).to_have_text('连接已更新，草稿保留')
    expect(model).to_have_value('opencode-later')
    save.press('Enter')
    expect(details).to_contain_text('opencode-from-other-window')
    expect(details).to_contain_text('opencode-later')
    assert not writes
    page.screenshot(path=str(output/f'kernel-conflict-{width}.png'),animations='disabled')
    details.get_by_role('button',name='保留当前草稿').press('Enter')
    expect(save).to_be_focused()
    assert current('opencode')['route']['model']=='opencode-from-other-window'
    save.press('Enter')
    request,response=take(writes)
    assert response.ok
    request.fulfill(response=response)
    expect(status).to_have_text('OpenCode · 已保存模型连接')
    assert current('opencode')['route']['model']=='opencode-later'
    expect(dialog.locator('.wf-kernel-draft-status')).to_have_text('')

    versions=dialog.get_by_role('button',name='更新和切换版本')
    versions.press('Enter')
    checks=[]
    page.route(ROOT+'/*/release',lambda route:checks.append(route))
    dialog.get_by_role('button',name='检查官方版本').press('Enter')
    check=take(checks)
    engine.select_option('claude-code')
    release={'version':'7.0.1','integrity':'sha512-test-fixture',
             'package':'vendor-opencode','url':'https://registry.npmjs.org/vendor-opencode.tgz'}
    check.fulfill(json=release)
    expect(dialog.get_by_role('button',name='下载并启用')).to_have_attribute('aria-disabled','true')
    expect(engine).to_have_value('claude-code')
    dialog.get_by_role('button',name='从本机导入版本').press('Enter')
    binary=dialog.get_by_role('textbox',name='原厂程序的绝对路径')
    binary.fill('C:/fixture/claude.exe')
    engine.select_option('opencode')
    expect(dialog.get_by_role('button',name='下载并启用')).to_have_attribute('aria-disabled','false')
    expect(binary).to_have_value('')
    engine.select_option('claude-code')
    expect(binary).to_have_value('C:/fixture/claude.exe')
    page.keyboard.press('Escape')
    expect(opener).to_be_focused()
    opener.press('Enter')
    expect(engine).to_have_value('claude-code')
    expect(binary).to_have_value('C:/fixture/claude.exe')
    dialog.get_by_role('button',name='模型连接').press('Enter')
    expect(model).to_have_value('claude-unsaved')
    target='opencode' if width==1400 else 'claude-code'
    engine.select_option(target)
    versions.press('Enter')
    if target=='claude-code':
        dialog.get_by_role('button',name='检查官方版本').press('Enter')
        take(checks).fulfill(json={**release,'package':'vendor-claude-code'})
    revision=current(target)['revision']
    endpoint=f'/api/workflow-kernels/{target}/download'
    trace(False)
    with patch('packages.dynamic_workflow.kernel_updates.download') as download:
        with page.expect_response(lambda r:r.url.endswith(endpoint) and r.request.method=='POST') as pending:
            dialog.get_by_role('button',name='下载并启用').press('Enter')
        reply=pending.value
        assert reply.ok,(reply.status,reply.json())
        assert reply.request.post_data_json=={'version':release['version'],'integrity':release['integrity'],'revision':revision}
        download.assert_called_once_with(data,target,revision,release['version'],release['integrity'])
    expect(status).to_contain_text('已启用更新')
    download_trace=finish_trace()
    return {'width':width,'traces':[loading_trace,save_trace,failed_refresh_trace,download_trace],'per_engine_drafts':True,
            'explicit_conflict_resolution':True,'release_scoped':True,'save_requests':2,
            'download_contract':target}


def check_initial_engine(app, page, url):
    pid=app.state.store.create_project('检查原阶段内核','')['id']
    owner=app.state.conversation_creation.create(CreateConversation(
        project_id=pid,title='Claude Code 阶段',operation_id='engine-target-owner'))['conversation_ref']['id']
    service=app.state.stage_workflows
    definition={'id':'engine-target','name':'选定内核','nodes':[
        {'id':'custom','kind':'custom','name':'目标内核阶段','inputs':[],'kernel':'claude-code'}]}
    service.define(pid,owner,'engine-target-def',0,definition)
    run=service.start(pid,owner,'engine-target-start',definition['id'])
    sid=run['stages'][0]['stage_session_id']
    page.goto(url,wait_until='domcontentloaded')
    page.evaluate("""([project,session])=>{
      localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id:session}));
      localStorage.setItem('cirven-stage-view-v1:'+JSON.stringify([project,session]),'workflow');
    }""",[pid,sid])
    page.reload(wait_until='domcontentloaded')
    studio=page.locator('.wf-studio')
    expect(studio.get_by_role('heading',name='目标内核阶段')).to_be_visible()
    studio.get_by_role('button',name='内核与连接').press('Enter')
    dialog=studio.get_by_role('dialog',name='内核与连接',exact=True)
    expect(dialog.get_by_label('Anthropic Messages 网关地址')).to_be_visible()
    expect(dialog.get_by_role('combobox',name='内核',exact=True)).to_have_value('claude-code')
    return {'stage_engine_on_first_open':'claude-code'}


def check(renderer, evidence, browser_path, baseline):
    evidence.mkdir(parents=True)
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    sock.listen()
    data=evidence/'data'
    app=create_app(Settings(data_dir=data,port=port,worker_enabled=False))
    app.router.routes[:]=[route for route in app.router.routes if getattr(route,'name','')!='agent-shell']
    app.mount('/agent-shell',StaticFiles(directory=renderer,html=True),name='agent-shell')
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning'))
    thread=Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
    thread.start()
    results=[]
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=browser_path)
            for width in (1400,390):
                page=browser.new_page(locale='zh-CN',viewport={'width':width,'height':750},reduced_motion='reduce')
                try:
                    results.append(run_case(app,data,page,f'http://127.0.0.1:{port}/agent-shell/',width,evidence,baseline))
                except Exception:
                    page.screenshot(path=str(evidence/f'failed-{width}.png'))
                    raise
                finally:
                    page.close()
            if not baseline:
                page=browser.new_page(locale='zh-CN',viewport={'width':520,'height':740},reduced_motion='reduce')
                results.append(check_initial_engine(app,page,f'http://127.0.0.1:{port}/agent-shell/'))
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

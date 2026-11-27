"""Verify explicit definition saves against the local renderer, API and version history."""
import argparse
import json
from pathlib import Path
import socket
from threading import Thread

from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright
import uvicorn

from apps.api.config import Settings
from apps.api.conversation_creation import CreateConversation
from apps.api.main import create_app


def check_case(app, page, url, width):
    project = app.state.store.create_project(f'定义草稿 {width}', '')['id']
    owner = app.state.conversation_creation.create(CreateConversation(
        project_id=project, title='定义草稿', operation_id=f'qa-definition-owner-{width}'))['conversation_ref']['id']
    mode = 'cirven-stage-view-v1:'+json.dumps([project, owner], separators=(',', ':'))
    page.add_init_script("""(() => {
        if(location.origin==='null')return;
        for(const [key,value] of Object.entries("""+json.dumps({
            'eeas-current-thread-v1':json.dumps({'version':1,'id':owner}),mode:'workflow',
        })+""")) localStorage.setItem(key,value);
    })()""")
    page.goto(url, wait_until='domcontentloaded')
    studio = page.locator('.wf-studio')
    form = studio.locator('.wf-definition')
    field = form.get_by_role('textbox', name='执行指令')
    status = form.locator('.wf-definition-status')
    store_key = mode+':definition-draft'
    start = studio.get_by_role('button',name='新建',exact=True)
    save = form.locator('button[type=submit]')
    recordings = []
    geometry = []

    def definitions():
        return {d['id']:d for d in app.state.stage_workflows.list(project,owner)['definitions']}

    def pending():
        for _ in range(150):
            if recordings:
                route,response = recordings.pop(0)
                assert response.ok,(response.status,response.text())
                return route,response
            page.evaluate('new Promise(requestAnimationFrame)')
        raise AssertionError('Could not observe the original save')

    def hold():
        page.route('**/api/stage-workflows/definitions',lambda route:recordings.append((route,route.fetch())),times=1)

    def trace():
        studio.evaluate("""root=>{
          const form=root.querySelector('.wf-definition'), field=form.querySelector('textarea'), save=form.querySelector('[type=submit]');
          const nodes=[form,field,save], rect=e=>{const b=e.getBoundingClientRect();return [b.x,b.y,b.width,b.height];};
          const read=()=>JSON.stringify([root.scrollTop,document.activeElement===field,nodes.every(n=>n.isConnected),...nodes.map(rect)]);
          const before=read(), changes=[];
          let frame,count=0;
          const sample=()=>{count++;if(before!==read())changes.push(read());frame=requestAnimationFrame(sample);};
          frame=requestAnimationFrame(sample);
          window.finishDefinitionTrace=()=>{cancelAnimationFrame(frame);return {count,changes};};
        }""")

    start.press('Enter')
    expect(form.get_by_role('heading',level=1)).to_be_focused()
    form.get_by_role('textbox',name='工作流名称').fill('冻结提交与随后修改')
    field.fill('第一次保存的原始说明')
    hold()
    save.press('Enter')
    expect(save).to_be_focused()
    expect(save).to_have_attribute('aria-disabled','true')
    route,response = pending()
    submitted = route.request.post_data_json
    first = response.json()
    assert first['revision']==1 and first['nodes'][0]['instructions']=='第一次保存的原始说明'
    field.fill('等待回执时继续写的第二份说明')
    field.scroll_into_view_if_needed()
    field.focus()
    trace()
    route.fulfill(response=response)
    expect(status).to_have_text('已保存 v1，后续修改仍在本机。')
    expect(field).to_have_value('等待回执时继续写的第二份说明')
    expect(field).to_be_focused()
    for _ in range(8):
        page.evaluate('new Promise(requestAnimationFrame)')
    samples=page.evaluate('window.finishDefinitionTrace()')
    assert samples['count']>=6 and not samples['changes'],samples
    geometry.append(samples['count'])
    assert definitions()[first['id']]==first

    hold()
    save.press('Enter')
    route,response = pending()
    second_body = route.request.post_data_json
    second = response.json()
    assert second['revision']==2 and second_body['operation_id']!=submitted['operation_id']
    field.fill('掉回执前后的第三份说明')
    route.fulfill(status=503,json={'detail':'测试：服务已保存，响应丢失'})
    expect(save).to_have_text('重试上次保存')
    expect(field).to_have_value('掉回执前后的第三份说明')
    page.reload(wait_until='domcontentloaded')
    expect(field).to_have_value('掉回执前后的第三份说明')
    expect(save).to_have_text('重试上次保存')
    hold()
    save.press('Enter')
    route,replay_response = pending()
    assert route.request.post_data_json==second_body
    assert replay_response.json()==second and definitions()[first['id']]==second
    route.fulfill(response=replay_response)
    expect(status).to_have_text('已保存 v2，后续修改仍在本机。')
    expect(field).to_have_value('掉回执前后的第三份说明')
    with page.expect_response(lambda response:response.url.endswith('/definitions')) as third_response:
        save.press('Enter')
    third = third_response.value.json()
    assert third['revision']==3 and third['nodes'][0]['instructions']=='掉回执前后的第三份说明'
    expect(studio.get_by_role('heading',name='选择工作流')).to_be_visible()
    expect(studio.get_by_role('combobox',name='工作流定义')).to_be_focused()
    assert page.evaluate('key=>localStorage.getItem(key)',store_key) is None

    edit = studio.get_by_role('button',name='编辑定义')
    edit.press('Enter')
    field.fill('与另一窗口的改动不同')
    external = {**third,'nodes':[{**third['nodes'][0],'instructions':'由另一窗口保存'}]}
    app.state.stage_workflows.define(project,owner,f'other-window-save-{width}',3,external)
    with page.expect_response(lambda response:response.url.endswith('/definitions')) as conflict:
        save.press('Enter')
    assert conflict.value.status==409
    expect(save).to_have_text('转为副本')
    expect(field).to_have_value('与另一窗口的改动不同')
    expect(save).to_have_attribute('aria-disabled','false')
    save.press('Enter')
    expect(save).to_have_text('保存工作流')
    expect(field).to_have_value('与另一窗口的改动不同')
    with page.expect_response(lambda response:response.url.endswith('/definitions')) as copy_result:
        save.press('Enter')
    copy = copy_result.value.json()
    assert copy['id']!=third['id'] and copy['revision']==1
    assert copy['nodes'][0]['instructions']=='与另一窗口的改动不同'
    assert definitions()[third['id']]['nodes'][0]['instructions']=='由另一窗口保存'
    expect(edit).to_be_focused()

    page.evaluate('([key,value])=>localStorage.setItem(key,JSON.stringify(value))',[store_key,third])
    page.reload(wait_until='domcontentloaded')
    expect(field).to_have_value('掉回执前后的第三份说明')
    form.get_by_role('button',name='取消').press('Enter')
    assert page.evaluate('key=>localStorage.getItem(key)',store_key) is None
    return {'width':width,'stable_frames':geometry,'same_request_replay':True,'separate_copy_on_conflict':True,
            'legacy_draft_restored':True,'saved_revisions':[first['revision'],second['revision'],third['revision']]}


def check(renderer, output, chromium):
    output.mkdir(parents=True)
    sock=socket.socket()
    sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    sock.listen()
    app=create_app(Settings(data_dir=output/'data',port=port,worker_enabled=False))
    app.router.routes[:]=[r for r in app.router.routes if getattr(r,'name','')!='agent-shell']
    app.mount('/agent-shell',StaticFiles(directory=renderer,html=True),name='agent-shell')
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning'))
    thread=Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True)
    thread.start()
    cases=[]
    errors=[]
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=chromium)
            for width in (1400,390):
                page=browser.new_page(locale='zh-CN',viewport={'width':width,'height':745},
                    reduced_motion='reduce' if width==390 else 'no-preference')
                page.on('pageerror',lambda e:errors.append(str(e)))
                try:
                    cases.append(check_case(app,page,f'http://127.0.0.1:{port}/agent-shell/',width))
                except BaseException:
                    page.screenshot(path=str(output/f'failure-{width}.png'))
                    raise
                finally:
                    page.close()
            browser.close()
        assert not errors,errors
    finally:
        server.should_exit=True
        thread.join(10)
        (output/'RESULT.json').write_text(json.dumps({'cases':cases,'page_errors':errors},ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--renderer',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--chromium')
    args=parser.parse_args()
    check(args.renderer,args.output,args.chromium)

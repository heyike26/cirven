"""Verify delayed transition receipts against real stage drafts."""
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


def check(renderer, evidence, executable):
    evidence.mkdir(parents=True)
    sock = socket.socket()
    sock.bind(('127.0.0.1',0))
    port = sock.getsockname()[1]
    sock.listen()
    app = create_app(Settings(data_dir=evidence/'data', port=port, worker_enabled=False))
    app.router.routes[:] = [r for r in app.router.routes if getattr(r,'name','')!='agent-shell']
    app.mount('/agent-shell', StaticFiles(directory=renderer, html=True), name='agent-shell')
    config = uvicorn.Config(app, host='127.0.0.1', port=port, log_level='warning')
    server = uvicorn.Server(config)
    thread = Thread(target=server.run, kwargs={'sockets':[sock]}, daemon=True)
    thread.start()
    result = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=executable)
            for width, outcome in ((1400,'recover'),(390,'direct'),(520,'unchanged'),(768,'queued-retry')):
                page = browser.new_page(locale='zh-CN',viewport={'width':width,'height':785}, reduced_motion='reduce')
                try:
                    result.append(scenario(app, page, port, width, outcome, evidence))
                except Exception:
                    page.screenshot(path=str(evidence/f'failure-{width}.png'))
                    raise
                finally:
                    page.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(10)
        (evidence/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


def scenario(app, page, port, width, outcome, evidence):
    project = app.state.store.create_project(f'草稿回执验收 {width}','')['id']
    owner = app.state.conversation_creation.create(CreateConversation(
        project_id=project, title='批准成果草稿验收', operation_id=f'draft-owner-{width}'))['conversation_ref']['id']
    service = app.state.stage_workflows
    definition = {'id':f'draft-{width}','name':'交付审阅','nodes':[
        {'id':'review','kind':'custom','name':'批准成果阶段','inputs':[]}]}
    service.define(project,owner,'draft-define-'+str(width),0,definition)
    run = service.start(project,owner,f'draft-start-{width}',definition['id'])
    session = run['stages'][0]['stage_session_id']
    artifact = {'summary':'原阶段的成果正文','items':[{'id':'ITEM-1','title':'检查结果','description':'原审批正文。'}]}
    service.executor = lambda _: {'artifact':artifact}
    reviewed = service.action(project,session,run['id'],f'prepare-review-{width}',run['revision'],'review','execute',{})
    queued = outcome=='queued-retry'
    if not queued:
        approved = service.action(project,session,run['id'],f'approve-first-{width}',reviewed['revision'],'review','approve',{})
        assert approved['stages'][0]['status']=='approved'
    original = '请明确交付成果的状态与日期。'
    revision_receipts = []
    def observe_revision(route):
        body = route.request.post_data_json
        assert body['action']==('supplement' if queued else 'revise')
        response = route.fetch()
        assert response.ok
        revision_receipts.append((route,response,body))
    page.route('**/api/stage-workflows/runs/*/actions',observe_revision)
    page.goto(f'http://127.0.0.1:{port}/agent-shell/',wait_until='domcontentloaded')
    mode_key = 'cirven-stage-view-v1:'+json.dumps([project,session],separators=(',',':'))
    draft_key = 'cirven-stage-input-v1:'+json.dumps([project,owner,run['id'],'review',session],separators=(',',':'))
    pending_key = mode_key+':pending-transition'
    page.evaluate("""([session,mode])=>{
        localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id:session}));
        localStorage.setItem(mode,'workflow');
    }""",[session,mode_key])
    page.reload(wait_until='domcontentloaded')
    studio = page.locator('.wf-studio')
    expect(studio.get_by_role('heading',name='批准成果阶段')).to_be_visible()
    studio.get_by_role('button',name='打开阶段对话').click()
    floating = studio.get_by_role('group',name='阶段对话面板')
    floating.get_by_role('button',name='补充或修订').click()
    composer = floating.get_by_role('textbox')
    composer.fill(original)
    submit = floating.get_by_role('button',name='发送要求' if queued else '创建修订')
    submit.click()
    for _ in range(50):
        if revision_receipts:
            break
        page.wait_for_timeout(100)
    held, response, submitted = revision_receipts[0]
    successor = response.json()['stages'][0]['stage_session_id']
    assert (successor==session)==queued
    assert 'draft_edit' not in submitted and 'draftEdit' not in submitted['payload']
    if outcome!='unchanged':
        composer.fill('用户在等待回执期间另写了一个版本。')
        composer.fill(original)
    # Store another field to verify that reconciling this request preserves it.
    page.evaluate("""key=>{
        const draft=JSON.parse(localStorage.getItem(key));
        localStorage.setItem(key,JSON.stringify({...draft,note:'之后保存的本机笔记'}));
    }""",draft_key)
    if queued:
        held.fulfill(status=503,json={'detail':'验收夹具截断原成功响应'})
        expect(submit).to_be_enabled()
        submit.click()
        for _ in range(50):
            if len(revision_receipts)==2:
                break
            page.wait_for_timeout(100)
        next_route,next_response,retry_body = revision_receipts[1]
        assert retry_body['operation_id']==submitted['operation_id']
        next_route.fulfill(response=next_response)
        expect(submit).to_be_enabled()
        expect(floating.get_by_text(original,exact=True)).to_be_visible()
    elif outcome=='recover':
        held.fulfill(status=503,json={'detail':'验收夹具截断原成功响应'})
        expect(studio.get_by_role('button',name='核查原操作')).to_be_visible()
        page.reload(wait_until='domcontentloaded')
        recovery = studio.get_by_role('button',name='核查原操作')
        recovery.click()
        expect(studio.get_by_role('button',name='打开已保存阶段')).to_be_enabled()
    else:
        held.fulfill(response=response)
        page.wait_for_function('key=>!localStorage.getItem(key)',arg=pending_key)
        page.wait_for_function("sid=>JSON.parse(localStorage.getItem('eeas-current-thread-v1')||'null')?.id===sid",arg=successor)
    draft = page.evaluate('key=>JSON.parse(localStorage.getItem(key))',draft_key)
    assert draft['note']=='之后保存的本机笔记'
    assert (draft.get('followUp') or '')==('' if outcome=='unchanged' else original),draft
    assert len(revision_receipts)==(2 if queued else 1)
    actual = service.get(project,successor,run['id'])['stages'][0]['follow_ups']
    assert len(actual)==1 and actual[0]['id']==submitted['operation_id']
    page.screenshot(path=str(evidence/f'review-{outcome}-{width}.png'),animations='disabled')
    history = verify_historical_recovery(page, port, studio, project, owner, run['id'], session, successor, draft_key, draft,
                                         width, outcome, evidence) if not queued else None
    assert len(revision_receipts)==(2 if queued else 1)
    return {'outcome':outcome,'width':width,'old_operation':submitted['operation_id'],'submission_requests':len(revision_receipts),
            'kept_new_draft':outcome!='unchanged','other_field_retained':True,'historical_recovery':history}


def verify_historical_recovery(page, port, studio, project, owner, run, session, successor, old_key, old_draft,
                               width, outcome, evidence):
    if outcome=='recover':
        studio.get_by_role('button',name='打开已保存阶段').click()
    expect(studio.get_by_role('button',name='查看本机草稿')).to_have_count(0)
    current_key = 'cirven-stage-input-v1:'+json.dumps([project,owner,run,'review',successor],separators=(',',':'))
    future = '当前版本独立草稿，继续在当前页完成。'
    comment = '保留说明原文和段落间空行。\n\n'+'这一段应该自动折，并且还能复制完整。'*100
    old_draft.update({'comment':comment,'mark':{'target':'ITEM-1'},'question':'这条如何验收？','rework':{'reason':'原返工原因'}})
    writes = []
    page.on('request',lambda request: writes.append(request.url) if request.method!='GET' and '/api/stage-workflows' in request.url else None)
    page.evaluate("""([oldKey, old, currentKey, future])=>{
        localStorage.setItem(oldKey,JSON.stringify(old));
        localStorage.setItem(currentKey,JSON.stringify({input:{},followUp:future}));
    }""",[old_key,old_draft,current_key,future])
    page.context.grant_permissions(['clipboard-read','clipboard-write'],origin=f'http://127.0.0.1:{port}')
    studio.get_by_role('button',name='查看此阶段上一版').click()
    expect(studio.get_by_text('正在查看阶段的历史版本。')).to_be_visible()
    opener=studio.get_by_role('button',name='查看本机草稿')
    opener.scroll_into_view_if_needed()
    studio.evaluate("""root=>{
        const page=root.querySelector('.wf-page'), article=root.querySelector('.wf-artifact');
        const measure=()=>[page,article].flatMap(el=>{
            const r=el.getBoundingClientRect(), s=getComputedStyle(el);
            return [r.x,r.y,r.width,r.height,s.opacity,s.visibility];
        }).concat(root.scrollTop,root.scrollHeight);
        const base=measure(),changes=[];
        let frame,count=0;
        const sample=()=>{count++;const value=measure();
            if(!page.isConnected||!article.isConnected||value.some((v,i)=>v!==base[i]))changes.push(value);
            frame=requestAnimationFrame(sample)};
        frame=requestAnimationFrame(sample);
        window.finishDraftTrace=()=>{cancelAnimationFrame(frame);return {count,changes}};
    }""")
    opener.press('Enter')
    dialog=studio.get_by_role('dialog',name='本机草稿',exact=True)
    expect(dialog).to_be_visible()
    chooser=dialog.get_by_role('combobox',name='选择草稿')
    expected = 'comment' if outcome=='unchanged' else 'followUp'
    expect(chooser).to_have_value(expected)
    expect(dialog.get_by_role('textbox')).to_have_value(old_draft[expected])
    frame=dialog.bounding_box()
    for field, value in [('comment',comment),('question','这条如何验收？'),('rework','原返工原因'),('note','之后保存的本机笔记')]:
        chooser.select_option(field)
        expect(dialog.get_by_role('textbox')).to_have_value(value)
        assert dialog.bounding_box()==frame
    chooser.select_option('comment')
    field=dialog.get_by_role('textbox',name='页面批注')
    expect(field).not_to_be_editable()
    assert field.evaluate('el=>el.scrollHeight>el.clientHeight')
    copy=dialog.get_by_role('button',name='复制草稿')
    if width==520:
        page.evaluate("""() => Object.defineProperty(navigator.clipboard,'writeText',{value:()=>Promise.reject(new Error('blocked'))})""")
    copy.press('Enter')
    if width==520:
        expect(dialog.get_by_role('status')).to_have_text('已选中原文，可用系统复制快捷键。')
        expect(field).to_be_focused()
        assert field.evaluate('el=>el.selectionStart===0 && el.selectionEnd===el.value.length')
    else:
        expect(dialog.get_by_role('status')).to_have_text('已复制到剪贴板。')
        assert page.evaluate('navigator.clipboard.readText()')==comment
    assert dialog.bounding_box()==frame
    page.screenshot(path=str(evidence/f'historical-drafts-{width}.png'),animations='disabled')
    page.keyboard.press('Escape')
    expect(opener).to_be_focused()
    expect(dialog).not_to_be_visible()
    trace=page.evaluate('window.finishDraftTrace()')
    assert trace['count']>=3 and not trace['changes'],trace
    assert page.evaluate('k=>JSON.parse(localStorage.getItem(k))',old_key)==old_draft
    studio.get_by_role('button',name='返回当前版本').click()
    page.wait_for_function("id=>JSON.parse(localStorage.getItem('eeas-current-thread-v1'))?.id===id",arg=successor)
    expect(studio.get_by_role('button',name='查看本机草稿')).to_have_count(0)
    studio.get_by_role('button',name='打开阶段对话').click()
    floating=studio.get_by_role('group',name='阶段对话面板')
    floating.get_by_role('button',name='补充或修订').click()
    expect(floating.get_by_role('textbox')).to_have_value(future)
    assert not writes,writes
    return {'history_source':session,'current_stage':successor,'trace':trace,'full_text_clipboard':width!=520,
            'selection_fallback':width==520,'current_draft_preserved':True,'business_writes':len(writes)}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--renderer',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--browser')
    args=parser.parse_args()
    check(args.renderer,args.evidence,args.browser)

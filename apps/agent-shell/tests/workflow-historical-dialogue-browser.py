"""Verify a late reply in the packaged renderer and a real workflow store."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
from threading import Event, Thread

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
            for width, outcome in ((1400,'answered'),(390,'answered'),(520,'failed')):
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
    project = app.state.store.create_project(f'历史咨询验收 {width}','')['id']
    conversation = app.state.conversation_creation.create(CreateConversation(
        project_id=project, title='历史咨询验收', operation_id=f'late-owner-{width}'))['conversation_ref']['id']
    service = app.state.stage_workflows
    definition = {'id':f'history-{width}','name':'交付审阅','nodes':[
        {'id':'review','kind':'custom','name':'原验收阶段','inputs':[]}]}
    service.define(project,conversation,'late-define-'+str(width),0,definition)
    run = service.start(project,conversation,f'late-start-{width}',definition['id'])
    session = run['stages'][0]['stage_session_id']
    artifact = {'summary':'原阶段的成果正文','items':[{'id':'ITEM-1','title':'检查结果','description':'原审批正文。'}]}
    service.executor = lambda _: {'artifact':artifact}
    def action(value, operation, kind, payload):
        return service.action(project,session,run['id'],f'{operation}-{width}',value['revision'],'review',kind,payload)
    reviewed = action(run,'execute','execute',{})
    assert reviewed['stages'][0]['status']=='review'
    action(reviewed,'approve','approve',{})
    begun, release = Event(), Event()
    question = '请说明原审批的依据。'
    answer = ('原审批以原会话中的成果条目为依据。' if outcome=='answered'
              else '原咨询的本机模型连接中断。')
    def answer_after_revision(context):
        assert context['artifact']==artifact
        begun.set()
        assert release.wait(80)
        if outcome=='failed':
            raise RuntimeError(answer)
        return answer
    service.dialogue_executor = answer_after_revision
    revision = f'late-question-{width}'
    read_marker, writes = [], []
    def observe(request):
        if '/api/stage-workflows' in request.url:
            if request.method!='GET':
                writes.append(request.url)
            elif request.url.split('?')[0].endswith('/api/stage-workflows'):
                read_marker.append(request.url)
    page.on('request', observe)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(service.ask, project,session,run['id'],'review',revision,question)
        try:
            assert begun.wait(10)
            latest = service.get(project,session,run['id'])
            changed = action(latest,'revise','revise',{'text':'为后继版本补充运行约束'})
            assert changed['stages'][0]['stage_session_id']!=session
            url = f'http://127.0.0.1:{port}/agent-shell/'
            page.goto(url,wait_until='domcontentloaded')
            page.evaluate("""([project,session])=>{
                localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id:session}));
                localStorage.setItem('cirven-stage-view-v1:'+JSON.stringify([project,session]),'workflow');
            }""",[project,session])
            page.reload(wait_until='domcontentloaded')
            studio = page.locator('.wf-studio')
            expect(studio.get_by_text('正在查看阶段的历史版本。')).to_be_visible()
            trigger = studio.get_by_role('button', name='打开阶段对话')
            trigger.click()
            floating = studio.get_by_role('group',name='阶段对话面板')
            expect(floating.get_by_text(question,exact=True)).to_be_visible()
            expect(floating.get_by_text('正在回答…',exact=True)).to_be_visible()
            closer = floating.get_by_role('button',name='收起阶段对话')
            closer.focus()
            if width==390:
                page.route('**/api/stage-workflows?*',lambda route:route.fulfill(
                    status=503,json={'detail':'固定夹具暂时无法读取历史阶段'}),times=1)
            studio.evaluate("""root=>{
                const wp=root.querySelector('.wf-page'), box=root.querySelector('.wf-conversation-float');
                const message=[...box.querySelectorAll('article')].find(el=>el.textContent.includes('请说明原审批的依据。'));
                const focus=document.activeElement;
                const read=()=>[wp,box,root.querySelector('.wf-artifact')].flatMap(el=>{
                    const {x,y,width,height}=el.getBoundingClientRect();
                    return [x,y,width,height];
                }).concat(root.scrollTop,root.scrollHeight);
                const baseline=read(),changed=[];
                let frame, count=0;
                const sample=()=>{
                    count++;const next=read();
                    if(!wp.isConnected||!box.isConnected||!message.isConnected||document.activeElement!==focus||
                       next.some((x,i)=>x!==baseline[i]))changed.push(next);
                    frame=requestAnimationFrame(sample);
                };
                frame=requestAnimationFrame(sample);
                window.finishTrace=()=>{cancelAnimationFrame(frame);return {count,changed}};
            }""")
            read_count = len(read_marker)
            release.set()
            pending.result(timeout=15)
            observed = service.list(project,session)['historical_stage']['dialogue'][0]
            assert observed['status']==outcome and observed.get('answer',observed.get('error'))==answer
            expect(floating.get_by_text(answer,exact=True)).to_be_visible(timeout=5000)
            trace = page.evaluate('window.finishTrace()')
            assert trace['count']>=2 and not trace['changed'],trace
            assert len(read_marker)>read_count and not writes
            after_reply = len(read_marker)
            page.wait_for_timeout(1900)
            assert len(read_marker)==after_reply and not writes
            assert not service.list(project,changed['stages'][0]['stage_session_id'])['runs'][0]['stages'][0]['dialogue']
            page.screenshot(path=str(evidence/f'historical-{outcome}-{width}.png'),animations='disabled')
            page.keyboard.press('Escape')
            expect(trigger).to_be_focused()
            return {'width':width,'outcome':outcome,'trace':trace,'writes':writes,'followup_reads':after_reply-read_count}
        finally:
            release.set()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--renderer',type=Path,required=True)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--browser')
    args=parser.parse_args()
    check(args.renderer,args.evidence,args.browser)

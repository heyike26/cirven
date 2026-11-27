"""Full compiled Cirven renderer + localhost REST/SQLite. Only the model output is controlled."""
import argparse
import asyncio
from io import BytesIO
from docx import Document
import hashlib
import json
from pathlib import Path
from queue import Queue
from runpy import run_path
import socket
import sys
import threading
import time
from unittest.mock import patch
from urllib.parse import unquote
from zipfile import ZipFile

import uvicorn
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.requests import Request
from playwright.sync_api import sync_playwright, expect

from apps.api.config import Settings
from apps.api.conversation_creation import CreateConversation
from apps.api.conversation_organization import OrganizationChange
from apps.api.main import create_app
from packages.domain.native_capabilities import NativeCapabilities
from packages.dynamic_workflow.stage_contracts import source_urs
from packages.dynamic_workflow import stage_workspace


def check(root, output):
    output.mkdir(parents=True, exist_ok=True)
    server_socket = socket.socket()
    server_socket.bind(('127.0.0.1', 0))
    port = server_socket.getsockname()[1]
    server_socket.listen()
    app = create_app(Settings(data_dir=output/'data', port=port, worker_enabled=False))
    # Serve only the explicit compiled renderer, preserving the production dist symlink and installation.
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, 'name', '') != 'agent-shell']
    app.mount('/agent-shell', StaticFiles(directory=root, html=True), name='agent-shell')
    pid = app.state.store.create_project('工作流浏览器验证', '')['id']
    conversation = app.state.conversation_creation.create(CreateConversation(
        operation_id='workflow_browser_case', project_id=pid,title='产品设计验证'))['conversation_ref']['id']
    summaries={'cases':'已为导出需求列出操作步骤、预期结果与自动检查。',
               'design':'已整理导出文件的目录、实现方式和自检要求。',
               'development':'导出文件已写入工作区，已保留本次的文件版本。',
               'testing':'已按审核通过的用例核对导出文件的正文。',
               'delivery':'导出文件已备妥，可下载并核对文件版本。'}
    slow_mount = {'armed':False, 'reads':[]}
    test_inputs = {'shadow':False,'restore':False}
    delivery_input = {'replace':False}
    member_path='验收/联动设备结果和最终审阅记录归档-中文长文件名.txt'
    member_bytes='保存实际审阅摘要。'.encode()
    archive_buffer=BytesIO()
    with ZipFile(archive_buffer,'w') as archive:
        archive.writestr(member_path,member_bytes)
    release_buffer=BytesIO()
    with ZipFile(release_buffer,'w') as archive:
        archive.writestr('diagnostics.zip',archive_buffer.getvalue())
        for i in range(40):
            archive.writestr(f'samples/run-{i:02}.txt',f'fixture {i}')
    release_bytes=release_buffer.getvalue()
    history_traffic = {'action_path':None,'posts':[]}
    entry_reads = Queue()
    entry_requests = []
    async def delay_requirements_mount(request):
        child = request.query_params.get('conversation_id')
        if request.method=='POST' and request.url.path==history_traffic['action_path']:
            history_traffic['posts'].append(request.url.path)
        if child==conversation and request.method=='GET' and request.url.path=='/api/stage-workflows':
            entry_requests.append(time.monotonic())
            if not entry_reads.empty():
                entry=entry_reads.get_nowait()
                entry['seen'].set()
                await asyncio.to_thread(entry['release'].wait, 12)
                if entry['outcome']=='cancel':
                    return JSONResponse(app.state.stage_workflows.list(pid,conversation))
                if entry['outcome']=='failed':
                    return JSONResponse({'detail':'测试用工作流入口读取失败'},status_code=503)
        if (slow_mount['armed'] or slow_mount.get('fail_node')) and request.method=='GET' and request.url.path=='/api/stage-workflows' and child:
            runs=app.state.stage_workflows.list(pid,conversation)['runs']
            fail_node=slow_mount.get('fail_node')
            if fail_node and runs and any(stage['node_id']==fail_node and stage['stage_session_id']==child
                for stage in runs[0]['stages']):
                slow_mount.pop('fail_node')
                return JSONResponse({'detail':'测试用目标会话读取失败'},status_code=503)
            if runs and any(stage['node_id']=='requirements' and stage['stage_session_id']==child for stage in runs[0]['stages']):
                started=time.monotonic()
                remaining=slow_mount.setdefault('resume',started+4.2)-started
                if remaining>0:
                    await asyncio.sleep(remaining)
                    slow_mount['reads'].append(round(time.monotonic()-started,3))
    class ControlledStageLookup:
        def __init__(self, app):
            self.downstream = app

        async def __call__(self, scope, receive, send):
            # Pass through real disconnects without requiring a response head.
            if scope["type"] == "http":
                response = await delay_requirements_mount(Request(scope))
                if response is not None:
                    return await response(scope, receive, send)
            await self.downstream(scope, receive, send)

    app.add_middleware(ControlledStageLookup)
    model_inputs = []
    request_gate = threading.Event()
    progress_updates = Queue()
    progress_values = (1,0,2)
    probe_progress = threading.Event()
    release_reply = threading.Event()
    def model(ctx):
        model_inputs.append({'node':ctx['kind'], 'session':ctx['stage_session_id'],
                             'approved':list(ctx['inputs']), 'annotation_ids':[a['id'] for a in ctx['annotations']]})
        if ctx['kind']=='init':
            fields=['background','development_constraints','build_environment','runtime_environment']
            if not all(ctx['user_input'].get(k) for k in fields):
                if not request_gate.wait(12):
                    raise RuntimeError('等待执行中的补充要求超时')
                assert not ctx['user_input'].get('stage_follow_ups')
                return {'questions':['请填写开发、编译与执行环境。']}
            assert ctx['user_input']['stage_follow_ups']==['初始化说明保持简短。']
            rows=[{'id':f'P-{i}', 'title':ctx['user_input'][key], 'topic':key, 'detail':ctx['user_input'][key]} for i,key in enumerate(fields)]
            return {'artifact':{'summary':'项目要点已整理。','items':rows}}
        if ctx['kind']=='requirements':
            request='补上审核人的完整名称。'
            ur_sources=source_urs(ctx['user_input'])
            revision_requested=request in ctx['user_input'].get('stage_follow_ups',[]) or request in ur_sources.values()
            if revision_requested:
                rows=json.loads(json.dumps(ctx['prior_artifact']['items']))
                rows[0]['description']+=' 每项记录审核人全名。'
                rows[0]['source_ur']=list(ur_sources)
            elif not ctx['annotations']:
                rows=[{'id':'OR-001','title':'导出审阅清单','category':'functional','sub_description':'按项目导出','description':'导出带审核状态的清单。',
                       'design_spec':'保存可追溯编号。','source_ur':list(source_urs(ctx['user_input'])),'constraints':'离线生成','remarks':'',
                       'priority':'P1','acceptance':['导出文件包含每项编号。'],'surfaces':['frontend']}]
            else:
                if probe_progress.is_set():
                    for step,completed in enumerate(progress_values,1):
                        assert progress_updates.get(timeout=25)==step
                        message='正在核对导出正文' if step%2 else '正在逐项核对已审核的导出正文和来源版本，窗口变窄时保持原成果的阅读位置。'
                        app.state.stage_workflows._progress(ctx['run_id'],ctx['operation_id'],ctx['node_id'],completed,3,message)
                        if step in (1,3):
                            assert progress_updates.get(timeout=25)==('history',step)
                            for number in range(1,21):
                                message=f'查看原执行期间的新记录 {step} · {number}'
                                app.state.stage_workflows._progress(ctx['run_id'],ctx['operation_id'],ctx['node_id'],completed,3,message)
                    assert progress_updates.get(timeout=25)=='finish'
                    probe_progress.clear()
                rows=json.loads(json.dumps(ctx['prior_artifact']['items']))
                rows[0]['description'] += ' '+ ' / '.join(a['comment'] for a in ctx['annotations'])
            slot=ctx['template']['edit_targets'][0]
            template_text='项目要求\n导出清单\n'+'\n'.join(
                f'{index:02d} · 复核导出字段的原需求、审核时间和记录状态。' for index in range(1,41))
            return {'artifact':{'summary':'已补充审核人字段。' if revision_requested else '已整理可审核的包需求。','items':rows,'template_output':template_text,
                                'template_edits':[{'target':slot['target'],'values':[row['id'],row['title']]} for row in rows]}}
        if ctx['kind']=='alternatives':
            rows=[]
            for depth in range(1,4):
                rows.append({'id':f'D-{depth}','title':['条目 D-1','文件处理与多批次导出重试期间的审阅清单一致性核验及重复编号检查','导出实现'][depth-1],'parent_id':f'D-{depth-1}' if depth>1 else None,
                             'level':depth,'key_points':'导出完整性','priority':'P1','design_requirement':'完整导出需求','requirement_ids':['OR-001'],
                             'dimensions':['核对成本'],'candidates':[
                                 {'id':'C1','title':'逐行输出','advantages':'编号与原清单一一对应。','disadvantages':'超长清单耗时增加。','evaluations':{'核对成本':'可逐项比对。'}},
                                 {'id':'C2','title':'分批写出','advantages':'长清单可分批处理。','disadvantages':'必须保存每个批次的末项编号，重试时按同一顺序衔接，避免遗漏或重复。','evaluations':{'核对成本':'需核对批次边界。'}},
                                 {'id':'C3','title':'快照导出','advantages':'导出期间内容稳定。','disadvantages':'需暂存一份副本。','evaluations':{'核对成本':'需比对快照版本。'}}],
                             'selected_id':'C1','rationale':'当前清单规模适中，逐行输出更容易核对编号。'})
            return {'artifact':{'summary':'已按层级归纳。','items':rows}}
        kind=ctx['kind']
        if kind=='cases':
            row={'id':'TC-1','title':'审阅清单导出','requirement_ids':['OR-001'],'category':'功能','subcategory':'导出',
                 'content':'检查导出内容','priority':'P1','preconditions':'','method':'核对实际文件',
                 'steps':['导出','检查文件'],'checks':['正确性'],'expected':'保留原始名称','coverage':['runtime','layout','motion','interaction','accessibility'],
                 'actions':[{'id':'content','method':'text_equals','path':'src/report.txt','expected':'已修正'}]}
        elif kind=='design':
            row={'id':'DS-1','title':'导出文稿','directory':'src/report.txt','requirement_ids':['OR-001'],'decision_ids':['D-3'],
                 'description':'保留原始名称。','interfaces':'文本结果。','implementation':'写入审阅清单。','self_check':'检查编码。',
                 'behavior_scenarios':[{'id':'SC-1','path_kind':'normal','condition':'用户导出已审清单',
                    'requirement_id':'OR-001','criterion':1,'case_id':'TC-1','action_id':'content'}]}
        elif kind=='development':
            if sum(item['node']=='development' for item in model_inputs)==1:
                raise RuntimeError('内核退出（1），请检查版本、连接和模型配置。')
            previous=stage_workspace.file_list(ctx)
            record=stage_workspace.file_write(ctx,'src/report.txt','已修正' if previous else '需要修改',previous[0]['sha256'] if previous else '')
            close=stage_workspace.complete_design_item(ctx,'DS-1',[record['id']])
            row={'id':'DEV-1','title':'实现导出','design_ids':['DS-1'],'status':'done','evidence_refs':[record['id'],close['id']],'self_check':'已记录文件版本。'}
        elif kind=='testing':
            if test_inputs['restore']:
                head,=stage_workspace.file_list(ctx)
                stage_workspace.file_write(ctx,head['path'],'已修正',head['sha256'])
            receipt=stage_workspace.check(ctx,'TC-1','src/report.txt','text_equals','已修正')
            if test_inputs['shadow']:
                assert receipt['status']=='passed'
                stage_workspace.file_write(ctx,receipt['path'],'测试后改写',receipt['sha256'])
            row={'id':'TEST-1','title':'核对正文','case_id':'TC-1','status':receipt['status'],'evidence_refs':[receipt['id']],'rework':[]}
        elif kind=='delivery':
            app.state.stage_workflows._progress(ctx['run_id'],ctx['operation_id'],ctx['node_id'],0,1,'旧的交付执行进度')
            file=next(r for r in ctx['approved_evidence'] if r['kind']=='file')
            if delivery_input['replace']:
                changed=stage_workspace.file_write(ctx,file['path'],'交付时改写了内容',file['sha256'])
                file=stage_workspace.package_files(ctx,'substituted.zip',[changed['path']])
            row={'id':'PKG-1','title':'导出文件','path':file['path'],'sha256':file['sha256'],'description':'供用户下载。'}
        else:raise ValueError('unknown fixture stage')
        artifact={'summary':summaries[kind],'items':[row]}
        if kind=='delivery':
            if not delivery_input['replace']:
                release=stage_workspace.file_write_bytes(ctx,'release.zip',release_bytes,'')
                artifact['items'].append({'id':'PKG-2','title':'验收明细包','path':release['path'],
                                          'sha256':release['sha256'],'description':'供接手人员核对。'})
            artifact.update(lessons=['已核对原交付文件及文稿。'],
                            plugin_suggestions=['在交付复核时再次执行已审核的测试。'])
        return {'artifact':artifact,'verified_evidence':stage_workspace.operation_receipts(ctx)}
    app.state.stage_workflows.executor=model
    dialogue_inputs = []
    def reply(context):
        if source:=context.get('follow_up'):
            assert source['source_id']=='UR-A001' and source['text']=='清单需要记录审核人的完整名称。'
            assert context['annotation'] is None
            if source['status']=='queued':
                return 'UR-A001 已登记，预计会影响导出清单字段。'
            assert source['status']=='processed'
            assert source['execution_summary']=='已整理可审核的包需求。'
            return 'UR-A001 已纳入本次生成，可以查看对应执行摘要。'
        dialogue_inputs.append(context)
        if len(dialogue_inputs)==1:
            assert context['annotation']['target']=='OR-001'
            assert context['annotation']['anchor']['points']
            return 'OR-001 的通过标准是检查每项编号。'
        assert context['history'][-1]['answer']=='OR-001 的通过标准是检查每项编号。'
        assert context['annotation']['id']==dialogue_inputs[0]['annotation']['id']
        assert release_reply.wait(20)
        return '第二轮可继续核对审核时间。'
    app.state.stage_workflows.dialogue_executor=reply
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[server_socket]},daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    assert server.started
    errors,console,actions=[],[],[]
    info={'source':'compiled application, real REST and SQLite; controlled kernel','project':pid,'conversation':conversation,
          'renderer_sha256':hashlib.sha256((root/'index.html').read_bytes()).hexdigest()}
    def record(name): actions.append(name)
    def bounds(locator):
        box=locator.bounding_box()
        assert box is not None
        return {k:round(v,1) for k,v in box.items()}

    def measure(page,panel,key):
        value=panel.locator('.wf-page').evaluate('''stage=>{
            const visible=el=>{
                if(el.closest('dialog:not([open])')||!el.checkVisibility()||getComputedStyle(el).visibility==='hidden')return false;
                const r=el.getBoundingClientRect(),clip=stage.closest('.wf-studio').getBoundingClientRect();
                return r.width>0&&r.height>0&&r.right>Math.max(0,clip.left)&&r.left<Math.min(innerWidth,clip.right)&&r.bottom>Math.max(0,clip.top)&&r.top<Math.min(innerHeight,clip.bottom);
            };
            const controls=[...stage.querySelectorAll('button,a[href],summary,input,textarea,select')].filter(visible);
            return {viewport:[innerWidth,innerHeight],stage_width:stage.clientWidth,overflow_px:Math.max(0,stage.scrollWidth-stage.clientWidth),
                    controls_in_stage_view:controls.length,buttons_in_stage_view:controls.filter(e=>e.tagName==='BUTTON').length,
                    button_styles:[...stage.closest('.wf-studio').querySelectorAll('.wf-page button,.wf-toolbar button')].filter(visible).map(el=>{
                        const v=getComputedStyle(el);return {name:el.getAttribute('aria-label')||el.textContent.trim(),disabled:el.disabled,focus_visible:el.matches(':focus-visible'),current:el.getAttribute('aria-current'),
                        background:v.backgroundColor,decoration:v.textDecorationLine,decoration_color:v.textDecorationColor,border:[v.borderTopWidth,v.borderTopStyle,v.borderTopColor],outline:[v.outlineWidth,v.outlineStyle,v.outlineColor],shadow:v.boxShadow};
                    }),
                    focus_inside_closed_dialog:!!document.activeElement.closest('dialog:not([open])')};
        }''')
        assert value['overflow_px']<=1 and not value['focus_inside_closed_dialog'],value
        info.setdefault('visible_density',{})[key]=value
    try:
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch()
            page=browser.new_page(locale='zh-CN',viewport={'width':1400,'height':910})
            page.set_default_timeout(15000)
            expect.set_options(timeout=15000)
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.on('console',lambda msg:console.append({'type':msg.type,'text':msg.text}) if msg.type in ('warning','error') else None)
            state=json.dumps({'version':1,'id':conversation})
            page.add_init_script("if(location.origin!=='null'&&!localStorage.getItem('eeas-current-thread-v1'))localStorage.setItem('eeas-current-thread-v1',"+json.dumps(state)+");")
            page.add_init_script('''window.workflowAnimations=[];document.addEventListener('animationstart',e=>{
                if(['wf-accepted-page','wf-return-page'].includes(e.animationName)){
                    const entry={effect:e.animationName,kind:e.target.closest('.wf-studio')?.dataset.arrival||null,stage:e.target.closest('.wf-page')?.dataset.phase||null};
                    window.workflowAnimations.push(entry);
                    document.documentElement.dataset.workflowObserved=entry.effect+'/'+entry.kind+'/'+entry.stage;
                }
            },true);
            window.workflowPaint=[];new MutationObserver(()=>{
                const studio=document.querySelector('.wf-studio'),stage=studio?.querySelector('.wf-page');
                const view=JSON.stringify([studio?.dataset.arrival||null,stage?.dataset.phase||null,stage?getComputedStyle(stage).animationName:null]);
                if(window.workflowPaint.at(-1)?.view!==view)window.workflowPaint.push({ms:Math.round(performance.now()),view});
            }).observe(document,{subtree:true,childList:true,attributes:true,attributeFilter:['data-arrival','data-phase']});''')
            page.goto(f'http://127.0.0.1:{port}/agent-shell/',wait_until='domcontentloaded')
            panel=page.locator('.wf-studio')
            button=lambda name:panel.get_by_role('button',name=name,exact=True)
            label=lambda name:panel.get_by_label(name,exact=True)
            def stable_stage_overlay():
                kind=panel.locator('.wf-page').get_attribute('data-phase')
                checked=info.setdefault('stable_shared_stage_overlay',{})
                if kind in checked:
                    return
                viewport=page.viewport_size
                scroll=panel.evaluate('el => el.scrollTop')
                controls=('笔记与执行记录','本步配置')
                checked[kind]=[]
                try:
                    for width,height in ((1400,910),(390,745)):
                        page.set_viewport_size({'width':width,'height':height})
                        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                        for name in controls:
                            tools=button('本步工具')
                            compact=tools.is_visible()
                            origin=tools if compact else button(name)
                            origin.scroll_into_view_if_needed()
                            start_layout_trace(('.wf-heading','.wf-footer'))
                            origin.click()
                            if compact:
                                menu=panel.get_by_role('dialog',name='本步工具')
                                menu.evaluate('el=>Promise.all(el.getAnimations().map(a=>a.finished)).then(()=>true)')
                                menu.get_by_role('button',name=name).focus()
                                menu.evaluate("""el=>{
                                    const read=()=>{const {x,y,width,height}=el.getBoundingClientRect();return [x,y,width,height]};
                                    const baseline=read(),failures=[];
                                    let handle,frames=0;
                                    const sample=()=>{
                                        frames++;
                                        const box=read(),style=getComputedStyle(el);
                                        if(!el.open||style.opacity!=='1'||box.some((value,i)=>value!==baseline[i]))failures.push(box);
                                        handle=requestAnimationFrame(sample);
                                    };
                                    handle=requestAnimationFrame(sample);
                                    window.finishToolTrace=()=>{cancelAnimationFrame(handle);return {baseline,frames,failures}};
                                }""")
                                menu.get_by_role('button',name=name).press('Enter')
                            dialog=panel.get_by_role('dialog',name=name)
                            expect(dialog).to_be_visible()
                            if compact:
                                dialog.get_by_role('button',name='返回本步工具').press('Enter')
                                expect(menu.get_by_role('button',name=name)).to_be_focused()
                                result=page.evaluate('window.finishToolTrace()')
                                assert result['frames']>=2 and not result['failures'],result
                                info.setdefault('workflow_tool_surface_frames',{})[f'{kind}-{width}-{name}']=result
                            page.keyboard.press('Escape')
                            expect(dialog).not_to_be_visible()
                            expect(origin).to_be_focused()
                            finish_layout_trace(f'shared-{kind}-{width}-{name}')
                        conversation_button=button('打开阶段对话')
                        conversation_button.scroll_into_view_if_needed()
                        start_layout_trace(('.wf-heading','.wf-footer'))
                        close_stage_conversation(open_followup())
                        finish_layout_trace(f'shared-{kind}-{width}-conversation')
                        checked[kind].append(width)
                finally:
                    page.set_viewport_size(viewport)
                    panel.evaluate('(el,top) => { el.scrollTop=top }',scroll)
            def start_layout_trace(selectors=(), root_selector='.wf-page'):
                return panel.evaluate("""(el,[rootSelector,...selectors])=>{
                    const read=()=>{const boxes=[rootSelector,...selectors].map(selector=>el.querySelector(selector)?.getBoundingClientRect());
                      return boxes.every(Boolean)?[...boxes.flatMap(b=>[b.x,b.y,b.width,b.height]),el.scrollTop,el.scrollHeight].map(x=>Math.round(x*100)/100):null};
                    const baseline=read(),stage=el.querySelector(rootSelector),failures=[];
                    let frames=0,handle;
                    const measure=()=>{const current=read(),style=getComputedStyle(stage);frames++;
                      if(!current||el.querySelector(rootSelector)!==stage||baseline.some((v,i)=>v!==current[i])
                       ||style.visibility!=='visible'||style.opacity!=='1')failures.push({current,visibility:style.visibility,opacity:style.opacity});
                      handle=requestAnimationFrame(measure)};
                    handle=requestAnimationFrame(measure);
                    window.finishWorkflowLayoutTrace=()=>{cancelAnimationFrame(handle);return {baseline,frames,failures}};
                    return baseline;
                }""",[root_selector,*selectors])
            def finish_layout_trace(key):
                result=page.evaluate('window.finishWorkflowLayoutTrace()')
                assert result['frames']>=2 and not result['failures'],result
                info.setdefault('workflow_layout_frames',{})[key]=result
            def open_followup():
                region=panel.get_by_role('region',name='阶段对话')
                region.get_by_role('button',name='打开阶段对话',exact=True).click()
                controls=region.get_by_role('group',name='对话用途')
                if controls.is_visible():
                    followup=controls.get_by_role('button',name='补充或修订',exact=True)
                    if followup.get_attribute('aria-pressed')!='true':
                        followup.click()
                return region.locator('.wf-conversation-float')
            def close_stage_conversation(conversation):
                conversation.get_by_role('button',name='收起阶段对话',exact=True).click()
                expect(conversation).to_have_count(0)
                expect(button('打开阶段对话')).to_be_focused()
            def choose_artifact(ident):
                toggle=panel.locator('.wf-index-toggle')
                if toggle.is_visible() and toggle.get_attribute('aria-expanded')!='true':
                    toggle.click()
                panel.locator('.wf-artifact-index [data-workflow-target="'+ident+'"]').click()
                expect(panel.locator('[data-workflow-row="'+ident+'"] [data-review-title]')).to_be_focused()
            def check_tree(width_key):
                tree=panel.locator('.wf-outline-index')
                snapshot=tree.evaluate("""root=>[...root.querySelectorAll('button[data-workflow-target]')].map(el=>{
                    const c=getComputedStyle(el),b=el.getBoundingClientRect(),id=el.querySelector('.wf-index-id').getBoundingClientRect(),name=el.querySelector('.wf-index-name');
                    return {id:el.dataset.workflowTarget,label:el.getAttribute('aria-label'),visible_text:el.innerText,active:el.getAttribute('aria-current')==='true',
                        border:[c.borderTopWidth,c.borderRightWidth,c.borderBottomWidth,c.borderLeftWidth],shadow:c.boxShadow,outline:c.outlineWidth,
                        background:c.backgroundColor,branch_line:getComputedStyle(el.closest('ul')).borderInlineStartWidth,
                        x:b.x,row_height:b.height,id_top:id.top,name_top:name?.getClientRects()[0]?.top??null,name_lines:name?.getClientRects().length??0}
                })""")
                assert sum(row['active'] for row in snapshot)==1
                for row in snapshot:
                    assert row['border']==['0px']*4 and row['shadow']=='none' and row['outline']=='0px',row
                    neutral=row['background'] in ['rgba(0, 0, 0, 0)','transparent']
                    assert neutral!=row['active'],row
                root,branch,leaf=snapshot
                assert root['visible_text']=='D-1' and root['label']=='条目 D-1'
                assert root['x']<branch['x']<leaf['x'] and branch['branch_line']==leaf['branch_line']=='1px'
                assert branch['name_lines']>1 and abs(leaf['name_top']-leaf['id_top'])<5
                info.setdefault('unboxed_tree',{})[width_key]=snapshot
            try:
                mode_group=page.get_by_role('group',name='工作模式')
                expect(mode_group).to_be_visible(timeout=35000)
                expect(page.locator('.as-agent-header >strong')).to_contain_text('产品设计验证',timeout=25000)
                workflow_mode=mode_group.get_by_role('radio',name='工作流模式',exact=True)
                daily_mode=mode_group.get_by_role('radio',name='日常模式',exact=True)
                composer=page.locator('.as-agent-content .as-composer textarea')
                mode_key=page.evaluate("ids=>'cirven-stage-view-v1:'+JSON.stringify(ids)",[pid,conversation])
                info['stable_workflow_entry']={}
                for width,height,outcome in ((768,565,'cancel'),(390,745,'failed'),(1400,910,'open')):
                    page.set_viewport_size({'width':width,'height':height})
                    page.emulate_media(reduced_motion='reduce' if outcome=='failed' else 'no-preference')
                    composer.fill('保留切入前的当前草稿。')
                    page.evaluate('''() => {
                      if(window.entryRaf)cancelAnimationFrame(window.entryRaf);
                      window.entryFrames=[];
                      const before=document.querySelector('.as-agent-content .as-composer textarea');
                      const rect=el=>[...['x','y','width','height'].map(k=>Math.round(el?.getBoundingClientRect()[k]??0))];
                      const frame=()=>{
                        const studio=document.querySelector('.wf-studio');
                        const current=document.querySelector('.as-agent-content .as-composer textarea');
                        window.entryFrames.push({
                          source:!studio&&before===current&&current.checkVisibility(), mode:document.querySelector('.wf-mode-switch').dataset.workflow,
                          fixed:[rect(document.querySelector('.as-agent-header')),rect(document.querySelector('.wf-mode-switch')),rect(current)],
                          scroll:document.querySelector('.as-agent-content .as-conversation')?.scrollTop??0,
                          loading:[...studio?.querySelectorAll('p[role=status]')??[]].some(el=>el.textContent.includes('正在读取工作流')),
                          ready:!!studio?.querySelector('.wf-library-head')
                        });
                        window.entryRaf=requestAnimationFrame(frame);
                      };
                      frame();
                    }''')
                    entry={'seen':threading.Event(),'release':threading.Event(),'outcome':outcome}
                    entry_reads.put(entry)
                    requests_before=len(entry_requests)
                    workflow_mode.focus()
                    workflow_mode.press('Space')
                    assert entry['seen'].wait(4),entry_requests
                    if panel.count():
                        page.screenshot(path=str(output/'workflow-entry-replaced-source.png'))
                    expect(panel).to_have_count(0,timeout=600)
                    expect(mode_group.get_by_role('status')).to_have_text('正在打开工作流…')
                    expect(daily_mode).to_be_checked()
                    expect(workflow_mode).to_be_focused()
                    expect(composer).to_have_value('保留切入前的当前草稿。')
                    assert page.evaluate('''() => {
                      const status=document.querySelector('.wf-mode-feedback[role=status]');
                      const r=status.getBoundingClientRect();
                      status.style.pointerEvents='auto';
                      const visible=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)===status;
                      status.style.removeProperty('pointer-events');
                      return {visible,inside:r.width>0&&r.left>=0&&r.right<=innerWidth&&r.top>=0&&r.bottom<=innerHeight};
                    }''')=={'visible':True,'inside':True}
                    page.screenshot(path=str(output/f'workflow-entry-pending-{width}.png'))
                    if outcome=='cancel':
                        workflow_mode.press('Escape')
                        expect(mode_group.get_by_role('status')).to_be_empty()
                    entry['release'].set()
                    if outcome=='failed':
                        expect(mode_group.get_by_role('alert')).to_have_text('暂未打开，点击工作流模式重试。')
                        expect(mode_group.get_by_role('alert')).to_have_css('background-color','rgb(255, 245, 237)')
                        page.screenshot(path=str(output/'workflow-entry-error-390.png'))
                    if outcome=='open':
                        expect(workflow_mode).to_be_checked()
                        expect(button('开始此工作流 →')).to_be_visible()
                        assert page.evaluate('key=>localStorage.getItem(key)',mode_key)=='workflow'
                    else:
                        expect(daily_mode).to_be_checked()
                        expect(composer).to_have_value('保留切入前的当前草稿。')
                        assert page.evaluate('key=>localStorage.getItem(key)',mode_key) is None
                    page.wait_for_function('() => window.entryFrames.length >= 15')
                    frames=page.evaluate('cancelAnimationFrame(window.entryRaf); window.entryFrames')
                    first=frames[0]
                    assert first['source'] and len(entry_requests)==requests_before+1,(first,requests_before,entry_requests)
                    assert all(f['source'] and f['mode']=='false' and f['fixed']==first['fixed'] and f['scroll']==first['scroll']
                               for f in frames if not f['ready']),frames
                    assert bool([f for f in frames if f['ready']])==(outcome=='open')
                    assert not any(f['loading'] for f in frames)
                    info['stable_workflow_entry'][outcome]={'width':width,'height':height,'directory_reads':1,'frames':frames}
                page.emulate_media(reduced_motion='no-preference')
                record('workflow entry keeps the previous page visible until the directory is ready')
                expect(button('开始此工作流 →')).to_be_visible()
                expect(page.locator('.as-sidebar')).to_be_visible()
                button('新建').click()
                label('工作流名称').fill('校核流程')
                label('节点名称').fill('首次校核')
                button('保存工作流').click()
                expect(panel.get_by_role('combobox',name='工作流定义',exact=True)).to_have_value(__import__('re').compile(r'wf-'))
                record('create custom workflow and reload persisted definition')
                panel.get_by_role('combobox',name='工作流定义',exact=True).select_option('default')
                reopen=panel.locator('.wf-stage-reopen')
                recover_stage=reopen.get_by_role('button',name='打开已保存阶段')
                transition_pending=panel.locator('.wf-transition-recovery')
                check_transition=transition_pending.get_by_role('button',name='核查原操作')
                pending_start=[]
                def lose_start_before_write(route):
                    pending_start.append(route.request.post_data_json)
                    route.fulfill(status=503,json={'detail':'验收注入：发送前断开'})
                page.route('**/api/stage-workflows/runs',lose_start_before_write,times=1)
                button('开始此工作流 →').click()
                expect(transition_pending.get_by_role('status')).to_have_text('尚未确认原操作是否保存。')
                assert not app.state.stage_workflows.list(pid,conversation)['runs']
                page.reload(wait_until='domcontentloaded')
                expect(check_transition).to_be_visible()
                check_transition.press('Enter')
                expect(transition_pending.get_by_role('status')).to_have_text('暂未查到保存回执。')
                assert not app.state.stage_workflows.list(pid,conversation)['runs']
                slow_mount['fail_node']='init'
                with page.expect_response(lambda res:res.request.method=='POST' and res.url.endswith('/api/stage-workflows/runs')) as start:
                    transition_pending.get_by_role('button',name='按原请求重试').click()
                assert start.value.request.post_data_json==pending_start[0]
                expect(reopen.get_by_role('status')).to_have_text('工作流已开始，「工程初始化」暂未打开。')
                expect(recover_stage).to_be_focused()
                expect(panel.get_by_role('heading',name='选择工作流')).to_be_visible()
                open_start=app.state.stage_workflows.list(pid,conversation)['runs']
                recover_stage.click()
                expect(recover_stage).not_to_have_attribute('aria-disabled','true')
                expect(reopen.get_by_role('status')).to_have_text('工作流已开始，「工程初始化」暂未打开。')
                recover_stage.click()
                expect(panel.get_by_role('heading',name='工程初始化')).to_be_visible()
                expected_init=start.value.json()['stages'][0]['stage_session_id']
                assert page.evaluate("JSON.parse(localStorage.getItem('eeas-current-thread-v1')).id")==expected_init
                assert app.state.stage_workflows.list(pid,conversation)['runs']==open_start
                info['saved_start_navigation']={'exact_target':expected_init,'run_count':len(open_start),'unchanged_run':True,
                    'first_request_never_reached_server':True,'retry_same_body':True}
                record('missing start receipt is checked and the identical original request can be retried after reload')
                # Wait for the activated child to take ownership before writing.
                expect(page.locator('.as-agent-header >strong')).not_to_have_text('产品设计验证')
                for width,height in ((1400,910),(390,745)):
                    page.set_viewport_size({'width':width,'height':height})
                    page.emulate_media(reduced_motion='reduce' if width==390 else 'no-preference')
                    optional=panel.locator('.wf-optional-topics')
                    optional.locator('summary').click()
                    code_map=label('代码图谱（如有）')
                    code_map.fill('临时图谱')
                    code_map.focus()
                    start_layout_trace(('.wf-heading','.wf-input-panel','.wf-optional-topics','.wf-footer'))
                    code_map.press('Control+a')
                    code_map.press('Backspace')
                    expect(optional).to_have_attribute('open','')
                    expect(code_map).to_be_focused()
                    expect(code_map).to_have_value('')
                    page.evaluate('new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)))')
                    finish_layout_trace(f'clear-optional-topics-{width}')
                    optional.locator('summary').click()
                    expect(optional).not_to_have_attribute('open','')
                page.set_viewport_size({'width':1400,'height':910})
                page.emulate_media(reduced_motion='no-preference')
                record('clearing the final optional field preserves the expanded section, keyboard focus and layout')
                background=label('项目背景')
                background.scroll_into_view_if_needed()
                background.focus()
                start_layout_trace(('.wf-heading','.wf-input-panel','.wf-stage-composer','.wf-footer'))
                page.evaluate("""() => {
                    const original=Storage.prototype.setItem;
                    Storage.prototype.setItem=function(key,value){
                        if(key.startsWith('cirven-stage-input-v1:'))throw new DOMException('Test quota','QuotaExceededError');
                        return original.call(this,key,value);
                    };
                    window.restoreWorkflowDraftStorage=()=>{Storage.prototype.setItem=original;};
                }""")
                try:
                    background.fill('仍在编辑的项目背景')
                    warning=panel.locator('.wf-heading').get_by_role('alert')
                    expect(warning).to_have_text('本机未能保存草稿，请保留此页。')
                    expect(background).to_be_focused()
                    expect(background).to_have_value('仍在编辑的项目背景')
                finally:
                    page.evaluate('window.restoreWorkflowDraftStorage()')
                background.fill('审阅工具')
                expect(warning).to_have_count(0)
                finish_layout_trace('workflow-primary-input-local-draft-recovery')
                page.reload(wait_until='domcontentloaded')
                expect(panel).to_be_visible()
                expect(label('项目背景')).to_have_value('审阅工具')
                original_background=label('项目背景').element_handle()
                button('执行此步').click()
                expect(panel.locator('.wf-status')).to_have_text('执行中')
                expect(label('项目背景')).to_have_value('审阅工具')
                expect(label('项目背景')).to_be_disabled()
                assert label('项目背景').evaluate('(current, original)=>current===original',original_background)
                active_request=open_followup()
                active_request.get_by_role('textbox').fill('初始化说明保持简短。')
                send_request=active_request.get_by_role('button',name='发送要求',exact=True)
                send_request.scroll_into_view_if_needed()
                start_layout_trace(('.wf-heading','.wf-input-panel','.wf-stage-composer','.wf-footer'))
                send_request.click()
                expect(active_request.get_by_text('待随下次执行处理',exact=True)).to_be_visible()
                assert panel.locator('.wf-conversation-inline').locator('textarea, [data-follow-up]').count()==0
                page.screenshot(path=str(output/'queued-while-running.png'),animations='disabled')
                close_stage_conversation(active_request)
                finish_layout_trace('supplement-during-execution-1400')
                request_gate.set()
                expect(panel.get_by_text('请填写开发、编译与执行环境。')).to_be_visible()
                for key,val in [('开发约束','小团队'),('编译环境','Python 3.10'),('执行环境','Windows x64')]:
                    label(key).fill(val)
                button('按补充要求执行').click()
                expect(panel.locator('.wf-summary')).to_have_text('项目要点已整理。')
                stable_stage_overlay()
                assert page.evaluate('window.workflowAnimations')==[]
                transitions=[]
                def observe_transition(req):
                    if req.method=='POST' and '/api/stage-workflows/runs/' in req.url and req.url.endswith('/actions'):
                        command=req.post_data_json
                        if command['action'] in ('approve','revise','rework'):transitions.append(command)
                page.on('request',observe_transition)
                slow_mount['fail_node']='requirements'
                with page.expect_response(lambda res:res.request.method=='POST' and res.url.endswith('/actions')) as approved:
                    button('审核通过，进入下一步 →').click()
                approval=approved.value.json()
                target=next(item['stage_session_id'] for item in approval['stages'] if item['node_id']=='requirements')
                expect(reopen.get_by_role('status')).to_have_text('审核已保存，「需求梳理」暂未打开。')
                expect(panel.get_by_role('heading',name='工程初始化')).to_be_visible()
                for width,height in ((1400,910),(390,745)):
                    page.set_viewport_size({'width':width,'height':height})
                    expect(recover_stage).to_be_in_viewport(ratio=1)
                    page.screenshot(path=str(output/f'saved-stage-reopen-{width}.png'),animations='disabled')
                page.set_viewport_size({'width':1400,'height':910})
                page.reload(wait_until='domcontentloaded')
                expect(reopen.get_by_role('status')).to_have_text('审核已保存，「需求梳理」暂未打开。')
                expect(panel.get_by_role('heading',name='工程初始化')).to_be_visible()
                assert len(transitions)==1
                paint_index=page.evaluate('window.workflowPaint.length')
                slow_mount['armed']=True
                recover_stage.press('Enter')
                expect(recover_stage).to_have_attribute('aria-disabled','true')
                page.keyboard.press('Enter')
                expect(panel.get_by_role('heading',name='工程初始化')).to_be_visible(timeout=2000)
                expect(panel.get_by_role('heading',name='需求梳理')).to_be_visible()
                expect(panel.get_by_role('heading',name='需求梳理')).to_be_focused()
                expect(reopen).to_have_count(0)
                assert len(transitions)==1 and transitions[0]['operation_id']==approved.value.request.post_data_json['operation_id']
                assert page.evaluate("JSON.parse(localStorage.getItem('eeas-current-thread-v1')).id")==target
                expect(page.locator('.as-agent-header >strong')).to_contain_text('需求梳理')
                expect(page.locator('html')).to_have_attribute('data-workflow-observed','wf-accepted-page/approve/requirements')
                slow_mount['armed']=False
                assert max(slow_mount['reads'],default=0)>=4,slow_mount
                assert len(slow_mount['reads'])==1,slow_mount
                info['slow_requirement_mount']={'get_delays_s':slow_mount['reads'],'arrival_events':page.evaluate('window.workflowAnimations'),'paint':page.evaluate('window.workflowPaint')}
                painted=[json.loads(entry['view'])[1] for entry in info['slow_requirement_mount']['paint'][paint_index:]]
                assert painted and all(stage in ('init','requirements') for stage in painted),painted
                assert painted.count('requirements')==1,painted
                info['saved_approval_navigation']={'exact_target':target,'approval_posts':len(transitions),'reload_before_retry':True}
                stable_stage_overlay()
                nav=panel.get_by_role('navigation',name='流程节点')
                if nav.get_by_role('combobox').is_visible():
                    expect(nav.get_by_role('combobox')).to_have_value('requirements')
                else:
                    expect(nav.locator('button[aria-current="step"]')).to_have_accessible_name(__import__('re').compile('需求梳理'))
                record('clarification, stage-bound draft restore, approval and child navigation')
                label('用户需求 UR').fill('我需要导出当前的审阅清单。')
                request_box=panel.get_by_role('region',name='阶段对话')
                page.set_viewport_size({'width':520,'height':745})
                button('打开阶段对话').scroll_into_view_if_needed()
                start_layout_trace(('.wf-heading','.wf-input-panel','.wf-stage-composer','.wf-footer'))
                editing_request=open_followup()
                editing_request.get_by_role('textbox').fill('清单需要记录审核人的完整名称。')
                close_stage_conversation(editing_request)
                finish_layout_trace('supplement-draft-520')
                page.set_viewport_size({'width':390,'height':745})
                page.emulate_media(reduced_motion='reduce')
                editing_request=open_followup()
                expect(editing_request).to_have_css('animation-name','none')
                expect(editing_request.get_by_role('textbox')).to_have_value('清单需要记录审核人的完整名称。')
                editing_request.get_by_role('radio',name='新增需求',exact=True).check()
                send_requirement=editing_request.get_by_role('button',name='登记需求',exact=True)
                send_requirement.scroll_into_view_if_needed()
                page.screenshot(path=str(output/'register-requirement.png'),animations='disabled')
                start_layout_trace(('.wf-heading','.wf-input-panel','.wf-stage-composer','.wf-footer'))
                start_layout = editing_request.evaluate('''float=>{
                    const form=float.querySelector('form'), input=form.querySelector('textarea'), action=form.querySelector('[type=submit]');
                    const caption=form.querySelector('[role=status]');
                    const box=el=>{const r=el.getBoundingClientRect();return [r.x,r.y,r.width,r.height]};
                    const read=()=>[float,input,action].flatMap(box);
                    const baseline=read(), pending=[], frames=[];
                    let handle;
                    const sample=()=>{const values=read();
                        const isPending=(caption.textContent+action.textContent).includes('正在发送…');
                        const delta=Math.max(...values.map((value,i)=>Math.abs(value-baseline[i])));
                        frames.push(delta);if(isPending)pending.push(delta);
                        handle=requestAnimationFrame(sample);
                    };
                    handle=requestAnimationFrame(sample);
                    window.finishFollowUpGeometry=()=>{cancelAnimationFrame(handle);return {baseline,frames,pending}};
                    return baseline;
                }''')
                def reject_registration(route):
                    time.sleep(.45)
                    route.fulfill(status=503,json={'detail':'测试用需求登记连接中断'})
                send_requirement.focus()
                page.route('**/api/stage-workflows/runs/*/actions',reject_registration,times=1)
                send_requirement.press('Enter')
                expect(panel.locator('.wf-studio-feedback').get_by_role('alert')).to_have_text('测试用需求登记连接中断')
                expect(editing_request.get_by_role('textbox')).to_have_value('清单需要记录审核人的完整名称。')
                followup_geometry=page.evaluate('window.finishFollowUpGeometry()')
                assert followup_geometry['baseline']==start_layout
                info['workflow_followup_geometry']=followup_geometry
                assert len(followup_geometry['pending'])>=4 and max(followup_geometry['frames'])<=1,followup_geometry
                expect(send_requirement).to_be_focused()
                send_requirement.click()
                expect(editing_request.get_by_text('UR-A001',exact=True)).to_be_visible()
                expect(editing_request.locator('[data-follow-up]')).to_have_count(1)
                expect(panel.locator('.wf-studio-feedback .wf-notice')).to_have_count(0)
                expect(panel.locator('.wf-conversation-inline').locator('textarea, [data-follow-up]')).to_have_count(0)
                finish_layout_trace('register-requirement-390-reduced-motion')
                page.emulate_media(reduced_motion='no-preference')
                request_box.get_by_role('button',name='追问这条',exact=True).click()
                expect(request_box.get_by_text('引用补充要求 · UR-A001',exact=True)).to_be_visible()
                followup_input=request_box.get_by_label('对当前阶段提问')
                expect(followup_input).to_be_focused()
                followup_input.fill('这条会影响什么？')
                request_box.get_by_role('button',name='发送问题',exact=True).click()
                expect(request_box.get_by_text('UR-A001 已登记，预计会影响导出清单字段。',exact=True)).to_be_visible()
                close_stage_conversation(editing_request)
                page.set_viewport_size({'width':1400,'height':910})
                template=Document();template.add_heading('项目要求',1);table=template.add_table(rows=2,cols=2);table.style='Table Grid'
                table.rows[0].cells[0].text='条款编号';table.rows[0].cells[1].text='名称'
                table.rows[1].cells[0].text='{{编号}}';table.rows[1].cells[1].text='{{名称}}'
                template.sections[0].header.paragraphs[0].text='固定页眉';stream=BytesIO();template.save(stream)
                label('输出模板（可选）').set_input_files({'name':'template.docx','mimeType':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','buffer':stream.getvalue()})
                expect(panel.get_by_text('template.docx',exact=True)).to_be_visible()
                button('按补充要求执行').click()
                expect(panel.locator('.wf-summary')).to_have_text('已整理可审核的包需求。')
                request_box.get_by_role('button',name='打开阶段对话',exact=True).click()
                expect(request_box.get_by_text('引用补充要求 · UR-A001',exact=True)).to_be_visible()
                followup_input.fill('这次实际怎样处理了？')
                request_box.get_by_role('button',name='发送问题',exact=True).click()
                expect(request_box.get_by_text('UR-A001 已纳入本次生成，可以查看对应执行摘要。',exact=True)).to_be_visible()
                page.set_viewport_size({'width':390,'height':745})
                expect(request_box.get_by_text('UR-A001 已纳入本次生成，可以查看对应执行摘要。',exact=True)).to_be_in_viewport(ratio=1)
                page.screenshot(path=str(output/'followup-quoted-390.png'),animations='disabled')
                request_box.get_by_role('button',name='查看引用的补充要求 · UR-A001',exact=True).last.click()
                followup_row=request_box.locator('.wf-conversation-float li[data-follow-up]').filter(has_text='UR-A001')
                expect(followup_row).to_be_focused()
                expect(followup_row.get_by_text('已纳入执行',exact=True)).to_be_visible()
                followup_row.get_by_text('对应执行摘要',exact=True).click()
                expect(followup_row.get_by_text('已整理可审核的包需求。',exact=True)).to_be_in_viewport(ratio=1)
                page.screenshot(path=str(output/'followup-receipt-390.png'),animations='disabled')
                source_coverage=followup_row.get_by_role('button',name='查看覆盖',exact=True)
                source_coverage.scroll_into_view_if_needed()
                held_coverage=[]
                coverage_route='**/api/stage-workflows/runs/*/stages/*/coverage?*'
                page.route(coverage_route,lambda route:held_coverage.append(route),times=1)
                start_layout_trace()
                source_coverage.click()
                coverage=page.get_by_role('dialog',name='需求覆盖')
                expect(coverage.get_by_text('正在读取需求覆盖…',exact=True)).to_be_visible()
                coverage.evaluate('el => Promise.all(el.getAnimations().map(a => a.finished))')
                pending_dialog=bounds(coverage)
                assert len(held_coverage)==1
                held_coverage.pop().continue_()
                expect(coverage.get_by_text('清单需要记录审核人的完整名称。',exact=True)).to_be_visible()
                expect(coverage.get_by_text('当前没有可定位的验收用例。',exact=True)).to_be_visible()
                assert bounds(coverage)==pending_dialog
                finish_layout_trace('coverage-loading-to-report')
                page.keyboard.press('Escape')
                expect(coverage).not_to_be_visible()
                expect(source_coverage).to_be_focused()
                page.set_viewport_size({'width':1400,'height':910})
                request_box.get_by_role('button',name='收起阶段对话',exact=True).click()
                record('follow-up source in dialogue before and after execution, return to original receipt and focus at 390 px')
                row=panel.locator('[data-workflow-row="OR-001"]')
                expect(row.get_by_role('heading',name='导出审阅清单')).to_be_visible()
                for width in (390,1400):
                    page.set_viewport_size({'width':width,'height':745 if width==390 else 910})
                    source_button=row.get_by_role('button',name='查看需求原文',exact=True)
                    empty_button=row.get_by_role('button',name=__import__('re').compile(r'^查看未填写字段'))
                    document_button=button('查看模板文稿')
                    fields_button=panel.get_by_role('button',name=__import__('re').compile(r'^查看模板填写内容'))
                    for key, opener, title in (
                        ('source',source_button,'需求原文'),('empty',empty_button,'未填写字段'),
                        ('template',document_button,'按模板生成的文稿'),('fields',fields_button,'模板填写内容'),
                    ):
                        opener.scroll_into_view_if_needed()
                        start_layout_trace(('.wf-heading','.wf-stage-main','[data-workflow-row="OR-001"]','.wf-footer'))
                        opener.press('Enter')
                        reading=panel.get_by_role('dialog',name=title,exact=True)
                        expect(reading).to_be_visible()
                        if key=='source':
                            assert reading.bounding_box()['height']<400
                            submitted_source=reading.get_by_text('清单需要记录审核人的完整名称。',exact=True)
                            expect(submitted_source).to_be_visible()
                            submitted_source.scroll_into_view_if_needed()
                            page.screenshot(path=str(output/f'requirement-source-{width}.png'),animations='disabled')
                        if key=='template':
                            expect(reading.locator('.wf-reading-text')).to_contain_text('导出清单')
                            assert reading.locator('.wf-dialog-body').evaluate('el=>el.scrollHeight>el.clientHeight')
                            reading.get_by_text(__import__('re').compile('40 · 复核')).scroll_into_view_if_needed()
                            reading.locator('.wf-dialog-body').evaluate('el=>el.scrollTop=el.scrollHeight')
                            assert reading.locator('.wf-dialog-body').evaluate('el=>el.scrollTop>0')
                        if key=='fields':
                            expect(reading.get_by_text('OR-001',exact=True).first).to_be_visible()
                        page.keyboard.press('Escape')
                        expect(reading).not_to_be_visible()
                        expect(opener).to_be_focused()
                        finish_layout_trace(f'artifact-{key}-{width}')
                record('new UR and editorial changes are independent, with verbatim source under the approved OR')
                session=panel.get_by_role('region',name='阶段对话')
                floating=session.locator('.wf-conversation-float')
                comment=floating.locator('[data-annotation-editor] textarea')
                orb=row.get_by_role('button',name='批注此项')
                def reading_geometry():
                    return panel.evaluate('''el=>{
                        const rect=s=>{const b=el.querySelector(s).getBoundingClientRect();
                            return [b.x,b.y,b.width,b.height].map(n=>Number(n.toFixed(2)))};
                        return {page:rect('.wf-page'),record:rect('[data-workflow-row="OR-001"]'),
                            footer:rect('.wf-footer'),scrollTop:Number(el.scrollTop.toFixed(2)),scrollHeight:el.scrollHeight};
                    }''')
                for width in (1400,390):
                    page.set_viewport_size({'width':width,'height':910 if width==1400 else 745})
                    orb.scroll_into_view_if_needed()
                    orb_style=orb.evaluate("el=>{const s=getComputedStyle(el);return [s.borderRadius,el.offsetWidth,el.offsetHeight]}")
                    assert orb_style==['50%',40,40],orb_style
                    before=reading_geometry()
                    orb.press('Enter')
                    expect(comment).to_be_focused()
                    comment.fill('尚未提交的批注')
                    assert reading_geometry()==before
                    page.screenshot(path=str(output/f'annotation-float-{width}.png'),animations='disabled')
                    comment.press('Escape')
                    expect(orb).to_be_focused()
                    assert reading_geometry()==before
                    orb.press('Enter')
                    expect(comment).to_have_value('尚未提交的批注')
                    floating.get_by_role('button',name='取消',exact=True).click()
                    expect(orb).to_be_focused()
                    assert reading_geometry()==before
                    info.setdefault('annotation_float_geometry',{})[str(width)]=before
                page.set_viewport_size({'width':1400,'height':910})
                record('round annotation trigger, stable page and scroll geometry at 1400 and 390, keyboard focus and draft')
                draw=button('圈画')
                draw.scroll_into_view_if_needed()
                start_layout_trace(('.wf-heading','.wf-output-heading','.wf-summary','.wf-footer'))
                draw.press('Enter')
                expect(draw).to_have_attribute('aria-pressed','true')
                expect(panel.locator('.wf-summary').locator('..')).to_have_attribute('data-drawing','true')
                finish_layout_trace('enable-drawing-keeps-body-stable')
                field=row.locator('[data-field="description"]')
                field.scroll_into_view_if_needed()
                field_box=field.bounding_box()
                x,y=field_box['x']+4,field_box['y']+4
                page.mouse.move(x,y);page.mouse.down()
                page.mouse.move(x+42,y+5,steps=8);page.mouse.move(x+22,y+15,steps=8);page.mouse.move(x,y,steps=8)
                page.mouse.up()
                comment.fill('保留每次审核的时间。')
                start_layout_trace()
                page.route('**/api/stage-workflows/runs/*/actions',lambda route:
                    route.fulfill(status=503,json={'detail':'测试用临时连接故障'}),times=1)
                button('保存批注').click()
                notice=panel.locator('.wf-studio-feedback .wf-notice')
                expect(notice.get_by_role('alert')).to_contain_text('测试用临时连接故障')
                expect(comment).to_have_value('保留每次审核的时间。')
                button('保存批注').click()
                expect(button('审核通过，进入下一步 →')).to_be_disabled()
                expect(row.locator('polyline')).to_have_count(1)
                expect(session.get_by_text('引用批注 · OR-001',exact=True)).to_be_visible()
                expect(session.get_by_label('对当前阶段提问')).to_be_focused()
                expect(notice).to_have_count(0)
                finish_layout_trace('comment-rejected-then-saved')
                session.get_by_label('对当前阶段提问').fill('这条怎么验收？')
                button('发送问题').click()
                expect(session.get_by_text('OR-001 的通过标准是检查每项编号。',exact=True)).to_be_visible()
                session.get_by_label('对当前阶段提问').fill('再核对时间？')
                button('发送问题').click()
                expect(session.get_by_text('正在回答…',exact=True).first).to_be_visible()
                first_reference=session.get_by_role('button',name='定位引用 · OR-001').first
                first_reference.focus()
                existing_reference=first_reference.element_handle()
                try:
                    start_layout_trace()
                    # Wait until the pending turn really reaches this view by GET.
                    expect(session.get_by_text('再核对时间？',exact=True)).to_have_count(1)
                    expect(first_reference).to_be_focused()
                finally:
                    release_reply.set()
                expect(session.get_by_text('第二轮可继续核对审核时间。',exact=True)).to_be_visible()
                expect(first_reference).to_be_focused()
                assert first_reference.evaluate('(current, original)=>current===original',existing_reference)
                finish_layout_trace('reply-arrival-preserves-reference')
                assert session.locator('[role="log"] article').all_text_contents()[-1].endswith('第二轮可继续核对审核时间。')
                expect(session.get_by_role('button',name='定位引用 · OR-001')).to_have_count(2)
                expect(panel.locator('.wf-heading h1')).to_have_text('需求梳理')
                assert floating.evaluate("(el)=>getComputedStyle(el).position")=='fixed'
                page.screenshot(path=str(output/'conversation-wide.png'),animations='disabled')
                page.set_viewport_size({'width':390,'height':745})
                pane=floating.bounding_box()
                assert pane['x']>=0 and pane['x']+pane['width']<=391
                for control in (session.get_by_label('对当前阶段提问'),session.get_by_role('button',name='发送问题')):
                    box=control.bounding_box()
                    assert pane['y']<=box['y'] and box['y']+box['height']<=pane['y']+pane['height']
                expect(session.get_by_text('第二轮可继续核对审核时间。',exact=True)).to_be_in_viewport(ratio=1)
                page.screenshot(path=str(output/'conversation-390.png'),animations='disabled')
                page.set_viewport_size({'width':1400,'height':910})
                session.get_by_role('button',name='收起阶段对话').click()
                comments_trigger=button('审核批注')
                comments_trigger.scroll_into_view_if_needed()
                start_layout_trace()
                comments_trigger.click()
                comments=panel.get_by_role('dialog',name='审核批注')
                expect(comments.get_by_text('保留每次审核的时间。',exact=True)).to_be_visible()
                comments.evaluate('el => Promise.all(el.getAnimations().map(a => a.finished))')
                page.screenshot(path=str(output/'review-comments-wide.png'),animations='disabled')
                page.keyboard.press('Escape')
                expect(comments_trigger).to_be_focused()
                finish_layout_trace('review-comments-wide')
                page.set_viewport_size({'width':390,'height':745})
                tools=button('本步工具')
                tools.scroll_into_view_if_needed()
                start_layout_trace()
                tools.click()
                panel.get_by_role('dialog',name='本步工具').get_by_role('button',name='审核批注').click()
                expect(comments.get_by_text('保留每次审核的时间。',exact=True)).to_be_visible()
                comments.evaluate('el => Promise.all(el.getAnimations().map(a => a.finished))')
                page.screenshot(path=str(output/'review-comments-390.png'),animations='disabled')
                page.keyboard.press('Escape')
                expect(tools).to_be_focused()
                finish_layout_trace('review-comments-390')
                page.set_viewport_size({'width':1400,'height':910})
                requirements_thread=page.evaluate("""project=>{
                    const current=JSON.parse(localStorage.getItem('eeas-current-thread-v1'));
                    localStorage.removeItem('cirven-stage-view-v1:'+JSON.stringify([project,current.id]));
                    return current.id;
                }""",pid)
                page.reload(wait_until='domcontentloaded')
                expect(panel.get_by_role('heading',name='需求梳理')).to_be_visible()
                sidebar=page.locator('.as-sidebar')
                same_thread=sidebar.get_by_role('button',name='需求梳理',exact=True)
                with page.expect_response(lambda res: '/api/conversations/' in res.url and '/history?' in res.url):
                    same_thread.click()
                expect(same_thread).to_have_attribute('aria-busy','false')
                expect(panel.get_by_role('heading',name='需求梳理')).to_be_visible()
                sidebar.get_by_text('工程初始化',exact=True).click()
                expect(panel.get_by_role('heading',name='工程初始化')).to_be_visible()
                button('打开阶段对话').click()
                expect(session.locator('.wf-conversation-heading')).to_contain_text('工程初始化')
                expect(session.get_by_text('第二轮可继续核对审核时间。',exact=True)).to_have_count(0)
                set_stage_preference="""({pid,id,mode})=>{
                    const key='cirven-stage-view-v1:'+JSON.stringify([pid,id]);
                    if(mode)localStorage.setItem(key,mode);else localStorage.removeItem(key);
                }"""
                preference={'pid':pid,'id':requirements_thread,'mode':'workflow'}
                page.evaluate(set_stage_preference,preference)
                start_layout_trace()
                page.route(lambda url:'/api/stage-workflows?' in url and
                    requirements_thread in unquote(url),lambda route:
                    route.fulfill(status=503,json={'detail':'测试用阶段读取故障'}),times=1)
                sidebar.get_by_text('需求梳理',exact=True).click()
                host_error=page.locator('.as-agent-content > .as-error-bar')
                expect(host_error).to_contain_text('会话暂时无法打开')
                expect(panel.get_by_role('heading',name='工程初始化')).to_be_visible()
                host_error.get_by_role('button',name='关闭错误提示').click()
                finish_layout_trace('saved-stage-navigation-failed')
                page.evaluate(set_stage_preference,{**preference,'mode':None})
                paint_index=page.evaluate('window.workflowPaint.length')
                sidebar.get_by_text('需求梳理',exact=True).click()
                expect(panel.get_by_role('heading',name='需求梳理')).to_be_visible()
                switch_paint=page.evaluate('window.workflowPaint.slice('+str(paint_index)+')')
                assert switch_paint and [json.loads(entry['view'])[1] for entry in switch_paint]==['requirements'],switch_paint
                info['undesignated_stage_switch']=switch_paint
                button('打开阶段对话').click()
                expect(session.get_by_text('第二轮可继续核对审核时间。',exact=True)).to_be_visible()
                expect(session.get_by_text('引用批注 · OR-001',exact=True)).to_be_visible()
                assert len(dialogue_inputs)==2
                for width,height in ((1400,910),(520,730),(390,620)):
                    page.set_viewport_size({'width':width,'height':height})
                    page.emulate_media(reduced_motion='reduce' if width==390 else 'no-preference')
                    if width!=1400:
                        button('本步工具').click()
                        panel.get_by_role('dialog',name='本步工具').get_by_role('button',name='审核批注').click()
                        comments.get_by_role('button',name='在对话中讨论').click()
                        expect(session.get_by_text('引用批注 · OR-001',exact=True)).to_be_visible()
                    floating.evaluate('el=>Promise.all(el.getAnimations().map(a=>a.finished)).then(()=>true)')
                    question=session.get_by_label('对当前阶段提问')
                    question.fill('还在编辑的问题')
                    remove=session.get_by_role('button',name='移除引用')
                    remove.focus()
                    start_layout_trace(('.wf-heading','.wf-artifact','.wf-footer','.wf-conversation-float',
                                        '.wf-conversation-controls','textarea[maxlength="4000"]',
                                        '.wf-conversation-controls button[type=submit]'))
                    remove.press('Enter')
                    expect(question).to_be_focused()
                    expect(question).to_have_value('还在编辑的问题')
                    expect(session.get_by_text('引用批注 · OR-001',exact=True)).not_to_be_visible()
                    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    trace=page.evaluate('window.finishWorkflowLayoutTrace()')
                    info.setdefault('workflow_reference_removal_frames',{})[str(width)]=trace
                    assert trace['frames']>=2 and not trace['failures'],trace
                    question.fill('')
                    session.get_by_role('button',name='收起阶段对话').click()
                page.set_viewport_size({'width':1400,'height':910})
                page.emulate_media(reduced_motion='no-preference')
                record('direct page comment, two exact references, isolated stages, reopen from the project directory, explicit removal')
                comments_trigger.click()
                probe_progress.set()
                try:
                    comments.get_by_role('button',name='按此条修改',exact=True).click()
                    expect(panel.locator('.wf-stage-detail')).to_have_attribute('data-stage-status','running')
                    for step,(width,completed) in enumerate(zip((1400,520,390),progress_values),1):
                        page.set_viewport_size({'width':width,'height':910 if width==1400 else 745})
                        heading=panel.locator('.wf-heading')
                        heading.scroll_into_view_if_needed()
                        page.evaluate('new Promise(done=>requestAnimationFrame(()=>requestAnimationFrame(done)))')
                        stage_nav=nav.get_by_role('button',name='工程初始化',exact=False) if width==1400 else nav.locator('select')
                        stage_nav.focus()
                        original_nav=stage_nav.element_handle()
                        original_record=row.element_handle()
                        if width in (1400,390):
                            page.emulate_media(reduced_motion='reduce' if width==390 else 'no-preference')
                            activity_button=button('笔记与执行记录' if width==1400 else '本步工具')
                            activity_button.click()
                            if width==390:
                                panel.get_by_role('dialog',name='本步工具').get_by_role('button',name='笔记与执行记录').click()
                            activity=panel.get_by_role('dialog',name='笔记与执行记录')
                            activity.evaluate('el=>Promise.all(el.getAnimations().map(a=>a.finished))')
                            existing=activity.get_by_role('button',name='查看该次记录').first
                            existing.focus()
                            previous_entry=existing.element_handle()
                            previous_geometry=activity.bounding_box()
                        start_layout_trace(('.wf-heading','.wf-artifact','[data-workflow-row="OR-001"]'))
                        progress_updates.put(step)
                        expect(panel.get_by_role('progressbar',name='执行进度')).to_have_attribute('value',str(completed))
                        if width in (1400,390):
                            assert previous_entry.evaluate('el=>el.isConnected && document.activeElement===el')
                            assert activity.bounding_box()==previous_geometry
                            viewport_position="""el=>{
                                const body=el.closest('.wf-dialog-body'), bounds=el.getBoundingClientRect();
                                const offset=bounds.top-body.getBoundingClientRect().top-body.clientTop;
                                return {offset,height:bounds.height,viewport:body.clientHeight,scroll:body.scrollTop};
                            }"""
                            original_position=previous_entry.evaluate(viewport_position)
                            previous_entry.press('Enter')
                            execution_details=panel.get_by_role('dialog',name='本次执行记录')
                            expect(execution_details).to_be_visible()
                            progress_updates.put(('history',step))
                            # This fixture commits twenty separate progress writes before renderer polling can see the last one.
                            expect(panel.locator('.wf-heading-progress')).to_have_attribute(
                                'title',f'查看原执行期间的新记录 {step} · 20',timeout=30000)
                            assert execution_details.bounding_box()==previous_geometry
                            if width==1400:
                                execution_details.get_by_role('button',name='返回执行记录列表').press('Enter')
                            else:
                                page.keyboard.press('Escape')
                            expect(existing).to_be_focused()
                            assert activity.bounding_box()==previous_geometry
                            returned=existing.evaluate(viewport_position)
                            assert abs(returned['offset']-original_position['offset'])<=1,(original_position,returned)
                            assert returned['offset']>=0 and returned['offset']+returned['height']<=returned['viewport']
                            assert returned['scroll']>original_position['scroll']
                            info.setdefault('activity_return_reading_position',{})[str(width)]={'before':original_position,'after':returned}
                            page.keyboard.press('Escape')
                            expect(activity_button).to_be_focused()
                            info.setdefault('activity_focus_on_live_refresh',[]).append(width)
                        else:
                            expect(stage_nav).to_be_focused()
                        assert stage_nav.evaluate('(current, original)=>current===original',original_nav)
                        assert row.evaluate('(current, original)=>current===original',original_record)
                        page.screenshot(path=str(output/f'state-progress-{width}.png'),animations='disabled')
                        finish_layout_trace(f'running-progress-{width}')
                finally:
                    progress_updates.put('finish')
                    page.set_viewport_size({'width':1400,'height':910})
                    page.emulate_media(reduced_motion='no-preference')
                expect(panel.locator('.wf-stage-detail')).to_have_attribute('data-stage-status','review')
                info['live_progress_values']=list(progress_values)
                record('progress can decrease without moving the original page, record, navigation focus or viewport')
                comments_trigger.click()
                expect(comments.get_by_text('已采用',exact=True)).to_be_visible()
                page.keyboard.press('Escape')
                for note in ['导出顺序稳定。','保留原始名称。']:
                    row.get_by_role('button',name='批注此项').click()
                    comment.fill(note);button('保存批注').click()
                    expect(session.locator('.wf-conversation-annotation p')).to_have_text(note)
                    session.get_by_role('button',name='收起阶段对话').click()
                    comments_trigger.click()
                    expect(comments.get_by_role('button',name='按此条修改',exact=True)).to_have_count(1 if note.startswith('导') else 2)
                    page.keyboard.press('Escape')
                comments_trigger.click()
                comments.get_by_role('button',name='按全部待处理批注修改').click()
                comments_trigger.click()
                expect(comments.get_by_text('已采用',exact=True)).to_have_count(3)
                page.keyboard.press('Escape')
                record('multipart template, normalized freehand mark, single and batch rework')
                for width,height,name in [(1400,910,'wide'),(520,745,'narrow'),(390,745,'compact390')]:
                    page.set_viewport_size({'width':width,'height':height})
                    panel.locator('.wf-heading').scroll_into_view_if_needed()
                    page.evaluate('''async()=>{await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);await Promise.allSettled(document.getAnimations().filter(a=>a.effect?.getComputedTiming().endTime<5000).map(a=>a.finished))}''')
                    dims=page.evaluate('''()=>{const b=e=>{const r=e.getBoundingClientRect();return [r.left,r.right,r.top,r.bottom]};const sidebar=document.querySelector('.as-sidebar');const title=document.querySelector('.wf-heading h1');const r=title.getBoundingClientRect();return {sidebar:b(sidebar),sidebarVisibility:getComputedStyle(sidebar).visibility,title:b(title),clear:document.elementFromPoint(r.left+2,r.top+5)?.closest('.wf-heading')!==null}}''')
                    assert dims['clear'],dims
                    info.setdefault('geometry',{})[name]=dims
                    measure(page,panel,name+'-requirements')
                    page.screenshot(path=str(output/f'{name}.png'))
                    assert panel.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1'),f'{name} stage overflow'
                    last=button('审核通过，进入下一步 →');last.scroll_into_view_if_needed()
                    assert last.is_visible()
                    rect=last.bounding_box();assert rect and 0<=rect['x'] and rect['x']+rect['width']<=width+1
                    record(f'{name} layout and reachable approval')
                page.set_viewport_size({'width':1400,'height':910})
                button('审核通过，进入下一步 →').click()
                expect(panel.get_by_role('heading',name='方案论证')).to_be_visible()
                expect(page.locator('.as-agent-header >strong')).to_contain_text('方案论证')
                stable_stage_overlay()
                button('执行此步').click()
                expect(panel.get_by_text('已按层级归纳。')).to_be_visible()
                choose_artifact('D-1')
                expect(panel.locator('[data-workflow-row="D-1"] [data-review-title]')).to_have_text('条目 D-1')
                choose_artifact('D-2')
                choose_artifact('D-3')
                selected=panel.locator('[data-workflow-row="D-3"]')
                selected.get_by_role('button',name='批注此项').click()
                pending_comment=panel.locator('[data-annotation-editor] textarea')
                pending_comment.fill('保留当前方案的批注草稿')
                button('收起目录').scroll_into_view_if_needed()
                wide_before=bounds(selected)
                start_layout_trace(('.wf-summary','[data-workflow-row="D-3"]'))
                button('收起目录').click()
                expect(button('展开目录')).to_be_focused()
                expect(selected.get_by_role('heading',name='导出实现')).to_be_visible()
                wide_after=bounds(selected)
                assert wide_after==wide_before,(wide_before,wide_after)
                button('展开目录').click()
                expect(panel.locator('[data-workflow-target="D-3"]')).to_have_attribute('aria-current','true')
                expect(pending_comment).to_have_value('保留当前方案的批注草稿')
                page.wait_for_timeout(200)
                finish_layout_trace('wide-index-stable-with-comment-draft')
                panel.locator('[data-annotation-editor]').get_by_role('button',name='取消').click()
                info['index_pane']={'wide_open':wide_before,'wide_closed':wide_after,'annotation_draft_preserved':True}
                expect(panel.locator('[data-workflow-row="D-3"] .wf-candidate')).to_have_count(3)
                expect(panel.locator('[data-workflow-row="D-3"] .wf-candidate[data-picked=true]')).to_have_count(1)
                panel.locator('[data-workflow-row="D-3"] .wf-candidate-grid').scroll_into_view_if_needed()
                candidates=panel.locator('.wf-candidate-grid .wf-candidate')
                boxes=[bounds(candidates.nth(i)) for i in range(3)]
                assert max(b['y'] for b in boxes)-min(b['y'] for b in boxes)<2 and min(b['width'] for b in boxes)>180,boxes
                tree_box=bounds(panel.locator('.wf-artifact-index .wf-object-tree').first)
                assert tree_box['y']+tree_box['height']>0 and tree_box['y']<910 and boxes[0]['y']<855,(tree_box,boxes)
                info['candidate_comparison']={'three_columns':boxes,'visible_tree':tree_box}
                page.mouse.move(8,8)
                check_tree('wide')
                measure(page,panel,'wide-alternatives')
                page.screenshot(path=str(output/'decisions.png'))
                button('收起目录').focus()
                page.keyboard.press('Tab')
                keyboard_row=panel.locator('[data-workflow-target="D-1"]')
                expect(keyboard_row).to_be_focused()
                visual_focus=keyboard_row.evaluate("el=>{const s=getComputedStyle(el);return {visible:el.matches(':focus-visible'),outline:s.outlineWidth,style:s.outlineStyle}}")
                assert visual_focus=={'visible':True,'outline':'2px','style':'solid'},visual_focus
                info['tree_keyboard_focus']=visual_focus
                page.set_viewport_size({'width':520,'height':745})
                expect(button('展开目录')).to_be_visible()
                directory_focus_after_shrink=button('展开目录').evaluate('element=>document.activeElement===element')
                button('展开目录').scroll_into_view_if_needed()
                start_layout_trace(('.wf-summary','[data-workflow-row="D-3"]'))
                button('展开目录').click()
                index_dialog=panel.locator('.wf-index-rail dialog[open]')
                expect(index_dialog).to_be_visible()
                page.wait_for_timeout(200)
                page.keyboard.press('Escape')
                expect(index_dialog).to_have_count(0)
                expect(button('展开目录')).to_be_focused()
                finish_layout_trace('compact-index-overlay')
                button('展开目录').click()
                expect(panel.locator('[data-workflow-target="D-3"]')).to_be_visible()
                compact_index=bounds(panel.locator('.wf-artifact-index'))
                compact_detail=bounds(selected)
                for width in (520,390):
                    page.set_viewport_size({'width':width,'height':745})
                    page.mouse.move(8,8)
                    index_dialog.evaluate("""async el=>{
                        await new Promise(requestAnimationFrame);
                        await Promise.allSettled(el.getAnimations({subtree:true}).map(animation=>animation.finished));
                    }""")
                    check_tree(str(width))
                    page.screenshot(path=str(output/f'decisions-tree-{width}.png'))
                page.set_viewport_size({'width':1400,'height':910})
                expect(panel.locator('.wf-artifact-browser')).to_have_attribute('data-compact','false')
                expect(button('收起目录')).to_be_focused()
                page.set_viewport_size({'width':520,'height':745})
                expect(panel.locator('.wf-artifact-browser')).to_have_attribute('data-compact','true')
                assert index_dialog.count()==0,'The modal reopened without a directory action'
                assert directory_focus_after_shrink,'Keyboard focus was lost when the wide directory moved'
                expect(button('展开目录')).to_have_attribute('aria-expanded','false')
                expect(button('展开目录')).to_be_focused()
                expect(selected).to_be_visible()
                info['index_pane'].update(narrow_open_index=compact_index,narrow_open_detail=compact_detail)
                record('wide and compact index keep reading position and preserve selection and comment draft')
                expect(panel.locator('.wf-candidate-grid')).to_be_visible()
                measure(page,panel,'narrow-alternatives')
                panel.locator('.wf-candidate-grid').locator('section').first.evaluate("e=>e.scrollIntoView({block:'start'})")
                page.screenshot(path=str(output/'decisions-narrow.png'))
                page.set_viewport_size({'width':1400,'height':910})
                source_ref=panel.locator('[data-workflow-row="D-3"]').get_by_role('button',name='OR-001 ↗')
                ref_style=source_ref.evaluate("el=>{const s=getComputedStyle(el);return {decoration:s.textDecorationLine,color:s.textDecorationColor,offset:s.textUnderlineOffset,border:s.borderTopWidth}}")
                assert 'underline' in ref_style['decoration'] and '↗' in source_ref.inner_text(),ref_style
                info['requirement_link_rested']=ref_style
                source_ref.click()
                expect(panel.get_by_role('heading',name='需求梳理')).to_be_visible()
                expect(panel.locator('[data-workflow-row="OR-001"] [data-review-title]')).to_be_focused()
                with page.expect_download() as filled:
                    panel.get_by_role('link',name='下载已审核文稿 · .docx').click()
                approved_doc=Document(filled.value.path())
                assert approved_doc.tables[0].rows[1].cells[0].text=='OR-001'
                assert approved_doc.sections[0].header.paragraphs[0].text=='固定页眉'
                record('approved native DOCX download, original header and filled table')
                button('返回当前步骤').click()
                page.reload(wait_until='domcontentloaded')
                expect(panel.get_by_text('已按层级归纳。')).to_be_visible(timeout=25000)
                record('3-level decision tree, source requirement deep link, current stage after reload')
                button('内核与连接').click()
                expect(panel.locator('.workflow-kernel-settings')).to_be_visible()
                page.screenshot(path=str(output/'kernel-connection.png'),animations='disabled')
                assert panel.locator('form form').count()==0
                record('engine settings opens without nested forms')
                page.keyboard.press('Escape')
                expect(panel.get_by_role('dialog',name='内核与连接')).to_have_count(0)
                def advance(title):
                    button('审核通过，进入下一步 →').click()
                    expect(panel.get_by_role('heading',name=title)).to_be_visible()
                    expect(page.locator('.as-agent-header >strong')).to_contain_text(title)
                    stable_stage_overlay()
                def run_stage(kind):
                    arrival_before_run=panel.get_attribute('data-arrival')
                    button('执行此步').click()
                    expect(panel.get_by_text(summaries[kind],exact=True)).to_be_visible()
                    assert panel.get_attribute('data-arrival') is None
                    if arrival_before_run:
                        info.setdefault('cleared_previous_arrivals_on_real_execute',[]).append([kind,arrival_before_run])
                advance('测试用例');run_stage('cases')
                expect(panel.locator('[data-workflow-row="TC-1"] [data-review-title]')).to_be_visible()
                expect(panel.get_by_text('验收动作',exact=True)).to_be_visible()
                advance('详细设计');run_stage('design')
                choose_artifact('DS-1')
                expect(panel.locator('.wf-file-location')).to_have_text('src/report.txt')
                advance('开发')
                composer=open_followup()
                composer.get_by_role('textbox').fill('保留当前阶段的补充要求')
                expect(composer.get_by_role('button',name='发送要求')).to_be_enabled()
                close_stage_conversation(composer)
                def expect_development_draft():
                    reopened=open_followup()
                    expect(reopened.get_by_role('textbox')).to_have_value('保留当前阶段的补充要求')
                    close_stage_conversation(reopened)
                panel.locator('.wf-heading').scroll_into_view_if_needed()
                button('笔记与执行记录').click()
                notes=panel.get_by_role('dialog',name='笔记与执行记录')
                notes.get_by_label('添加笔记').fill('复核开发目录路径')
                notes.get_by_role('button',name='保存笔记').click()
                expect(notes.get_by_text('复核开发目录路径')).to_be_visible()
                page.keyboard.press('Escape')
                button('本步配置').click()
                config=panel.get_by_role('dialog',name='本步配置')
                expect(config.get_by_text('允许读取的交付物',exact=True)).to_be_visible()
                limit=config.get_by_label('子 Agent 上限')
                expect(limit).to_be_visible()
                active_limit=limit.input_value()
                save_policy=config.get_by_role('button',name='保存节点权限')
                expect(save_policy).to_be_disabled()
                limit.fill(str(1 if active_limit=='0' else 0));limit.press('Tab')
                expect(save_policy).to_be_enabled()
                limit.fill(active_limit);limit.press('Tab')
                expect(save_policy).to_be_disabled()
                page.keyboard.press('Escape')
                record('stage inputs, scoped permissions and authenticated notes drawers restore focus')
                button('执行此步').click()
                failure=panel.get_by_role('region',name='执行故障')
                expect(failure.get_by_role('alert')).to_contain_text('内核退出')
                expect(button('重试本步')).to_be_visible()
                expect_development_draft()
                panel.locator('.wf-heading').scroll_into_view_if_needed()
                basis_trigger=button('查看依据')
                expect(basis_trigger).to_be_visible()
                before_basis=bounds(panel.locator('.wf-stage-main'))
                start_layout_trace()
                basis_trigger.click()
                basis=panel.get_by_role('dialog',name='本步依据')
                expect(basis).to_contain_text(summaries['design'])
                basis.evaluate('el => Promise.all(el.getAnimations().map(a => a.finished))')
                after_basis=bounds(panel.locator('.wf-stage-main'))
                wide_basis=bounds(basis)
                assert after_basis==before_basis and wide_basis['width']<=640
                page.keyboard.press('Escape')
                expect(basis_trigger).to_be_focused()
                assert not basis.is_visible()
                finish_layout_trace('approved-inputs-wide')
                info['basis_pane']={'wide_closed_main':before_basis,'wide_open_main':after_basis,'wide_basis':wide_basis}
                expect(panel).not_to_have_attribute('data-arrival','approve')
                panel.locator('.wf-heading h1').click()
                measure(page,panel,'wide-development-failed')
                page.screenshot(path=str(output/'development-failed.png'))
                for width,name in [(520,'narrow'),(390,'compact390')]:
                    page.set_viewport_size({'width':width,'height':745})
                    panel.locator('.wf-heading').scroll_into_view_if_needed()
                    tools=button('本步工具')
                    expect(tools).to_be_visible()
                    expect(basis_trigger).to_have_count(0)
                    expect(button('笔记与执行记录')).to_have_count(0)
                    expect(button('重试本步')).to_be_in_viewport()
                    expect(button('检查内核连接')).to_be_in_viewport()
                    panel.locator('.wf-heading h1').click()
                    measure(page,panel,name+'-development-failed')
                    page.screenshot(path=str(output/f'development-failed-{name}.png'))
                    start_layout_trace()
                    tools.click()
                    tool_menu=panel.get_by_role('dialog',name='本步工具',exact=True)
                    expect(tool_menu).to_be_visible()
                    options=tool_menu.locator('.wf-stage-tool-menu button')
                    expect(options).to_have_count(3)
                    menu_styles=options.evaluate_all("(els)=>els.map(el=>{const s=getComputedStyle(el);return [el.textContent.trim(),s.borderTopWidth,s.borderTopStyle,s.borderTopColor]})")
                    assert all(v[1]!='0px' and v[2]!='none' for v in menu_styles),menu_styles
                    tool_menu.get_by_role('button',name='查看依据').click()
                    compact_basis=panel.get_by_role('dialog',name='本步依据')
                    expect(compact_basis).to_contain_text(summaries['design'])
                    compact_basis.evaluate('el => Promise.all(el.getAnimations().map(a => a.finished))')
                    receipt_box=bounds(compact_basis)
                    assert receipt_box['x']>=0 and receipt_box['x']+receipt_box['width']<=width+1
                    page.keyboard.press('Escape')
                    expect(tools).to_be_focused()
                    finish_layout_trace('approved-inputs-'+name)
                    keyboard_style=tools.evaluate("el=>{const s=getComputedStyle(el);return {focus:el.matches(':focus-visible'),width:s.outlineWidth,style:s.outlineStyle,color:s.outlineColor}}")
                    assert keyboard_style['focus'] and keyboard_style['style'] not in ('none','hidden'),keyboard_style
                    expect_development_draft()
                    tools.click()
                    tool_menu.get_by_role('button',name='本步配置').click()
                    expect(config).to_be_visible()
                    config.evaluate('el => Promise.all(el.getAnimations().map(a => a.finished))')
                    modal=bounds(config)
                    assert abs(modal['x']-(width-modal['width'])/2)<2
                    groups=config.locator('.wf-permission-group')
                    expect(groups).to_have_count(3)
                    assert groups.evaluate_all('items => items.every(item => !item.open)')
                    page.screenshot(path=str(output/f'permissions-{name}.png'))
                    page.keyboard.press('Escape')
                    expect(tools).to_be_focused()
                    info['basis_pane'][name]={'drawer':receipt_box,'keyboard_focus':keyboard_style,'menu_choices':menu_styles}
                    page.screenshot(path=str(output/f'development-keyboard-{name}.png'))
                record('compact stage tools at 520 and 390 show audited inputs, retain draft and return keyboard focus; failure actions stay in view')
                page.set_viewport_size({'width':1400,'height':910})
                failure.get_by_role('button',name='检查内核连接').click()
                expect(panel.locator('.workflow-kernel-settings')).to_be_visible()
                page.keyboard.press('Escape')
                button('本步配置').click()
                expect(save_policy).to_be_enabled()
                before=app.state.stage_workflows.list(pid,conversation)['runs'][0]
                saved_config=next(step['config'] for step in before['stages'] if step['node_id']=='development')
                with page.expect_response(lambda response:response.url.split('?')[0].endswith('/actions') and response.request.method=='POST' and response.request.post_data_json.get('action')=='configure') as configured:
                    save_policy.click()
                assert configured.value.ok
                payload=configured.value.request.post_data_json['payload'];assert payload==saved_config
                after=configured.value.json()
                persisted=next(step for step in after['stages'] if step['node_id']=='development')
                assert after['revision']>before['revision'] and persisted['config']==saved_config
                info['unchanged_failure_policy_confirmation']={'stage':persisted['node_id'],'http':configured.value.status,'revisions':[before['revision'],after['revision']],'config':saved_config}
                expect(config).to_have_count(0)
                button('重试本步').click()
                expect(panel.get_by_text(summaries['development'],exact=True)).to_be_visible()
                expect_development_draft()
                record('kernel failure, connection settings and successful explicit retry')
                expect(panel.get_by_role('progressbar')).to_have_attribute('value','1')
                advance('测试');run_stage('testing')
                expect(button('审核通过，进入下一步 →')).to_be_disabled()
                expect(panel.locator('.wf-case-comparison')).to_contain_text('用例预期')
                expect(panel.locator('.wf-case-comparison')).to_contain_text('执行失败')
                for width,height in ((1400,910),(390,745)):
                    page.set_viewport_size({'width':width,'height':height})
                    narrow_tools=button('本步工具').is_visible()
                    focus_origin=button('本步工具') if narrow_tools else button('笔记与执行记录')
                    focus_origin.scroll_into_view_if_needed()
                    start_layout_trace(('.wf-heading','.wf-stage-main','.wf-footer'))
                    focus_origin.click()
                    if narrow_tools:
                        panel.get_by_role('dialog',name='本步工具').get_by_role('button',name='笔记与执行记录').click()
                    activity=panel.get_by_role('dialog',name='笔记与执行记录')
                    activity.get_by_role('button',name='查看该次记录',exact=True).click()
                    detail=panel.get_by_role('dialog',name='本次执行记录')
                    check_row=detail.get_by_role('listitem').filter(has_text='src/report.txt')
                    expect(check_row).to_have_count(1)
                    expect(check_row.get_by_text('TC-1',exact=True)).to_be_visible()
                    expect(check_row).to_contain_text('执行失败')
                    assert check_row.evaluate('e=>e.scrollWidth <= e.clientWidth + 1')
                    page.screenshot(path=str(output/f'failed-test-case-execution-{width}.png'),animations='disabled')
                    page.keyboard.press('Escape')
                    expect(activity).to_be_visible()
                    if narrow_tools:
                        activity.get_by_role('button',name='返回本步工具').click()
                    page.keyboard.press('Escape')
                    expect(focus_origin).to_be_focused()
                    finish_layout_trace(f'execution-check-case-identity-{width}')
                page.set_viewport_size({'width':1400,'height':910})
                record('failed check execution row shows both the original case number and its file path at wide and narrow sizes')
                nav.get_by_role('button',name='需求梳理',exact=False).click()
                button('查看需求覆盖').click()
                expect(coverage.get_by_text('报告失败',exact=True)).to_be_visible()
                coverage.get_by_role('button',name=__import__('re').compile(r'TEST-1')).click()
                expect(panel.locator('[data-workflow-row="TEST-1"] [data-review-title]')).to_be_focused()
                expect(panel.locator('.wf-case-comparison')).to_contain_text('执行失败')
                rework_trigger=button('返回开发返工')
                page.set_viewport_size({'width':390,'height':745})
                rework_trigger.scroll_into_view_if_needed()
                page.route('**/rework-preview?*',lambda route:route.fulfill(status=503,json={'detail':'暂时读不到返工范围'}),times=1)
                start_layout_trace()
                rework_trigger.click()
                rework=panel.get_by_role('dialog',name='返回开发返工')
                expect(rework).to_be_visible()
                label('返工原因').fill('正文与已审核的预期不同。')
                expect(rework.get_by_role('alert')).to_have_text('暂时读不到返工范围')
                expect(button('记录并返回开发')).to_be_disabled()
                rework.get_by_role('button',name='重试读取范围').click()
                expect(rework.locator('.wf-rework-target')).to_have_text('开发节点开发')
                rework.locator('summary').click()
                expect(rework.locator('.wf-rework-impact li')).to_have_text(['开发','测试'])
                expect(rework.locator('.wf-rework-impact p')).to_contain_text('TC-1')
                page.screenshot(path=str(output/'rework-scope-390.png'),animations='disabled')
                rework.evaluate('el => Promise.all(el.getAnimations().map(a => a.finished))')
                page.keyboard.press('Escape')
                expect(rework_trigger).to_be_focused()
                finish_layout_trace('rework-scope-390')
                page.set_viewport_size({'width':1400,'height':910})
                page.reload(wait_until='domcontentloaded')
                expect(rework_trigger).to_be_visible()
                rework_trigger.scroll_into_view_if_needed()
                start_layout_trace()
                rework_trigger.click()
                expect(label('返工原因')).to_have_value('正文与已审核的预期不同。')
                expect(rework.locator('.wf-rework-target')).to_contain_text('开发')
                rework.locator('summary').click()
                expect(rework.locator('.wf-rework-impact li')).to_have_text(['开发','测试'])
                page.screenshot(path=str(output/'rework-scope-1400.png'),animations='disabled')
                page.keyboard.press('Escape')
                expect(rework_trigger).to_be_focused()
                finish_layout_trace('rework-scope-1400')
                record('scoped rework shows only affected stages, preserves the unsent reason across reload and retries a failed read')
                rework_trigger.click()
                expect(label('返工原因')).to_have_value('正文与已审核的预期不同。')
                slow_mount['fail_node']='development'
                rework_count=sum(item['action']=='rework' for item in transitions)
                rework_commit={}
                def lose_rework_receipt(route):
                    result=route.fetch()
                    assert result.ok,result.text()
                    rework_commit.update(result.json())
                    route.fulfill(status=503,json={'detail':'验收注入：提交入库后回执丢失'})
                page.route('**/actions',lose_rework_receipt,times=1)
                button('记录并返回开发').click()
                expect(transition_pending.get_by_role('status')).to_have_text('尚未确认原操作是否保存。')
                expect(rework).not_to_be_visible()
                expect(panel.locator('.wf-case-comparison')).to_contain_text('执行失败')
                expect(rework_trigger).to_have_count(1)
                saved_target=next(s['stage_session_id'] for s in rework_commit['stages'] if s['node_id']=='development')
                page.set_viewport_size({'width':390,'height':745})
                page.reload(wait_until='domcontentloaded')
                expect(check_transition).to_be_visible()
                expect(panel.locator('.wf-case-comparison')).to_contain_text('执行失败')
                start_layout_trace()
                page.screenshot(path=str(output/'transition-pending-rework-390.png'),animations='disabled')
                check_transition.press('Enter')
                expect(recover_stage).to_be_focused()
                expect(reopen.get_by_role('status')).to_have_text('返工已记录，「开发」暂未打开。')
                expect(panel.locator('.wf-case-comparison')).to_contain_text('执行失败')
                finish_layout_trace('transition-receipt-readonly-narrow')
                page.set_viewport_size({'width':1400,'height':910})
                recover_stage.click()
                expect(recover_stage).not_to_have_attribute('aria-disabled','true')
                expect(reopen.get_by_role('status')).to_have_text('返工已记录，「开发」暂未打开。')
                recover_stage.click()
                expect(page.locator('.as-agent-header >strong')).to_contain_text('开发')
                assert page.evaluate("JSON.parse(localStorage.getItem('eeas-current-thread-v1')).id")==saved_target
                assert sum(item['action']=='rework' for item in transitions)==rework_count+1
                sent_rework=next(item for item in reversed(transitions) if item['action']=='rework')
                assert sent_rework['payload']['case_ids']==['TC-1']
                info['saved_rework_navigation']={'rework_posts':1,'source_output_visible_before_retry':True,
                    'draft_restored_after_reload':True,'sent_case_ids':sent_rework['payload']['case_ids'],
                    'lost_commit_receipt':True,'exact_destination':saved_target}
                record('already committed rework is recovered by a readonly receipt after restart without a second POST')
                expect(panel.get_by_text('测试退回开发')).to_be_visible()
                expect(page.locator('html')).to_have_attribute('data-workflow-observed','wf-return-page/rework/development')
                run_stage('development');advance('测试')
                button('本步配置').click()
                group=config.locator('details').filter(has_text='Tools')
                group.locator('summary').click()
                group.get_by_role('checkbox',name='写入工程文件').check()
                save_policy.click()
                expect(config).to_have_count(0)
                test_inputs['shadow']=True
                run_stage('testing')
                expect(button('审核通过，进入下一步 →')).to_be_disabled()
                expect(panel.locator('[data-test-status="stale"]')).to_have_text('需复测')
                for width in (1400,390):
                    page.set_viewport_size({'width':width,'height':745})
                    review_tasks=button('审核待办')
                    review_tasks.evaluate("el=>el.focus({preventScroll:true})")
                    start_layout_trace(('.wf-heading','.wf-artifact','.wf-case-comparison','.wf-footer'))
                    review_tasks.press('Enter')
                    modal=panel.get_by_role('dialog',name='审核待办',exact=True)
                    expect(modal.get_by_text('TC-1 / content 被测文件覆盖了已审版本，请恢复已审版本后复测')).to_be_visible()
                    page.screenshot(path=str(output/f'test-input-stale-{width}.png'),animations='disabled')
                    page.keyboard.press('Escape')
                    expect(review_tasks).to_be_focused()
                    finish_layout_trace(f'test-input-stale-{width}')
                page.set_viewport_size({'width':1400,'height':910})
                test_inputs.update(shadow=False,restore=True)
                button('重新生成').click()
                expect(panel.locator('[data-test-status="passed"]')).to_have_text('通过')
                test_inputs['restore']=False
                record('changed file invalidates current test approval, keeps stable review geometry, and passes after the original input is restored and retested')
                expect(panel.get_by_role('progressbar')).to_have_attribute('value','1')
                nav.get_by_role('button',name='需求梳理',exact=False).click()
                button('查看需求覆盖').click()
                expect(coverage.get_by_text('已核验通过',exact=True)).to_be_visible()
                expect(coverage.get_by_text('用例已审核',exact=True)).to_be_visible()
                page.screenshot(path=str(output/'coverage-wide.png'),animations='disabled')
                page.set_viewport_size({'width':390,'height':745})
                result=coverage.get_by_text('已核验通过',exact=True)
                result.scroll_into_view_if_needed()
                expect(result).to_be_in_viewport(ratio=1)
                assert coverage.evaluate('(dialog)=>dialog.scrollWidth<=dialog.clientWidth+1')
                page.screenshot(path=str(output/'coverage-390.png'),animations='disabled')
                coverage_source_top=panel.evaluate('el=>el.scrollTop')
                panel.evaluate("""root=>{
                  const samples=[];let frame;
                  const measure=()=>{
                    const stage=root.querySelector('.wf-page');
                    const title=stage?.querySelector('[data-workflow-row="TC-1"] [data-review-title]');
                    if(stage?.dataset.phase==='cases')samples.push({
                      located:document.activeElement===title&&!!title,
                      header:document.querySelector('.as-agent-header > strong')?.textContent.trim(),
                      visible:title&&title.getBoundingClientRect().bottom>root.getBoundingClientRect().top
                       &&title.getBoundingClientRect().top<root.getBoundingClientRect().bottom
                    });
                    frame=requestAnimationFrame(measure);
                  };
                  frame=requestAnimationFrame(measure);
                  window.finishWorkflowNavigation=()=>{cancelAnimationFrame(frame);return samples};
                }""")
                coverage.get_by_role('button',name=__import__('re').compile(r'TC-1')).click()
                expect(panel.locator('[data-workflow-row="TC-1"] [data-review-title]')).to_be_focused()
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                linked_frames=page.evaluate('window.finishWorkflowNavigation()')
                assert linked_frames and all(frame['located'] and frame['visible'] and frame['header']=='测试用例' for frame in linked_frames),linked_frames
                button('返回需求覆盖').click()
                expect(coverage.get_by_text('已核验通过',exact=True)).to_be_visible()
                assert abs(panel.evaluate('el=>el.scrollTop')-coverage_source_top)<=1
                coverage.get_by_role('button',name=__import__('re').compile(r'TEST-1')).click()
                expect(panel.locator('[data-workflow-row="TEST-1"] [data-review-title]')).to_be_focused()
                info['coverage_first_paint']={'position_restored':True,'frames':linked_frames,'width':390}
                header=page.locator('.as-agent-header >strong')
                expect(header).to_have_text(__import__('re').compile(r'^测试(?: · v[0-9]+)?$'))
                button('返回需求覆盖').click()
                page.keyboard.press('Escape')
                expect(button('查看需求覆盖')).to_be_focused()
                expect(header).to_have_text('需求梳理')
                before_thread=page.evaluate("JSON.parse(localStorage.getItem('eeas-current-thread-v1')).id")
                coverage_writes=[]
                def observe_coverage_write(request):
                    if '/api/stage-workflows' in request.url and request.method!='GET':
                        coverage_writes.append((request.method,request.url))
                page.on('request',observe_coverage_write)
                mode_switch=page.locator('.as-agent-header .wf-mode-switch')

                def verify_origin_return(source, anchor):
                    source.scroll_into_view_if_needed()
                    source.focus()
                    top=panel.evaluate('el=>el.scrollTop')
                    switch_box=bounds(mode_switch)
                    source.press('Enter')
                    coverage.get_by_role('button',name=__import__('re').compile(r'TC-1')).click()
                    expect(panel.locator('[data-workflow-row="TC-1"] [data-review-title]')).to_be_focused()
                    expect(header).to_have_text('测试用例')
                    assert bounds(mode_switch)==switch_box
                    button('返回需求覆盖').click()
                    expect(coverage.get_by_role('button',name=__import__('re').compile(r'TC-1'))).to_be_visible()
                    expect(header).to_have_text('需求梳理')
                    page.keyboard.press('Escape')
                    expect(coverage).not_to_be_visible()
                    expect(source).to_be_focused()
                    expect(source).to_be_in_viewport(ratio=1)
                    expect(anchor).to_have_js_property('open',True)
                    assert abs(panel.evaluate('el=>el.scrollTop')-top)<=1
                    assert page.evaluate("JSON.parse(localStorage.getItem('eeas-current-thread-v1')).id")==before_thread

                for width in (390,1400):
                    page.set_viewport_size({'width':width,'height':745 if width==390 else 910})
                    original_trigger=panel.locator('[data-workflow-row="OR-001"]').get_by_role('button',name='查看需求原文')
                    original_trigger.click()
                    original=panel.get_by_role('dialog',name='需求原文',exact=True)
                    verify_origin_return(original.get_by_role('button',name='查看来源覆盖 · UR-A001',exact=True),original)
                    page.screenshot(path=str(output/f'coverage-return-{width}.png'),animations='disabled')
                    page.keyboard.press('Escape')
                    expect(original).not_to_be_visible()
                    expect(original_trigger).to_be_focused()
                page.set_viewport_size({'width':768,'height':565})
                source_discussion=open_followup()
                source_discussion.get_by_role('textbox').fill('返回后仍需保留这段草稿。')
                disclosure=source_discussion.locator('.wf-followups')
                if not disclosure.evaluate('el=>el.open'):
                    disclosure.locator(':scope > summary').click()
                followed=disclosure.get_by_role('listitem').filter(has_text='UR-A001')
                executed=followed.locator('.wf-followup-execution')
                executed.locator('summary').click()
                followup_coverage=followed.get_by_role('button',name='查看覆盖')
                verify_origin_return(followup_coverage,disclosure)
                assert followup_coverage.evaluate("""button=>{
                    const point=button.getBoundingClientRect();
                    const scroll=button.closest('.wf-conversation-scroll').getBoundingClientRect();
                    return point.top>=scroll.top&&point.bottom<=scroll.bottom&&
                        button.contains(document.elementFromPoint(point.x+point.width/2,point.y+point.height/2));
                }""")
                page.screenshot(path=str(output/'coverage-return-followup-short.png'),animations='disabled')
                expect(executed).to_have_js_property('open',True)
                expect(source_discussion.get_by_role('textbox')).to_have_value('返回后仍需保留这段草稿。')
                source_discussion.get_by_role('textbox').fill('')
                close_stage_conversation(source_discussion)
                button('查看需求覆盖').click()
                coverage.get_by_role('button',name=__import__('re').compile(r'TEST-1')).click()
                expect(header).to_have_text(__import__('re').compile(r'^测试(?: · v[0-9]+)?$'))
                page.remove_listener('request',observe_coverage_write)
                assert not coverage_writes,coverage_writes
                info['exact_coverage_origins']={'artifact_widths':[390,1400],'dialogue_viewport':[768,565],'reading_session_unchanged':True,
                                               'draft_restored':True,'workflow_writes':0}
                page.set_viewport_size({'width':1400,'height':910})
                record('requirement coverage from the original request to the case and verified result, with focus and exact return')
                page.emulate_media(reduced_motion='reduce')
                advance('交付')
                expect(panel.locator('.wf-page')).to_have_css('animation-name','none')
                assert not page.evaluate("window.workflowAnimations.some(x=>x.stage==='delivery')")
                button('本步配置').click()
                group=config.locator('details').filter(has_text='Tools')
                group.locator('summary').click()
                group.get_by_role('checkbox',name='写入工程文件').check()
                save_policy.click()
                expect(config).to_have_count(0)
                delivery_input['replace']=True
                run_stage('delivery')
                expect(button('审核通过并完成 →')).to_be_disabled()
                for width in (1400,390):
                    page.set_viewport_size({'width':width,'height':745})
                    tasks=button('审核待办')
                    tasks.evaluate("el=>el.focus({preventScroll:true})")
                    start_layout_trace(('.wf-heading','.wf-artifact','.wf-footer'))
                    tasks.press('Enter')
                    dialog=panel.get_by_role('dialog',name='审核待办')
                    expect(dialog.get_by_text('substituted.zip!/src/report.txt',exact=False)).to_be_visible()
                    page.screenshot(path=str(output/f'delivery-member-block-{width}.png'),animations='disabled')
                    page.keyboard.press('Escape')
                    expect(tasks).to_be_focused()
                    finish_layout_trace(f'delivery-member-block-{width}')
                page.set_viewport_size({'width':1400,'height':910})
                delivery_input['replace']=False
                button('重新生成').click()
                expect(button('审核通过并完成 →')).to_be_enabled()
                record('delivery rejects a replaced ZIP member and keeps review and approval controls stable')
                expect(panel.locator('[data-workflow-row="PKG-1"] [data-review-title]')).to_be_visible()
                expect(panel.get_by_role('progressbar')).to_have_count(0)
                receipt_entry=button('交付凭证')
                expect(receipt_entry).to_have_count(0)
                with page.expect_download() as saved:
                    panel.locator('[data-workflow-row="PKG-1"]').get_by_role('link',name='下载成果物').click()
                delivered=Path(saved.value.path()).read_bytes()
                assert delivered=='已修正'.encode()
                page.set_viewport_size({'width':390,'height':745})
                finish=button('审核通过并完成 →')
                finish.scroll_into_view_if_needed()
                finish.focus()
                completion_before=panel.evaluate("""root=>{
                  const paint=()=>{
                    const paper=root.querySelector('.wf-page'),style=getComputedStyle(paper);
                    return {scroll:root.scrollTop,heading:root.querySelector('.wf-heading').getBoundingClientRect().top,
                      paper:paper.getBoundingClientRect().width,visibility:style.visibility,opacity:style.opacity};
                  };
                  const samples=[];let frame;
                  const measure=()=>{samples.push(paint());frame=requestAnimationFrame(measure);};
                  frame=requestAnimationFrame(measure);
                  window.finishCompletionTrace=()=>{cancelAnimationFrame(frame);return {samples,after:paint()}};
                  return paint();
                }""")
                assert completion_before['scroll']>0,completion_before
                finish.press('Enter')
                expect(panel.get_by_text('所有节点均已审核通过。')).to_be_visible()
                expect(panel.get_by_role('progressbar')).to_have_count(0)
                expect(panel.locator('.wf-completion-message')).to_be_focused()
                expect(panel.locator('.wf-completion-message')).to_be_in_viewport()
                page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                completion_trace=page.evaluate('window.finishCompletionTrace()')
                info['completion_kept_view']={'before':completion_before,**completion_trace,'width':390}
                assert completion_trace['samples'] and all(sample==completion_before for sample in completion_trace['samples']),info['completion_kept_view']
                assert completion_trace['after']==completion_before,info['completion_kept_view']
                delivery_scope=app.state.stage_workflows.list(pid,conversation)['runs'][0]
                delivery_sha=delivery_scope['stages'][7]['approval_sha256']
                manifest=None
                delivery_bundle=None
                reference_requests=[]
                def observe_bundle_reference(request):
                    if '/delivery-bundle-reference?' in request.url:
                        reference_requests.append({'method':request.method,'body':request.post_data})
                page.on('request',observe_bundle_reference)
                def choose_saved_bundle(preview,name,data):
                    with page.expect_file_chooser() as selected:
                        preview.get_by_role('button',name='核验本机 ZIP').press('Enter')
                    selected.value.set_files({'name':name,'mimeType':'application/zip','buffer':data})
                for width in (1400,520,390):
                    page.set_viewport_size({'width':width,'height':910 if width==1400 else 745})
                    receipt_entry.scroll_into_view_if_needed()
                    receipt_entry.focus()
                    start_layout_trace(('.wf-heading','.wf-artifact'))
                    if width==1400:
                        page.route('**/delivery-receipt?*',lambda route:route.fulfill(
                            status=503,json={'detail':'测试用凭证读取故障'}),times=1)
                    receipt_entry.press('Enter')
                    preview=panel.get_by_role('dialog',name='交付凭证')
                    expect(preview).to_be_visible()
                    initial=bounds(preview)
                    if width==1400:
                        expect(preview.get_by_role('alert')).to_have_text('测试用凭证读取故障')
                        preview.get_by_role('button',name='重新核对').press('Enter')
                    expect(preview.get_by_text('已核对的成果物',exact=True)).to_be_visible()
                    assert preview.locator('dd').all_text_contents()==['2','8','1']
                    assert bounds(preview)==initial
                    expect(preview).to_have_css('animation-name','none')
                    save=preview.get_by_role('button',name='下载凭证')
                    if width==1400:
                        expect(save).to_be_focused()
                    with page.expect_download() as receipt_saved:
                        save.press('Enter')
                    assert receipt_saved.value.suggested_filename==f'cirven-delivery-{delivery_sha[:12]}.json'
                    data=Path(receipt_saved.value.path()).read_bytes()
                    if manifest is None:manifest=data
                    assert manifest==data
                    contents=json.loads(data)
                    assert contents['delivery_approval_sha256']==delivery_sha
                    direct,release=contents['files']
                    assert direct=={'item_id':'PKG-1','path':'src/report.txt',
                        'sha256':hashlib.sha256(delivered).hexdigest(),'bytes':len(delivered)}
                    member_sha=hashlib.sha256(member_bytes).hexdigest()
                    assert len(release['members'])==41
                    assert release['members'][0]['members']==[{'path':member_path,'bytes':len(member_bytes),'sha256':member_sha}]
                    assert contents['test_results'][0]['case_id']=='TC-1'
                    files_entry=preview.get_by_role('button',name='查看交付文件')
                    files_entry.press('Enter')
                    search_files=preview.get_by_role('searchbox',name='按路径或摘要查找')
                    expect(search_files).to_be_focused()
                    listing=preview.locator('.wf-delivery-file-list')
                    expect(listing.locator('summary')).to_have_count(32)
                    preview.get_by_role('button',name='显示更多').press('Enter')
                    expect(listing.locator('summary')).to_have_count(44)
                    expect(listing.locator('summary').nth(32)).to_be_focused()
                    search_files.fill(member_sha[:24])
                    matched=listing.locator('summary',has_text=member_path)
                    expect(listing.locator('summary')).to_have_count(1)
                    expect(matched).to_contain_text('release.zip › diagnostics.zip')
                    matched.press('Enter')
                    expect(listing.locator('code')).to_have_text(member_sha)
                    file_geometry=listing.evaluate("""el=>{
                        const digest=el.querySelector('code');
                        return {width:el.clientWidth,scrollWidth:el.scrollWidth,
                            digestWidth:digest.clientWidth,digestScrollWidth:digest.scrollWidth};
                    }""")
                    info.setdefault('workflow_delivery_file_geometry',{})[str(width)]=file_geometry
                    assert file_geometry['scrollWidth']<=file_geometry['width']+1 and (
                        file_geometry['digestScrollWidth']<=file_geometry['digestWidth']+1),file_geometry
                    assert bounds(preview)==initial
                    page.screenshot(path=str(output/f'delivery-file-list-{width}.png'),animations='disabled')
                    search_files.fill('no-such-deliverable')
                    expect(listing.get_by_role('status')).to_have_text('没有匹配的文件。')
                    preview.get_by_role('button',name='返回凭证').click()
                    expect(files_entry).to_be_focused()
                    assert bounds(preview)==initial
                    if width in (1400,390):
                        save_bundle=preview.get_by_role('button',name='下载交付包')
                        before_action=bounds(save_bundle)
                        if width==1400:
                            page.route('**/delivery-bundle?*',lambda route:route.fulfill(
                                status=503,json={'detail':'测试用打包响应中断'}),times=1)
                            expected_error='测试用打包响应中断'
                        else:
                            page.route('**/delivery-bundle?*',lambda route:route.fulfill(
                                status=200,headers={'content-type':'application/zip',
                                'x-cirven-delivery-approval':'0'*64},body=b''),times=1)
                            expected_error='返回的交付包与此审核版本不一致。'
                        save_bundle.press('Enter')
                        expect(preview.get_by_role('alert')).to_have_text(expected_error)
                        expect(save_bundle).to_be_focused()
                        assert bounds(preview)==initial and bounds(save_bundle)==before_action
                        with page.expect_download() as bundle_saved:
                            save_bundle.press('Enter')
                        assert bundle_saved.value.suggested_filename==f'cirven-delivery-{delivery_sha[:12]}.zip'
                        package=Path(bundle_saved.value.path()).read_bytes()
                        with ZipFile(BytesIO(package)) as archive:
                            assert archive.namelist()==['files/src/report.txt','files/release.zip','receipt.json']
                            assert archive.read('files/src/report.txt')==delivered
                            assert archive.read('files/release.zip')==release_bytes
                            assert archive.read('receipt.json')==manifest and archive.testzip() is None
                        if delivery_bundle is None:
                            delivery_bundle=package
                            (output/'approved-handoff.zip').write_bytes(package)
                        assert delivery_bundle==package
                        if width==390:
                            page.route('**/delivery-bundle-reference?*',lambda route:route.fulfill(
                                status=503,json={'detail':'测试用核验依据读取失败'}),times=1)
                        choose_saved_bundle(preview,'my-copy.zip',package)
                        if width==390:
                            expect(preview.get_by_role('alert')).to_have_text('测试用核验依据读取失败')
                            expect(preview.get_by_role('button',name='重试核验')).to_be_visible()
                            preview.get_by_role('button',name='重试核验').press('Enter')
                            expect(preview.get_by_role('button',name='核验本机 ZIP')).to_be_focused()
                        expect(preview.locator('[data-verification=match]')).to_have_text('核验通过。所选 ZIP 与本版交付完全一致。')
                        assert bounds(preview)==initial and bounds(save_bundle)==before_action
                        if width==390:
                            page.screenshot(path=str(output/'bundle-verification-match-390.png'),animations='disabled')
                            altered=package[:-1]+bytes([package[-1]^1])
                            choose_saved_bundle(preview,'same-size.zip',altered)
                            expect(preview.get_by_role('alert')).to_have_text('所选 ZIP 与本版交付不一致。请核对版本。')
                            assert bounds(preview)==initial and bounds(save_bundle)==before_action
                            page.screenshot(path=str(output/'bundle-verification-mismatch-390.png'),animations='disabled')
                    page.screenshot(path=str(output/f'delivery-receipt-{width}.png'),animations='disabled')
                    page.keyboard.press('Escape')
                    expect(preview).not_to_be_visible()
                    expect(receipt_entry).to_be_focused()
                    finish_layout_trace(f'delivery-receipt-{width}')
                assert app.state.stage_workflows.list(pid,conversation)['runs'][0]==delivery_scope
                page.set_viewport_size({'width':1400,'height':910})
                record('exact portable receipt, network read retry, keyboard download and stable layout in three window widths')
                record('delivery files show the approved nested member and complete digest with stable layout and keyboard return')

                improvement_entry=button('经验与改进')
                improvement=panel.get_by_role('dialog',name='经验与改进')
                for width in (1400,520,390):
                    page.set_viewport_size({'width':width,'height':910 if width==1400 else 745})
                    improvement_entry.scroll_into_view_if_needed()
                    improvement_entry.focus()
                    start_layout_trace(('.wf-heading','.wf-artifact'))
                    improvement_entry.press('Enter')
                    expect(improvement).to_be_visible()
                    improvement.get_by_label('阶段记录').select_option('plugin_suggestions:0')
                    expect(improvement.locator('blockquote')).to_have_text('在交付复核时再次执行已审核的测试。')
                    improvement.get_by_label('能力类别').select_option('tools')
                    improvement.get_by_label('选择能力').select_option('run_check')
                    expect(improvement.locator('.wf-improvement-diff')).to_contain_text('交付 · 新增 Tool run_check')
                    editor=improvement.get_by_label('工作流副本名称')
                    if width==1400:editor.fill('交付可复核版')
                    expect(editor).to_have_value('交付可复核版')
                    save_improvement=improvement.get_by_role('button',name='保存改进')
                    expect(save_improvement).to_be_in_viewport(ratio=1)
                    expect(improvement).to_have_css('animation-name','none')
                    page.screenshot(path=str(output/f'improvement-{width}.png'),animations='disabled')
                    page.keyboard.press('Escape')
                    expect(improvement).not_to_be_visible()
                    expect(improvement_entry).to_be_focused()
                    finish_layout_trace(f'improvement-{width}')
                improvement_entry.press('Enter')
                saved_improvement=[]
                def lose_improvement_reply(route):
                    original=route.fetch()
                    assert original.ok,original.text()
                    saved_improvement.append(original.json())
                    route.fulfill(status=503,json={'detail':'测试用改进保存响应丢失'})
                page.route('**/improvements',lose_improvement_reply,times=1)
                save_improvement.press('Enter')
                expect(improvement.get_by_role('alert')).to_have_text('测试用改进保存响应丢失')
                expect(improvement.get_by_role('button',name='核对或重试原保存')).to_be_visible()
                earlier_animations=page.evaluate('window.workflowAnimations')
                page.reload(wait_until='domcontentloaded')
                expect(improvement_entry).to_be_visible()
                improvement_entry.press('Enter')
                expect(improvement.get_by_role('status')).to_have_text('已保存 交付可复核版 · v1 · 交付 · 新增 run_check')
                expect(improvement.get_by_role('button',name='保存改进')).to_have_attribute('aria-disabled','true')
                assert len(saved_improvement)==1
                later=saved_improvement[0]['definition']
                scope_after=app.state.stage_workflows.list(pid,conversation)
                assert scope_after['runs'][0]==delivery_scope
                copied=[d for d in scope_after['definitions'] if d['name']=='交付可复核版']
                assert copied==[later] and later['id']!='default'
                assert 'run_check' in later['nodes'][-1]['capabilities']['tools']
                assert 'run_check' not in delivery_scope['definition']['nodes'][-1]['capabilities']['tools']
                improvement.get_by_text('已保存的改进',exact=False).click()
                improvement.get_by_role('button',name='在流程库中查看').click()
                expect(panel.locator('.wf-library-title h2')).to_have_text('交付可复核版')
                history_entry=button('改进记录')
                history=panel.get_by_role('dialog',name='改进记录')
                source_row=history.get_by_role('button',name=__import__('re').compile('交付.*run_check.*配置一致'))
                detail_route='**/definitions/*/improvements/*?*'
                for width in (1400,520,390):
                    page.set_viewport_size({'width':width,'height':910 if width==1400 else 745})
                    history_entry.scroll_into_view_if_needed()
                    history_entry.focus()
                    start_layout_trace(('.wf-library-title','.wf-library-preview','.wf-library-preview .wf-actions'),'.wf-setup')
                    if width==1400:
                        held_history=[]
                        page.route('**/definitions/*/improvements?*',lambda route:held_history.append(route),times=1)
                    history_entry.press('Enter')
                    expect(history).to_be_visible()
                    expect(history).to_have_css('animation-name','none')
                    if width==1400:
                        expect(history.get_by_role('status')).to_contain_text('正在读取')
                        assert len(held_history)==1
                        held_history.pop().continue_()
                    expect(source_row).to_be_visible()
                    if width==390:
                        page.route(detail_route,lambda route:route.fulfill(status=503,json={'detail':'测试用原审核读取失败'}),times=1)
                    source_row.press('Enter')
                    if width==390:
                        expect(history.get_by_role('alert')).to_have_text('测试用原审核读取失败')
                        history.get_by_role('button',name='重新核对').click()
                    expect(history.locator('blockquote')).to_have_text('在交付复核时再次执行已审核的测试。')
                    expect(history.get_by_role('button',name='打开原阶段')).to_be_in_viewport(ratio=1)
                    page.screenshot(path=str(output/f'definition-source-{width}.png'),animations='disabled')
                    history.get_by_role('button',name='返回改进列表').click()
                    expect(source_row).to_be_focused()
                    page.keyboard.press('Escape')
                    expect(history_entry).to_be_focused()
                    finish_layout_trace(f'definition-source-{width}')
                page.set_viewport_size({'width':1400,'height':910})
                button('编辑定义').click()
                label('工作流名称').fill('交付可复核版 · 已修订')
                button('保存工作流').click()
                expect(panel.locator('.wf-library-title h2')).to_have_text('交付可复核版 · 已修订')
                history_entry.press('Enter')
                expect(history.locator('.wf-history-version')).to_contain_text('v2')
                source_row.press('Enter')
                expect(history.locator('blockquote')).to_have_text('在交付复核时再次执行已审核的测试。')
                history.get_by_role('button',name='打开原阶段').click()
                expect(panel.locator('.wf-heading h1')).to_have_text('交付')
                expect(panel.locator('.wf-heading h1')).to_be_focused()
                expect(history).not_to_be_visible()
                assert app.state.stage_workflows.list(pid,conversation)['runs'][0]==delivery_scope
                page.locator('.as-conversation-sidebar:visible').first.get_by_role('button',name='产品设计验证',exact=True).click()
                expect(page.locator('.as-agent-header >strong')).to_have_attribute('title',__import__('re').compile(r'产品设计验证$'))
                assert page.evaluate("JSON.parse(localStorage.getItem('eeas-current-thread-v1')).id")==conversation
                panel.locator('.wf-crumb button').click()
                panel.get_by_role('combobox',name='工作流定义',exact=True).select_option(later['id'])
                expect(panel.locator('.wf-library-title h2')).to_have_text('交付可复核版 · 已修订')
                history_entry.press('Enter')
                source_row.press('Enter')
                expect(history.locator('blockquote')).to_have_text('在交付复核时再次执行已审核的测试。')
                history.get_by_role('button',name='打开原阶段').click()
                expect(panel.locator('.wf-heading h1')).to_have_text('交付')
                expect(panel.locator('.wf-heading h1')).to_be_focused()
                assert page.evaluate("JSON.parse(localStorage.getItem('eeas-current-thread-v1')).id")==saved_improvement[0]['receipt']['source']['conversation_id']
                expect(history).not_to_be_visible()
                record('definition history retains the original approved source through a manual edit, with stable library geometry and exact return')
                page.set_viewport_size({'width':1400,'height':910})
                record('approved improvement, future copy, lost write reply recovery without resubmit, three stable widths and library access')
                sidebar=page.locator('.as-conversation-sidebar:visible').first
                conversations=sidebar.locator('.as-conversation-row:visible')
                expect(conversations.nth(8)).to_be_visible()
                page.mouse.move(1180,140)
                panel.locator('.wf-heading h1').click()
                page.evaluate("()=>document.activeElement instanceof HTMLElement&&document.activeElement.blur()")
                page.mouse.move(1180,140)
                side_rows=conversations.evaluate_all("""els=>els.map(el=>{const s=getComputedStyle(el);return {name:el.textContent.trim(),current:el.getAttribute('aria-current'),background:s.backgroundColor,border:[s.borderTopWidth,s.borderRightWidth,s.borderBottomWidth,s.borderLeftWidth],shadow:s.boxShadow,outline:s.outlineWidth,outline_style:s.outlineStyle,focus_visible:el.matches(':focus-visible')}})""")
                assert sum(row['current']=='page' for row in side_rows)==1
                for row in side_rows:
                    assert row['border']==['0px']*4 and row['shadow']=='none' and not row['focus_visible'],row
                    assert row['outline']=='0px' or row['outline_style']=='none',row
                    assert (row['background'] in ['transparent','rgba(0, 0, 0, 0)'])==(row['current']!='page'),row
                page.screenshot(path=str(output/'completed-wide.png'))
                sidebar.locator('.as-conversation-sidebar-project').filter(has_text='工作流浏览器验证').screenshot(path=str(output/'completed-conversation-group.png'))
                conversations.first.focus();page.keyboard.press('ArrowDown')
                expect(conversations.nth(1)).to_be_focused()
                side_focus=conversations.nth(1).evaluate("el=>{const s=getComputedStyle(el);return {focus:el.matches(':focus-visible'),width:s.outlineWidth,style:s.outlineStyle}}")
                assert side_focus=={'focus':True,'width':'2px','style':'solid'},side_focus
                info['completed_conversation_sidebar']={'count':len(side_rows),'rows':side_rows,'keyboard':side_focus}
                record('completed real project has unboxed conversation rows, a single selection, and visible roving keyboard focus')
                animation_events=earlier_animations+page.evaluate('window.workflowAnimations')
                assert any(x['effect']=='wf-return-page' and x['kind']=='rework' for x in animation_events)
                info['transition_events']=animation_events
                record('approved and rework transitions occur on their real events; reduced motion honors native preference')
                record('approved case actions, directory design, real file and progress, failed check rework, retest and exact download')

                nav.get_by_role('button',name='需求梳理',exact=False).click()
                expect(panel.get_by_role('heading',name='需求梳理')).to_be_visible()
                amend=open_followup()
                amend.get_by_role('textbox').fill('补上审核人的完整名称。')
                expect(amend.get_by_text('将重新打开 6 个下游阶段。',exact=True)).to_be_visible()
                draft_text=amend.get_by_role('textbox')
                impact=amend.get_by_role('button',name='查看影响范围')
                impact_dialog=panel.get_by_role('dialog',name='修订影响')
                for width,height in ((1400,910),(520,745),(390,745)):
                    page.set_viewport_size({'width':width,'height':height})
                    impact.scroll_into_view_if_needed()
                    start_layout_trace(('.wf-heading','.wf-conversation-float','.wf-conversation-float textarea','.wf-follow-up-actions'))
                    impact.press('Enter')
                    expect(impact_dialog.get_by_role('listitem')).to_have_count(6)
                    expect(impact_dialog.get_by_text('交付',exact=True)).to_be_visible()
                    page.keyboard.press('Escape')
                    expect(impact).to_be_focused()
                    expect(draft_text).to_have_value('补上审核人的完整名称。')
                    finish_layout_trace(f'revision-impact-{width}')
                page.set_viewport_size({'width':1400,'height':910})
                record('revision impact keeps the project page, conversation, editor and action geometry across wide and narrow windows')
                slow_mount['fail_node']='requirements'
                revise_count=sum(item['action']=='revise' for item in transitions)
                button('创建修订').click()
                expect(reopen.get_by_role('status')).to_have_text('修订已创建，「需求梳理」暂未打开。')
                recover_stage.click()
                expect(button('查看此阶段上一版')).to_be_visible()
                assert sum(item['action']=='revise' for item in transitions)==revise_count+1
                info['saved_revision_navigation']={'revision_posts':1,'previous_version_reachable':True}
                expect(page.locator('.as-agent-header >strong')).to_contain_text('需求梳理 · v2')
                amended=open_followup()
                amended.locator('.wf-followups > summary').click()
                amended_item=amended.get_by_role('listitem').filter(has_text='补上审核人的完整名称。')
                expect(amended_item.get_by_text('待随下次执行处理',exact=True)).to_be_visible()
                page.screenshot(path=str(output/'revision-request.png'))
                close_stage_conversation(amended)
                button('查看此阶段上一版').click()
                expect(button('返回当前版本')).to_be_visible()
                expect(header).to_have_text('需求梳理 · 只读')
                expect(panel.locator('.wf-summary')).to_have_text('已整理可审核的包需求。')
                button('查看需求覆盖').click()
                expect(coverage.get_by_text('历史覆盖 · 按当时的审核与执行版本查看。',exact=True)).to_be_visible()
                results=coverage.get_by_role('button',name=__import__('re').compile(r'TEST-1'))
                expect(results).to_have_count(2)
                failed_result=coverage.get_by_role('button',name=__import__('re').compile(r'TEST-1.*v1'))
                passed_result=coverage.get_by_role('button',name=__import__('re').compile(r'TEST-1.*v2'))
                page.set_viewport_size({'width':390,'height':745})
                passed_result.scroll_into_view_if_needed()
                expect(passed_result).to_be_in_viewport(ratio=1)
                page.screenshot(path=str(output/'historical-coverage-390.png'),animations='disabled')
                failed_result.click()
                expect(panel.locator('[data-workflow-row="TEST-1"] [data-review-title]')).to_be_focused()
                expect(panel.locator('.wf-case-comparison')).to_contain_text('执行失败')
                expect(panel.locator('.wf-case-comparison')).to_contain_text('保留原始名称')
                button('返回需求覆盖').click()
                passed_result.click()
                expect(panel.locator('[data-workflow-row="TEST-1"] [data-review-title]')).to_be_focused()
                expect(panel.locator('.wf-case-comparison').get_by_text('通过',exact=True)).to_be_visible()
                button('返回需求覆盖').click()
                expect(results).to_have_count(2)
                page.set_viewport_size({'width':1400,'height':910})
                page.keyboard.press('Escape')
                button('返回当前版本').click()
                expect(page.locator('.as-agent-header >strong')).to_contain_text('需求梳理 · v2')
                expect(button('查看此阶段上一版')).to_be_visible()
                amended=open_followup()
                amended.locator('.wf-followups > summary').click()
                expect(amended.get_by_text('补上审核人的完整名称。',exact=True)).to_be_visible()
                close_stage_conversation(amended)
                comparison_entry=button('对比上一版')
                expect(comparison_entry).to_have_count(0)
                button('按补充要求执行').click()
                expect(panel.locator('.wf-summary')).to_have_text('已补充审核人字段。')
                expect(comparison_entry).to_be_visible()
                compare_before=app.state.stage_workflows.list(pid,conversation)['runs'][0]
                diff=panel.get_by_role('dialog',name='修订对比')
                row_diff=diff.locator('[data-diff-row="OR-001"]')
                for width in (1400,520,390):
                    page.set_viewport_size({'width':width,'height':910 if width==1400 else 745})
                    comparison_entry.scroll_into_view_if_needed()
                    comparison_entry.focus()
                    start_layout_trace(('.wf-heading','.wf-artifact','[data-workflow-row="OR-001"]'))
                    if width==1400:
                        held=[]
                        page.route('**/revision-difference?*',lambda route:held.append(route),times=1)
                    comparison_entry.press('Enter')
                    expect(diff).to_be_visible()
                    expect(diff).to_have_css('animation-name','none')
                    if width==1400:
                        expect(diff.get_by_role('status')).to_have_text('正在读取两个成果版本…')
                        assert len(held)==1
                        held.pop().continue_()
                    expect(diff.locator('.wf-diff-counts [data-change="modified"] strong')).to_have_text('1')
                    if width==390:
                        page.route('**/revision-difference?*',lambda route:
                            route.fulfill(status=503,json={'detail':'测试用字段差异读取失败'}),times=1)
                    row_diff.press('Enter')
                    if width==390:
                        expect(diff.get_by_role('alert')).to_have_text('测试用字段差异读取失败')
                        diff.get_by_role('button',name='重新读取').click()
                    field=diff.locator('.wf-diff-field',has_text='描述')
                    expect(field.locator('.wf-diff-value').nth(0)).to_contain_text('导出带审核状态的清单。')
                    expect(field.locator('.wf-diff-value').nth(0)).not_to_contain_text('审核人全名')
                    expect(field.locator('.wf-diff-value').nth(1)).to_contain_text('每项记录审核人全名。')
                    page.screenshot(path=str(output/f'revision-difference-{width}.png'),animations='disabled')
                    diff.get_by_role('button',name='返回变更清单').click()
                    expect(row_diff).to_be_focused()
                    overview=diff.get_by_role('button',name='成果说明',exact=False)
                    overview.press('Enter')
                    expect(diff.locator('.wf-diff-field')).to_contain_text('已补充审核人字段。')
                    page.keyboard.press('Escape')
                    expect(comparison_entry).to_be_focused()
                    finish_layout_trace(f'revision-difference-{width}')
                assert app.state.stage_workflows.list(pid,conversation)['runs'][0]==compare_before
                page.set_viewport_size({'width':1400,'height':910})
                record('reviewed revisions expose pinned row and artifact fields with no page movement or writes')
                record('approved stage revision shows impact, retains the old version and returns to the new session')
                record('historical coverage separates failed and passed versions and opens their original read-only records')
                previous_delivery=delivery_scope['stages'][-1]['stage_session_id']
                temporary_project=app.state.store.create_project('交付依据移动验证','')['id']
                def move_stage_session(session,target,operation):
                    header=app.state.conversation_threads.metadata_header(session)
                    return app.state.conversation_organization.change(session,OrganizationChange(
                        source=header['source'],action='move',target_project_id=target,
                        operation_id=operation,expected_revision=header['organization']['revision']))
                previous_row=sidebar.locator(f'.as-conversation-row[data-conversation-id="{previous_delivery}"]')
                expect(previous_row).to_be_visible()
                assert move_stage_session(previous_delivery,temporary_project,'move_previous_delivery')['project_id']==temporary_project
                assert move_stage_session(previous_delivery,pid,'restore_previous_delivery')['project_id']==pid
                with page.expect_response(lambda response:unquote(response.url).endswith(f'/{previous_delivery}/selection')) as stale_selection:
                    previous_row.click()
                assert stale_selection.value.status==409
                expect(panel.locator('.wf-summary')).to_have_text(summaries['delivery'])
                assert page.evaluate("JSON.parse(localStorage.getItem('eeas-current-thread-v1')).id")==previous_delivery
                record('stale sidebar opens a stage restored to its project with one click')
                with page.expect_download() as historical_file:
                    panel.locator('[data-workflow-row="PKG-1"]').get_by_role('link',name='下载成果物').click()
                assert Path(historical_file.value.path()).read_bytes()==delivered
                receipt_entry.press('Enter')
                old_files=panel.get_by_role('dialog',name='交付凭证').get_by_role('button',name='查看交付文件')
                old_files.press('Enter')
                old_preview=panel.get_by_role('dialog',name='交付凭证')
                old_preview.get_by_role('searchbox').fill(member_path)
                expect(old_preview.locator('.wf-delivery-file-list summary')).to_contain_text('release.zip › diagnostics.zip')
                old_preview.get_by_role('button',name='返回凭证').press('Enter')
                with page.expect_download() as historical_receipt:
                    panel.get_by_role('dialog',name='交付凭证').get_by_role('button',name='下载凭证').press('Enter')
                assert Path(historical_receipt.value.path()).read_bytes()==manifest
                with page.expect_download() as historical_bundle:
                    panel.get_by_role('dialog',name='交付凭证').get_by_role('button',name='下载交付包').press('Enter')
                assert Path(historical_bundle.value.path()).read_bytes()==delivery_bundle
                archived_preview=panel.get_by_role('dialog',name='交付凭证')
                choose_saved_bundle(archived_preview,'previously-saved.zip',delivery_bundle)
                expect(archived_preview.locator('[data-verification=match]')).to_have_text('核验通过。所选 ZIP 与本版交付完全一致。')
                assert len(reference_requests)==5 and all(
                    entry=={'method':'GET','body':None} for entry in reference_requests),reference_requests
                info['bundle_verification']={'reference_reads':len(reference_requests),'selected_bytes_sent':0,
                    'history_match':True,'same_size_mismatch':True,'retry_without_reselect':True}
                page.remove_listener('request',observe_bundle_reference)
                record('local handoff verification compares the original ZIP without upload, including retry and archived version')
                info['delivery_bundle']={'approvals':delivery_sha,'sha256':hashlib.sha256(delivery_bundle).hexdigest(),
                    'widths':[390,1400],'history_byte_identical':True}
                record('verified handoff bundle retries in two widths, keeps files and receipt together and reopens byte-identically')
                page.keyboard.press('Escape')
                expect(receipt_entry).to_be_focused()
                record('original deliverable and receipt remain byte-identical from the archived delivery conversation')
                source_session=delivery_scope['stages'][1]['stage_session_id']
                for width,height in ((1400,910),(390,745)):
                    page.set_viewport_size({'width':width,'height':height})
                    receipt_entry.press('Enter')
                    historic=panel.get_by_role('dialog',name='交付凭证')
                    export=historic.get_by_role('button',name='下载凭证')
                    expect(export).to_be_visible()
                    assert move_stage_session(source_session,temporary_project,f'move_delivery_source_{width}')['project_id']==temporary_project
                    cancelled=[]
                    def track_unwanted(download):cancelled.append(download.suggested_filename)
                    page.on('download',track_unwanted)
                    try:
                        export.focus()
                        start_layout_trace(('.wf-heading','.wf-artifact','dialog[open]','.wf-delivery-receipt-actions'))
                        with page.expect_response(lambda r:'/delivery-receipt?' in r.url) as refused:
                            export.press('Enter')
                        assert refused.value.status==409
                        expect(historic.get_by_role('alert')).to_have_text('此交付版本引用的审核会话已不可访问。')
                        expect(export).to_be_focused()
                        finish_layout_trace(f'receipt-export-source-unavailable-{width}')
                        assert not cancelled
                    finally:
                        page.remove_listener('download',track_unwanted)
                        assert move_stage_session(source_session,pid,f'restore_delivery_source_{width}')['project_id']==pid
                    with page.expect_download() as full_receipt:
                        export.press('Enter')
                    assert Path(full_receipt.value.path()).read_bytes()==manifest
                    page.keyboard.press('Escape')
                    expect(receipt_entry).to_be_focused()
                page.set_viewport_size({'width':1400,'height':910})
                record('archived receipt JSON requires present source scope, preserves review layout and restores byte-identical export')


                recovery=app.state.stage_workflows
                failure_owner=app.state.conversation_creation.create(CreateConversation(
                    operation_id='browser_recovery_conversation',project_id=pid,title='中断接续项目验证'))['conversation_ref']['id']
                recovery.define(pid,failure_owner,'browser_recovery_definition',0,
                    {'id':'resume-proof','name':'中断接续','nodes':[{'id':'resume','name':'接续核验','kind':'custom'}]})
                interrupted_run=recovery.start(pid,failure_owner,'browser_recovery_start','resume-proof')
                stage_id=interrupted_run['stages'][0]['stage_session_id']
                run_key=interrupted_run['id']
                def step(op,name,payload=None):
                    head=recovery.get(pid,stage_id,run_key)
                    return recovery.action(pid,stage_id,run_key,op,head['revision'],'resume',name,payload or {})

                original={'summary':'按指定范围核对文件。',
                          'items':[{'id':'R1','title':'目录','description':'核对目录'},{'id':'R2','title':'正文','description':'保留原正文'}]}
                recovery.executor=lambda _: {'artifact':original}
                step('resume_initial','execute')
                selected=step('resume_comment_1','annotate',{'target':'R1','comment':'核对回存目录'})['stages'][0]['annotations'][0]['id']
                unselected=step('resume_comment_2','annotate',{'target':'R2','comment':'请稍后检查正文'})['stages'][0]['annotations'][1]['id']
                class CutOff(BaseException):
                    pass
                calls=[]
                def lost(context):
                    calls.append(context['operation_id'])
                    stage_workspace.file_write(context,'src/recovered.txt','已写入的内容','')
                    raise CutOff
                recovery.executor=lost
                with patch('packages.dynamic_workflow.stage_runs._runtime_owner',return_value={'pid':2147483647,'birth':0}):
                    try:
                        step('resume_original','execute',{'annotation_ids':[selected]})
                    except CutOff:
                        pass
                    else:
                        raise AssertionError('Expected abandoned execution')

                def retry(context):
                    calls.append(context['operation_id'])
                    assert [a['id'] for a in context['annotations']]==[selected]
                    assert [e['path'] for e in context['prior_evidence'] if e['kind']=='file']==['src/recovered.txt']
                    return {'artifact':{**original,'items':[{**original['items'][0],'description':'已核对回存目录'},original['items'][1]]}}
                recovery.executor=retry
                interruption_polls=[]
                def observe_recovery_poll(request):
                    if request.method=='GET' and f'/api/stage-workflows/runs/{run_key}?' in request.url:
                        interruption_polls.append(request.url)
                page.on('request',observe_recovery_poll)
                page.evaluate("id=>localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id}))",stage_id)
                page.reload(wait_until='domcontentloaded')
                expect(panel.get_by_role('heading',name='接续核验')).to_be_visible()
                expect(panel.locator('.wf-status')).to_have_text('执行中断')
                stable_stage_overlay()
                page.wait_for_timeout(1800)
                assert not interruption_polls
                paused_request=open_followup()
                paused_request.get_by_role('textbox').fill('继续保留回存文件。')
                send_paused=paused_request.get_by_role('button',name='发送要求',exact=True)
                send_paused.scroll_into_view_if_needed()
                start_layout_trace(('.wf-heading','.wf-artifact','.wf-stage-composer','.wf-footer'))
                send_paused.click()
                expect(paused_request.get_by_text('待随下次执行处理',exact=True)).to_be_visible()
                expect(panel.locator('.wf-status')).to_have_text('执行中断')
                close_stage_conversation(paused_request)
                finish_layout_trace('supplement-on-interrupted-custom-stage')
                assert not interruption_polls
                page.remove_listener('request',observe_recovery_poll)
                assert calls==['resume_original']
                button('核查执行状态').click()
                execution=panel.get_by_role('dialog',name='本次执行记录')
                expect(execution.get_by_text('执行已中断，整步结果未确认',exact=True)).to_be_visible()
                expect(execution.get_by_text('当时选中的批注 · 1',exact=True)).to_be_visible()
                assert calls==['resume_original']
                page.set_viewport_size({'width':390,'height':745})
                with page.expect_download() as recovered_download:
                    execution.get_by_role('link',name='下载 src/recovered.txt').click()
                assert recovered_download.value.path().read_text()=='已写入的内容'
                page.screenshot(path=str(output/'interrupted-execution-390.png'),animations='disabled')
                page.keyboard.press('Escape')
                stage_status=panel.locator('.wf-heading-status')
                expect(stage_status).to_be_focused()
                stage_status.press('Enter')
                feedback=panel.get_by_role('dialog',name='本步反馈')
                expect(feedback).to_be_visible()
                retry_button=feedback.get_by_role('button',name='重试原批注')
                retry_button.click()
                expect(feedback).not_to_be_visible()
                expect(panel.locator('.wf-status')).to_have_text('待审核')
                assert len(calls)==2 and calls[-1]!='resume_original'
                stage=recovery.get(pid,stage_id,run_key)['stages'][0]
                assert [(a['id'],a['status']) for a in stage['annotations']]==[(selected,'applied'),(unselected,'open')]
                page.set_viewport_size({'width':1400,'height':910})
                button('笔记与执行记录').click()
                original_entry=page.locator('[data-execution-record="resume_original"]')
                original_entry.click()
                expect(execution.get_by_text('src/recovered.txt',exact=True)).to_be_visible()
                page.screenshot(path=str(output/'interrupted-execution-wide.png'),animations='disabled')
                execution.get_by_role('button',name='返回执行记录列表').click()
                expect(original_entry).to_be_focused()
                page.keyboard.press('Escape')
                assert len(calls)==2
                info['interrupted_execution']={'idle_poll_requests':len(interruption_polls),'execute_calls_before_retry':1,
                    'execute_calls_after_retry_and_history':len(calls),'retried_comments':1,'remaining_open_comments':1}
                record('interrupted execution, original file download, exact selected comment retry, reopen history')

                manual_owner=app.state.conversation_creation.create(CreateConversation(
                    operation_id='browser_manual_conversation',project_id=pid,title='实测录入验证'))['conversation_ref']['id']
                recovery.define(pid,manual_owner,'browser_manual_definition',0,{'id':'manual-proof','name':'实测工作流','nodes':[
                    {'id':'cases','name':'现场验收用例','kind':'cases'},
                    {'id':'requirements','name':'固件需求','kind':'requirements'},
                    {'id':'alternatives','name':'接口方案','kind':'alternatives','inputs':['requirements']},
                    {'id':'design','name':'联调准备','kind':'design','inputs':['requirements','alternatives']},
                    {'id':'development','name':'固件样件','kind':'development','inputs':['design']},
                    {'id':'testing','name':'现场实测','kind':'testing','inputs':['cases','development']}]})
                manual_run=recovery.start(pid,manual_owner,'browser_manual_start','manual-proof')
                case={'id':'TC-M1','title':'上电指示','requirement_ids':[],'category':'现场验收','subcategory':'状态指示',
                    'content':'观察模拟台架','priority':'P0','preconditions':'固定台架上电',
                    'method':'人工观察','steps':['观察指示灯'],'checks':['核对绿灯'],'expected':'指示灯亮绿',
                    'coverage':['runtime','hardware'],'actions':[{'id':'status-light','method':'manual_observation',
                        'instruction':'上电后观察指示灯。','expected':'一秒内亮绿。'}]}
                setup_calls=[]
                firmware_bytes=b'controlled firmware receipt fixture\x00'
                golden=run_path(str(Path(__file__).resolve().parents[3]/'tests/test_engineering_stage_contracts.py'))
                def manual_machine(ctx):
                    kind=ctx['kind']
                    setup_calls.append(kind)
                    if kind=='cases':
                        return {'artifact':{'summary':'现场实测项目。','items':[case]}}
                    if kind in ('requirements','alternatives'):
                        return {'artifact':golden['APPROVED'][kind]}
                    if kind=='design':
                        example=golden['APPROVED']['design']['items'][0]
                        return {'artifact':{'summary':'准备固件样件。','items':[{**example,'id':'DS-BENCH','directory':'out/sample.bin'}]}}
                    assert kind=='development'
                    firmware=stage_workspace.file_write_bytes(ctx,'out/sample.bin',firmware_bytes,'')
                    completed=stage_workspace.complete_design_item(ctx,'DS-BENCH',[firmware['id']])
                    return {'artifact':{'summary':'固件样件已准备。','items':[{'id':'DEV-BENCH','title':'生成测试样件',
                        'design_ids':['DS-BENCH'],'status':'done','evidence_refs':[completed['id']],'self_check':'核对文件。'}]},
                        'verified_evidence':stage_workspace.operation_receipts(ctx)}
                recovery.executor=manual_machine
                planning_stages=['cases','requirements','alternatives','design','development']
                for kind in planning_stages:
                    data={'user_input':golden['INPUT']} if kind=='requirements' else {}
                    manual_run=recovery.action(pid,manual_owner,manual_run['id'],'manual-plan-'+kind,manual_run['revision'],kind,'execute',data)
                    stage=next(stage for stage in manual_run['stages'] if stage['node_id']==kind)
                    assert stage['status']=='review' and not stage.get('approval_issues'),(kind,stage)
                    manual_run=recovery.action(pid,manual_owner,manual_run['id'],'manual-plan-approve-'+kind,manual_run['revision'],kind,'approve',{})
                firmware,=[item for item in manual_run['stages'][-2]['verified_evidence'] if item['kind']=='file']
                manual_id=manual_run['stages'][-1]['stage_session_id']
                page.evaluate("id=>localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id}))",manual_id)
                page.reload(wait_until='domcontentloaded')
                expect(panel.get_by_role('heading',name='现场实测')).to_be_visible()
                manual_dialog=panel.get_by_role('dialog',name='人工实测')
                for width in (1400,390):
                    page.set_viewport_size({'width':width,'height':830})
                    trigger=button('人工实测' if width==1400 else '本步工具')
                    trigger.scroll_into_view_if_needed()
                    start_layout_trace()
                    trigger.press('Enter')
                    if width==390:
                        panel.get_by_role('dialog',name='本步工具').get_by_role('button',name='人工实测').click()
                    expect(manual_dialog.get_by_text('一秒内亮绿。',exact=True)).to_be_visible()
                    manual_dialog.evaluate('el => Promise.all(el.getAnimations().map(a => a.finished))')
                    expect(manual_dialog.get_by_role('button',name='保存实测记录')).to_be_in_viewport(ratio=1)
                    assert manual_dialog.locator('.wf-manual-actions:visible').evaluate(
                        "el=>el.getBoundingClientRect().bottom<=el.closest('dialog').getBoundingClientRect().bottom")
                    page.screenshot(path=str(output/f'manual-observations-{width}.png'),animations='disabled')
                    page.keyboard.press('Escape')
                    expect(trigger).to_be_focused()
                    finish_layout_trace(f'manual-observations-{width}')
                page.set_viewport_size({'width':1400,'height':910})
                expect(button('执行此步')).to_have_count(0)
                button('人工实测').click()
                manual_dialog.get_by_label('结果',exact=True).select_option('blocked')
                manual_dialog.get_by_label('实测环境',exact=True).fill('验收夹具 A')
                manual_dialog.get_by_label('实测观察',exact=True).fill('夹具未接通，当前无法观察指示灯。')
                bench_entry=manual_dialog.get_by_role('button',name='工位与固件')
                bench_entry.scroll_into_view_if_needed()
                start_layout_trace(('.wf-heading','.wf-page','dialog[open]'))
                bench_entry.press('Enter')
                bench=panel.get_by_role('dialog',name='实测工位',exact=True)
                expect(bench).to_be_visible()
                assert bench.evaluate("(el)=>getComputedStyle(el,'::backdrop').backgroundColor")=='rgba(0, 0, 0, 0)'
                bench.get_by_label('上位机版本').fill('上位机 v2')
                bench.get_by_label('夹具与接线').fill('IO 适配器 CH-A')
                bench.get_by_role('button',name='添加设备').press('Enter')
                bench.get_by_label('设备名称和板卡版本').first.fill('主控 Rev.A')
                bench.get_by_label('设备编号或探针序列号').first.fill('DUT-001')
                bench.get_by_label('连接方式').first.fill('COM7 / 115200')
                bench.get_by_label('项目固件依据').first.select_option(firmware['id'])
                bench.get_by_role('button',name='添加设备').press('Enter')
                bench.get_by_label('设备名称和板卡版本').nth(1).fill('采集板 Rev.B')
                bench.get_by_label('设备编号或探针序列号').nth(1).fill('DUT-042')
                page.keyboard.press('Escape')
                expect(bench_entry).to_be_focused()
                finish_layout_trace('manual-observation-bench')
                page.keyboard.press('Escape')
                page.reload(wait_until='domcontentloaded')
                button('人工实测').click()
                expect(manual_dialog.get_by_label('实测观察',exact=True)).to_have_value('夹具未接通，当前无法观察指示灯。')
                expect(manual_dialog.get_by_text('已填写工位',exact=True)).to_be_visible()
                manual_submissions=[]
                def observe_manual(req):
                    if req.method=='POST' and req.url.endswith('/actions'):
                        body=req.post_data_json
                        if body['action']=='record_manual_check':manual_submissions.append(body)
                def drop_manual_receipt(route):
                    result=route.fetch()
                    assert result.ok
                    route.fulfill(status=503,json={'detail':'实测已落盘，测试故意丢弃回执'})
                page.on('request',observe_manual)
                page.route(f'**/runs/{manual_run["id"]}/actions',drop_manual_receipt,times=1)
                manual_dialog.get_by_role('button',name='保存实测记录').click()
                expect(manual_dialog.get_by_role('alert')).to_contain_text('测试故意丢弃回执')
                page.reload(wait_until='domcontentloaded')
                button('人工实测').click()
                expect(manual_dialog.get_by_label('实测观察',exact=True)).to_have_value('夹具未接通，当前无法观察指示灯。')
                manual_dialog.get_by_role('button',name='保存实测记录').click()
                expect(manual_dialog.get_by_role('status')).to_contain_text('人工实测 · 受阻')
                expect(manual_dialog.get_by_label('实测观察',exact=True)).to_have_value('')
                assert len(manual_submissions)==2 and manual_submissions[0]==manual_submissions[1]
                recorded_bench=manual_submissions[0]['payload']['bench']
                assert recorded_bench['host']=='上位机 v2' and [d['identity'] for d in recorded_bench['devices']]==['DUT-001','DUT-042']
                manual_dialog.locator('.wf-bench-receipt > summary').press('Enter')
                with page.expect_download() as stored_firmware:
                    manual_dialog.get_by_role('link',name='out/sample.bin').click()
                assert Path(stored_firmware.value.path()).read_bytes()==firmware_bytes
                bench_entry.press('Enter')
                bench.get_by_role('button',name='沿用最近记录的工位').press('Enter')
                expect(bench.get_by_label('设备编号或探针序列号').nth(1)).to_have_value('DUT-042')
                expect(bench.get_by_label('上位机版本')).to_have_value('上位机 v2')
                expect(bench.get_by_label('项目固件依据').first).to_have_value(firmware['id'])
                page.keyboard.press('Escape')
                expect(manual_dialog.get_by_label('结果',exact=True)).to_have_value('')
                expect(manual_dialog.get_by_label('实测观察',exact=True)).to_have_value('')
                record('embedded test bench reopens as a stable nested dialog and reuses setup without copying an outcome')
                page.keyboard.press('Escape')
                expect(button('审核通过并完成 →')).to_be_disabled()
                button('人工实测').click()
                manual_dialog.get_by_label('结果',exact=True).select_option('passed')
                manual_dialog.get_by_label('实测环境',exact=True).fill('验收夹具 A')
                manual_dialog.get_by_label('实测观察',exact=True).fill('模拟夹具记录：上电后 0.6 秒亮绿。')
                manual_dialog.get_by_role('button',name='保存实测记录').click()
                expect(manual_dialog.get_by_role('status')).to_contain_text('人工实测 · 通过')
                manual_dialog.locator('summary').filter(has_text='较早记录').click()
                expect(manual_dialog.get_by_text('夹具未接通，当前无法观察指示灯。',exact=True)).to_be_visible()
                page.screenshot(path=str(output/'manual-observation-history.png'),animations='disabled')
                page.keyboard.press('Escape')
                button('审核通过并完成 →').click()
                expect(panel.get_by_text('所有节点均已审核通过。')).to_be_visible()
                done=recovery.get(pid,manual_id,manual_run['id'])
                assert [entry['status'] for entry in done['stages'][-1]['verified_evidence']]==['blocked','passed']
                assert setup_calls==planning_stages and done['status']=='completed'
                info['manual_observations']={'statuses':['blocked','passed'],'manual_stage_model_calls':0,
                    'draft_restored_after_reload':True,'receipt_retry_same_operation':True,'approval_finished':True}
                record('manual-only approved check, stable modal, draft reload, blocked history and actual approval without a kernel')

                from packages.dynamic_workflow import kernel_settings
                reuse_owner=app.state.conversation_creation.create(CreateConversation(
                    operation_id='browser_reuse_conversation',project_id=pid,title='同输入复用验证'))['conversation_ref']['id']
                fake_kernel=output/'planning-fixture-kernel.bin'
                fake_kernel.write_bytes(b'planning identity fixture')
                kernel_settings.save_route(recovery.data_dir,'opencode',0,
                    {'endpoint':'http://127.0.0.1:9000','model':'planning-fixture','credential_env':'','allow_http':False})
                inputs=dict(background='保留已审项目原文',development_constraints='两人团队',
                    build_environment='Python 3.10',runtime_environment='Windows x64')
                generation_log=[]
                def plan_executor(ctx):
                    generation_log.append(ctx['stage_session_id'])
                    origin='原批准的交付边界。' if len(generation_log)==1 else '新批准的交付边界。'
                    topics={'background':'交付边界','development_constraints':'开发约束',
                        'build_environment':'编译环境','runtime_environment':'运行环境'}
                    return {'artifact':{'summary':origin,'items':[{'id':f'INIT-{i}','title':title,
                        'topic':topic,'detail':origin if topic=='background' else inputs[topic]}
                        for i,(topic,title) in enumerate(topics.items(),1)]}}
                recovery.executor=plan_executor
                def planning_action(run,operation,kind,payload=None):
                    ident=run['stages'][0]['stage_session_id']
                    return recovery.action(pid,ident,run['id'],operation,run['revision'],'init',kind,payload or {})
                with patch.object(kernel_settings,'bundled',return_value=fake_kernel):
                    first=recovery.start(pid,reuse_owner,'browser_plan_start','default')
                    first=planning_action(first,'browser_plan_execute','execute',{'user_input':inputs})
                    first=planning_action(first,'browser_plan_approve','approve')
                    repeated=recovery.start(pid,reuse_owner,'browser_repeat_start','default')
                    repeated=planning_action(repeated,'browser_repeat_execute','execute',{'user_input':inputs})
                    reused=repeated['stages'][0]
                    assert reused['reused_from']['approval_sha256']==first['stages'][0]['approval_sha256']
                    assert len(generation_log)==1
                    revised=planning_action(first,'browser_plan_revise','revise',{'text':'核对新一版边界'})
                    revised=planning_action(revised,'browser_plan_new_execute','execute',{'user_input':inputs})
                    revised=planning_action(revised,'browser_plan_new_approve','approve')
                assert len(generation_log)==2 and revised['stages'][0]['artifact']['summary']=='新批准的交付边界。'
                page.evaluate("id=>localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id}))",reused['stage_session_id'])
                page.reload(wait_until='domcontentloaded')
                source_trigger=button('核对原审批')
                source_dialog=panel.get_by_role('dialog',name='复用的原审批')
                expect(source_trigger).to_be_visible()
                draft_orb=open_followup()
                draft_orb.get_by_role('textbox').fill('保留复用成果旁的当前草稿。')
                close_stage_conversation(draft_orb)
                before_runs=recovery.list(pid,reuse_owner)['runs']
                source_requests,writes=[],[]
                def observe_planning_source(request):
                    if '/planning-source?' in request.url:
                        source_requests.append(request.method)
                    if '/api/stage-workflows' in request.url and request.method!='GET':
                        writes.append(request.method)
                page.on('request',observe_planning_source)
                for width,height in ((1400,910),(390,750),(768,565)):
                    page.set_viewport_size({'width':width,'height':height})
                    page.emulate_media(reduced_motion='reduce' if width==390 else 'no-preference')
                    source_trigger.scroll_into_view_if_needed()
                    if width==390:
                        page.route('**/planning-source?*',lambda route:route.fulfill(status=503,json={'detail':'验收注入：暂时读不到原审批'}),times=1)
                    start_layout_trace(('.wf-heading','.wf-stage-main > .wf-artifact'))
                    source_trigger.press('Enter')
                    if width==390:
                        expect(source_dialog.get_by_role('alert')).to_have_text('验收注入：暂时读不到原审批')
                        source_dialog.evaluate('el => Promise.all(el.getAnimations().map(a => a.finished))')
                        error_geometry=bounds(source_dialog)
                        source_dialog.get_by_role('button',name='重试读取').click()
                    expect(source_dialog.get_by_text('历史审批 · 只读',exact=True)).to_be_visible()
                    expect(source_dialog.locator('.wf-summary')).to_have_text('原批准的交付边界。')
                    expect(source_dialog.locator('code')).to_have_attribute('title',reused['reused_from']['approval_sha256'])
                    expect(source_dialog.get_by_text('新批准的交付边界。',exact=True)).to_have_count(0)
                    if width==390:
                        assert bounds(source_dialog)==error_geometry
                    page.screenshot(path=str(output/f'planning-source-{width}.png'),animations='disabled')
                    page.keyboard.press('Escape')
                    expect(source_trigger).to_be_focused()
                    finish_layout_trace(f'planning-source-{width}')
                page.emulate_media(reduced_motion='no-preference')
                page.remove_listener('request',observe_planning_source)
                assert source_requests==['GET']*4 and not writes,(source_requests,writes)
                assert recovery.list(pid,reuse_owner)['runs']==before_runs
                draft_orb=open_followup()
                expect(draft_orb.get_by_role('textbox')).to_have_value('保留复用成果旁的当前草稿。')
                close_stage_conversation(draft_orb)
                info['planning_source']={'original_approval':reused['reused_from']['approval_sha256'],
                    'new_approval':revised['stages'][0]['approval_sha256'],'business_writes':len(writes),
                    'directory_reads':len(source_requests),'model_invocations_for_identical_executions':1}
                record('reused planning output opens the exact original approval across revision, retry and focus return')

                history_owner=app.state.conversation_creation.create(CreateConversation(
                    operation_id='history_receipt_owner',project_id=pid,title='原操作版本验证'))['conversation_ref']['id']
                recovery.define(pid,history_owner,'history_receipt_define',0,{
                    'id':'receiptHistory','name':'历史回执验证','nodes':[
                        {'id':'basis','name':'报表约束','kind':'custom'},
                        {'id':'target','name':'报表主模块','kind':'custom','inputs':['basis']},
                    ]})
                history_run=recovery.start(pid,history_owner,'history_receipt_start','receiptHistory')
                basis_session=history_run['stages'][0]['stage_session_id']
                summary='原报表约束已整理。'
                recovery.executor=lambda _: {'artifact':{'summary':summary,'items':[{
                    'id':'R1','title':'校验列','description':summary}]}}
                def history_action(run,name,action,node,payload=None):
                    return recovery.action(pid,history_owner,run['id'],name,run['revision'],node,action,payload or {})
                history_run=history_action(history_run,'history_receipt_execute_basis','execute','basis')
                history_traffic['action_path']=f"/api/stage-workflows/runs/{history_run['id']}/actions"
                page.evaluate("id=>localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id}))",basis_session)
                page.reload(wait_until='domcontentloaded')
                expect(panel.get_by_text(summary,exact=True).first).to_be_visible()
                approved_before_cut={}
                def lose_history_approval(route):
                    response=route.fetch()
                    assert response.ok,response.text()
                    approved_before_cut.update(response.json())
                    assert route.request.post_data_json['action']=='approve'
                    route.fulfill(status=503,json={'detail':'验收注入：原审核回执丢失'})
                page.route('**/actions',lose_history_approval,times=1)
                button('审核通过，进入下一步 →').click()
                expect(check_transition).to_be_visible()
                original_target=approved_before_cut['stages'][1]['stage_session_id']
                summary='原模块使用原有校验列。'
                history_run=history_action(approved_before_cut,'history_receipt_execute_target','execute','target')
                history_run=history_action(history_run,'history_receipt_approve_target','approve','target')
                history_run=recovery.action(pid,basis_session,history_run['id'],'history_receipt_revise',
                    history_run['revision'],'basis','revise',{'text':'变更校验依据'})
                summary='新报表约束已整理。'
                history_run=history_action(history_run,'history_receipt_execute_basis_new','execute','basis')
                history_run=history_action(history_run,'history_receipt_approve_basis_new','approve','basis')
                summary='新模块增加第二列。'
                history_run=history_action(history_run,'history_receipt_execute_target_new','execute','target')
                current_target=history_run['stages'][1]['stage_session_id']
                assert current_target!=original_target

                page.set_viewport_size({'width':390,'height':745})
                page.reload(wait_until='domcontentloaded')
                expect(panel.get_by_role('heading',name='报表约束')).to_be_visible()
                expect(check_transition).to_be_visible()
                start_layout_trace(('.wf-heading','.wf-stage-main'))
                check_transition.press('Enter')
                original_button=button('查看原阶段')
                expect(original_button).to_be_focused()
                expect(reopen.get_by_role('status')).to_have_text('审核已保存。可查看「报表主模块」的历史版本。')
                finish_layout_trace('transition-history-receipt-390')
                page.screenshot(path=str(output/'transition-history-ready-390.png'),animations='disabled')
                page.reload(wait_until='domcontentloaded')
                expect(original_button).to_be_visible()
                for width in (390,1400):
                    page.set_viewport_size({'width':width,'height':745})
                    original_button.scroll_into_view_if_needed()
                    matches=lambda url: '/api/stage-workflows?' in url and original_target in unquote(url)
                    page.route(matches,lambda route:route.fulfill(status=503,json={'detail':'验收注入：历史阶段读取失败'}),times=1)
                    start_layout_trace(('.wf-heading','.wf-stage-main'))
                    with page.expect_response(lambda response:matches(response.url) and response.status==503):
                        original_button.press('Enter')
                    expect(original_button).to_have_attribute('aria-disabled','false')
                    expect(original_button).to_be_focused()
                    expect(panel.get_by_role('heading',name='报表约束')).to_be_visible()
                    finish_layout_trace(f'transition-history-read-failed-{width}')
                original_button.press('Enter')
                expect(panel.get_by_role('heading',name='报表主模块')).to_be_focused()
                expect(page.locator('.as-agent-header >strong')).to_have_text('报表主模块 · 只读')
                expect(panel.get_by_text('原模块使用原有校验列。',exact=True).first).to_be_visible()
                assert panel.get_by_text('新模块增加第二列。',exact=True).count()==0
                assert page.evaluate("JSON.parse(localStorage.getItem('eeas-current-thread-v1')).id")==original_target
                expect(button('审核通过，进入下一步 →')).to_have_count(0)
                page.screenshot(path=str(output/'transition-history-original.png'),animations='disabled')
                button('返回当前版本').click()
                expect(panel.get_by_text('新模块增加第二列。',exact=True).first).to_be_visible()
                assert page.evaluate("JSON.parse(localStorage.getItem('eeas-current-thread-v1')).id")==current_target
                assert history_traffic['posts']==[history_traffic['action_path']],history_traffic
                assert recovery.get(pid,history_owner,history_run['id'])==history_run
                info['historic_transition_destination']={'initial_post_count':1,'recovery_post_count':0,
                    'original_session':original_target,'current_session':current_target,'retry_widths':[390,1400]}
                record('superseded approval target opens the exact read-only history after restart and failed reads')

                stop_owner=app.state.conversation_creation.create(CreateConversation(
                    operation_id='qa_stop_owner',project_id=pid,title='阶段停止验证'))['conversation_ref']['id']
                meter_fixture=output/'stop-fixture-meter.py'
                meter_fixture.write_text('# Controlled executor fixture.\n')
                meter_cmd=[str(Path(sys.executable).resolve()),str(meter_fixture.resolve())]
                NativeCapabilities(recovery.store,recovery.data_dir).configure_mcp({
                    'id':'meter','operation_id':'stop-fixture-connector','expected_revision':0,
                    'configuration':{'type':'local','enabled':True,'command':meter_cmd,'timeout':3000,
                        'files_sha256':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in meter_cmd}}})
                recovery.define(pid,stop_owner,'qa_stop_definition',0,{
                    'id':'stopFixture','name':'停止验证','nodes':[{'id':'stop','name':'停止当前执行验证','kind':'custom',
                        'capabilities':{'mcp':['meter']}}]})
                stop_run=recovery.start(pid,stop_owner,'qa_stop_start','stopFixture')
                stop_session=stop_run['stages'][0]['stage_session_id']
                stop_gate=threading.Event()
                stop_calls, pending_tool_ids = [], []

                def stop_executor(context):
                    stop_calls.append(context['operation_id'])
                    stage_workspace.file_write(context,'partial.txt','保留这份记录','')
                    with stage_workspace.ledger(context['_data_dir']) as db:
                        pending_tool_ids.append(stage_workspace._record(db,context,'connector_call',server='meter',tool='observe',
                            status='unconfirmed',arguments_sha256='b'*64,config_ref={'id':'meter','revision':1})['id'])
                    assert stop_gate.wait(45),'Stop test did not release the worker'
                    return {'artifact':{'summary':'迟到成果不可采用。','items':[
                        {'id':'R1','title':'范围','description':'迟到'}]}}

                recovery.executor=stop_executor
                page.evaluate("id=>localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id}))",stop_session)
                page.reload(wait_until='domcontentloaded')
                expect(panel.get_by_role('heading',name='停止当前执行验证')).to_be_visible()
                button('执行此步').click()
                stop_button=button('停止本次执行')
                stop_dialog=panel.get_by_role('dialog',name='停止本次执行？',exact=True)
                stop_posts=[]

                def lose_stop_receipt(route):
                    stop_posts.append(route.request.post_data_json)
                    received=route.fetch()
                    assert received.ok
                    route.fulfill(status=503,json={'detail':'验收注入：停止已保存，响应丢失'})

                try:
                    for width in (1400,390):
                        page.set_viewport_size({'width':width,'height':745})
                        stop_button.scroll_into_view_if_needed()
                        start_layout_trace(('.wf-heading','.wf-footer'))
                        stop_button.press('Enter')
                        expect(stop_dialog).to_be_visible()
                        expect(stop_dialog.get_by_role('button',name='继续执行')).to_be_focused()
                        page.keyboard.press('Escape')
                        expect(stop_dialog).not_to_be_visible()
                        expect(stop_button).to_be_focused()
                        finish_layout_trace(f'stop-confirmation-{width}')
                    start_layout_trace(('.wf-heading','.wf-footer'))
                    stop_button.press('Enter')
                    expect(stop_dialog).to_be_visible()
                    page.route('**/actions',lose_stop_receipt,times=1)
                    stop_dialog.get_by_role('button',name='停止执行',exact=True).press('Enter')
                    expect(panel.get_by_text('已请求停止，正在收尾…',exact=True)).to_be_visible()
                    expect(stop_button).to_have_attribute('aria-disabled','true')
                    finish_layout_trace('stop-request-390')
                    page.reload(wait_until='domcontentloaded')
                    expect(panel.get_by_text('已请求停止，正在收尾…',exact=True)).to_be_visible()
                    stop_gate.set()
                    expect(panel.locator('.wf-status')).to_have_text('已停止')
                    button('查看本次记录').click()
                    records=panel.get_by_role('dialog',name='本次执行记录',exact=True)
                    expect(records.get_by_text('partial.txt',exact=True).first).to_be_visible()
                    expect(records.get_by_text('已停止',exact=True).first).to_be_visible()
                    expect(records.get_by_text('meter · observe',exact=True)).to_be_visible()
                    expect(records.get_by_text('MCP 调用 · 结果未确认',exact=True)).to_be_visible()
                    assert 'b'*64 not in records.inner_text()
                    expect(panel.get_by_text('迟到成果不可采用。',exact=True)).to_have_count(0)
                    assert len(stop_posts)==len(stop_calls)==1
                    assert stop_posts[0]['action']=='stop'
                    assert stop_posts[0]['payload']['operation_id']==stop_calls[0]
                    page.screenshot(path=str(output/'stopped-record-390.png'),animations='disabled')
                    info['workflow_stop']={'stop_posts':len(stop_posts),'model_attempts':len(stop_calls),
                        'original_operation':stop_calls[0],'evidence_path':'partial.txt'}
                    record('stop confirmation keeps layout and focus; lost reply reopens the same stop and preserves evidence')
                finally:
                    stop_gate.set()

                page.keyboard.press('Escape')
                retry=button('重试本步')
                retry_dialog=panel.get_by_role('dialog',name='重试前核对工具结果',exact=True)
                retry_calls,retry_posts=[],[]

                def observe_retry_request(request):
                    if request.method=='POST' and request.url.endswith('/runs/'+stop_run['id']+'/actions'):
                        retry_posts.append(request.post_data_json)

                def restored_executor(ctx):
                    retry_calls.append(ctx['operation_id'])
                    return {'artifact':{'summary':'核对后重新完成。','items':[{'id':'retry','title':'设备状态','description':'已检查'}]}}

                recovery.executor=restored_executor
                page.on('request',observe_retry_request)
                try:
                    for width in (1400,390):
                        page.set_viewport_size({'width':width,'height':745})
                        retry.scroll_into_view_if_needed()
                        start_layout_trace(('.wf-heading','.wf-stage-failure'))
                        retry.press('Enter')
                        expect(retry_dialog).to_be_visible()
                        expect(retry_dialog.get_by_text('meter · observe',exact=True)).to_be_visible()
                        expect(retry_dialog.get_by_role('button',name='先去核对')).to_be_focused()
                        page.screenshot(path=str(output/f'connector-retry-{width}.png'),animations='disabled')
                        page.keyboard.press('Escape')
                        expect(retry).to_be_focused()
                        finish_layout_trace(f'connector-retry-{width}')
                    assert not retry_posts and not retry_calls
                    page.reload(wait_until='domcontentloaded')
                    retry.press('Enter')
                    retry_dialog.get_by_role('button',name='已核对，继续执行').press('Enter')
                    expect(panel.get_by_text('核对后重新完成。',exact=True)).to_be_visible()
                    assert len(retry_calls)==len(retry_posts)==1
                    assert retry_posts[0]['action']=='execute'
                    assert retry_posts[0]['payload']['connector_retry_ack']=={
                        'operation_id':stop_calls[0],'evidence_ids':pending_tool_ids}
                    recorded=recovery.execution_record(pid,stop_session,stop_run['id'],'stop',stop_calls[0])
                    assert any(r['id']==pending_tool_ids[0] and r['status']=='unconfirmed' for r in recorded['evidence'])
                    info['connector_retry']={'cancel_posts':0,'accepted_posts':len(retry_posts),'original_status':'unconfirmed'}
                    record('restart retains unknown tool outcome; cancel never dispatches and confirmed retry binds the exact evidence')
                finally:
                    page.remove_listener('request',observe_retry_request)

                lineage_owner=app.state.conversation_creation.create(CreateConversation(
                    operation_id='qa_lineage_owner',project_id=pid,title='历史调用核对'))['conversation_ref']['id']
                recovery.define(pid,lineage_owner,'qa_lineage_definition',0,{'id':'connector-lineage','name':'历史调用核对',
                    'nodes':[{'id':'lineage','name':'新版工具核对','kind':'custom','capabilities':{'mcp':['meter']}}]})
                lineage=recovery.start(pid,lineage_owner,'qa_lineage_start','connector-lineage')
                previous_session=lineage['stages'][0]['stage_session_id']
                lineage_calls=[]
                def lineage_output(ctx):
                    lineage_calls.append(ctx['operation_id'])
                    if len(lineage_calls)==1:
                        with stage_workspace.ledger(ctx['_data_dir']) as db:
                            stage_workspace._record(db,ctx,'connector_call',server='meter',tool='observe',
                                status='unconfirmed',arguments_sha256='b'*64)
                    else:
                        assert not ctx['prior_evidence']
                    return {'artifact':{'summary':'现有成果保留。','items':[
                        {'id':'S1','title':'修订后核对','description':'保留已审核的业务内容。'}]},
                        'verified_evidence':stage_workspace.operation_receipts(ctx)}
                recovery.executor=lineage_output
                def lineage_action(owner,op,name,payload=None):
                    return recovery.action(pid,owner,lineage['id'],op,lineage['revision'],'lineage',name,payload or {})
                lineage=lineage_action(lineage_owner,'qa_lineage_original','execute')
                original_record=recovery.execution_record(pid,previous_session,lineage['id'],'lineage','qa_lineage_original')
                original_id,=[record['id'] for record in original_record['evidence']]
                lineage=lineage_action(previous_session,'qa_lineage_approved','approve')
                lineage=lineage_action(previous_session,'qa_lineage_revision','revise',{'text':'调整说明，保留已审核成果'})
                page.evaluate("id=>localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id}))",
                    lineage['stages'][0]['stage_session_id'])
                page.reload(wait_until='domcontentloaded')
                expect(panel.get_by_role('heading',name='新版工具核对')).to_be_visible()
                run_revision=button('按补充要求执行')
                lineage_posts=[]
                def observe_lineage(request):
                    if request.method=='POST' and request.url.endswith('/runs/'+lineage['id']+'/actions'):
                        lineage_posts.append(request.post_data_json)
                page.on('request',observe_lineage)
                try:
                    for width in (1400,390):
                        page.set_viewport_size({'width':width,'height':745})
                        run_revision.scroll_into_view_if_needed()
                        start_layout_trace(('.wf-heading','.wf-stage-main','.wf-footer'))
                        run_revision.press('Enter')
                        expect(retry_dialog.get_by_text('历史阶段会话',exact=True)).to_be_visible()
                        expect(retry_dialog.get_by_text('meter · observe',exact=True)).to_be_visible()
                        expect(retry_dialog.get_by_role('button',name='先去核对')).to_be_focused()
                        page.screenshot(path=str(output/f'connector-lineage-{width}.png'),animations='disabled')
                        page.keyboard.press('Escape')
                        expect(run_revision).to_be_focused()
                        finish_layout_trace(f'historical-connector-retry-{width}')
                    assert lineage_posts==[] and lineage_calls==['qa_lineage_original']
                    assert recovery.get(pid,lineage_owner,lineage['id'])==lineage
                    page.reload(wait_until='domcontentloaded')
                    run_revision.click()
                    retry_dialog.get_by_role('button',name='已核对，继续执行').click()
                    expect(panel.locator('.wf-status')).to_have_text('待审核')
                    assert len(lineage_posts)==1 and len(lineage_calls)==2
                    assert lineage_posts[0]['payload']['connector_retry_ack']=={'operation_id':None,'evidence_ids':[original_id]}
                    assert recovery.execution_record(pid,previous_session,lineage['id'],'lineage','qa_lineage_original')==original_record
                    info['connector_lineage']={'cancel_posts':0,'confirm_posts':1,'widths':[1400,390],'original_preserved':True}
                    record('revision shows the original connector receipt in place and confirms the exact history before execution')
                finally:
                    page.remove_listener('request',observe_lineage)

                reader_owner=app.state.conversation_creation.create(CreateConversation(
                    operation_id='qa_reading_owner',project_id=pid,title='阅读位置验证'))['conversation_ref']['id']
                recovery.define(pid,reader_owner,'qa_reading_definition',0,{
                    'id':'readingFixture','name':'阅读位置验证','nodes':[
                        {'id':'origin','name':'交接参考','kind':'custom'},
                        {'id':'requirements','name':'长需求清单','kind':'requirements','inputs':['origin']}]})
                reading_run=recovery.start(pid,reader_owner,'qa_reading_start','readingFixture')

                def reader_action(run,op,name,node,payload=None):
                    return recovery.action(pid,reader_owner,run['id'],op,run['revision'],node,name,payload or {})

                recovery.executor=lambda _: {'artifact':{'summary':'审阅范围已确认。','items':[
                    {'id':'S1','title':'参考','description':'阅读来源。'}]}}
                reading_run=reader_action(reading_run,'qa_reading_reference','execute','origin')
                reading_run=reader_action(reading_run,'qa_reading_reference_approval','approve','origin')
                reading_rows=[{'id':f'OR-{i:03d}','title':f'第 {i} 项独立要求','category':'functional',
                    'sub_description':'保持原成果关联', 'description':'\n'.join(['在工程接续时确认本条要求已经覆盖。']*18),
                    'design_spec':'\n'.join(['核对对应执行记录和审批版本。']*16),'constraints':'保留原审批',
                    'remarks':'复核后继续','source_ur':['UR-001'],'priority':'P1','acceptance':['逐项复核'],
                    'surfaces':['frontend']} for i in range(1,11)]
                recovery.executor=lambda _: {'artifact':{'summary':'当前编排。','items':reading_rows}}
                reading_run=reader_action(reading_run,'qa_reading_result','execute','requirements',{'user_input':{'ur':'需要逐项核对。'}})
                reader=reading_run['stages'][1]
                assert reader['status']=='review' and reader['operation_id']=='qa_reading_result'
                page.set_viewport_size({'width':1400,'height':910})
                page.evaluate("id=>localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id}))",reader['stage_session_id'])
                page.add_init_script("""window.readingResumePaint=[];const readResumeFrame=()=>{
                    const stage=document.querySelector('.wf-studio .wf-page[data-phase=requirements]');
                    const selected=stage?.querySelector('[data-workflow-target][aria-current=true]');
                    if(stage?.querySelector('h1')?.textContent==='长需求清单'&&selected&&window.readingResumePaint.length<16){
                        window.readingResumePaint.push([selected.dataset.workflowTarget,stage.closest('.wf-studio').scrollTop]);
                    }
                    if(window.readingResumePaint.length<16)requestAnimationFrame(readResumeFrame);
                    else document.documentElement.dataset.workflowReadingTrace='complete';
                };requestAnimationFrame(readResumeFrame);""")
                page.reload(wait_until='domcontentloaded')
                expect(panel.get_by_role('heading',name='长需求清单')).to_be_visible()
                choose_artifact('OR-006')
                design=panel.locator('[data-workflow-row="OR-006"] [data-field-name="design_spec"]')
                design.evaluate("""element=>{
                    const root=element.closest('.wf-studio');
                    root.scrollTop+=element.getBoundingClientRect().top-root.getBoundingClientRect().top+34;
                }""")
                page.wait_for_function("""()=>{
                    const items=JSON.parse(localStorage.getItem('cirven-workflow-reading-v1')||'[]');
                    return items.some(row=>row.operation==='qa_reading_result'&&row.row==='OR-006'&&row.field==='design_spec');
                }""")
                reading_top=panel.evaluate('el=>el.scrollTop')
                assert reading_top>400
                sidebar.get_by_text('交接参考',exact=True).click()
                expect(panel.get_by_role('heading',name='交接参考')).to_be_visible()
                sidebar.get_by_text('长需求清单',exact=True).click()
                expect(design).to_be_visible()
                assert abs(panel.evaluate('el=>el.scrollTop')-reading_top)<=2
                page.reload(wait_until='domcontentloaded')
                expect(design).to_be_visible()
                page.wait_for_function('() => window.readingResumePaint.length>=3')
                wide_resume=page.evaluate('window.readingResumePaint')
                assert all(ident=='OR-006' and abs(top-reading_top)<=2 for ident,top in wide_resume),wide_resume
                page.goto('about:blank')
                page.set_viewport_size({'width':390,'height':745})
                page.goto(f'http://127.0.0.1:{port}/agent-shell/',wait_until='domcontentloaded')
                expect(design).to_be_visible()
                page.wait_for_function('() => window.readingResumePaint.length>=3')
                compact_resume=page.evaluate('window.readingResumePaint')
                assert all(ident=='OR-006' and top>400 for ident,top in compact_resume),compact_resume
                assert design.evaluate("""element=>{
                    const r=element.getBoundingClientRect(),view=element.closest('.wf-studio').getBoundingClientRect();
                    return r.top<=view.top+view.height/3&&r.bottom>view.top+20;
                }""")
                page.screenshot(path=str(output/'reading-resume-390.png'),animations='disabled')
                button('展开目录').click()
                page.locator('dialog[open] .wf-artifact-index input[type=search]').fill('OR-006')
                page.set_viewport_size({'width':1400,'height':910})
                expect(panel.locator('.wf-artifact-index input[type=search]')).to_have_value('OR-006')
                expect(button('收起目录')).to_be_focused()
                page.set_viewport_size({'width':390,'height':745})
                expect(button('展开目录')).to_have_attribute('aria-expanded','false')
                button('展开目录').click()
                expect(page.locator('dialog[open] .wf-artifact-index input[type=search]')).to_have_value('OR-006')
                expect(page.locator('dialog[open] [data-workflow-target="OR-006"]')).to_have_attribute('aria-current','true')
                page.keyboard.press('Escape')
                design.evaluate("""element=>{
                    const root=element.closest('.wf-studio');root.scrollTop+=element.getBoundingClientRect().top-root.getBoundingClientRect().top+34;
                }""")
                page.wait_for_function("""()=>JSON.parse(localStorage.getItem('cirven-workflow-reading-v1')||'[]').some(
                    row=>row.operation==='qa_reading_result'&&row.row==='OR-006'&&row.field==='design_spec'&&row.compact===true)""")
                page.evaluate("sessionStorage.setItem('test-legacy-index', '1')")
                page.add_init_script("""if(location.origin!=='null'&&sessionStorage.getItem('test-legacy-index')){
                    sessionStorage.removeItem('test-legacy-index');
                    const key='cirven-workflow-reading-v1',items=JSON.parse(localStorage.getItem(key)||'[]');
                    const bookmark=items.find(row=>row.operation==='qa_reading_result'&&row.compact===true);
                    if(bookmark){bookmark.indexOpen=true;localStorage.setItem(key,JSON.stringify(items));}
                    window.legacyDirectoryBookmarkInjected=!!bookmark;
                    window.legacyDirectoryOpening=[];
                    const observe=()=>{const browser=document.querySelector('.wf-artifact-browser[data-compact=true]');
                        if(browser)window.legacyDirectoryOpening.push(browser.dataset.indexOpen);
                        if(window.legacyDirectoryOpening.length<12)requestAnimationFrame(observe);
                    };requestAnimationFrame(observe);
                }""")
                page.reload(wait_until='domcontentloaded')
                expect(design).to_be_visible()
                page.wait_for_function('() => window.legacyDirectoryOpening?.length===12')
                assert page.evaluate('window.legacyDirectoryBookmarkInjected') is True
                assert page.evaluate('window.legacyDirectoryOpening')==['false']*12
                assert panel.evaluate('element=>element.scrollTop')>400
                info['index_pane']['legacy_bookmark_frames']=page.evaluate('window.legacyDirectoryOpening')
                record('responsive directory keeps the current filter and row, and old bookmarks do not reopen it')
                continued_frames={}
                reader_output_executor=recovery.executor

                def failed_reader(_):
                    raise RuntimeError('审阅服务暂不可用。')

                for width,outcome,executor in (
                    (1400,'failed',failed_reader),
                    (390,'needs_input',lambda _: {'questions':['请确认审阅日期的时区。']}),
                ):
                    page.goto('about:blank')
                    page.set_viewport_size({'width':width,'height':910 if width==1400 else 745})
                    recovery.executor=executor
                    reading_run=reader_action(reading_run,'qa_reading_'+outcome,'execute','requirements',
                                              {'user_input':{'ur':'需要逐项核对。'}})
                    target=reading_run['stages'][1]
                    assert target['status']==outcome and target['artifact']==reader['artifact']
                    page.goto(f'http://127.0.0.1:{port}/agent-shell/',wait_until='domcontentloaded')
                    expect(panel.locator('.wf-stage-detail')).to_have_attribute('data-stage-status',outcome)
                    expect(page.locator('html')).to_have_attribute('data-workflow-reading-trace','complete')
                    opened=page.evaluate('window.readingResumePaint')
                    continued_frames[outcome]=opened
                    info['workflow_retained_output_reading']=continued_frames
                    assert all(ident=='OR-006' and top>400 for ident,top in opened),opened
                    expect(design).to_be_visible()
                    prior_top=panel.evaluate('el=>el.scrollTop')
                    if width==1400:
                        sidebar.get_by_text('交接参考',exact=True).click()
                    else:
                        panel.get_by_role('combobox',name='流程节点',exact=True).select_option('origin')
                    expect(panel.get_by_role('heading',name='交接参考')).to_be_visible()
                    if width==1400:
                        sidebar.get_by_text('长需求清单',exact=True).click()
                    else:
                        panel.get_by_role('combobox',name='流程节点',exact=True).select_option('requirements')
                    expect(design).to_be_visible()
                    assert abs(panel.evaluate('el=>el.scrollTop')-prior_top)<=2
                    assert recovery.get(pid,reader_owner,reading_run['id'])['revision']==reading_run['revision']
                recovery.executor=reader_output_executor
                record('failed attempts and clarification keep the original output’s reading bookmark on first paint and return')
                page.goto('about:blank')
                revised=reader_action(reading_run,'qa_reading_replacement','execute','requirements')
                assert revised['stages'][1]['operation_id']=='qa_reading_replacement'
                assert revised['stages'][1]['stage_session_id']==reader['stage_session_id']
                page.goto(f'http://127.0.0.1:{port}/agent-shell/',wait_until='domcontentloaded')
                expect(panel.locator('[data-workflow-row="OR-001"]')).to_be_visible()
                assert panel.evaluate('el=>el.scrollTop')==0
                info['workflow_reading_resume']={'original_operation':reader['operation_id'],
                    'revised_operation':revised['stages'][1]['operation_id'],'wide_frames':wide_resume,'compact_frames':compact_resume}
                record('per-version reading position restores on navigation and reload without a first-row flash')

                for width, outcome in ((1400,'failed'),(390,'needs_input')):
                    feedback_owner=app.state.conversation_creation.create(CreateConversation(
                        operation_id=f'qa_feedback_owner_{width}',project_id=pid,title='阶段反馈验证'))['conversation_ref']['id']
                    recovery.define(pid,feedback_owner,f'qa_feedback_definition_{width}',0,{
                        'id':f'feedback-{width}','name':'稳定反馈','nodes':[
                            {'id':'feedback','name':'成果反馈验证','kind':'custom'}]})
                    feedback_run=recovery.start(pid,feedback_owner,f'qa_feedback_start_{width}',f'feedback-{width}')
                    original_output={'summary':'正在阅读的原成果',
                        'items':[{'id':f'ROW-{i}','title':f'条目 {i}','description':'原始审阅记录与核对依据。' * 7} for i in range(12)]}
                    recovery.executor=lambda _: {'artifact':original_output}
                    feedback_run=recovery.action(pid,feedback_owner,feedback_run['id'],f'qa_feedback_original_{width}',
                        feedback_run['revision'],'feedback','execute',{})
                    session=feedback_run['stages'][0]['stage_session_id']
                    released=threading.Event()
                    def return_feedback(_):
                        assert released.wait(15),'Feedback fixture was not released'
                        if outcome=='failed':
                            raise RuntimeError('测试反馈：内核连接中断。')
                        return {'questions':['请确认现有审阅清单中日期的时区。']}

                    recovery.executor=return_feedback
                    page.set_viewport_size({'width':width,'height':785})
                    page.evaluate("id=>localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id}))",session)
                    page.reload(wait_until='domcontentloaded')
                    expect(panel.get_by_role('heading',name='成果反馈验证')).to_be_visible()
                    expect(panel.locator('.wf-summary')).to_have_text(original_output['summary'])
                    page.evaluate('''() => {
                        const studio=document.querySelector('.wf-studio');
                        studio.scrollTop=220;
                    }''')
                    panel.evaluate("""studio=>{
                        const stage=studio.querySelector('.wf-stage-detail');
                        const artifact=stage.querySelector('.wf-artifact');
                        const row=artifact.querySelector('[data-workflow-row]');
                        const read=()=>[stage.querySelector('.wf-heading'),artifact,row].flatMap(el=>{
                          const {x,y,width}=el.getBoundingClientRect();
                          return [x,y,width];
                        }).concat(studio.scrollTop);
                        const baseline=read(),failures=[];
                        let handle, frames=0;
                        const sample=()=>{
                          ++frames;
                          const current=read(),style=getComputedStyle(stage);
                          if(!artifact.isConnected||!row.isConnected||studio.querySelector('.wf-stage-detail')!==stage||
                             baseline.some((value,i)=>value!==current[i])||style.opacity!=='1'||style.visibility!=='visible'){
                            failures.push({current,status:stage.dataset.stageStatus});
                          }
                          handle=requestAnimationFrame(sample);
                        };
                        handle=requestAnimationFrame(sample);
                        window.finishFeedbackTrace=()=>{cancelAnimationFrame(handle);return {baseline,frames,failures};};
                    }""")
                    regenerate=button('重新生成')
                    regenerate.evaluate("el=>el.focus({preventScroll:true})")
                    try:
                        regenerate.press('Enter')
                        expect(panel.locator('.wf-stage-detail')).to_have_attribute('data-stage-status','running')
                        released.set()
                        expect(panel.locator('.wf-stage-detail')).to_have_attribute('data-stage-status',outcome)
                        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                        unchanged=page.evaluate('window.finishFeedbackTrace()')
                        info.setdefault('workflow_feedback_frames',{})[str(width)]=unchanged
                        assert unchanged['frames']>=8 and unchanged['baseline'][-1]>0 and not unchanged['failures'],unchanged
                    finally:
                        released.set()
                    status=panel.locator('button.wf-heading-status')
                    status.evaluate("el=>el.focus({preventScroll:true})")
                    status.press('Enter')
                    feedback=panel.get_by_role('dialog',name='本步反馈',exact=True)
                    expect(feedback).to_be_visible()
                    if outcome=='failed':
                        expect(feedback.get_by_role('alert')).to_contain_text('测试反馈：内核连接中断。')
                        details=feedback.get_by_role('button',name='查看本次记录')
                        details.press('Enter')
                        execution=panel.get_by_role('dialog',name='本次执行记录',exact=True)
                        expect(execution.get_by_text('这次没有保存工具回执。')).to_be_visible()
                        page.keyboard.press('Escape')
                        expect(details).to_be_focused()
                    else:
                        expect(feedback.get_by_text('请确认现有审阅清单中日期的时区。')).to_be_visible()
                        expect(feedback.get_by_role('button',name='在悬浮对话中补充')).to_be_visible()
                    page.screenshot(path=str(output / f'stage-feedback-{width}.png'),animations='disabled')
                    page.keyboard.press('Escape')
                    expect(status).to_be_focused()
                    assert panel.evaluate('el=>el.scrollTop')==unchanged['baseline'][-1]
                    if outcome=='needs_input':
                        status.press('Enter')
                        feedback.get_by_role('button',name='在悬浮对话中补充').click()
                        conversation_panel=panel.get_by_role('group',name='阶段对话面板')
                        composer=conversation_panel.locator('textarea[placeholder]')
                        expect(composer).to_be_focused()
                        composer.fill('日期使用北京时间。')
                        conversation_panel.get_by_role('button',name='发送要求').click()
                        expect(conversation_panel.get_by_text('日期使用北京时间。',exact=True)).to_be_visible()
                        conversation_panel.get_by_role('button',name='收起阶段对话').click()
                        expect(status).to_be_focused()
                        expect(panel.locator('.wf-summary')).to_have_text(original_output['summary'])
                        assert panel.evaluate('el=>el.scrollTop')==unchanged['baseline'][-1]
                        info['clarification_reply_persisted_in_stage']=True
                record('slow execution failures and clarifications preserve the review document, scroll and keyboard return')

                recovery.executor=reader_output_executor
                for width in (1400,390):
                    page.goto('about:blank')
                    page.set_viewport_size({'width':width,'height':745})
                    revised=recovery.action(pid,revised['stages'][1]['stage_session_id'],revised['id'],
                        f'qa_review_task_ur_{width}',revised['revision'],'requirements','supplement',
                        {'source_type':'requirement','text':'补充外部设备的联动验收。'})
                    revised=reader_action(revised,f'qa_review_task_output_{width}','execute','requirements')
                    stage=revised['stages'][1]
                    issue,=stage['approval_issues']
                    assert stage['status']=='review' and stage['artifact']==reader['artifact'] and 'UR-A' in issue
                    page.goto(f'http://127.0.0.1:{port}/agent-shell/',wait_until='domcontentloaded')
                    page.evaluate("id=>localStorage.setItem('eeas-current-thread-v1',JSON.stringify({version:1,id}))",
                        stage['stage_session_id'])
                    page.reload(wait_until='domcontentloaded')
                    expect(panel.locator('.wf-index-toggle')).to_be_visible()
                    choose_artifact('OR-006')
                    status=panel.get_by_role('button',name='审核待办',exact=True)
                    approval=button('审核通过并完成 →')
                    expect(approval).to_be_disabled()
                    status.evaluate("el=>el.focus({preventScroll:true})")
                    tracked=('.wf-heading','.wf-artifact','[data-workflow-row="OR-006"]','.wf-stage-composer','.wf-footer')
                    start_layout_trace(tracked)
                    status.press('Enter')
                    tasks=panel.get_by_role('dialog',name='审核待办',exact=True)
                    expect(tasks.get_by_text(issue,exact=True)).to_be_visible()
                    page.screenshot(path=str(output/f'approval-tasks-{width}.png'),animations='disabled')
                    page.keyboard.press('Escape')
                    expect(status).to_be_focused()
                    finish_layout_trace(f'approval-tasks-dialog-{width}')
                    conversation_panel=open_followup()
                    conversation_panel.locator('.wf-followups > summary').click()
                    withdraw=conversation_panel.get_by_role('button',name='撤回',exact=True)
                    expect(withdraw).to_be_visible()
                    start_layout_trace(tracked)
                    withdraw.click()
                    expect(panel.locator('.wf-heading-status')).to_have_text('待审核')
                    expect(approval).to_be_enabled()
                    page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
                    finish_layout_trace(f'approval-tasks-cleared-{width}')
                    revised=recovery.get(pid,reader_owner,revised['id'])
                    assert not revised['stages'][1]['approval_issues']
                    assert revised['stages'][1]['artifact']==stage['artifact']
                record('approval tasks use a stable status slot and modal and clear without moving the output or footer')

            except Exception:
                info['transition_events_at_failure']=page.evaluate('window.workflowAnimations')
                info['stage_paint_at_failure']=page.evaluate('window.workflowPaint')
                info['slow_gets_at_failure']=slow_mount['reads']
                info['arrival_at_failure']=panel.get_attribute('data-arrival') if panel.count() else None
                info['current_heading_at_failure']=page.locator('.wf-heading').all_text_contents()
                page.screenshot(path=str(output/'failed.png'))
                (output/'failure-dom.txt').write_text(page.locator('body').inner_text()[:26000])
                raise
            finally:browser.close()
        assert not errors,errors
        assert set(info['stable_shared_stage_overlay'])=={
            'init','requirements','alternatives','cases','design','development','testing','delivery','custom',
        },info['stable_shared_stage_overlay']
        assert all(widths==[1400,390] for widths in info['stable_shared_stage_overlay'].values())
        info['status']='PASS'
    finally:
        server.should_exit=True;thread.join(10)
        info.update(actions=actions,page_errors=errors,console=console,model_inputs=model_inputs)
        (output/'browser-results.json').write_text(json.dumps(info,ensure_ascii=False,indent=2))
    print(json.dumps(info,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--renderer',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();check(args.renderer.resolve(),args.out.resolve())

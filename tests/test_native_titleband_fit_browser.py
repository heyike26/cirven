"""Real component chrome regression; isolated evidence, no model or Windows inputs."""
import argparse
import functools
import hashlib
import http.server
import json
from legacy_evidence import internal_output
from pathlib import Path
import re
import threading
import time
from playwright.sync_api import sync_playwright

GEOMETRY = """() => {
 const rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width,height:r.height}};
 const button=document.querySelector('.as-directory-current'),b=rect(button);
 const visible=e=>{let r=rect(e);for(let p=e.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),q=rect(p);if(/hidden|clip|auto|scroll/.test(s.overflowX)){r.x=Math.max(r.x,q.x);r.right=Math.min(r.right,q.right)}if(/hidden|clip|auto|scroll/.test(s.overflowY)){r.y=Math.max(r.y,q.y);r.bottom=Math.min(r.bottom,q.bottom)}}return r};
 const leaves=[...button.children].map(e=>{const r=rect(e),style=getComputedStyle(e);return {tag:e.tagName,rect:r,effective:visible(e),display:style.display,visibility:style.visibility,rendered:r.width>0&&r.height>0&&style.display!=='none'&&!['hidden','collapse'].includes(style.visibility)}});
 const checks=[button,document.querySelector('.as-supervisor-command-trigger')].map(e=>{const r=visible(e),hit=document.elementFromPoint((r.x+r.right)/2,(r.y+r.bottom)/2);return {rect:rect(e),effective:r,hit:!!hit&&(e===hit||e.contains(hit))}});
 const brand=rect(document.querySelector('.as-titleband-leading>strong'));
 return {button:b,leaves,checks,brand,grid:getComputedStyle(document.querySelector('.as-titleband')).gridTemplateColumns,captionStart:innerWidth-148,task:rect(document.querySelector('.as-supervisor-command-trigger'))};
}"""

def validate(g):
    b=g['button']
    leaves=[leaf for leaf in g['leaves'] if leaf['rendered']]
    assert leaves, ('no visible directory descendants',g)
    for leaf in leaves:
        r=leaf['rect'];effective=leaf['effective']
        assert r['x'] >= b['x']-0.6 and r['right'] <= b['right']+0.6, ('directory descendant overflow',g)
        assert r['y'] >= b['y']-0.6 and r['bottom'] <= b['bottom']+0.6
        assert effective['x'] <= r['x']+0.6 and effective['right'] >= r['right']-0.6
        assert effective['y'] <= r['y']+0.6 and effective['bottom'] >= r['bottom']-0.6
    for a,b_leaf in zip(leaves,leaves[1:]):
        assert a['rect']['right'] <= b_leaf['rect']['x']+0.6, ('directory leaf overlap',g)
    assert g['brand']['right'] <= g['button']['x']+0.6
    assert g['button']['right'] <= g['task']['x']+0.6
    assert g['task']['right'] <= g['captionStart']+0.6
    for c in g['checks']:
        r,e=c['rect'],c['effective']
        assert c['hit'] and e['right']-e['x'] >= r['width']-0.6 and e['bottom']-e['y'] >= r['height']-0.6

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',required=True)
    parser.add_argument('--dist',required=True)
    parser.add_argument('--source-css',required=True)
    parser.add_argument('--baseline',action='store_true')
    parser.add_argument('--widths',default='769,780,920,1100,1286')
    args=parser.parse_args()
    out=Path(args.out).resolve();dist=Path(args.dist).resolve();css=Path(args.source_css).resolve()
    out = internal_output(out)
    out.mkdir(parents=True, exist_ok=True)
    production=re.search(r'href="(/agent-shell/assets/index-[^\"]+\.css)"', (dist/'index.html').read_text()).group(1)
    css_data=css.read_bytes();status_css=css.with_name('supervisor-command-center.css').read_bytes();(out/'subject.css').write_bytes(css_data+status_css)
    class Handler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith('/agent-shell/'):
                self.path='/production/'+self.path[len('/agent-shell/'):]
            return super().do_GET()
        def log_message(self,*a): pass
    (out/'production').symlink_to(dist,target_is_directory=True)
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(out)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    results=[];errors=[];started=time.monotonic()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                for lang in ['zh-CN','en']:
                    for width in [int(value) for value in args.widths.split(',')]:
                        for long in [False,True]:
                            page=browser.new_page(viewport={'width':width,'height':475})
                            page.on('pageerror',lambda error:errors.append(str(error)))
                            label=('验收原始长目录-Example-😀-'*8) if long else 'first-stage-b-r1'
                            roots=['D:\\Cirven-QA\\'+label,'D:\\Cirven-QA\\second-project']
                            workspaces=[dict(id='fixture-'+str(i),project_id='qa-project',revision=1,binding_sha256='b'*64,root=root,allowed_write_paths=[],native_location={'directory':root}) for i,root in enumerate(roots)]
                            now='2026-10-21T00:00:00Z'
                            snapshot=dict(settings=dict(enabled=True,notifications_enabled=True,interval_seconds=30,revision=1,sha256='a'*64),observer=dict(status='READY',last_attempt_at=now,last_success_at=now,actual_error_code=None,running=True),tasks=[dict(key=dict(kind='PLANNING',project_id='qa-project',object_id='qa-task',operation_id='qa-op',generation=1),label=('校验完整任务输入与原始资料 Long original task '*5) if long else '校验任务 Verify task',source_status='RUNNING',observed_at=now,actual_error_code=None,execution_returned=None,validation_status='NOT_RUN',wait_reason=None,coverage='SOURCE_SNAPSHOT',fresh_for_seconds=8640000,classification='RUNNING',observation_status='CURRENT',last_checked_at=now)],alerts=[],alerts_total=0,coverage='ONLY_COLLECTED_EXISTING_TASKS')
                            initial=json.dumps(dict(workspaces=workspaces,snapshot=snapshot),ensure_ascii=False)
                            html=f'<!doctype html><html lang="{lang}"><meta charset="utf-8"><link rel="stylesheet" href="{production}"><link rel="stylesheet" href="/subject.css"><body><div id="root"></div><script>localStorage.setItem("eeas.ui-language.v1",{json.dumps(lang)});window.qaInitial={initial};</script><script src="/fixture.js"></script></body></html>'
                            (out/'index.html').write_text(html)
                            page.goto(f'http://127.0.0.1:{server.server_port}/index.html');page.wait_for_selector('.as-directory-current')
                            page.evaluate('document.fonts.ready');page.wait_for_timeout(400)
                            g=page.evaluate(GEOMETRY)
                            status_checks=[]
                            if not args.baseline:
                                for classification,reason in [('RUNNING',None),('WAITING_USER','PERMISSION'),('EXECUTION_RETURNED',None),('TIMED_OUT',None),('EXECUTION_ERROR',None),('CANCEL_REQUESTED',None)]:
                                    snapshot['tasks'][0].update(classification=classification,wait_reason=reason)
                                    page.evaluate('(value)=>qa.publish(value)',snapshot)
                                    page.wait_for_timeout(180)
                                    status=page.locator('.as-supervisor-command-ticker').evaluate('(e)=>({text:e.textContent,leaves:[...e.children].map(n=>({client:n.clientWidth,scroll:n.scrollWidth}))})')
                                    if width>760:
                                        assert status['leaves'] and all(n['client']>0 and n['scroll']<=n['client']+1 for n in status['leaves']), ('summary clipped',classification,status)
                                    expected=('暂无进行中的任务' if lang=='zh-CN' else 'No active tasks') if classification=='EXECUTION_RETURNED' else ('待处理' if lang=='zh-CN' else 'Needs attention') if classification in ('WAITING_USER','TIMED_OUT','EXECUTION_ERROR') else ('运行中' if lang=='zh-CN' else 'Running')
                                    assert expected in status['text'], ('wrong aggregate',classification,status)
                                    trigger=page.locator('.as-supervisor-command-trigger')
                                    assert expected in trigger.get_attribute('title') and expected in trigger.get_attribute('aria-label')
                                    badge=page.locator('.as-supervisor-command-count')
                                    assert badge.is_visible()==(width<=760 and classification!='EXECUTION_RETURNED'), ('badge visibility',width,classification)
                                    assert snapshot['tasks'][0]['label'] not in status['text']
                                    status_checks.append(dict(classification=classification,badge_visible=badge.is_visible(),**status))
                                snapshot['tasks'][0].update(classification='RUNNING',wait_reason=None)
                                page.evaluate('(value)=>qa.publish(value)',snapshot)
                                page.wait_for_timeout(180)
                            name=f'{lang}-{width}-'+('long' if long else 'short')
                            try: validate(g)
                            except AssertionError as exc:
                                page.screenshot(path=str(out/(name+'-failure.png')))
                                results.append(dict(case=name,status='EXPECTED_BASELINE_FAILURE' if args.baseline else 'FAIL',geometry=g,reason=str(exc)))
                                if args.baseline:
                                    assert 'directory descendant overflow' in str(exc)
                                    return
                                raise
                            assert not args.baseline, 'Baseline unexpectedly passed'
                            page.screenshot(path=str(out/(name+'-initial.png')))
                            original=page.locator('#original-draft').element_handle();value=original.input_value()
                            trigger=page.locator('.as-directory-current');assert trigger.get_attribute('title')==roots[0]
                            trigger.click();menu=page.locator('.as-directory-menu');menu.wait_for(state='visible');page.wait_for_timeout(250)
                            assert menu.locator('button').count()==3
                            assert menu.locator('button').nth(0).get_attribute('title')==roots[0]
                            box=menu.bounding_box();assert box['x']>=0 and box['x']+box['width']<=width+0.6
                            page.keyboard.press('Escape');menu.wait_for(state='hidden')
                            page.wait_for_function("document.activeElement===document.querySelector('.as-directory-current')",timeout=3000)
                            trigger.click();menu.wait_for(state='visible');menu.locator('button').nth(1).click();menu.wait_for(state='hidden')
                            assert page.evaluate('qa.selected')==['fixture-1']
                            assert trigger.get_attribute('title')==roots[1]
                            task=page.locator('.as-supervisor-command-trigger');task.click();panel=page.locator('.as-supervisor-command-popover');panel.wait_for(state='visible');page.keyboard.press('Escape');panel.wait_for(state='hidden')
                            page.wait_for_function("document.activeElement===document.querySelector('.as-supervisor-command-trigger')",timeout=3000)
                            assert original.evaluate('(e)=>e===document.querySelector("#original-draft")') and original.input_value()==value
                            validate(page.evaluate(GEOMETRY))
                            page.screenshot(path=str(out/(name+'.png')))
                            results.append(dict(case=name,status='PASS',geometry=g,directory_select_once=True,menus_escape_focus=True,same_draft_dom=True,status_checks=status_checks))
                            page.close()
                assert not errors,errors
            finally: browser.close()
    finally:
        server.shutdown();server.server_close();thread.join()
        (out/'RESULT.json').write_text(json.dumps(dict(baseline=args.baseline,cases=results,errors=errors,elapsed_seconds=round(time.monotonic()-started,3),source_css_sha256=hashlib.sha256(css_data).hexdigest(),status_css_sha256=hashlib.sha256(status_css).hexdigest(),source_unchanged=css.read_bytes()==css_data and css.with_name('supervisor-command-center.css').read_bytes()==status_css,boundary='Real components and production AR7 CSS plus exact subject stylesheet; no App/API/model/Windows qualification',server_stopped=True),ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__': main()

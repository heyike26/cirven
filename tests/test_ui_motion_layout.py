"""Original E07 regression boundaries found by the independent visual reviewer."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import sync_playwright, expect

from test_web import launch_browser  # Reuse launch infrastructure only.


def evidence(name, value):
    folder = os.environ.get('EEAS_EVIDENCE_DIR')
    if folder: (Path(folder)/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


@pytest.fixture
def source_desk(live_server):
    with httpx.Client(base_url=live_server.origin,trust_env=False,timeout=15) as api:
        token=api.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'}).json()['csrf_token']
        api.headers.update({'Origin':live_server.origin,'X-EEAS-CSRF':token})
        p=api.post('/api/projects',json={'name':'原创阅读与运动回归','description':'独立观察后形成；无企业或模型调用'})
        p.raise_for_status();project=p.json()
        filename='任务书_控制终端与设备接口_'+ '完整原始来源版本和协同工程审阅_'*5+'.txt'
        raw=('第一行 原创来源与版本证据\n'+'只核对交互状态及长文件名下真实下载动作。\n'*180).encode()
        response=api.post(f"/api/projects/{project['id']}/documents",files={'file':(filename,raw,'text/plain')},data={'source':'原创回归'})
        response.raise_for_status();doc=response.json()
        yield {'origin':live_server.origin,'project':project,'doc':doc,'filename':filename,'raw':raw,'api':api}


@pytest.fixture
def visual(source_desk):
    with sync_playwright() as p:
        browser=launch_browser(p);context=browser.new_context(viewport={'width':1440,'height':900},reduced_motion='no-preference')
        page=context.new_page();errors=[];external=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.on('request',lambda r:external.append(r.url) if r.url.startswith(('http://','https://')) and not r.url.startswith(source_desk['origin']+'/') else None)
        yield page
        evidence('browser-'+page.url.split('view=')[-1].split('&')[0],{'pageerrors':errors,'external_requests':external,'browser_version':browser.version})
        context.close();browser.close()
        assert not errors and not external


def test_long_filename_keeps_original_download_visible_and_pointer_reachable(source_desk, visual):
    page=visual;desk=source_desk
    page.goto(desk['origin']+f"/?view=library&project={desk['project']['id']}")
    page.wait_for_load_state('networkidle');page.locator('#documents button').filter(has_text=desk['filename']).click()
    expect(page.locator('#reader-title')).to_have_text(desk['filename']);expect(page.locator('#document-download')).to_be_visible()
    response=desk['api'].get(page.locator('#document-download').get_attribute('href'))
    assert response.status_code==200 and hashlib.sha256(response.content).hexdigest()==hashlib.sha256(desk['raw']).hexdigest()
    observations=[]
    for width,height in [(1440,900),(900,650),(390,844)]:
        page.set_viewport_size({'width':width,'height':height})
        page.locator('.reader-heading').scroll_into_view_if_needed();page.wait_for_timeout(60)
        observed=page.evaluate('''()=>{
            let link=document.querySelector('#document-download'),heading=document.querySelector('.reader-heading'),reader=document.querySelector('.document-reader');
            let l=link.getBoundingClientRect(),h=heading.getBoundingClientRect(),r=reader.getBoundingClientRect();
            let hit=document.elementFromPoint(l.x+l.width/2,l.y+l.height/2);
            return {viewport:[innerWidth,innerHeight],link:{x:l.x,right:l.right,y:l.y,width:l.width},heading:{x:h.x,right:h.right},
                    reader:{x:r.x,right:r.right},pointer_reachable:hit===link||link.contains(hit),download_href:link.getAttribute('href')};}''')
        observations.append(observed)
        if os.environ.get('EEAS_EVIDENCE_DIR'): page.screenshot(path=str(Path(os.environ['EEAS_EVIDENCE_DIR'])/f'download-{width}.png'))
    evidence('original-download-layout',observations)
    assert all(o['link']['right']<=o['reader']['right']-1 and o['link']['x']>=o['reader']['x']+1 and o['pointer_reachable'] for o in observations), 'Valid long filename clips the original-download control on a supported viewport'


def navigation_interrupt(page):
    return page.evaluate('''async()=>{
        const indicator=document.querySelector('#nav-active-indicator');const y=()=>new DOMMatrix(getComputedStyle(indicator).transform).m42;
        document.querySelector('nav [data-view=settings]').click();const start=performance.now(),samples=[];
        while(performance.now()-start<60){await new Promise(requestAnimationFrame);samples.push({ms:performance.now()-start,y:y()});}
        const before=y();document.querySelector('nav [data-view=library]').click();const after=y();
        await new Promise(requestAnimationFrame);return {before,after,jump:Math.abs(after-before),next_frame:y(),samples,
        view:document.body.dataset.view,url:location.href,motion_count:indicator.dataset.motionCount};}''')


def test_navigation_indicator_interrupt_starts_at_actual_visible_position(source_desk, visual):
    page=visual;page.goto(source_desk['origin']);page.wait_for_load_state('networkidle')
    observation=navigation_interrupt(page);evidence('navigation-continuity',observation)
    assert observation['view']=='library' and 'view=library' in observation['url'] and int(observation['motion_count'])>=2
    assert observation['jump']<=1.0, 'Fast route reselection jumps to the previous target instead of continuing from the visible indicator'


def test_reducing_motion_during_navigation_finishes_at_selected_route(source_desk, visual):
    page=visual;page.goto(source_desk['origin']);page.wait_for_load_state('networkidle')
    observation=page.evaluate('''async()=>{
        const e=document.querySelector('#nav-active-indicator');document.querySelector('nav [data-view=settings]').click();
        await new Promise(requestAnimationFrame);await new Promise(requestAnimationFrame);
        const before={running:e.getAnimations().filter(a=>a.playState==='running').length,y:new DOMMatrix(getComputedStyle(e).transform).m42};
        const input=document.querySelector('#reduce-motion');input.checked=true;input.dispatchEvent(new Event('change',{bubbles:true}));
        await new Promise(requestAnimationFrame);return {before,reduced:document.body.classList.contains('reduce-motion'),
            running:e.getAnimations().filter(a=>a.playState==='running').length,y:new DOMMatrix(getComputedStyle(e).transform).m42,
            target:document.querySelector('nav [data-view=settings]').offsetTop,view:document.body.dataset.view};}''')
    evidence('reduce-during-navigation',observation)
    assert observation['before']['running']>0 and observation['reduced'] and observation['running']==0
    assert abs(observation['y']-observation['target'])<1 and observation['view']=='settings'

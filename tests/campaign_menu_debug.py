"""Read-only timing instrumentation around the unchanged source-bound workspace node."""
import json

from playwright.sync_api import sync_playwright

from campaign_browser_probes import Desk, test_ui_workspace as workspace_node
from test_web import launch_browser


TIMELINE = """() => {
  const events = window.__menuTimeline = [];
  const state = () => ({time:performance.now(),width:innerWidth,height:innerHeight,
    active:document.activeElement?.id || document.activeElement?.getAttribute('data-view') || document.activeElement?.tagName,
    sidebarOpen:document.body?.classList.contains('sidebar-open'),
    ariaExpanded:document.querySelector('#menu-toggle')?.getAttribute('aria-expanded'),
    inert:document.querySelector('#desktop-sidebar')?.inert,
    focusInside:!!document.querySelector('#desktop-sidebar')?.contains(document.activeElement)});
  const log = (type,event,extra={}) => events.push({type,...state(),trusted:event?.isTrusted,...extra});
  const breakpoint = matchMedia('(max-width:700px)');
  breakpoint.addEventListener('change', e => {log('trusted-mql-change',e,{matches:e.matches});
    queueMicrotask(()=>log('mql-microtask',e,{matches:e.matches}));
    requestAnimationFrame(()=>log('mql-frame',e,{matches:e.matches}));});
  window.addEventListener('resize',e=>log('resize',e));
  document.addEventListener('click',e=>{if(e.target.closest?.('#menu-toggle'))log('menu-click-before',e)},true);
  document.addEventListener('click',e=>{if(e.target.closest?.('#menu-toggle'))log('menu-click-after',e)});
  document.addEventListener('focusin',e=>log('focusin',e,{target:e.target.id || e.target.getAttribute('data-view') || e.target.tagName}));
  document.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key==='Escape'||e.key==='Tab')log('keydown',e,{key:e.key})},true);
  document.addEventListener('DOMContentLoaded',()=>new MutationObserver(changes=>{
    for(const c of changes)log('attribute-change',null,{attribute:c.attributeName,target:c.target.id || c.target.tagName,oldValue:c.oldValue});
  }).observe(document.body,{subtree:true,attributes:true,attributeOldValue:true,attributeFilter:['class','inert','aria-expanded']}),{once:true});
}"""


def test_public_workspace_records_actual_menu_breakpoint_events(tmp_path):
    owner = Desk("ui-workspace", tmp_path)
    try:
        owner.server.start()
        with sync_playwright() as playwright:
            browser = launch_browser(playwright)
            owner.version = browser.version
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, reduced_motion="no-preference")
            context.add_init_script("(" + TIMELINE + ")()")
            owner.context, owner.browser = context, browser
            owner.page = owner.attach(context.new_page())
            try:
                owner.import_source()
                workspace_node(owner)  # Original state/focus assertions unchanged.
            finally:
                owner.evidence.save("actual-menu-timeline.json", owner.page.evaluate("window.__menuTimeline"))
                owner.shot("actual-menu-final")
                context.close()
                browser.close()
    finally:
        owner.persist()

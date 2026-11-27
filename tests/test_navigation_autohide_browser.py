"""Original real-input probes for the floating navigation; no synthetic UI events."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3

import httpx
import pytest
from playwright.sync_api import expect, sync_playwright

from test_web import launch_browser


TIMELINE = """() => {
  const events=window.__navigationProbe=[];
  const snapshot=()=>({time:performance.now(),width:innerWidth,windowY:scrollY,
    hidden:document.body?.classList.contains('navigation-hidden'),
    mobileOpen:document.body?.classList.contains('sidebar-open'),
    active:document.activeElement?.id||document.activeElement?.getAttribute('data-view'),
    focusInside:!!document.querySelector('#desktop-sidebar')?.contains(document.activeElement),
    inert:document.querySelector('#desktop-sidebar')?.inert,
    expanded:document.querySelector('#navigation-reveal')?.getAttribute('aria-expanded')});
  const log=(type,e,extra={})=>events.push({type,trusted:e?.isTrusted,...snapshot(),...extra});
  matchMedia('(max-width:700px)').addEventListener('change',e=>{
    log('breakpoint',e,{matches:e.matches});
    requestAnimationFrame(()=>log('breakpoint-frame',e,{matches:e.matches}));
  });
  matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change',e=>{
    log('reduction',e,{matches:e.matches});
    requestAnimationFrame(()=>log('reduction-frame',e,{matches:e.matches}));
  });
  document.addEventListener('keydown',e=>log('key',e,{key:e.key}),true);
  document.addEventListener('focusin',e=>log('focus',e),true);
  document.addEventListener('scroll',e=>log('scroll',e,{
    target:e.target.id||e.target.className||'#document',
    targetY:e.target instanceof Element?e.target.scrollTop:scrollY}),true);
  document.addEventListener('DOMContentLoaded',()=>new MutationObserver(()=>log('state',null))
    .observe(document.body,{subtree:true,attributes:true,
      attributeFilter:['class','inert','aria-expanded']}),{once:true});
}"""


def keep(name, value):
    target = os.environ.get("EEAS_EVIDENCE_DIR")
    if target:
        with (Path(target) / name).open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")


def settle_motion(page):
    """Wait for actual finite animations, never an invented elapsed-time sleep."""
    page.evaluate("""async () => {
      await Promise.all(document.getAnimations().filter(a=>{
        const t=a.effect?.getComputedTiming();
        return a.playState==='running' && Number.isFinite(t?.duration) && t.iterations!==Infinity;
      }).map(a=>a.finished.catch(()=>{})));
    }""")


def state(page):
    return page.evaluate("""() => {
      const nav=document.querySelector('#desktop-sidebar'),r=nav.getBoundingClientRect();
      return {hidden:document.body.classList.contains('navigation-hidden'),
        expanded:document.querySelector('#navigation-reveal').getAttribute('aria-expanded'),
        y:scrollY,nav:{x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom},
        animations:nav.getAnimations().map(a=>({state:a.playState,
          duration:a.effect?.getComputedTiming().duration})),
        transition:getComputedStyle(nav).transitionDuration,
        focusInside:nav.contains(document.activeElement),inert:nav.inert};
    }""")


def wheel(page, delta, selector=None):
    """Observe a trusted scroll plus its rAF commit, then read the product state."""
    if selector:
        page.locator(selector).hover()
    else:
        page.mouse.move(page.viewport_size["width"] - 40, page.viewport_size["height"] - 45)
    page.evaluate("""selector => {
      const target=selector?document.querySelector(selector):document;
      const read=()=>selector?target.scrollTop:scrollY;
      const before=read();
      window.__actualWheelCommit=new Promise((resolve,reject)=>{
        const deadline=setTimeout(()=>{document.removeEventListener('scroll',observe,true);
          reject(new Error('TRUSTED_SCROLL_EVENT_NOT_DELIVERED_1000MS'));},1000);
        function observe(event){
          if(event.target!==target || read()===before)return;
          clearTimeout(deadline);document.removeEventListener('scroll',observe,true);
          requestAnimationFrame(()=>resolve({trusted:event.isTrusted,before,after:read(),
            target:selector||'#document'}));
        }
        document.addEventListener('scroll',observe,true);
      });
    }""", selector)
    page.mouse.wheel(0, delta)
    committed = page.evaluate("window.__actualWheelCommit")
    assert committed["trusted"] is True
    assert committed["after"] > committed["before"] if delta > 0 else committed["after"] < committed["before"]
    return committed


def arm_native_change(page, query):
    page.evaluate("""query => {
      const media=matchMedia(query);
      window.__actualNativeChange=new Promise((resolve,reject)=>{
        const deadline=setTimeout(()=>{media.removeEventListener('change',change);
          reject(new Error('TRUSTED_MEDIA_EVENT_NOT_DELIVERED_1000MS'));},1000);
        function change(event){clearTimeout(deadline);
          requestAnimationFrame(()=>resolve({trusted:event.isTrusted,matches:event.matches}));}
        media.addEventListener('change',change,{once:true});
      });
    }""", query)


@pytest.fixture
def navigation_desk(live_server):
    raw = ("# 原创导航滚动资料\n" + "".join(
        f"原创第 {index:04d} 行：核对版本与行号，阅读器内滚动不替换资料。\n" for index in range(1600)
    )).encode("utf-8")
    with httpx.Client(base_url=live_server.origin, trust_env=False, timeout=15) as api:
        bootstrap = api.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"})
        bootstrap.raise_for_status()
        api.headers.update({"Origin": live_server.origin, "X-EEAS-CSRF": bootstrap.json()["csrf_token"]})
        projects = []
        for index in range(14):
            response = api.post("/api/projects", json={"name": f"原创导航项目 {index:02d}"})
            response.raise_for_status()
            projects.append(response.json())
        response = api.post(f"/api/projects/{projects[-1]['id']}/documents",
                            files={"file": ("原创滚动原文.md", raw, "text/markdown")},
                            data={"source": "original: navigation observer fixture"})
        response.raise_for_status()
        document = response.json()
        assert document["sha256"] == hashlib.sha256(raw).hexdigest()
        yield {"origin": live_server.origin, "api": api, "project": projects[-1],
               "document": document, "raw_sha256": hashlib.sha256(raw).hexdigest()}
        with sqlite3.connect(live_server.data_dir / "eeas.sqlite3") as connection:
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("projects", "documents", "task_runs", "runner_jobs", "agent_executions")}
        assert counts == {"projects": 14, "documents": 1, "task_runs": 0,
                          "runner_jobs": 0, "agent_executions": 0}


@pytest.fixture
def navigation_page(navigation_desk, request):
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="no-preference")
        context.add_init_script("(" + TIMELINE + ")()")
        page = context.new_page()
        errors, external, network = [], [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda item: external.append(item.url) if item.url.startswith(("http://", "https://"))
                and not item.url.startswith(navigation_desk["origin"] + "/") else None)
        page.on("response", lambda response: network.append({"url": response.url, "status": response.status,
                                                             "method": response.request.method}))
        observations = []
        try:
            yield page, context, observations
        finally:
            stem = re.sub(r"[^A-Za-z0-9_.-]", "-", request.node.name)
            try:
                keep(stem + "-actual.json", {"observations": observations,
                     "timeline": page.evaluate("window.__navigationProbe||[]"), "final": state(page),
                     "page_errors": errors, "external_requests": external, "responses": network,
                     "browser_version": browser.version,
                     "scope": "Original input; actual API/SQLite/Chromium. No company or native Windows qualification."})
                target = os.environ.get("EEAS_EVIDENCE_DIR")
                if target:
                    page.screenshot(path=str(Path(target) / (stem + "-actual.png")), full_page=True)
            finally:
                context.close()
                browser.close()
            assert not errors and not external


def home(page, desk):
    page.goto(desk["origin"])
    page.wait_for_load_state("networkidle")
    page.evaluate("async () => { await document.fonts.ready; }")
    expect(page.locator("#home-project-count")).to_have_text("14")
    settle_motion(page)


@pytest.mark.parametrize("width", [720, 900, 1440])
def test_desktop_topnav_trusted_scroll_hover_keyboard_and_horizontal_targets(navigation_desk, navigation_page, width):
    page, _, observations = navigation_page
    page.set_viewport_size({"width": width, "height": 640})
    home(page, navigation_desk)
    geometry = page.locator("#desktop-sidebar").evaluate("""nav => {
      const r=nav.getBoundingClientRect();
      return {box:{x:r.x,y:r.y,width:r.width,height:r.height},
        links:[...nav.querySelectorAll('[data-view]')].map(a=>{
          const b=a.getBoundingClientRect();
          return {view:a.dataset.view,x:b.x,y:b.y,right:b.right,width:b.width,
            hit:document.elementFromPoint(b.x+b.width/2,b.y+b.height/2)?.closest('a')===a};
        })};
    }""")
    observations.append({"phase": "initial", "geometry": geometry, "state": state(page)})
    assert geometry["box"]["width"] >= width - 64
    assert len(geometry["links"]) == 8 and all(link["hit"] for link in geometry["links"])
    assert max(link["y"] for link in geometry["links"]) - min(link["y"] for link in geometry["links"]) <= 1
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    assert not state(page)["inert"]
    observations.append({"phase": "down", "event": wheel(page, 600), "state": state(page)})
    assert state(page)["hidden"] and state(page)["expanded"] == "false"
    settle_motion(page)
    assert state(page)["nav"]["bottom"] <= 0
    observations.append({"phase": "up", "event": wheel(page, -180), "state": state(page)})
    assert not state(page)["hidden"] and state(page)["expanded"] == "true"
    settle_motion(page)
    assert state(page)["nav"]["y"] >= 0
    wheel(page, 260)
    assert state(page)["hidden"]
    settle_motion(page)
    page.locator("#navigation-reveal").hover()
    assert not state(page)["hidden"]
    settle_motion(page)
    page.locator("#open-workbench").focus()
    wheel(page, 220)
    assert state(page)["hidden"]
    page.keyboard.press("Shift+Tab")
    expect(page.locator("#navigation-reveal")).to_be_focused()
    assert not state(page)["hidden"]
    settle_motion(page)
    page.keyboard.press("Enter")
    assert state(page)["focusInside"]
    page.locator("nav [data-view='library']").click()
    expect(page.locator("#library-zone")).to_be_visible()
    settle_motion(page)
    aligned = page.locator("#nav-active-indicator").evaluate("""indicator => {
      const a=indicator.getBoundingClientRect(),b=document.querySelector('nav [data-view=library]').getBoundingClientRect();
      return {x:a.x,y:a.y,width:a.width,height:a.height,target:{x:b.x,y:b.y,width:b.width,height:b.height}};
    }""")
    observations.append({"phase": "horizontal-current", "geometry": aligned, "state": state(page)})
    for key in ("x", "y", "width", "height"):
        assert abs(aligned[key] - aligned["target"][key]) <= 1
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")


def test_focused_nav_and_open_dialog_guard_then_real_reader_inner_scroll(navigation_desk, navigation_page):
    page, _, observations = navigation_page
    home(page, navigation_desk)
    page.locator("nav [data-view='projects']").focus()
    page.keyboard.press("Tab")
    assert state(page)["focusInside"]
    observations.append({"phase": "focused-nav-down", "event": wheel(page, 300), "state": state(page)})
    assert not state(page)["hidden"] and state(page)["focusInside"]
    page.locator("#new-project-secondary").click()
    expect(page.locator("#project-drawer")).to_be_visible()
    settle_motion(page)
    assert page.locator("#project-drawer").evaluate("e=>e.contains(document.activeElement)")
    before = state(page)
    page.mouse.move(30, page.viewport_size["height"] - 40)
    page.evaluate("""() => {
      window.__modalWheel=new Promise((resolve,reject)=>{
        const deadline=setTimeout(()=>{document.removeEventListener('wheel',observe,true);
          reject(new Error('TRUSTED_MODAL_WHEEL_NOT_DELIVERED_1000MS'));},1000);
        function observe(event){clearTimeout(deadline);document.removeEventListener('wheel',observe,true);
          requestAnimationFrame(()=>requestAnimationFrame(()=>resolve({trusted:event.isTrusted,
            deltaY:event.deltaY,windowY:scrollY,modalOpen:!!document.querySelector('dialog[open]')})));}
        document.addEventListener('wheel',observe,{capture:true,passive:true});
      });
    }""")
    page.mouse.wheel(0, 600)
    native_wheel = page.evaluate("window.__modalWheel")
    assert native_wheel["trusted"] and native_wheel["deltaY"] > 0 and native_wheel["modalOpen"]
    # A native modal may lock document scrolling; either way navigation stays present.
    assert not state(page)["hidden"]
    assert page.locator("#project-drawer").evaluate("e=>e.open && e.contains(document.activeElement)")
    observations.append({"phase": "modal-wheel", "native_wheel": native_wheel,
                         "before": before, "after": state(page)})
    page.keyboard.press("Escape")
    expect(page.locator("#project-drawer")).to_be_hidden()
    button = page.locator("#projects").get_by_role("button", name=navigation_desk["project"]["name"], exact=True)
    button.click()
    expect(page.locator("#project-context")).to_have_text(navigation_desk["project"]["name"])
    page.keyboard.press("Tab")
    page.locator("nav [data-view='library']").click()
    page.locator("#documents").get_by_role("button", name="原创滚动原文.md · v1", exact=True).click()
    expect(page.locator("#reader-title")).to_have_text("原创滚动原文.md")
    expect(page.locator("#reader-version")).to_contain_text("v1")
    paper = page.locator(".reader-paper")
    paper.hover()
    assert paper.evaluate("e=>e.scrollHeight>e.clientHeight+600")
    page.keyboard.press("Tab")
    settle_motion(page)
    window_y = page.evaluate("scrollY")
    observations.append({"phase": "reader-down", "event": wheel(page, 600, ".reader-paper"), "state": state(page)})
    assert state(page)["hidden"]
    assert page.evaluate("scrollY") == window_y
    observations.append({"phase": "reader-up", "event": wheel(page, -180, ".reader-paper"), "state": state(page)})
    assert not state(page)["hidden"]
    assert page.evaluate("scrollY") == window_y
    actual = navigation_desk["api"].get(
        f"/api/projects/{navigation_desk['project']['id']}/documents/{navigation_desk['document']['id']}",
        params={"revision": 1})
    actual.raise_for_status()
    assert actual.json()["sha256"] == navigation_desk["raw_sha256"]
    assert actual.json()["revision"] == 1
    expect(page.locator("#reader-title")).to_have_text("原创滚动原文.md")


def test_native_os_reduction_keeps_autohide_and_restore_static(navigation_desk, navigation_page):
    page, _, observations = navigation_page
    home(page, navigation_desk)
    arm_native_change(page, "(prefers-reduced-motion: reduce)")
    page.emulate_media(reduced_motion="reduce")
    native = page.evaluate("window.__actualNativeChange")
    assert native == {"trusted": True, "matches": True}
    wheel(page, 500)
    hidden = state(page)
    assert hidden["hidden"] and hidden["nav"]["bottom"] <= 0
    assert all(float(item.removesuffix("s")) == 0 for item in hidden["transition"].split(", "))
    assert not any(item["state"] == "running" for item in hidden["animations"])
    wheel(page, -180)
    restored = state(page)
    assert not restored["hidden"] and restored["nav"]["y"] >= 0
    assert not any(item["state"] == "running" for item in restored["animations"])
    observations.append({"phase": "native-reduced", "event": native, "hidden": hidden, "restored": restored})


def test_mobile_initial_inert_native_breakpoint_open_keyboard_and_desktop_close(navigation_desk, navigation_page):
    page, _, observations = navigation_page
    page.set_viewport_size({"width": 390, "height": 540})
    home(page, navigation_desk)
    assert page.locator("#desktop-sidebar").evaluate("e=>e.inert")
    assert page.locator("#navigation-reveal").evaluate("e=>getComputedStyle(e).display==='none'")
    arm_native_change(page, "(max-width:700px)")
    page.set_viewport_size({"width": 900, "height": 700})
    assert page.evaluate("window.__actualNativeChange") == {"trusted": True, "matches": False}
    assert not state(page)["inert"]
    arm_native_change(page, "(max-width:700px)")
    page.set_viewport_size({"width": 390, "height": 540})
    page.locator("#menu-toggle").focus()
    page.keyboard.press("Enter")
    # Immediate original focus semantics, before synchronizing native MQL delivery.
    assert state(page)["focusInside"] and not state(page)["inert"]
    native = page.evaluate("window.__actualNativeChange")
    assert native == {"trusted": True, "matches": True}
    assert state(page)["focusInside"] and not state(page)["inert"]
    expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "true")
    page.keyboard.press("Tab")
    assert state(page)["focusInside"]
    page.keyboard.press("Shift+Tab")
    assert state(page)["focusInside"]
    page.keyboard.press("Escape")
    expect(page.locator("#menu-toggle")).to_be_focused()
    assert state(page)["inert"]
    page.keyboard.press("Enter")
    arm_native_change(page, "(max-width:700px)")
    page.set_viewport_size({"width": 1440, "height": 900})
    desktop = page.evaluate("window.__actualNativeChange")
    assert desktop == {"trusted": True, "matches": False}
    assert not page.locator("body").evaluate("e=>e.classList.contains('sidebar-open')")
    assert not state(page)["inert"]
    expect(page.locator("#menu-toggle")).to_have_attribute("aria-expanded", "false")
    assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
    observations.append({"phase": "mobile-open-current-intent", "native": native, "desktop": desktop,
                         "state": state(page)})

"""Native navigation counterexample and desktop contract migration; not yet run.

The late document capture listener models an existing business focus restore,
after the product's scroll listener has queued its rAF work. No event dispatch,
fake clock, scroll assignment, class mutation or model transport is used.
"""
import sqlite3

from playwright.sync_api import expect

from test_navigation_autohide_browser import (
    home,
    keep,
    navigation_desk,
    navigation_page,
    settle_motion,
    state,
    wheel,
)


def four_execution_tables(data_dir):
    database = data_dir / "eeas.sqlite3"
    assert database.is_file()
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("tasks", "task_runs", "runner_jobs", "agent_executions")}


def test_native_business_focus_restore_invalidates_same_frame_pending_hide(
    navigation_desk, navigation_page, live_server
):
    page, _, observations = navigation_page
    home(page, navigation_desk)
    before = four_execution_tables(live_server.data_dir)
    assert before == {"tasks": 0, "task_runs": 0, "runner_jobs": 0, "agent_executions": 0}
    assert not state(page)["hidden"]
    target = page.locator("#projects button[data-project-id]").first
    target_id = target.get_attribute("data-project-id")
    assert target_id
    tasks = navigation_desk["api"].get(f"/api/projects/{target_id}/tasks")
    tasks.raise_for_status()
    assert tasks.json()["tasks"] == []

    page.evaluate("""() => {
      const target=document.querySelector('#projects button[data-project-id]');
      const nav=document.querySelector('#desktop-sidebar');
      if(!target || !document.querySelector('#main-content').contains(target)
          || target.disabled || document.activeElement===target)
        throw new Error('REAL_MAIN_PROJECT_FOCUS_TARGET_REQUIRED');
      const before=scrollY;
      const proof=window.__navigationFocusRaceProof={events:[],
        source:'original native wheel + standard business HTMLElement.focus restore',
        targetProjectId:target.dataset.projectId};
      const snapshot=(phase,event)=>({phase,time:performance.now(),trusted:event?.isTrusted,
        y:scrollY,hidden:document.body.classList.contains('navigation-hidden'),
        expanded:document.querySelector('#navigation-reveal').getAttribute('aria-expanded'),
        focusIsTarget:document.activeElement===target,
        activeProjectId:document.activeElement?.dataset?.projectId,
        navTop:nav.getBoundingClientRect().top});
      window.__navigationFocusRace=new Promise((resolve,reject)=>{
        // A delivery watchdog only; the observations use real native events/rAF.
        const deadline=setTimeout(()=>{
          cleanup();reject(new Error('NATIVE_SCROLL_FOCUS_COMMIT_NOT_DELIVERED_1000MS'));
        },1000);
        function cleanup(){
          clearTimeout(deadline);
          document.removeEventListener('scroll',observe,true);
          document.removeEventListener('focusin',focus,true);
          document.removeEventListener('wheel',recordWheel,true);
        }
        function focus(event){
          if(event.target===target)proof.events.push(snapshot('native-focus',event));
        }
        function recordWheel(event){proof.events.push(snapshot('native-wheel',event));}
        function observe(event){
          if(event.target!==document || scrollY<=before)return;
          // This capture listener was registered after the loaded product listener.
          // It handles exactly one matching, actual document scroll event.
          document.removeEventListener('scroll',observe,true);
          proof.events.push(snapshot('scroll-after-product-listener',event));
          target.focus({preventScroll:true});
          proof.events.push(snapshot('after-business-focus',event));
          requestAnimationFrame(()=>{
            proof.events.push(snapshot('after-product-rAF',event));
            const finite=nav.getAnimations().filter(animation=>{
              const timing=animation.effect?.getComputedTiming();
              return animation.playState==='running' && Number.isFinite(timing?.duration)
                && timing.iterations!==Infinity;
            });
            Promise.all(finite.map(animation=>animation.finished.catch(()=>{}))).then(()=>{
              requestAnimationFrame(()=>{
                proof.events.push(snapshot('settled-native-frame',event));
                cleanup();resolve(proof);
              });
            });
          });
        }
        document.addEventListener('focusin',focus,true);
        document.addEventListener('wheel',recordWheel,true);
        document.addEventListener('scroll',observe,true);
      });
    }""")

    proof = None
    try:
        committed = wheel(page, 500)
        proof = page.evaluate("window.__navigationFocusRace")
    finally:
        captured = page.evaluate("window.__navigationFocusRaceProof||null")
        keep("native-business-focus-race-proof.json", captured)
        observations.append({"phase": "native-business-focus-race", "proof": captured})
        keep("native-business-focus-race-observed-store.json",
             {"before": before, "observed": four_execution_tables(live_server.data_dir)})
    assert proof is not None
    assert committed["trusted"] is True and committed["after"] > committed["before"]
    assert proof["targetProjectId"] == target_id
    events = proof["events"]
    focus = [event for event in events if event["phase"] == "native-focus"]
    native_wheel = [event for event in events if event["phase"] == "native-wheel"]
    assert len(focus) == 1 and focus[0]["trusted"] is True and focus[0]["focusIsTarget"]
    assert native_wheel and all(event["trusted"] is True for event in native_wheel)
    phases = {event["phase"]: event for event in events}
    assert phases["scroll-after-product-listener"]["trusted"] is True
    assert phases["after-business-focus"]["focusIsTarget"]
    assert phases["after-business-focus"]["y"] == phases["scroll-after-product-listener"]["y"]
    assert not phases["after-business-focus"]["hidden"]
    assert phases["after-product-rAF"]["focusIsTarget"]
    assert not phases["after-product-rAF"]["hidden"]
    assert phases["after-product-rAF"]["expanded"] == "true"
    assert not phases["settled-native-frame"]["hidden"]
    assert phases["settled-native-frame"]["focusIsTarget"]
    settle_motion(page)
    assert not state(page)["hidden"]
    after = four_execution_tables(live_server.data_dir)
    keep("native-business-focus-race-store-counts.json", {"before": before, "after": after})
    assert after == before
    tasks = navigation_desk["api"].get(f"/api/projects/{target_id}/tasks")
    tasks.raise_for_status()
    assert tasks.json()["tasks"] == []


def test_desktop_480_topnav_reachability_and_native_keyboard_reconnect(
    navigation_desk, navigation_page, live_server
):
    """Reachability replaces the historical desktop vertical-overflow premise.

    The old sidebar test stays unchanged. This node does not manufacture scroll
    overflow: it checks eight actual entry points and a real GET-only reconnect.
    """
    page, _, observations = navigation_page
    page.set_viewport_size({"width": 1440, "height": 480})
    home(page, navigation_desk)
    before = four_execution_tables(live_server.data_dir)
    assert before == {"tasks": 0, "task_runs": 0, "runner_jobs": 0, "agent_executions": 0}
    with sqlite3.connect((live_server.data_dir / "eeas.sqlite3").as_uri() + "?mode=ro", uri=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    projects_before = navigation_desk["api"].get("/api/projects")
    projects_before.raise_for_status()
    assert len(projects_before.json()["projects"]) == 14

    geometry = page.locator("#desktop-sidebar").evaluate("""nav => ({
      width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth,
      bodyWidth:document.body.scrollWidth,clientWidth:nav.clientWidth,scrollWidth:nav.scrollWidth,
      links:[...nav.querySelectorAll('nav a[data-view]')].map(link=>{
        const box=link.getBoundingClientRect();
        return {view:link.dataset.view,label:link.textContent.trim(),href:link.getAttribute('href'),
          x:box.x,y:box.y,right:box.right,bottom:box.bottom,width:box.width,height:box.height,
          centerY:box.y+box.height/2,
          hit:link.contains(document.elementFromPoint(box.x+box.width/2,box.y+box.height/2))};
      })})""")
    keep("desktop-480-topnav-geometry.json", geometry)
    observations.append({"phase": "desktop-480-topnav", "geometry": geometry})
    expected = {"projects": "主页", "library": "资料库", "requirements": "需求与计划",
                "tasks": "任务中心", "results": "运行与结果", "approvals": "审批收件箱",
                "capabilities": "扩展管理", "settings": "设置"}
    assert len(geometry["links"]) == 8
    assert {link["view"]: link["label"] for link in geometry["links"]} == expected
    assert geometry["documentWidth"] <= 1440 and geometry["bodyWidth"] <= 1440
    assert geometry["scrollWidth"] <= geometry["clientWidth"]
    centers = [link["centerY"] for link in geometry["links"]]
    assert max(centers) - min(centers) <= 1
    for link in geometry["links"]:
        assert link["href"].startswith("#")
        assert link["width"] > 0 and link["height"] > 0 and link["hit"]
        assert 0 <= link["x"] < link["right"] <= 1440
        assert 0 <= link["y"] < link["bottom"] <= 480

    page.evaluate("""() => {
      const events=window.__desktop480Keyboard=[];
      for(const type of ['keydown','focusin','click'])document.addEventListener(type,event=>{
        const target=event.target.closest?.('#desktop-sidebar a, #desktop-sidebar button');
        if(target)events.push({type,trusted:event.isTrusted,key:event.key,
          id:target.id,view:target.dataset.view||null,detail:event.detail??null,time:performance.now()});
      },true);
    }""")
    settings = page.locator("#desktop-sidebar nav a[data-view]").last
    assert settings.get_attribute("data-view") == "settings"
    settings.focus()
    expect(settings).to_be_focused()
    page.keyboard.press("Tab")
    switch = page.locator("#sidebar-projects")
    expect(switch).to_be_focused()
    page.keyboard.press("Tab")
    retry = page.locator("#retry-connection")
    expect(retry).to_be_focused()
    expect(retry).to_have_attribute("type", "button")
    expect(retry).to_have_accessible_name("重试连接")
    expect(switch).to_have_accessible_name("切换项目")
    retry_geometry = retry.evaluate("""button=>{
      const box=button.getBoundingClientRect();
      return {tag:button.tagName,disabled:button.disabled,focused:document.activeElement===button,
        x:box.x,y:box.y,right:box.right,bottom:box.bottom,
        hit:button.contains(document.elementFromPoint(box.x+box.width/2,box.y+box.height/2))};
    }""")
    keep("desktop-480-retry-geometry.json", retry_geometry)
    assert retry_geometry["tag"] == "BUTTON" and not retry_geometry["disabled"]
    assert retry_geometry["focused"] and retry_geometry["hit"]
    assert 0 <= retry_geometry["x"] < retry_geometry["right"] <= 1440
    assert 0 <= retry_geometry["y"] < retry_geometry["bottom"] <= 480

    requests = []
    def capture(request):
        if request.url.startswith(navigation_desk["origin"] + "/api/"):
            requests.append({"url": request.url, "method": request.method})
    page.on("request", capture)
    try:
        origin = navigation_desk["origin"]
        with page.expect_response(lambda response: response.url == origin + "/api/projects") as projects_response, \
             page.expect_response(lambda response: response.url == origin + "/api/runtime") as runtime_response, \
             page.expect_response(lambda response: response.url == origin + "/api/runner") as runner_response, \
             page.expect_response(lambda response: response.url == origin + "/api/extensions") as extensions_response:
            page.keyboard.press("Enter")
        responses = {}
        for name, pending in (("projects", projects_response), ("runtime", runtime_response),
                              ("runner", runner_response), ("extensions", extensions_response)):
            response = pending.value
            assert response.status == 200 and response.request.method == "GET"
            responses[name] = {"url": response.url, "status": response.status, "body": response.json()}
        assert responses["projects"]["body"] == projects_before.json()
        expect(page.locator("#connection-state")).to_have_text("已连接本地服务")
        expect(page.locator("#connection-state")).to_have_attribute("data-state", "ready")
        expect(page.locator("#capability-summary")).not_to_have_text("正在读取实际版本")
        expect(page.locator("#home-project-count")).to_have_text("14")
        expect(page.locator("#error")).to_have_text("")
        health = navigation_desk["api"].get("/health")
        health.raise_for_status()
        assert health.json()["status"] == "ok" and health.json()["storage"] == "sqlite"
        keep("desktop-480-reconnect-responses.json", {"responses": responses, "health": health.json()})
    finally:
        page.remove_listener("request", capture)
        keyboard = page.evaluate("window.__desktop480Keyboard")
        keep("desktop-480-native-keyboard-and-requests.json", {"events": keyboard, "requests": requests})
        observations.append({"phase": "desktop-480-native-reconnect", "events": keyboard, "requests": requests})
    keys = [event for event in keyboard if event["type"] == "keydown"]
    assert [(event["key"], event["id"], event["view"]) for event in keys] == [
        ("Tab", "", "settings"), ("Tab", "sidebar-projects", None), ("Enter", "retry-connection", None)]
    assert all(event["trusted"] is True for event in keyboard)
    clicks = [event for event in keyboard if event["type"] == "click"]
    assert len(clicks) == 1 and clicks[0]["id"] == "retry-connection" and clicks[0]["detail"] == 0
    assert requests and all(request["method"] == "GET" for request in requests)
    settle_motion(page)
    assert not state(page)["hidden"]
    after = four_execution_tables(live_server.data_dir)
    with sqlite3.connect((live_server.data_dir / "eeas.sqlite3").as_uri() + "?mode=ro", uri=True) as connection:
        approvals = connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0]
    keep("desktop-480-store-counts.json", {"before": before, "after": after, "approvals": approvals})
    assert after == before and approvals == 0

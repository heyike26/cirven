"""Actual compact Orb HTML/preload in Chromium; Electron IPC is the explicit UNIT boundary.

No native focus, Windows rendering, model or desktop qualification is claimed.
"""
import asyncio
import json
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / ".node-tools/node_modules/node/bin/node"
EXPORT = r"""
const fs=require('node:fs'),path=require('node:path'),root=process.argv[1];
const {orbHtml,arcLayout,noticeLayout,AssistantOrb}=require(path.join(root,'apps/desktop-host/assistant-orb.cjs'));
const {stableOrbGeometry}=require(path.join(root,'apps/desktop-host/orb-geometry.cjs'));
const cases=[];
for(const[edge,area,px,py]of[
 ['NW',{x:0,y:0,width:380,height:720},0,0],['SE',{x:0,y:0,width:380,height:720},1,1],
 ['DEFAULT',{x:0,y:0,width:380,height:720},24/324,640/664],
 ['CENTER',{x:-1536,y:-128,width:1536,height:864},.5,.5]]){
 const bubble={x:Math.round(area.x+(area.width-56)*px),y:Math.round(area.y+(area.height-56)*py),width:56,height:56},arc=arcLayout(bubble,area);
 if(!arc)throw Error('Real four-action arc unavailable');
 const notice=noticeLayout(bubble,area);
 if(!notice)throw Error('Real bounded notice unavailable');
 // The actual fixed client includes all three roles even when only the arc
 // is visible. Electron/HWND lifetime is outside this DOM/preload test.
 const geometry={...stableOrbGeometry({bubble,area,arc,notice}),arc,notice};
 const layout=AssistantOrb.prototype._layout.call({_geometry:()=>geometry,_noticeEligible:()=>false,_noticeBounds:()=>notice});
 cases.push({area,edge,bubble,arc,bounds:geometry.bounds,state:{visible:true,expanded:true,busy:false,layout}});
}
console.log(JSON.stringify({html:{'zh-CN':orbHtml('zh-CN'),en:orbHtml('en')},preload:fs.readFileSync(path.join(root,'apps/desktop-host/assistant-orb-preload.cjs'),'utf8'),cases}));
"""
METRICS = r"""()=>{
 const rect=e=>e.getBoundingClientRect().toJSON(),orb=document.querySelector('#orb'),o=rect(orb);
 const actions=[...document.querySelectorAll('.action')].map(e=>{
  const b=rect(e),t=e.querySelector('span'),r=document.createRange();r.selectNodeContents(t);const ink=r.getBoundingClientRect(),cx=b.x+b.width/2,cy=b.y+b.height/2;
  const clearance=q=>b.width/2-Math.max(...[q.left,q.right].flatMap(x=>[q.top,q.bottom].map(y=>Math.hypot(x-cx,y-cy))));
  const icon=rect(e.querySelector('svg'));return{action:e.dataset.action,label:t.textContent,title:e.title,hasTitle:e.hasAttribute('title'),dataUiTitle:e.getAttribute('data-ui-title'),aria:e.getAttribute('aria-label'),rect:b,ink:ink.toJSON(),clearance:clearance(ink),iconClearance:clearance(icon),font:getComputedStyle(t).fontSize,
   cornerHits:[[1,1],[b.width-1,1],[1,b.height-1],[b.width-1,b.height-1]].map(([x,y])=>document.elementFromPoint(b.x+x,b.y+y)?.closest('.action')?.dataset.action??null)};
 });
 let insideSamples=0,outsideSamples=0,wrongInside=0,wrongOutside=0,overlapSamples=0;
 for(let x=.5;x<innerWidth;x+=2)for(let y=.5;y<innerHeight;y+=2){
  const ds=actions.map(a=>Math.hypot(x-a.rect.x-a.rect.width/2,y-a.rect.y-a.rect.height/2)-a.rect.width/2);
  const inside=actions.filter((a,i)=>ds[i]<-.75),hit=document.elementFromPoint(x,y)?.closest('.action')?.dataset.action;
  if(inside.length>1)overlapSamples++;
  if(inside.length===1){insideSamples++;if(hit!==inside[0].action)wrongInside++;}
  if(ds.every(d=>d>.75)){outsideSamples++;if(hit)wrongOutside++;}
 }
 return{orb:o,orbAccessibility:{title:orb.title,hasTitle:orb.hasAttribute('title'),dataUiTitle:orb.getAttribute('data-ui-title'),aria:orb.getAttribute('aria-label'),description:orb.getAttribute('aria-description')},actions,insideSamples,outsideSamples,wrongInside,wrongOutside,overlapSamples};
}"""


def require(value, detail):
    if not value:
        raise AssertionError(detail)


async def scenario(tmp):
    exported = subprocess.run([str(NODE), "-e", EXPORT, str(ROOT)], capture_output=True, text=True, timeout=10)
    require(exported.returncode == 0, exported.stderr)
    data = json.loads(exported.stdout)
    rows = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            for locale in ["zh-CN", "en"]:
                for index, item in enumerate(data["cases"]):
                    bounds = item["bounds"]
                    area = item["area"]
                    require(bounds["x"] >= area["x"] and bounds["y"] >= area["y"]
                            and bounds["x"] + bounds["width"] <= area["x"] + area["width"]
                            and bounds["y"] + bounds["height"] <= area["y"] + area["height"], item)
                    page = await browser.new_page(viewport={"width": bounds["width"], "height": bounds["height"]}, device_scale_factor=[1, 1.5, 1.25, 2.24][index], bypass_csp=True)
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    await page.set_content(data["html"][locale])
                    await page.evaluate(r"""s=>{window.__state=s;window.__events={};window.__calls=[];window.require=n=>{
                     if(n!=='electron')throw Error('Only the declared preload port is available');return{ipcRenderer:{on:(c,f)=>__events[c]=f,invoke:async(c,p)=>{__calls.push(p);return p.kind==='state'?__state:{status:'ACCEPTED'}}}}}}
                    """, {**item["state"], "locale": locale})
                    await page.add_script_tag(content=data["preload"])
                    await page.evaluate("window.dispatchEvent(new Event('DOMContentLoaded'))")
                    # Position is immediate; only scale/opacity animate. A
                    # fast user must not encounter circles flying through
                    # each other's hit regions during the opening motion.
                    await page.wait_for_timeout(60)
                    entering = await page.evaluate(METRICS)
                    require(not any(entering[key] for key in ["wrongInside", "wrongOutside", "overlapSamples"]), entering)
                    for action in entering["actions"]:
                        b = action["rect"]
                        x = b["x"] + b["width"] / 2 - entering["orb"]["x"] - 24
                        y = b["y"] + b["height"] / 2 - entering["orb"]["y"] - 24
                        require(abs((x*x + y*y)**.5 - item["arc"]["radius"]) < .05, {"entering": entering, "arc": item["arc"]})
                    await page.locator(".action").evaluate_all("es=>Promise.all(es.flatMap(e=>e.getAnimations().map(a=>a.finished.catch(()=>{}))))")
                    actual = await page.evaluate(METRICS)
                    orb = actual["orb"]
                    require(abs(bounds["x"] + orb["x"] + 24 - item["bubble"]["x"] - 28) < .05, actual)
                    require(abs(bounds["y"] + orb["y"] + 24 - item["bubble"]["y"] - 28) < .05, actual)
                    require(abs(orb["width"] - 48) < .05, actual)
                    require(item["arc"]["radius"] == (80 if item["edge"] == "CENTER" else 88 if item["edge"] == "DEFAULT" else 112), item)
                    require(actual["insideSamples"] > 2000 and actual["outsideSamples"] > 1000, actual)
                    require(not any(actual[key] for key in ["wrongInside", "wrongOutside", "overlapSamples"]), actual)
                    for action, offset in zip(actual["actions"], item["arc"]["offsets"]):
                        b = action["rect"]
                        require(abs(b["width"] - 56) < .05 and abs(b["height"] - 56) < .05, action)
                        require(b["left"] >= -.05 and b["top"] >= -.05 and b["right"] <= bounds["width"] + .05 and b["bottom"] <= bounds["height"] + .05, action)
                        require(action["clearance"] >= 4 and action["iconClearance"] >= 4, action)
                        expected_aria = {
                            "zh-CN": {"annotate": "批注", "tasks": "任务", "show-agent": "对话", "workbench": "工作台"},
                            "en": {"annotate": "Annotate", "tasks": "Tasks", "show-agent": "Chat", "workbench": "Workbench"},
                        }
                        require(action["font"] == "12px" and action["aria"] == expected_aria[locale][action["action"]], action)
                        # HTML title creates a separate native tooltip HWND,
                        # which can cover an adjacent circular action.
                        require(action["title"] == "" and not action["hasTitle"] and action["dataUiTitle"] is None, action)
                        require(all(hit != action["action"] for hit in action["cornerHits"]), action)
                        require(abs(b["x"] + 28 - orb["x"] - 24 - offset["dx"]) < .05 and abs(b["y"] + 28 - orb["y"] - 24 - offset["dy"]) < .05, action)
                    require(actual["actions"][-1]["label"] == ("工作台" if locale == "zh-CN" else "Desk"), actual)
                    accessible = actual["orbAccessibility"]
                    require(accessible["title"] == "" and not accessible["hasTitle"] and accessible["dataUiTitle"] is None, accessible)
                    require(accessible["aria"] == ("询问当前页面" if locale == "zh-CN" else "Ask about the current page"), accessible)
                    require(accessible["description"] == ("询问当前页面 · 拖动可移动" if locale == "zh-CN" else "Ask about the current page · drag to move"), accessible)
                    # Real pointer traversal from the orb to each item, then
                    # between adjacent items: stage never emits a leave.
                    await page.mouse.move(orb["x"] + 24, orb["y"] + 24)
                    await page.evaluate("__calls.length=0")
                    for action in actual["actions"]:
                        b = action["rect"]
                        await page.mouse.move(orb["x"] + 24, orb["y"] + 24, steps=8)
                        await page.mouse.move(b["x"] + 28, b["y"] + 28, steps=12)
                        await page.mouse.click(b["x"] + 28, b["y"] + 28)
                    for action in actual["actions"]:
                        b = action["rect"]
                        await page.mouse.move(b["x"] + 28, b["y"] + 28, steps=12)
                        await page.wait_for_timeout(160)
                    calls = await page.evaluate("__calls")
                    require(not any(call.get("kind") == "hover" and call.get("inside") is False for call in calls), calls)
                    require([call["action"] for call in calls if call.get("kind") == "action"] == [action["action"] for action in actual["actions"]], calls)
                    moved = await page.evaluate(METRICS)
                    require(moved == actual, {"before": actual, "after": moved})
                    for action in actual["actions"]:
                        target = page.locator('[data-action="' + action["action"] + '"]')
                        await target.focus()
                        require(await target.evaluate("e=>e===document.activeElement&&getComputedStyle(e).outlineStyle!=='none'"), action)
                        await page.keyboard.press("Enter")
                    await page.screenshot(path=str(tmp / f"expanded-{locale}-{item['edge']}.png"))
                    await page.keyboard.press("Escape")
                    require((await page.evaluate("__calls"))[-1] == {"kind": "dismiss"}, "Escape not dispatched")
                    require(not errors, errors)
                    rows.append({"locale": locale, "edge": item["edge"], "area": item["area"], "bounds": bounds, "actual": actual, "entering": entering, "mouse_and_keyboard_actions": await page.evaluate("__calls.filter(p=>p.kind==='action').map(p=>p.action)")})
                    await page.close()
        finally:
            await browser.close()
    (tmp / "BROWSER_METRICS.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")


def test_actual_orb_fixed_targets_and_preload_navigation(tmp_path):
    asyncio.run(scenario(tmp_path))

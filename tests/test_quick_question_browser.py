"""Real Quick HTML + preload in Chromium; Electron IPC/window boundaries only.

Source functions create actual surface projections using the existing controlled
Electron fixture. No API/model/desktop software is invoked. Durable FS + actual
HTTP Main request behavior is covered by the adjacent Node integration tests.
"""
import asyncio
import json
import os
from pathlib import Path
import subprocess

import pytest
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / '.node-tools/node_modules/node/bin/node'
EXTRACT = r"""
const path=require('node:path'),fs=require('node:fs'),vm=require('node:vm'),{createRequire}=require('node:module');
const root=process.argv[1],locale=process.argv[2],file=path.join(root,'apps/desktop-host/tests/quick-surface.test.cjs');
const text=fs.readFileSync(file,'utf8'),end=text.indexOf("test('constructs both durable");if(end<0)throw Error('Fixture boundary changed');
const context=vm.createContext({require:createRequire(file),__dirname:path.dirname(file),console,Buffer,setTimeout,clearTimeout,setImmediate,queueMicrotask});
vm.runInContext(text.slice(0,end)+`
globalThis.projections=async(locale)=>{
 const f=fixture({locale,onSend:async body=>({operation_id:body.operationId,status:'UNKNOWN_OUTCOME',
  first_submission_observation:{kind:'HTTP_RESPONSE',http_status:409,reason_code:'LINKED_DOCUMENT_UNAVAILABLE',api_code:'REVISION_CONFLICT'},
  error:{message:'尚未查到原操作记录，不能据此认定未执行；未自动重发。'}})});
 try{
  await f.surface.openAsk();await f.edit('这是旧问题，不能自动复制、重发或停止。'.repeat(7));await f.command('send');await f.surface.openAsk();
  const unknown=f.surface.get(),height=f.surface.windows.quickPopover.getBounds().height;
  await f.command('new-question');const fresh=f.surface.get();
  f.surface.setLocale(locale==='en'?'zh-CN':'en');const switched=f.surface.get();
  return {html:quickHtml('popover',locale),unknown,fresh,switched,height};
 }finally{f.surface.dispose();}
};`,context,{filename:file});
context.projections(locale).then(value=>console.log(JSON.stringify(value))).catch(error=>{console.error(error);process.exitCode=1;});
"""

def check(value, message):
    if not value:
        raise AssertionError(message)


async def run_browser(locale, tmp_path):
    result = subprocess.run([str(NODE), '-e', EXTRACT, str(ROOT), locale],
                            capture_output=True, text=True, check=True, timeout=20)
    fixture = json.loads(result.stdout)
    preload = (ROOT/'apps/desktop-host/quick-surface-preload.cjs').read_text()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(viewport={'width': 380, 'height': fixture['height']}, bypass_csp=True)
            page = await context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            await page.set_content(fixture['html'])
            await page.evaluate("""({preload,fresh})=>{
              globalThis.__quickCalls=[];globalThis.__quickListeners={};
              globalThis.require=()=>({ipcRenderer:{on:(channel,fn)=>__quickListeners[channel]=fn,
                invoke:async(channel,payload)=>{__quickCalls.push(payload);
                  if(payload.command==='new-question')queueMicrotask(()=>__quickListeners['eeas:quick-surface:state']({},fresh));
                  return {};
                }}});
              (0,eval)(preload);window.dispatchEvent(new Event('DOMContentLoaded'));
            }""", {'preload': preload, 'fresh': fixture['fresh']})
            await page.evaluate("value=>__quickListeners['eeas:quick-surface:state']({},value)", fixture['unknown'])
            button = page.locator('[data-command="new-question"]')
            check(await button.is_visible(), 'UNKNOWN must expose New question')
            check(await button.inner_text() == ('New question' if locale == 'en' else '新问题'), 'localized explicit new-question label')
            status = await page.locator('#status').inner_text()
            check('HTTP 409' in status, 'First actual HTTP status must remain visible after GET404')
            check(('file was busy' in status) if locale == 'en' else ('文件占用' in status), 'Safe authored reason must be localized')
            check(await page.locator('#status').evaluate('(node)=>node.scrollHeight<=node.clientHeight+1'), 'Primary bounded diagnostic must fit without internal scrolling')
            reply = await page.locator('#reply').inner_text()
            check(('was not found' in reply) if locale == 'en' else ('尚未查到' in reply), 'Latest GET404 observation stays separate and localized')
            await page.wait_for_timeout(240)  # bounded finite CSS enter, no product delay
            geometry = await button.bounding_box()
            check(geometry and geometry['x'] >= 0 and geometry['y'] >= 0 and geometry['x']+geometry['width'] <= 380.5
                  and geometry['y']+geometry['height'] <= fixture['height']+.5, f'New-question action clipped: {geometry}, height={fixture["height"]}')
            await page.screenshot(path=str(tmp_path/f'unknown-{locale}.png'))
            await button.focus()
            await page.keyboard.press('Enter')
            await page.wait_for_function("document.querySelector('#comment').disabled===false")
            editor = page.locator('#comment')
            check(await editor.input_value() == '', 'New question must not copy old text')
            check(await page.locator('#question').is_hidden(), 'Old question summary must be hidden')
            check(await page.locator('#status').inner_text() != status, 'Old diagnostic must not become new-question error')
            await editor.fill('保存新版本，这是用户正文，不翻译。')
            await page.evaluate("value=>__quickListeners['eeas:quick-surface:state']({},value)", fixture['switched'])
            check(await editor.input_value() == '保存新版本，这是用户正文，不翻译。', 'Language update must preserve current input')
            calls = await page.evaluate('__quickCalls')
            check(sum(item.get('command') == 'new-question' for item in calls) == 1, calls)
            check(not any(item.get('command') in {'send', 'stop', 'inspect', 'expand', 'workbench'} for item in calls), calls)
            check(not errors, errors)
            await page.screenshot(path=str(tmp_path/f'fresh-{locale}.png'))
            (tmp_path/f'geometry-{locale}.json').write_text(json.dumps({'height':fixture['height'],'button':geometry,'calls':calls},ensure_ascii=False,indent=2))
        finally:
            await browser.close()


@pytest.mark.parametrize('locale', ['zh-CN', 'en'])
def test_actual_quick_unknown_to_empty_question_keyboard_and_locale(locale, tmp_path):
    asyncio.run(run_browser(locale, tmp_path))

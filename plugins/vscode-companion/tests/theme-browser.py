"""Actual extension HTML + JS + CSS under an explicit VSCode host boundary.

No VSCode/Windows/App/model execution. Tests live theme-token updates, layout,
keyboard and unchanged draft/CAS messages in a real headless browser.
"""
import argparse,hashlib,http.server,json,os,subprocess,threading
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[3]
PLUGIN=ROOT/'plugins/vscode-companion'
NODE=ROOT/'.node-tools/node_modules/node/bin/node'
RENDER=PLUGIN/'tests/theme-html.cjs'

def check(value,message):
    if not value:raise AssertionError(message)

def markdown_cases(page,snapshot,emit,out,records,screenshots,reply):
    """Production lexer/DOM; only the VSCode message boundary is a fixture."""
    literal='## Literal **user/status** [file](file:///D:/private.docx) <b>raw</b>'
    line='完整中文 English paragraph preserves every word. '
    long_reply='# Long response\n\n**Readable** and *complete*.\n\n'+('| '+ ' | '.join('Column '+str(i) for i in range(12))+' |\n')+('| '+ ' | '.join('---' for _ in range(12))+' |\n')+('| '+ ' | '.join('cell-'+str(i)+'-'+'W'*24 for i in range(12))+' |\n\n')+'```text\n'+('code_'+('x'*240)+'\n')+'```\n\n'+('[remote](https://blocked.invalid/) [command](command:run) ![image alt](https://blocked.invalid/img) <img src=x onerror="window.__unsafe=1">\n\n')+('> quoted **content**\n\n')+((line+'\n\n')*1400)+'LONG_REPLY_TAIL_最后一段'
    page.evaluate("()=>document.documentElement.style.setProperty('--vscode-font-size','13px')")
    snapshot['session']['artifacts']=[];snapshot['artifactTokens']=[]
    for locale,width,height,body,tail,name in [('zh-CN',320,620,reply,reply.splitlines()[-1].replace('**',''),'saved-reply-zh-narrow'),('en',620,620,reply,reply.splitlines()[-1].replace('**',''),'saved-reply-en-wide'),('zh-CN',320,480,long_reply,'LONG_REPLY_TAIL_最后一段','long-reply-zh-narrow'),('en',620,620,long_reply,'LONG_REPLY_TAIL_最后一段','long-reply-en-wide')]:
        page.set_viewport_size({'width':width,'height':height})
        snapshot['session']['messages']=[{'info':{'id':'literal-user','role':'user'},'parts':[{'type':'text','text':literal}]},{'info':{'id':'literal-status','role':'system'},'parts':[{'type':'text','text':literal}]},{'info':{'id':'observed-tool','role':'assistant'},'parts':[{'type':'tool','tool':'read','state':{'status':'running'}}]},{'info':{'id':'semantic-reply','role':'assistant'},'parts':[{'id':'body','type':'text','text':body}]}]
        emit(locale);page.wait_for_function("()=>document.querySelector('.markdown table')&&document.querySelector('.markdown strong')")
        prose=page.locator('.prose.markdown');check(prose.count()==1,name+' only assistant Markdown is parsed')
        check(page.locator('.prose:not(.markdown)').all_text_contents()==[literal,literal],name+' user/status Markdown remains literal')
        check(page.locator('.prose :is(a,img,script,iframe)').count()==0,name+' model links, images and raw HTML stay inert')
        check(page.locator('.artifact-link,.artifact-file').count()==0,name+' no unsigned artifact action')
        check(prose.inner_text().endswith(tail),name+' full final paragraph survives')
        if body==long_reply:check(prose.text_content().count(line)==1400,name+' every long paragraph survives')
        table=page.locator('.prose-table').first;table.focus();check(table.evaluate('e=>e===document.activeElement&&e.tabIndex===0'),name+' table keyboard focus')
        local=table.evaluate('e=>({width:e.clientWidth,scrollWidth:e.scrollWidth})')
        if body==reply and locale=='zh-CN':
            table.evaluate("e=>{const h=document.querySelector('#history');h.scrollTop=e.offsetTop-h.offsetTop;}")
            shot=name+'-table.png';page.screenshot(path=str(out/shot));screenshots.append(shot)
        if local['scrollWidth']>local['width']:
            table.press('ArrowRight');page.wait_for_function("()=>document.querySelector('.prose-table').scrollLeft>0")
        if body==long_reply:
            pre=page.locator('.markdown pre');pre.focus();pre.press('ArrowRight');page.wait_for_function("()=>document.querySelector('.markdown pre').scrollLeft>0")
            check(pre.locator('code').text_content()=='code_'+('x'*240),name+' code is exact and scrolls locally')
        # Native keyboard scrolling continues after the first nonzero offset.
        # Wait for that finite movement before comparing retained positions.
        page.wait_for_timeout(350)
        page.evaluate("""()=>{const h=document.querySelector('#history'),reply=document.querySelector('.markdown').parentElement;h.scrollTop=reply.offsetTop-h.offsetTop;document.querySelector('.prose-table').scrollLeft=0;}""")
        if body==reply:
            shot=name+'.png';page.screenshot(path=str(out/shot));screenshots.append(shot)
        # A changed tool observation must retain the old reply DOM, browser
        # selection, local table offset, history offset and textarea selection.
        before=page.evaluate("""()=>{const h=document.querySelector('#history'),p=document.querySelector('.markdown'),table=p.querySelector('.prose-table'),walker=document.createTreeWalker(p,NodeFilter.SHOW_TEXT);let text;while((text=walker.nextNode())&&!text.textContent.trim()){}const r=document.createRange();r.setStart(text,0);r.setEnd(text,Math.min(4,text.length));getSelection().removeAllRanges();getSelection().addRange(r);h.scrollTop=Math.floor((h.scrollHeight-h.clientHeight)/2);table.scrollLeft=48;input.setSelectionRange(2,5);window.__replyNode=p;return {scroll:h.scrollTop,table:table.scrollLeft,selection:getSelection().toString(),draft:input.value,start:input.selectionStart,end:input.selectionEnd};}""")
        calls=page.evaluate('()=>window.__posted.length');snapshot['session']['messages'][2]['parts'][0]['state']['status']='completed';emit(locale)
        page.wait_for_function("()=>!document.querySelector('.tool-state.running')")
        after=page.evaluate("""()=>{const p=document.querySelector('.markdown');return {same:p===window.__replyNode,inputSame:input===window.__originalInput,scroll:document.querySelector('#history').scrollTop,table:p.querySelector('.prose-table').scrollLeft,selection:getSelection().toString(),draft:input.value,start:input.selectionStart,end:input.selectionEnd};}""")
        check(after.pop('same') and after.pop('inputSame'),name+' original reply/input DOM retained');check(after==before,name+' selection and all scroll/draft positions retained: '+json.dumps({'before':before,'after':after},ensure_ascii=False));check(page.evaluate('()=>window.__posted.length')==calls,name+' observation emits no actions')
        page.evaluate("()=>{const h=document.querySelector('#history');h.scrollTop=h.scrollHeight;}")
        geometry=page.locator('.markdown > p').last.evaluate("""e=>{let r=e.getBoundingClientRect(),visible={left:r.left,right:r.right,top:r.top,bottom:r.bottom};for(let p=e.parentElement;p;p=p.parentElement){const s=getComputedStyle(p),b=p.getBoundingClientRect();if(/auto|scroll|hidden|clip/.test(s.overflowX)){visible.left=Math.max(visible.left,b.left);visible.right=Math.min(visible.right,b.right);}if(/auto|scroll|hidden|clip/.test(s.overflowY)){visible.top=Math.max(visible.top,b.top);visible.bottom=Math.min(visible.bottom,b.bottom);}}return {height:r.height,visibleHeight:visible.bottom-visible.top,pageOverflow:document.documentElement.scrollWidth>innerWidth+1};}""")
        check(not geometry['pageOverflow'],name+' no page horizontal overflow');check(geometry['visibleHeight']>=geometry['height']-1,name+' final paragraph is reachable through every overflow ancestor')
        if body==long_reply:
            shot=name+'-tail.png';page.screenshot(path=str(out/shot));screenshots.append(shot)
        records.append({'case':name,'status':'PASS','reply_characters':len(body),'table_scroll':local,'tail_geometry':geometry,'retained_state':after})
    check(page.evaluate("()=>window.__posted.filter(x=>x.type==='send').length")==0,'Markdown scenarios never request a model')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--reply-fixture',type=Path);args=parser.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False);(out/'tmp').mkdir();records=[]
    # Server serves only the current real production renderer and generated HTML.
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            route={'/':out/'webview.html','/media/view.css':PLUGIN/'media/view.css','/media/view.js':PLUGIN/'media/view.js','/media/vendor/marked.js':PLUGIN/'media/vendor/marked.js'}.get(self.path)
            if route is None:self.send_error(404);return
            body=route.read_bytes();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8' if self.path=='/' else 'text/css' if self.path.endswith('.css') else 'text/javascript');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);origin=f'http://127.0.0.1:{server.server_port}'
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    env={**os.environ,'TMPDIR':str(out/'tmp'),'TEMP':str(out/'tmp'),'TMP':str(out/'tmp'),'PYTHONDONTWRITEBYTECODE':'1'}
    result=subprocess.run([str(NODE),str(RENDER),origin,str(out/'webview.html'),str(out/'owned-storage')],cwd=ROOT,env=env,text=True,capture_output=True)
    (out/'html-factory.log').write_text(result.stdout+result.stderr);check(result.returncode==0,'actual HTML factory failed')
    screenshots=[];errors=[]
    try:
      with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,env=env);context=browser.new_context(viewport={'width':320,'height':620},device_scale_factor=1)
        context.add_init_script("window.__posted=[];window.acquireVsCodeApi=()=>({getState:()=>undefined,setState:value=>window.__privateState=value,postMessage:value=>window.__posted.push(value)});")
        page=context.new_page();page.on('pageerror',lambda error:errors.append(str(error)));page.goto(origin,wait_until='networkidle')
        page.wait_for_function("()=>window.__posted?.some(row=>row.type==='ready')")
        scope='a'*64;long='长期研究工程_跨目录资料与复杂开发需求_😀_'+('用户保存稿'*10)
        snapshot={'projects':[{'id':'p','name':long}],'sessions':[{'session_ref':{'id':'s','revision':1,'sha256':'b'*64},'title':long}], 'sessionTokens':['c'*64],'projectId':'p','scopeToken':scope,'draft':{'text':'','revision':0},'status':{'connected':True},'pending':[],'recoverable':[],'session':{'session_ref':{'id':'s','revision':1,'sha256':'b'*64},'messages':[{'info':{'role':'assistant'},'parts':[{'type':'text','text':'保留用户正在阅读的内容。\n'+('Long Unicode text 中文路径与文件内容 😀 /path/without/spaces/'*14)},{'type':'tool','tool':long,'state':{'status':'running'}},{'type':'file','filename':'D:/资料/'+long+'.docx'}]}],'artifacts':[],'artifact_channel':'NOT_CONNECTED'}}
        snapshot['session']['messages'].append({'info':{'role':'assistant'},'parts':[{'type':'text','text':'已经整理好。先确认接口边界和验收要求，再继续下一步。'}]})
        snapshot['session']['messages'][-1]['info']['id']='msg_artifact'
        snapshot['session']['artifacts']=[{'filename':'可审阅制作稿_中文🙂_'+('A'*150)+'.pptx','source':{'message_id':'msg_artifact'}}]
        snapshot['session']['artifact_channel']='REGISTERED_REPORT_ARTIFACTS';snapshot['artifactTokens']=['d'*64]
        snapshot['session']['messages'].append({'info':{'id':'msg_links','role':'assistant'},'parts':[{'id':'prt_links','type':'text','text':'文件：[Word 修改稿](report.docx)、[说明](notes.md)。\n\n`[不要打开](secret.docx)` <img src="https://blocked.invalid/image"> [远程](https://blocked.invalid/)'}]})
        for index,(filename,target) in enumerate([('report.docx','native'),('notes.md','editor')]):
            snapshot['session']['artifacts'].append({'filename':filename,'target':target,'source':{'message_id':'msg_links','part_id':'prt_links','link_index':index}})
            snapshot['artifactTokens'].append(str(index+1)*64)
        def emit(locale='zh-CN',connection='PAIRED',notice=''):
            page.evaluate("s=>window.postMessage(s,'*')",{'type':'state','locale':locale,'connection':connection,'trusted':True,'notice':notice,'snapshot':snapshot})
        emit();page.locator('#input').wait_for(state='visible');page.locator('#input').fill('保留我的未发送草稿 😀 / 不要重发')
        draft=page.evaluate("()=>window.__posted.filter(row=>row.type==='draft').at(-1)")
        page.evaluate("m=>window.postMessage(m,'*')",{'type':'draftResult','token':scope,'sequence':draft['sequence'],'accepted':True,'revision':1});snapshot['draft']={'text':'保留我的未发送草稿 😀 / 不要重发','revision':1};emit()
        artifact=page.locator('.artifact-file');artifact.focus();artifact.press('Enter')
        check(page.evaluate("()=>window.__posted.at(-1).type==='artifact'&&window.__posted.at(-1).index===0&&window.__posted.at(-1).token==='d'.repeat(64)"),'actual file keyboard callback binds host token')
        check(page.locator('#input').input_value()==snapshot['draft']['text'],'opening a file keeps original draft')
        records.append({'case':'actual-registered-file-keyboard-callback','status':'PASS','host_rpc_boundary':'STUB_NO_NATIVE_EXECUTION'})
        check(page.locator('.artifact-link').count()==2,'exact Markdown links render without duplicate cards')
        markdown_link=page.locator('.artifact-link').first;markdown_link.focus();markdown_link.press('Enter')
        check(page.evaluate("()=>window.__posted.at(-1).type==='artifact'&&window.__posted.at(-1).index===1&&window.__posted.at(-1).token==='1'.repeat(64)"),'actual Markdown link keyboard uses exact host token')
        check(page.locator('.prose :is(a,img,script,iframe)').count()==0,'model HTML and remote href stay inert')
        check(page.locator('#input').input_value()==snapshot['draft']['text'],'Markdown link preserves composer')
        records.append({'case':'actual-markdown-file-link-keyboard-callback','status':'PASS','host_rpc_boundary':'STUB_NO_NATIVE_EXECUTION'})
        page.evaluate("()=>{window.__originalInput=document.querySelector('#input');window.__beforeThemeCalls=window.__posted.length;}")
        themes=[('light',320,620,'vscode-light',{'canvas':'#f3f4f5','paper':'#ffffff','ink':'#202326','muted':'#5b6268','line':'#bcc2c6','field':'#ffffff','fieldink':'#222222','focus':'#1769aa','button':'#145eb0','buttonink':'#ffffff','hover':'#d7dde2','selection':'#b5d4fa'}),('dark',320,480,'vscode-dark',{'canvas':'#202326','paper':'#292d31','ink':'#e5e8eb','muted':'#b2bbc3','line':'#59636a','field':'#30373c','fieldink':'#f1f3f5','focus':'#70b8ff','button':'#1669b2','buttonink':'#ffffff','hover':'#3c464e','selection':'#254d78'}),('high-contrast',320,480,'vscode-high-contrast',{'canvas':'#000000','paper':'#000000','ink':'#ffffff','muted':'#ffffff','line':'#ffffff','field':'#000000','fieldink':'#ffffff','focus':'#ffff00','button':'#000000','buttonink':'#ffffff','hover':'#303030','selection':'#ffff00'}),('high-contrast-light',320,480,'vscode-high-contrast-light',{'canvas':'#ffffff','paper':'#ffffff','ink':'#000000','muted':'#000000','line':'#000000','field':'#ffffff','fieldink':'#000000','focus':'#0022dd','button':'#ffffff','buttonink':'#000000','hover':'#e0e0e0','selection':'#a9c7ff'}),('custom',430,820,'vscode-dark',{'canvas':'#172d30','paper':'#1d393d','ink':'#e4f4ea','muted':'#bad7cd','line':'#63867d','field':'#284a46','fieldink':'#f1fff6','focus':'#b7e695','button':'#4c734c','buttonink':'#ffffff','hover':'#3d5d55','selection':'#527460'}),('custom-wide',620,480,'vscode-dark',{'canvas':'#172d30','paper':'#1d393d','ink':'#e4f4ea','muted':'#bad7cd','line':'#63867d','field':'#284a46','fieldink':'#f1fff6','focus':'#b7e695','button':'#4c734c','buttonink':'#ffffff','hover':'#3d5d55','selection':'#527460'})]
        names={'canvas':'sideBar-background','paper':'editorWidget-background','ink':'foreground','muted':'descriptionForeground','line':'panel-border','field':'input-background','fieldink':'input-foreground','focus':'focusBorder','button':'button-background','buttonink':'button-foreground','hover':'toolbar-hoverBackground','selection':'editor-selectionBackground'}
        for name,width,height,bodyclass,palette in themes:
            page.set_viewport_size({'width':width,'height':height})
            tokens={'--vscode-'+names[key]:value for key,value in palette.items()};tokens.update({'--vscode-font-family':'Georgia, serif','--vscode-font-size':'13px','--vscode-contrastBorder':palette['line'],'--vscode-contrastActiveBorder':palette['focus'],'--vscode-dropdown-background':palette['paper'],'--vscode-dropdown-foreground':palette['ink'],'--vscode-input-placeholderForeground':palette['muted'],'--vscode-menu-background':palette['paper'],'--vscode-menu-foreground':palette['ink'],'--vscode-menu-selectionBackground':palette['hover'],'--vscode-menu-selectionForeground':palette['ink'],'--vscode-button-hoverBackground':palette['hover'],'--vscode-disabledForeground':palette['muted']})
            page.evaluate("({bodyclass,tokens})=>{for(const n of ['vscode-light','vscode-dark','vscode-high-contrast','vscode-high-contrast-light'])document.body.classList.remove(n);document.body.classList.add(bodyclass);for(const [key,value]of Object.entries(tokens))document.documentElement.style.setProperty(key,value);}",{'bodyclass':bodyclass,'tokens':tokens})
            page.locator('.artifact-file').scroll_into_view_if_needed()
            page.locator('#input').focus();expected_focus='rgb('+', '.join(str(int(palette['focus'][i:i+2],16)) for i in (1,3,5))+')';page.wait_for_function("color=>getComputedStyle(document.querySelector('.input-shell')).outlineColor===color",arg=expected_focus)
            value=page.evaluate("()=>{const css=e=>getComputedStyle(e),rect=e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom}};return {background:css(document.body).backgroundColor,ink:css(document.body).color,inputbg:css(document.querySelector('.input-shell')).backgroundColor,inputink:css(input).color,focus:css(document.querySelector('.input-shell')).outlineColor,font:css(input).fontFamily,bodySize:css(document.querySelector('.prose')).fontSize,controlSize:css(document.querySelector('#agent-mode')).fontSize,metaSize:css(document.querySelector('.role')).fontSize,overflow:document.documentElement.scrollWidth>innerWidth+1,textarea:rect(input),send:rect(document.querySelector('#send')),header:rect(document.querySelector('.app-header')),nav:rect(document.querySelector('#navigation')),composer:rect(document.querySelector('#composer')),same:input===window.__originalInput,draft:input.value,calls:window.__posted.length,visibleText:document.body.innerText};}")
            rgb=lambda text:'rgb('+', '.join(str(int(text[i:i+2],16)) for i in (1,3,5))+')'
            check(value['background']==rgb(palette['canvas']),name+' host background');check(value['ink']==rgb(palette['ink']),name+' host foreground');check(value['inputbg']==rgb(palette['field']),name+' host input surface');check(value['inputink']==rgb(palette['fieldink']),name+' host input text');check(value['focus']==rgb(palette['focus']),name+' host focus outline');check('Georgia' in value['font'],name+' host font family')
            check(float(value['bodySize'][:-2])>=14 and float(value['controlSize'][:-2])>=13 and float(value['metaSize'][:-2])>=12,name+' readable type hierarchy');check(not value['overflow'],name+' no horizontal page overflow');check(value['same'] and value['draft']==snapshot['draft']['text'],name+' same textarea and draft');check(value['calls']==page.evaluate('()=>window.__beforeThemeCalls'),name+' theme change sends no messages');check('Cirven Agent' not in value['visibleText'] and '\nAgent\n' not in value['visibleText'],name+' only Cirven visible brand')
            for key in ['textarea','send']:
                r=value[key];check(r['x']>=0 and r['right']<=width+1 and r['height']>0 and r['y']>=0 and r['bottom']<=height+1,name+' '+key+' fits pane')
            check(value['nav']['bottom']<=value['composer']['y']+1,name+' navigation does not overlap composer')
            file_rect=page.locator('.artifact-file').bounding_box();check(file_rect and file_rect['x']>=0 and file_rect['x']+file_rect['width']<=width+1,name+' long filename is within pane')
            check(page.locator('.artifact-file').inner_text()==snapshot['session']['artifacts'][0]['filename'],name+' full filename is text')
            value['artifact_bounds']=file_rect

            page.locator('#send').hover();page.wait_for_function("color=>getComputedStyle(document.querySelector('#send')).backgroundColor===color",arg=rgb(palette['hover']));value['button_hover']=page.locator('#send').evaluate('e=>getComputedStyle(e).backgroundColor');check(value['button_hover']==rgb(palette['hover']),name+' host button hover');page.locator('#input').hover();
            shot=name+'.png';page.screenshot(path=str(out/shot));screenshots.append(shot);records.append({'case':name,'status':'PASS','actual':value})
        # Real native details/keyboard, not a string/source-only assertion.
        page.set_viewport_size({'width':320,'height':480});summary=page.locator('#context-tools > summary');summary.click();page.locator('#attach').wait_for(state='visible')
        bounds=page.locator('.context-menu').bounding_box();check(bounds and bounds['x']>=0 and bounds['x']+bounds['width']<=320+1 and bounds['y']>=0 and bounds['y']+bounds['height']<=480+1,'expanded context menu fits actual small pane')
        summary.press('Escape');check(not page.locator('#context-tools').get_attribute('open'),'Esc closes context menu');check(page.evaluate("()=>document.activeElement===document.querySelector('#context-tools > summary')"),'Esc restores summary focus')
        summary.click();page.locator('#diff').click();check(not page.locator('#context-tools').get_attribute('open'),'native editor action closes menu');check(page.evaluate("()=>window.__posted.at(-1).type==='diff'"),'diff routes existing explicit message')
        before=page.evaluate('()=>window.__posted.length');emit('en');page.wait_for_function("()=>document.body.dataset.uiLocale==='en'");check(page.locator('#input').input_value()==snapshot['draft']['text'],'locale retains original draft');check(page.evaluate('()=>input===window.__originalInput'),'locale retains textarea');check(page.evaluate('()=>window.__posted.length')==before,'locale emits no send');check(page.locator('#context-tools > summary').get_attribute('aria-label')=='Context and tools','new tool label localizes');records.append({'case':'actual-keyboard-native-actions-and-locale','status':'PASS','context_menu_bounds':bounds})
        emit('en',notice='TRANSPORT_TIMEOUT');page.wait_for_function("()=>document.querySelector('.notice')?.textContent.includes('timed out')");notice=page.locator('.notice');notice.scroll_into_view_if_needed();check(notice.is_visible(),'unknown original outcome remains reachable');check(page.locator('#input').input_value()==snapshot['draft']['text'],'unknown retains draft');check(page.evaluate("()=>window.__posted.filter(x=>x.type==='send').length")==0,'no model send/replay during all observations');records.append({'case':'unknown-outcome-visible-without-replay','status':'PASS'})
        page.screenshot(path=str(out/'final-recovery-en.png'));screenshots.append('final-recovery-en.png')
        # Representative, explicitly fixture-only reading views are separate from
        # worst-case long-path stress captures above. No product copy is faked.
        snapshot['projects'][0]['name']='接口改版';snapshot['sessions'][0]['title']='确定读写边界'
        snapshot['session']['messages']=[{'info':{'role':'user'},'parts':[{'type':'text','text':'我刚改了接口方案，保留我的修改，帮我核对剩余的问题。'}]},{'info':{'role':'assistant'},'parts':[{'type':'text','text':'会保留已修改的内容。先核对读写边界与失败恢复，再继续下一步。'}]}]
        emit('zh-CN');page.wait_for_function("()=>document.body.dataset.uiLocale==='zh-CN'")
        for width,height in [(320,480),(430,820)]:
            page.set_viewport_size({'width':width,'height':height});page.evaluate("()=>{document.documentElement.style.setProperty('--vscode-font-family','Segoe UI, system-ui, sans-serif');document.querySelector('#history').scrollTop=document.querySelector('#history').scrollHeight;}")
            last=page.locator('.assistant .prose').last.bounding_box();history=page.locator('#history').bounding_box();check(last and history and last['y']>=history['y']-1 and last['y']+last['height']<=history['y']+history['height']+1,'last response fits representative reader')
            check(page.locator('#input').input_value()==snapshot['draft']['text'],'representative view retains exact pending draft')
            shot=f'representative-fixture-{width}x{height}.png';page.screenshot(path=str(out/shot));screenshots.append(shot);records.append({'case':f'representative-fixture-{width}x{height}','status':'PASS','last_response_bounds':last,'history_bounds':history})
        spacing=page.evaluate("""()=>{const violations=[];for(const sheet of document.styleSheets)for(const rule of sheet.cssRules){const visit=r=>{if(r.cssRules){for(const child of r.cssRules)visit(child);return;}if(!r.style)return;for(const key of r.style){if(!/^(gap|row-gap|column-gap|(?:padding|margin|inset)(?:-(?:top|right|bottom|left|block|inline)(?:-(?:start|end))?)?|top|right|bottom|left)$/.test(key))continue;for(const hit of r.style.getPropertyValue(key).matchAll(/(-?\\d+(?:\\.\\d+)?)px/g))if(Number(hit[1])%4)violations.push({selector:r.selectorText,key,value:r.style.getPropertyValue(key)});}};visit(rule);}return violations;}""")
        check(not spacing,'actual CSSOM spacing uses four-pixel scale')
        calls=page.evaluate('()=>window.__posted.length');page.evaluate("()=>document.documentElement.style.setProperty('--vscode-font-size','18px')")
        check(page.locator('.prose').first.evaluate('e=>parseFloat(getComputedStyle(e).fontSize)')>=18,'larger host font preference is respected');check(page.evaluate('()=>window.__posted.length')==calls,'font preference emits no messages');records.append({'case':'host-font-and-four-pixel-spacing','status':'PASS','spacing_violations':spacing})
        reply=json.loads(args.reply_fixture.read_text())['value']['text'] if args.reply_fixture else '# Saved reply\n\n**Complete content.**\n\n| Item | Result |\n| --- | --- |\n| T01 | Preserved |\n\nFINAL_SAVED_REPLY'
        markdown_cases(page,snapshot,emit,out,records,screenshots,reply)
        snapshot['sessions']=[{'kind':'NATIVE_CONVERSATION','project_id':'p','conversation_id':cid} for cid in ('sameuuid-11111111','sameuuid-22222222')]
        snapshot['session']={**snapshot['sessions'][1],'messages':[{'info':{'id':'native-request','role':'user'},'native_request':True,'parts':[{'type':'text','text':'原始请求 '+('long_context_without_spaces_'*300)}]},{'info':{'id':'native-reply','role':'assistant'},'native_status':'RETURNED','parts':[{'type':'text','text':'已保留当前讨论。\n\n'+('中文 / English feedback. '*40)}]}]}
        snapshot['artifactTokens']=[]
        for locale,width,height in [('zh-CN',320,480),('en',620,620)]:
            page.set_viewport_size({'width':width,'height':height});emit(locale)
            page.wait_for_function("()=>document.querySelector('.native-request')")
            choices=page.locator('#sessions option').all_text_contents();check(choices[-1]!=choices[-2],'native short ids remain distinguishable')
            check(page.locator('#sessions').input_value()=='1','exact selected native discussion')
            check(page.locator('#agent-mode').is_hidden() and page.locator('#session-changes').is_hidden(),'unadapted plan and diff are not advertised')
            details=page.locator('.native-request');check(not details.evaluate('e=>e.open'),'original internal request starts collapsed')
            shot=f'native-discussion-{locale}-{width}-default.png';page.screenshot(path=str(out/shot));screenshots.append(shot)
            details.locator('summary').click();pre=details.locator('pre');check(pre.evaluate('e=>e.scrollWidth<=e.clientWidth+1'),'long native request wraps inside its container')
            check(page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth+1'),'native panel has no document overflow')
            check(page.locator('#input').input_value()==snapshot['draft']['text'],'native updates retain the current unsent draft')
            shot=f'native-discussion-{locale}-{width}.png';page.screenshot(path=str(out/shot));screenshots.append(shot);records.append({'case':f'native-discussion-{locale}-{width}','status':'PASS'})
        snapshot['session']['messages']=[{'info':{'id':'expired-native-reply','role':'assistant'},'native_status':'UNKNOWN_OUTCOME','native_execution':{'stop':{'reason':'OWNER_DISCONNECTED_NO_POST_REPLAY','owner_stop_status':'EXPIRED'}},'parts':[]}]
        for locale,width,height in [('zh-CN',320,480),('en',620,620)]:
            page.set_viewport_size({'width':width,'height':height});emit(locale)
            text='本地执行时限已到，最终回复待核查。' if locale=='zh-CN' else 'The local execution time limit was reached. The final reply needs inspection.'
            notice=page.locator('#history .notice');notice.wait_for(state='visible');check(notice.inner_text()==text,'safe local expiry reason is localized')
            check(notice.evaluate('e=>e.scrollWidth<=e.clientWidth+1'),'expiry reason wraps inside the chat')
            check(page.locator('#input').input_value()==snapshot['draft']['text'],'expiry observation keeps the unsent draft')
            check(not page.evaluate("()=>window.__posted.some(m=>m.type==='send')"),'expiry observation never sends another question')
            shot=f'native-expiry-{locale}-{width}.png';page.screenshot(path=str(out/shot));screenshots.append(shot);records.append({'case':f'native-expiry-{locale}-{width}','status':'PASS'})
        check(not errors,'no page errors: '+repr(errors));browser.close()
    finally:
      server.shutdown();server.server_close();thread.join(timeout=5)
      receipt={'scope':'ACTUAL_BROWSER_WITH_EXPLICIT_VSCODE_BOUNDARY_NOT_WINDOWS','records':records,'passed':len(records),'page_errors':errors,'screenshots':screenshots,'source':{name:hashlib.sha256((PLUGIN/name).read_bytes()).hexdigest() for name in ['extension.cjs','media/view.js','media/view.css','media/vendor/marked.js','product.json','package.json','tests/theme-html.cjs','tests/theme-browser.py']},'reply_fixture':{'path':str(args.reply_fixture),'sha256':hashlib.sha256(args.reply_fixture.read_bytes()).hexdigest()} if args.reply_fixture else None,'model_calls':0,'windows':'NOT_RUN','server_thread_stopped':not thread.is_alive()}
      (out/'BROWSER_RECEIPT.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':'PASS','cases':len(records),'windows':'NOT_RUN'}))
if __name__=='__main__':main()

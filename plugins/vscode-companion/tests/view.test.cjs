'use strict';
async function untilFixture(predicate){const deadline=performance.now()+5000;while(!predicate()){if(performance.now()>=deadline)throw Error('UNIT fixture did not reach callback');await new Promise(resolve=>setImmediate(resolve));}}
const test=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path'),os=require('node:os');
// Small DOM and VSCode boundary fixtures: actual renderer/controller functions
// run, but this suite is UNIT, not a running VSCode extension host.
class Element{
  constructor(tag='div'){this.tag=tag;this.children=[];this.listeners={};this.value='';this.textContent='';this.hidden=false;this.disabled=false;this.selectionStart=0;this.selectionEnd=0;this.scrollHeight=0;this.scrollTop=0;this.clientHeight=100;}
  append(...children){for(const c of children)if(c.tag==='#fragment')this.append(...c.children);else this.insertBefore(c,null);}
  insertBefore(child,before){if(child.parentElement)child.parentElement.removeChild(child);const index=before===null?this.children.length:this.children.indexOf(before);assert.ok(index>=0);this.children.splice(index,0,child);child.parentElement=this;return child;}
  removeChild(child){const index=this.children.indexOf(child);assert.ok(index>=0);this.children.splice(index,1);child.parentElement=null;return child;}
  replaceChildren(...children){for(const child of this.children)child.parentElement=null;this.children=[];this.append(...children);}
  addEventListener(type,fn){(this.listeners[type]??=[]).push(fn);}
  fire(type,event={}){for(const fn of this.listeners[type]||[])fn(event);}
  get classList(){return {toggle:(name,on)=>{const names=new Set((this.className||'').split(' ').filter(Boolean));on?names.add(name):names.delete(name);this.className=[...names].join(' ');}};}
  getAttribute(key){return this.attributes?.[key]??null;}
  setAttribute(key,value){(this.attributes??={})[key]=value;}
  hasAttribute(key){return this.getAttribute(key)!==null;}
  matches(selector){return selector.split(',').some(part=>{const s=part.trim();if(/^[a-z]+$/.test(s))return this.tag===s;if(/^\[[a-z-]+\]$/.test(s))return this.hasAttribute(s.slice(1,-1));throw Error('UNIT selector not implemented: '+s);});}
  querySelectorAll(selector){return this.children.flatMap(child=>[...(child.matches(selector)?[child]:[]),...child.querySelectorAll(selector)]);}
  querySelector(selector){return this.querySelectorAll(selector)[0]??null;}
  get dataset(){const node=this;return new Proxy({},{get:(_target,key)=>node.getAttribute('data-'+String(key).replace(/[A-Z]/g,c=>'-'+c.toLowerCase()))??undefined,set:(_target,key,value)=>{node.setAttribute('data-'+String(key).replace(/[A-Z]/g,c=>'-'+c.toLowerCase()),String(value));return true;}});}
  setSelectionRange(a,b){this.selectionStart=a;this.selectionEnd=b;}
  get childNodes(){return this.children;}
  get firstChild(){return this.children[0]??null;}
  get nextSibling(){const siblings=this.parentElement?.children;return siblings?.[siblings.indexOf(this)+1]??null;}
}
const descendants=element=>[element,...element.children.flatMap(descendants)];
const textOf=element=>element.textContent+element.children.map(textOf).join('');
// AUTHORED_NOT_RUN: settings retain their own local buffer; no automatic RPC.
test('execution form keeps exact command whitespace across renders and only confirms on its explicit button',()=>{
 const v=webview(),token='b'.repeat(64),id='native:'+'c'.repeat(64),settings={workspace_token:null,write:false,commands_enabled:false,commands_text:'',processes:false,remote:false,tool_tokens:[],unavailable_original_tools:[],ttl_seconds:600,max_calls:24,max_output_tokens:4096},cfg={supported:true,ready:true,token,settings,can_confirm:true,can_apply:true,workspaces:[],tools:[]};
 const base={projectId:'project',session:{kind:'CANONICAL_CONVERSATION',conversation_ref:{id,revision:1},messages:[],canonical_history:[],artifacts:[]},draft:{text:'KEEP_MAIN_INPUT',revision:1},canonical:{selected:true,can_send:true,can_connect:false},connectionConfiguration:cfg};v.show(base);const before=v.out.length;
 v.ids.get('configuration-commands').value='  python keep.py  ';v.ids.get('configuration-commands').fire('input');assert.equal(v.out.length,before);v.show(base);assert.equal(v.ids.get('configuration-commands').value,'  python keep.py  ');assert.equal(v.ids.get('input').value,'KEEP_MAIN_INPUT');assert.equal(v.persisted.at(-1).configuration_buffers[0][1].settings.commands_text,'  python keep.py  ');
 v.ids.get('configuration-apply').fire('click');const posted=v.out.at(-1);assert.equal(posted.type,'configurationApply');assert.equal(posted.token,token);assert.equal(posted.settings.commands_text,'  python keep.py  ');assert.doesNotMatch(JSON.stringify(posted),/KEEP_MAIN_INPUT/);assert.equal(v.ids.get('input').value,'KEEP_MAIN_INPUT');
});
test('changed configuration requires explicit review while retaining the exact unsaved settings',()=>{
 const v=webview(),token='b'.repeat(64),id='native:'+'c'.repeat(64),settings={workspace_token:null,write:false,commands_enabled:false,commands_text:'',processes:false,remote:false,tool_tokens:[],unavailable_original_tools:[],ttl_seconds:600,max_calls:24,max_output_tokens:4096},cfg={supported:true,ready:true,token,settings,can_confirm:true,workspaces:[],tools:[]};
 const base={session:{kind:'CANONICAL_CONVERSATION',conversation_ref:{id,revision:1},messages:[],canonical_history:[],artifacts:[]},connectionConfiguration:cfg};v.show(base);v.ids.get('configuration-commands').value='KEEP_UNCONFIRMED_COMMAND';v.ids.get('configuration-commands').fire('input');v.show({...base,connectionConfiguration:{...cfg,token:'d'.repeat(64)}});assert.equal(v.ids.get('configuration-apply').disabled,true);assert.equal(v.ids.get('configuration-adopt').hidden,false);assert.equal(v.ids.get('configuration-commands').value,'KEEP_UNCONFIRMED_COMMAND');v.ids.get('configuration-adopt').fire('click');assert.equal(v.ids.get('configuration-apply').disabled,false);assert.equal(v.ids.get('configuration-commands').value,'KEEP_UNCONFIRMED_COMMAND');
});
// AUTHORED_NOT_RUN: canonical UI actions use saved tokens, never unsent text.
test('canonical navigation and model choice preserve the composer and never send text through settings',()=>{
 const v=webview(),token='b'.repeat(64),model='c'.repeat(64),reference={id:'native:'+'d'.repeat(64),revision:1};
 const base={projectId:'__cirven_chats__',session:{kind:'CANONICAL_CONVERSATION',conversation_ref:reference,organization_project_id:null,messages:[],canonical_history:[],artifacts:[],available_agent_modes:['plan','build']},draft:{text:'KEEP_UNSENT',revision:1},canonical:{supported:true,selected:true,token,choice_token:model,can_send:true,can_connect:true,changed:true,providers:[{token:model,label:'Small model'}]}};
 v.show(base);assert.equal(v.ids.get('canonical-navigation').hidden,false);assert.equal(v.ids.get('model-settings').hidden,false);v.ids.get('conversation-model').value=model;v.ids.get('conversation-model').fire('change');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'conversationModel',token,profile_token:model});assert.equal(v.ids.get('input').value,'KEEP_UNSENT');
 v.ids.get('conversation-connect').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'conversationConnect',token});assert.equal(v.ids.get('input').value,'KEEP_UNSENT');
});
test('empty canonical chat blocks sending until explicit model choice and unknown creation offers original lookup',()=>{
 const v=webview();v.show({projectId:'__cirven_chats__',session:{kind:'CANONICAL_CONVERSATION',messages:[],canonical_history:[],artifacts:[]},draft:{text:'KEEP_PRIVATE',revision:1},canonical:{supported:true,selected:true,can_send:false,creation_pending:true,creation_request_id:'original_create'}});
 assert.equal(v.ids.get('send').disabled,true);assert.equal(v.ids.get('conversation-new').disabled,true);assert.equal(v.ids.get('conversation-creation-check').hidden,false);v.ids.get('conversation-creation-check').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'conversationCreationInspect',id:'original_create'});assert.equal(v.ids.get('input').value,'KEEP_PRIVATE');
});
test('running root foreground guidance stays in the existing child composer and disables a stale target',()=>{
 const v=webview(),token='c'.repeat(64),key='e'.repeat(64),childRef={id:'ses_child',root_native_session_id:'ses_root',sha256:'b'.repeat(64)},
 delegation={scopeToken:'a'.repeat(64),supported:true,sendSupported:true,ownerReady:true,canGuide:true,entries:[{title:'Child',token}],
  detail:{token,value:{child_ref:childRef,observation_is_current:true,messages:[]}},draft:{key,token,text:'Correct this task',revision:1},active:null};
 const base={session:{session_ref:{id:'s'},messages:[],artifacts:[],active_operation_id:'original_root'},draft:{text:'KEEP_ROOT',revision:1},delegation};
 v.show(base);assert.equal(v.ids.get('child-send').disabled,false);assert.match(v.ids.get('child-send').textContent,/指导/);
 v.ids.get('child-send').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'childSend',token,revision:1});
 assert.equal(v.ids.get('input').value,'KEEP_ROOT');
 v.show({...base,delegation:{...delegation,canGuide:false}});assert.equal(v.ids.get('child-send').disabled,true);assert.equal(v.ids.get('child-input').value,'Correct this task');
});
test('child composer synchronizes its private revision before sending and keeps the root input',()=>{
 const v=webview(),token='c'.repeat(64),key='e'.repeat(64),childRef={id:'ses_child',root_native_session_id:'ses_root',sha256:'b'.repeat(64)},
 delegation={scopeToken:'a'.repeat(64),supported:true,sendSupported:true,ownerReady:true,entries:[{title:'Child',token}],
  detail:{token,value:{child_ref:childRef,observation_is_current:false,messages:[]}},draft:{key,token,text:'',revision:0},active:null};
 const base={session:{session_ref:{id:'s'},messages:[],artifacts:[],active_operation_id:null},draft:{text:'ROOT_UNSENT',revision:1},delegation};
 v.show(base);const input=v.ids.get('child-input');input.value='继续原子任务';input.fire('input');const saved=v.out.at(-1);
 assert.equal(saved.type,'childDraft');assert.equal(saved.token,token);assert.equal(saved.revision,0);assert.equal(v.ids.get('child-send').disabled,true);
 v.show({...base,delegation:{...delegation,draft:{key,token,text:input.value,revision:1}}});
 v.receive({type:'childDraftResult',token,sequence:saved.sequence,accepted:true,revision:1});
 v.ids.get('child-send').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'childSend',token,revision:1});
 assert.equal(v.ids.get('input').value,'ROOT_UNSENT');
 input.fire('compositionstart');input.value='中文输入中';const before=v.out.length;input.fire('input');assert.equal(v.out.length,before);
 input.fire('compositionend');assert.equal(v.out.at(-1).type,'childDraft');assert.equal(v.out.at(-1).text,'中文输入中');
});
test('child view uses an exact token and retains partial history as inert text',()=>{
 const v=webview(),token='c'.repeat(64),entry={title:'Original child',token};
 v.show({session:{session_ref:{id:'s'},messages:[],artifacts:[]},delegation:{scopeToken:'d'.repeat(64),supported:true,entries:[entry],detail:{token,value:{observation_is_current:false,messages:[{info:{role:'assistant'},parts:[{type:'text',text:'<script>do not execute</script>'}]}]}}}});
 const list=v.ids.get('children-list');list.children[0].fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'childView',token});
 const record=v.ids.get('child-record');assert.match(textOf(record),/历史可能不完整/);assert.match(textOf(record),/<script>/);assert.equal(descendants(record).some(e=>e.tag==='script'),false);
});
test('tool details send only the exact action token and display original output as inert bounded text',()=>{
 const v=webview(),token='b'.repeat(64),message={info:{id:'msg_tool',role:'assistant'},parts:[{id:'prt_tool',type:'tool',tool:'bash',state:{status:'error'}}]},
 entry={message_id:'msg_tool',part_id:'prt_tool',token},detail={token,value:{title:'Check',input:{text:'python check.py',truncated:false},output:{text:'<img src=x onerror=alert(1)>',truncated:true},error:{text:'FAILED',truncated:false}}};
 v.show({session:{session_ref:{id:'s'},messages:[message],artifacts:[]},toolDetailsSupported:true,tools:{entries:[entry],detail}});
 const history=v.ids.get('history'),all=descendants(history),read=all.find(e=>e.tag==='button'&&e.textContent==='查看工具详情');
 read.fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'toolDetails',token});
 assert.ok(all.some(e=>e.tag==='pre'&&e.textContent==='<img src=x onerror=alert(1)>'));assert.equal(all.some(e=>e.tag==='img'),false);
 assert.match(textOf(history),/仅显示片段/);assert.match(textOf(history),/不会重新执行工具/);
});
// AUTHORED_NOT_RUN: inert report facts in the original tool; no file/URI execution.
test('original report actions carry only source tokens and display report paths as inert text',()=>{
 const v=webview(),token='b'.repeat(64),source='c'.repeat(64),message={info:{id:'msg_report',role:'assistant'},parts:[{id:'prt_report',type:'tool',tool:'bash',state:{status:'error'}}]},entry={message_id:'msg_report',part_id:'prt_report',token},reports={supported:true,owner:token,loading:null,error:null,catalog:{sources:[{token:source,path:'nested/报告.xml'}]},detail:{source_token:source,report:{basis:{report_path:'nested/报告.xml'},summary:{outcome:'FAILED',counts:{PASSED:0,FAILED:1,ERROR:0,SKIPPED:0,UNKNOWN:0},integrity_issues:[],metadata_warnings:[]},command_status:'EXITED',command_exit_code:1,association:'UNCHANGED_BYTES',cases:[{ordinal:0,name:'<img onerror=evil()>',status:'FAILED',suite:'suite',classname:'',file:'vscode://evil/../../file.py',line:'2',message:'original failure',detail:'<script>never execute</script>',text_truncated:true}],page:{offset:0,outcome:'ALL',matching:1,next_offset:null}}}};
 v.show({session:{session_ref:{id:'s'},messages:[message],artifacts:[]},tools:{entries:[entry],reports},draft:{text:'KEEP_MAIN_DRAFT',revision:1}});
 const all=descendants(v.ids.get('history'));all.find(e=>e.tag==='button'&&e.textContent==='查看报告').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'toolReport',token:source,offset:0,outcome:'ALL'});
 assert.equal(all.some(e=>['img','script','a'].includes(e.tag)),false);assert.ok(all.some(e=>e.tag==='pre'&&e.textContent==='<script>never execute</script>'));assert.match(textOf(v.ids.get('history')),/不能证明本次重新生成/);assert.match(textOf(v.ids.get('history')),/非零退出/);assert.equal(v.ids.get('input').value,'KEEP_MAIN_DRAFT');
});
test('report failure preserves the previous page and pagination remains an explicit original token action',()=>{
 const v=webview(),token='b'.repeat(64),source='c'.repeat(64),message={info:{id:'msg_report',role:'assistant'},parts:[{id:'prt_report',type:'tool',tool:'bash',state:{status:'completed'}}]},entry={message_id:'msg_report',part_id:'prt_report',token},report={basis:{report_path:'report.xml'},summary:{outcome:'PASSED_WITH_SKIPS',counts:{PASSED:20,FAILED:0,ERROR:0,SKIPPED:1,UNKNOWN:0},integrity_issues:[],metadata_warnings:[]},command_status:'UNKNOWN_OUTCOME',command_exit_code:null,association:'BEFORE_UNAVAILABLE',cases:[],page:{offset:0,outcome:'ALL',matching:21,next_offset:20}},patch={session:{session_ref:{id:'s'},messages:[message],artifacts:[]},tools:{entries:[entry],reports:{supported:true,owner:token,loading:null,error:null,catalog:{sources:[{token:source,path:'report.xml'}]},detail:{source_token:source,report}}}};
 v.show(patch);v.ids.get('input').value='CONTINUE_EDITING';v.ids.get('input').fire('input');v.show({...patch,tools:{...patch.tools,reports:{...patch.tools.reports,error:'ETIMEDOUT'}}});
 const all=descendants(v.ids.get('history'));assert.match(textOf(v.ids.get('history')),/已读结果保留/);assert.match(textOf(v.ids.get('history')),/含跳过/);assert.match(textOf(v.ids.get('history')),/原命令结果未知/);all.find(e=>e.tag==='button'&&e.textContent==='下一页').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'toolReport',token:source,offset:20,outcome:'ALL'});assert.equal(v.ids.get('input').value,'CONTINUE_EDITING');
});
function webview(){const ids=new Map(),out=[],events={},persisted=[];const document={body:new Element('body'),querySelectorAll:selector=>[...ids.values()].filter(e=>e.getAttribute(selector.slice(1,-1))!==null),getElementById:id=>{if(!ids.has(id))ids.set(id,new Element());return ids.get(id);},createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('#fragment')};
  document.documentElement={lang:'zh-CN'};document.getElementById('input-label').setAttribute('data-ui-key','给原会话发消息');document.getElementById('mode-label').setAttribute('data-ui-key','原 Agent 模式');document.getElementById('sync-draft').setAttribute('data-ui-key','保留输入并同步');document.getElementById('send').setAttribute('data-ui-aria-label','发送到原会话');document.getElementById('input').setAttribute('data-ui-placeholder','给原会话发消息');Object.assign(document.body.dataset,{uiLocale:'zh-CN',i18n:JSON.stringify(require('../i18n/vscode.json').messages)});document.body.setAttribute('data-product-name',require('../product.json').display_name);document.body.setAttribute('data-product-short-name',require('../product.json').short_name);document.getElementById('agent-mode').value='build';const window={addEventListener:(k,f)=>events[k]=f};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../media/view.js'),'utf8'),{document,window,marked:require('../../../apps/desktop-host/vendor/marked.cjs'),acquireVsCodeApi:()=>({postMessage:m=>out.push(m),getState:()=>null,setState:v=>persisted.push(JSON.parse(JSON.stringify(v)))})});
  const scope='a'.repeat(64),snapshot={projects:[],sessions:[],sessionTokens:[],projectId:null,session:null,scopeToken:scope,draft:{text:'',revision:0},status:{},error:null,pending:[],recoverable:[]};
  const show=(patch={})=>events.message({data:{type:'state',connection:'PAIRED',trusted:true,notice:'',snapshot:{...snapshot,...patch}}});show();return {ids,out,show,snapshot,persisted,body:document.body,receive:data=>events.message({data})};}
// batch44 AUTHORED_NOT_RUN: live unsynced form buffer, no implicit execution.
test('observation form keeps inactive paths and only applies them through explicit configuration',()=>{
 const v=webview(),token='b'.repeat(64),id='native:'+'a'.repeat(64),settings={workspace_token:null,write:false,commands_enabled:true,commands_text:'python check.py',processes:false,remote:false,tool_tokens:[],unavailable_original_tools:[],ttl_seconds:600,max_calls:24,max_output_tokens:4096};
 const cfg={supported:true,ready:true,token,settings,can_confirm:true,can_apply:true,workspaces:[],tools:[]},patch={session:{kind:'CANONICAL_CONVERSATION',conversation_ref:{id,revision:1},canonical_history:[],messages:[],artifacts:[]},connectionConfiguration:cfg,draft:{text:'KEEP_CHAT_INPUT',revision:1}};v.show(patch);const n=v.out.length;
 const enabled=v.ids.get('configuration-observation-enabled'),paths=v.ids.get('configuration-observation-paths');enabled.checked=true;enabled.fire('change');paths.value='reports/测试.xml\nspace name.py';paths.fire('input');enabled.checked=false;enabled.fire('change');assert.equal(paths.value,'reports/测试.xml\nspace name.py');assert.equal(v.ids.get('configuration-observation-options').hidden,true);enabled.checked=true;enabled.fire('change');assert.equal(paths.value,'reports/测试.xml\nspace name.py');assert.equal(v.out.length,n);
 v.ids.get('configuration-apply').fire('click');assert.equal(v.out.at(-1).type,'configurationApply');assert.equal(v.out.at(-1).settings.observation_paths_text,paths.value);assert.equal(v.out.at(-1).settings.observation_enabled,true);assert.equal(v.out.at(-1).settings.write,false);assert.equal(v.ids.get('input').value,'KEEP_CHAT_INPUT');
});
test('observation IME and changed preparation retain local paths until explicit review',()=>{
 const v=webview(),token='b'.repeat(64),id='native:'+'a'.repeat(64),settings={workspace_token:null,write:false,commands_enabled:true,commands_text:'python check.py',observation_enabled:true,observation_paths_text:'original.xml',processes:false,remote:false,tool_tokens:[],unavailable_original_tools:[],ttl_seconds:600,max_calls:24,max_output_tokens:4096},cfg={supported:true,ready:true,token,settings,can_confirm:true,can_apply:true,workspaces:[],tools:[]},patch={session:{kind:'CANONICAL_CONVERSATION',conversation_ref:{id,revision:1},canonical_history:[],messages:[],artifacts:[]},connectionConfiguration:cfg};v.show(patch);
 const input=v.ids.get('configuration-observation-paths'),n=v.out.length;input.fire('compositionstart');input.value='reports/未完成/';input.fire('input');assert.equal(v.ids.get('configuration-apply').disabled,true);input.fire('compositionend');v.show({...patch,connectionConfiguration:{...cfg,token:'c'.repeat(64)}});assert.equal(input.value,'reports/未完成/');assert.equal(v.ids.get('configuration-apply').disabled,true);assert.equal(v.out.length,n);assert.equal(v.persisted.at(-1).configuration_buffers[0][1].settings.observation_paths_text,'reports/未完成/');
});
test('actual webview input handles IME, draft CAS and older acknowledgements without clobber',()=>{const v=webview(),input=v.ids.get('input');input.value='hello';input.fire('input');const draft=v.out.at(-1);assert.equal(draft.type,'draft');assert.equal(draft.revision,0);v.show({draft:{text:'',revision:0}});assert.equal(input.value,'hello');input.value='hello new';input.fire('input');assert.equal(v.out.at(-1).revision,1);v.show({draft:{text:'hello',revision:1}});assert.equal(input.value,'hello new');
  input.fire('compositionstart');input.value='中文输入';const before=v.out.length;input.fire('input');input.fire('keydown',{ctrlKey:true,key:'Enter',isComposing:true});assert.equal(v.out.length,before);input.fire('compositionend');assert.equal(v.out.at(-1).text,'中文输入');v.ids.get('agent-mode').value='plan';v.ids.get('send').fire('click');assert.equal(v.out.at(-1).agent,'plan');assert.equal(v.out.at(-1).revision,3);});
test('renderer treats agent HTML as inert text and never makes arbitrary URI or HTML links',()=>{const v=webview();v.show({session:{session_ref:{id:'s'},messages:[{info:{role:'assistant'},parts:[{type:'text',text:'<img src=x onerror=alert(1)> vscode://evil'},{type:'file',filename:'javascript:evil()'}]}],artifacts:[],artifact_channel:'NOT_CONNECTED'}});const h=v.ids.get('history');assert.equal(textOf(h.children[0].children[1]),'<img src=x onerror=alert(1)> vscode://evil');assert.equal(h.children[0].children[2].tag,'div');assert.match(v.ids.get('coverage').textContent,/尚未连接/);});
test('session choice carries bound exact list token; unknown offers inspection only',()=>{const v=webview();v.show({sessions:[{session_ref:{id:'s'},title:'One'}],sessionTokens:['f'.repeat(64)],pending:[{request_id:'r',method:'session.message',status:'UNKNOWN',operation_id:'op',observed_status:'RETURNED'}]});v.ids.get('sessions').value='0';v.ids.get('sessions').fire('change');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'session',index:0,token:'f'.repeat(64)});const p=v.ids.get('pending');p.children[0].children[1].fire('click');assert.equal(v.out.at(-1).type,'inspect');assert.equal(v.out.at(-1).id,'r');assert.match(p.children[0].children.at(-1).textContent,/原执行已返回/);});
function extensionFixture({platform='linux',trusted=false,language='zh-CN',preference='system',settings={},clientBoundary=null}={}){const source=fs.readFileSync(path.join(__dirname,'../extension.cjs'),'utf8'),module={exports:{}};const commands=[],registered=new Map();let selectedLanguage=preference,configurationChanged,provider;const disposable={dispose(){}};const vscode={env:{remoteName:undefined,language},workspace:{isTrusted:trusted,getConfiguration:()=>({get:(key,fallback)=>key==='language'?selectedLanguage:settings[key]??fallback}),onDidChangeConfiguration:fn=>{configurationChanged=fn;return disposable;},registerTextDocumentContentProvider:()=>disposable},window:{registerWebviewViewProvider:(_id,value)=>{provider=value;return disposable;}},commands:{executeCommand:async(...args)=>commands.push(args),registerCommand:(id,callback)=>{registered.set(id,callback);return disposable;}},Uri:{joinPath:(base,...parts)=>({toString:()=>String(base)+'/'+parts.join('/')})}};const req=id=>id==='vscode'?vscode:id==='./client.cjs'&&clientBoundary?{...require('../client.cjs'),...clientBoundary}:require(require.resolve(id,{paths:[path.join(__dirname,'..')]}));
  vm.runInNewContext(`(function(require,module,exports,__dirname){${source}\n})`,{process:{platform,env:process.env},Buffer,setTimeout,clearTimeout,console})(req,module,module.exports,path.join(__dirname,'..'));return {api:module.exports,vscode,commands,registered,get provider(){return provider;},changeLanguage:value=>{selectedLanguage=value;configurationChanged?.({affectsConfiguration:key=>key==='eeasCompanion.language'});}};}
test('Source16 human native discussion title does not append its internal id',()=>{const v=webview(),session={kind:'NATIVE_CONVERSATION',project_id:'p1',conversation_id:'chat_private_identifier',title:'原生文件反馈验收',messages:[]};v.show({session,sessions:[{...session,title:'Earlier directory title'}]});const options=v.ids.get('sessions').children.filter(e=>e.value==='0');assert.equal(options.length,1);assert.equal(options[0].textContent,session.title);assert.equal(options[0].title,session.title);assert.equal(v.ids.get('sessions').title,session.title);});
test('Source16 cold applied receipt is visible beside the original preview without rewriting the original answer',()=>{const v=webview(),plan={id:'plan',revision:1,sha256:'a'.repeat(64)},session={kind:'NATIVE_CONVERSATION',project_id:'p1',conversation_id:'discussion',messages:[{info:{id:'parent',role:'assistant'},native_parent_operation_id:'parent',native_action_token:'b'.repeat(64),native_plans:[{record_ref:plan}],parts:[{type:'text',text:'已准备预览，尚未写入。'}]}]};v.show({session,native:{methods:['native.rework.preview'],applied:[{parent_operation_id:'parent',plan_ref:plan}],context:null}});const h=v.ids.get('history'),buttons=descendants(h).filter(e=>e.tag==='button');assert.ok(buttons.some(e=>e.textContent==='查看已应用修改'));assert.ok(textOf(h).includes('此预览上次已应用。'));assert.ok(textOf(h).includes('已准备预览，尚未写入。'));assert.ok(!buttons.some(e=>e.textContent==='应用修改'));});
test('Source16 successful native stop uses task state and leaves no persistent global warning',async t=>{const f=extensionFixture({platform:'win32',trusted:true}),dir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-stop-notice16-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const s=new f.api.Sidebar({globalStorageUri:{fsPath:dir}});t.after(()=>s.dispose());s.connection='PAIRED';s.controller.selected={kind:'NATIVE_CONVERSATION',project_id:'p1',conversation_id:'c1',active_operation_id:'task',native_messages:[],messages:[]};s.controller.cancel=async()=>({status:'OK'});s.controller.refreshSelected=async()=>{};await s.handle({type:'cancel'});assert.equal(s.notice,'');});
test('Source16 a new send clears the previous action notice before dispatch',async t=>{const f=extensionFixture({platform:'win32',trusted:true}),dir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-new-notice16-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const s=new f.api.Sidebar({globalStorageUri:{fsPath:dir}});t.after(()=>s.dispose());s.connection='PAIRED';s.notice='文件有冲突，人工内容已保留。';s.checkNativeDraft=async()=>{};let sent=0;s.controller.send=async()=>{sent++;};await s.handle({type:'send',token:'a'.repeat(64),revision:1,agent:'build'});assert.equal(sent,1);assert.equal(s.notice,'');});
test('Source16 legacy stop acknowledgement belongs only to its original still-active operation',async t=>{const f=extensionFixture({platform:'win32',trusted:true}),dir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-legacy-stop16-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const s=new f.api.Sidebar({globalStorageUri:{fsPath:dir}});t.after(()=>s.dispose());const frames=[];s.view={webview:{postMessage:m=>frames.push(m)}};s.connection='PAIRED';s.controller.selected={project_id:'p1',session_ref:{id:'s1',revision:1,sha256:'a'.repeat(64)},generation:1,active_operation_id:'old-task',messages:[]};s.controller.cancel=async()=>({status:'OK'});s.controller.refreshSelected=async()=>{};await s.handle({type:'cancel'});s.render();assert.match(frames.at(-1).notice,/已请求停止/);s.controller.selected={...s.controller.selected,active_operation_id:'next-task'};s.render();assert.equal(frames.at(-1).notice,'');});
test('Source16 a delayed action error cannot overwrite another discussion notice',async t=>{const f=extensionFixture(),dir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-late-notice16-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const s=new f.api.Sidebar({globalStorageUri:{fsPath:dir},extensionUri:'file:///extension',subscriptions:[]});t.after(()=>s.dispose());let callback,rejectAction;const disposable={dispose(){}};const view={visible:false,webview:{cspSource:'test',asWebviewUri:u=>u.toString(),postMessage(){},onDidReceiveMessage:fn=>{callback=fn;return disposable;}},onDidChangeVisibility:()=>disposable,onDidDispose:()=>disposable};s.controller.selected={kind:'NATIVE_CONVERSATION',project_id:'p1',conversation_id:'old',native_messages:[],messages:[]};await s.resolveWebviewView(view);s.handle=async()=>await new Promise((_r,reject)=>{rejectAction=reject;});callback({type:'send'});s.controller.epoch++;s.controller.selected={...s.controller.selected,conversation_id:'new'};s.notice='CURRENT_DISCUSSION_NOTICE';rejectAction(Object.assign(Error('late failure'),{code:'SOURCE_UNAVAILABLE'}));await new Promise(setImmediate);assert.equal(s.notice,'CURRENT_DISCUSSION_NOTICE');});
test('Source16 confirmed revocation of the same client remains a global connection state after navigation',async t=>{const f=extensionFixture(),dir=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-revoke-notice16-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const s=new f.api.Sidebar({globalStorageUri:{fsPath:dir},extensionUri:'file:///extension',subscriptions:[]});t.after(()=>s.dispose());let callback,rejectAction;const disposable={dispose(){}};const view={visible:false,webview:{cspSource:'test',asWebviewUri:u=>u.toString(),postMessage(){},onDidReceiveMessage:fn=>{callback=fn;return disposable;}},onDidChangeVisibility:()=>disposable,onDidDispose:()=>disposable};s.controller.client={close(){}};s.controller.selected={kind:'NATIVE_CONVERSATION',project_id:'p1',conversation_id:'old',native_messages:[],messages:[]};await s.resolveWebviewView(view);s.connection='PAIRED';s.handle=async()=>await new Promise((_r,reject)=>{rejectAction=reject;});callback({type:'inspect'});s.controller.epoch++;s.controller.selected={...s.controller.selected,conversation_id:'new'};rejectAction(Object.assign(Error('revoked'),{code:'PAIRING_REVOKED'}));await new Promise(setImmediate);assert.equal(s.connection,'REVOKED');assert.equal(s.notice,'');});
test('webview schema refuses unknown keys/commands, requires exact scope and explicit mode',()=>{const {api}=extensionFixture();assert.throws(()=>api.parseMessage({type:'open',url:'file:///private'}),/INVALID_WEBVIEW_MESSAGE/);assert.throws(()=>api.parseMessage({type:'start',command:'rm -rf'}),/INVALID_WEBVIEW_MESSAGE/);assert.throws(()=>api.parseMessage({type:'session',index:0}),/INVALID_WEBVIEW_MESSAGE/);assert.throws(()=>api.parseMessage({type:'send',token:'a'.repeat(64),revision:1,agent:'shell'}),/INVALID_WEBVIEW_MESSAGE/);assert.equal(api.parseMessage({type:'send',token:'a'.repeat(64),revision:1,agent:'plan'}).agent,'plan');});
test('actual HTML factory has nonce CSP, restricted local resources and rejects nonWindows startup',async t=>{const {api}=extensionFixture(),dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'eeas-view-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const subscriptions=[],sidebar=new api.Sidebar({globalStorageUri:{fsPath:dir},extensionUri:'fixture-extension',subscriptions});
  const disposable={dispose(){}},view={visible:true,webview:{cspSource:'vscode-webview-resource:',asWebviewUri:uri=>uri.toString(),onDidReceiveMessage:()=>disposable,postMessage:()=>{}},onDidChangeVisibility:()=>disposable,onDidDispose:()=>disposable};await sidebar.resolveWebviewView(view);const html=view.webview.html;assert.match(html,/default-src 'none'/);assert.match(html,/connect-src 'none'/);assert.match(html,/script-src 'nonce-[A-Za-z0-9+/=]+'/);assert.match(html,/id="input"/);const brand=html.match(/<span class="brand-mark">([\s\S]+?)<\/span>/)[1];assert.match(brand,/fill="currentColor"/);assert.doesNotMatch(brand,/linearGradient|url\(#/);assert.equal(view.webview.options.localResourceRoots.length,1);assert.doesNotMatch(html,/http[s]?:|unsafe-inline|eval\(/);await assert.rejects(()=>sidebar.start(),{code:'LOCAL_WINDOWS_REQUIRED'});sidebar.dispose();});

test('send clear racing with fresh input never clobbers rejected CAS; switch and rebase preserve text',()=>{const v=webview(),input=v.ids.get('input');input.value='sent first';input.fire('input');let draft=v.out.at(-1);v.show({draft:{text:'sent first',revision:1}});v.receive({type:'draftResult',token:'a'.repeat(64),sequence:draft.sequence,accepted:true,revision:1});input.value='new thought';input.fire('input');draft=v.out.at(-1);v.show({draft:{text:'',revision:2}});assert.equal(input.value,'new thought');v.receive({type:'draftResult',token:'a'.repeat(64),sequence:draft.sequence,accepted:false,revision:2});assert.equal(input.value,'new thought');assert.equal(v.ids.get('send').disabled,true);v.show({scopeToken:'b'.repeat(64),draft:{text:'other',revision:0}});assert.equal(input.value,'other');v.show({draft:{text:'',revision:2}});assert.equal(input.value,'new thought');v.ids.get('sync-draft').fire('click');const rebased=v.out.at(-1);assert.equal(rebased.type,'draft');assert.equal(rebased.revision,2);assert.equal(rebased.text,'new thought');v.show({draft:{text:'new thought',revision:3}});v.receive({type:'draftResult',token:'a'.repeat(64),sequence:rebased.sequence,accepted:true,revision:3});assert.equal(input.value,'new thought');});

test('actual host draft handler returns exact accepted/rejected ack around persisted revision change',async t=>{const {api}=extensionFixture({platform:'win32',trusted:true}),dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'eeas-draft-host-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const sidebar=new api.Sidebar({globalStorageUri:{fsPath:dir},subscriptions:[]}),messages=[];sidebar.view={webview:{postMessage:m=>messages.push(m)}};sidebar.controller.selected={project_id:'p1',session_ref:{id:'s1',revision:1,sha256:'a'.repeat(64)},generation:1};const token=sidebar.controller.snapshot().scopeToken;await sidebar.handle({type:'draft',token,revision:0,text:'first',sequence:1});assert.equal(messages.at(-1).type,'draftResult');assert.equal(messages.at(-1).accepted,true);assert.equal(messages.at(-1).revision,1);sidebar.state.commit(v=>{v.drafts[token].text='';v.drafts[token].revision=2;});await sidebar.handle({type:'draft',token,revision:1,text:'new thought',sequence:2});const ack=messages.findLast(m=>m.type==='draftResult');assert.equal(ack.accepted,false);assert.equal(ack.revision,2);assert.equal(sidebar.state.value.drafts[token].text,'');sidebar.dispose();});

test('UNIT first-use onboarding explains the desktop dependency and shows only install or association actions',()=>{
  const v=webview();v.receive({type:'state',connection:'APP_NOT_CONFIGURED',connecting:false,trusted:true,notice:'APP_NOT_CONFIGURED',snapshot:{...v.snapshot,scopeToken:null}});
  const actions=v.ids.get('actions').children;assert.deepEqual(actions.map(a=>a.textContent),['关联桌面 App','安装说明']);
  assert.equal(v.ids.get('composer').hidden,true);assert.equal(v.ids.get('recovery').hidden,true);assert.equal(v.ids.get('reveal').hidden,true);
  const text=e=>[e.textContent,...e.children.map(text)].join('\n');const copy=text(v.ids.get('history'));assert.match(copy,/需要完整 Cirven 安装包/);assert.match(copy,/离线包/);assert.match(copy,/START_EEAS.cmd/);
  actions[1].fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'installationHelp'});
  v.receive({type:'state',connection:'PENDING',connecting:true,trusted:true,notice:'',snapshot:v.snapshot});assert.equal(v.ids.get('actions').children.length,1);assert.match(v.ids.get('actions').children[0].textContent,/批准后自动连接/);
  v.show();assert.equal(v.ids.get('composer').hidden,false);assert.equal(v.ids.get('reveal').hidden,false);
});
test('duplicate task titles get distinct short references and raw-name tooltips; selected option requires the exact generation and ref',()=>{
 const v=webview(),base={project_id:'p1',generation:1,title:'检查同一份 calc.py',messages:[]},one={...base,session_ref:{id:'samehead0001',revision:1,sha256:'a'.repeat(64)}},two={...base,generation:2,session_ref:{id:'samehead0002',revision:1,sha256:'b'.repeat(64)}};
 v.show({projectId:'p1',sessions:[one,two],session:two});const select=v.ids.get('sessions'),choices=select.children.slice(1);assert.equal(choices.length,2);assert.notEqual(choices[0].textContent,choices[1].textContent);assert.ok(choices.every(x=>x.textContent.startsWith(base.title+' · ')));assert.ok(choices[0].title.includes(one.session_ref.id));assert.ok(choices[1].title.startsWith(base.title+'\n'));assert.equal(select.value,'1');
 v.show({projectId:'p1',sessions:[one,two],session:{...two,generation:1}});assert.equal(select.value,'');v.show({projectId:'p1',sessions:[one,two],session:{...two,session_ref:{...two.session_ref,sha256:'c'.repeat(64)}}});assert.equal(select.value,'');v.show({projectId:'p1',sessions:[one,two],session:null,scopeToken:null});assert.equal(select.value,'');
});
test('public refresh and reordered session summaries retain the current draft, caret, history DOM and reading position without a send',()=>{
 const v=webview(),session={project_id:'p1',session_ref:{id:'s1',revision:1,sha256:'a'.repeat(64)},generation:1,title:'原任务',messages:[{info:{id:'message-1',role:'assistant'},parts:[{type:'text',text:'保留原回答'}]}]},other={...session,session_ref:{id:'s2',revision:1,sha256:'b'.repeat(64)}};
 v.show({projectId:'p1',sessions:[session,other],session});const history=v.ids.get('history'),article=history.children[0],input=v.ids.get('input');history.scrollHeight=1000;history.clientHeight=100;history.scrollTop=200;input.value='尚未发送的新问题';input.fire('input');input.selectionStart=2;input.selectionEnd=4;const count=v.out.length;v.ids.get('refresh').fire('click');assert.equal(v.out.length,count+1);assert.equal(v.out.at(-1).type,'refresh');
 v.show({projectId:'p1',sessions:[other,session],session});assert.equal(v.ids.get('sessions').value,'1');assert.equal(input.value,'尚未发送的新问题');assert.equal(input.selectionStart,2);assert.equal(input.selectionEnd,4);assert.equal(history.children[0],article);assert.equal(history.scrollTop,200);assert.equal(v.out.filter(x=>x.type==='send'||x.type==='start'||x.type==='repairPair').length,0);
});

test('one header Refresh remains reachable after a lost selected-session transport, with bounded state gating',()=>{
 const v=webview(),session={session_ref:{id:'s1'},messages:[]},input=v.ids.get('input');v.show({session});input.value='Preserve unsent input';input.fire('input');
 const lost={type:'state',connection:'APP_NOT_CONNECTED',trusted:true,connecting:false,notice:'ECONNRESET',snapshot:{...v.snapshot,session}};v.receive(lost);
 assert.equal(v.ids.get('navigation').hidden,true);assert.equal(v.ids.get('refresh').hidden,false);assert.equal(v.ids.get('refresh').disabled,false);assert.equal(input.value,'Preserve unsent input');
 const before=v.out.length;v.ids.get('refresh').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.slice(before))),[{type:'refresh'}]);
 for(const change of [{connecting:true},{trusted:false},{snapshot:{...lost.snapshot,loading:true}}]){v.receive({...lost,...change});assert.equal(v.ids.get('refresh').disabled,true);}
 for(const connection of ['APP_NOT_CONFIGURED','APP_NOT_FOUND','LOCAL_WINDOWS_REQUIRED','WORKSPACE_TRUST_REQUIRED','PAIRING_REQUIRED','PENDING','UNKNOWN','NOT_FOUND','DENIED','REVOKED','PROTOCOL_NOT_SUPPORTED']){v.receive({...lost,connection});assert.equal(v.ids.get('refresh').hidden,true,connection);}
 for(const connection of ['APP_NOT_CONNECTED','APP_NOT_RUNNING']){v.receive({...lost,connection,snapshot:{...lost.snapshot,session:null,scopeToken:null}});assert.equal(v.ids.get('refresh').hidden,false);assert.equal(v.ids.get('refresh').disabled,false);}assert.equal(v.out.some(m=>['send','connect','repairPair','start'].includes(m.type)),false);
});

test('production HTML has exactly one Refresh in the persistent header, outside hidden navigation',async t=>{
 const {api}=extensionFixture(),dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'eeas-refresh-html-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const sidebar=new api.Sidebar({globalStorageUri:{fsPath:dir},extensionUri:'fixture-extension',subscriptions:[]}),disposable={dispose(){}};
 await sidebar.resolveWebviewView({visible:false,webview:{cspSource:'vscode-webview-resource:',asWebviewUri:uri=>uri.toString(),onDidReceiveMessage:()=>disposable,postMessage:()=>{}},onDidChangeVisibility:()=>disposable,onDidDispose:()=>disposable});const html=sidebar.view.webview.html;
 assert.equal((html.match(/id="refresh"/g)||[]).length,1);assert.match(html.match(/<header[\s\S]*?<\/header>/)[0],/id="refresh"/);assert.doesNotMatch(html.match(/<section id="navigation"[\s\S]*?<\/section>/)[0],/id="refresh"/);sidebar.dispose();
});

test('UNIT installation help opens only the bundled guide without starting or trusting a runtime',async t=>{
  const {api,commands}=extensionFixture(),dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'eeas-guide-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const sidebar=new api.Sidebar({globalStorageUri:{fsPath:dir},extensionUri:'owned-extension',subscriptions:[]});
  await sidebar.handle({type:'installationHelp'});assert.equal(commands.length,1);assert.equal(commands[0][0],'markdown.showPreview');assert.equal(commands[0][1].toString(),'owned-extension/README.md');
  assert.equal(sidebar.controller.client,null);assert.throws(()=>api.parseMessage({type:'installationHelp',url:'https://untrusted.invalid'}),/INVALID_WEBVIEW_MESSAGE/);sidebar.dispose();
});

test('UNIT repeated observations preserve action buttons and select options without moving the history reader',()=>{
  const v=webview(),missing={type:'state',connection:'APP_NOT_CONFIGURED',connecting:false,trusted:true,notice:'APP_NOT_CONFIGURED',snapshot:{...v.snapshot,scopeToken:null}};
  v.receive(missing);const action=v.ids.get('actions').children[0];v.receive(missing);assert.equal(v.ids.get('actions').children[0],action);
  const session={session_ref:{id:'s'},messages:[{info:{role:'assistant'},parts:[{type:'text',text:'既有正文'}]}]};
  v.show({projects:[{id:'p',name:'超长的中文工程名'.repeat(30)}],sessions:[session],session});const projectOption=v.ids.get('projects').children[1];
  const h=v.ids.get('history');h.scrollHeight=5000;h.scrollTop=400;h.clientHeight=300;
  v.show({projects:[{id:'p',name:'超长的中文工程名'.repeat(30)}],sessions:[session],session:{...session,messages:[...session.messages,{info:{role:'assistant'},parts:[{type:'text',text:'新消息'}]}]}});
  assert.equal(v.ids.get('projects').children[1],projectOption);assert.equal(h.scrollTop,400);
});

test('UNIT tools show only observed execution state; unfamiliar states and file references never imply completion or opening',()=>{
  const v=webview();v.show({session:{session_ref:{id:'s'},messages:[{info:{role:'assistant'},parts:[
    {type:'tool',tool:'read',state:{status:'completed'}},{type:'tool',tool:'task',state:{status:'future_state'}},
    {type:'file',filename:'报告中文🙂'.repeat(100)+'.pptx'}]}],artifact_channel:'NOT_CONNECTED'}});
  const article=v.ids.get('history').children[0],states=descendants(article).filter(e=>(e.className||'').split(' ').includes('tool-state'));
  assert.deepEqual(states.map(e=>e.textContent),['已返回','状态待核查']);assert.equal(article.children[3].className,'file-reference');
  assert.equal((article.children[3].listeners.click||[]).length,0);assert.match(v.ids.get('coverage').textContent,/尚未连接/);
});

test('UNIT a lost connection hides composer and disables keyboard send while preserving its exact unsent input',()=>{
  const v=webview(),input=v.ids.get('input');input.value='保留这条未发送意见';input.fire('input');
  v.receive({type:'state',connection:'APP_NOT_RUNNING',trusted:true,notice:'',snapshot:{...v.snapshot,session:{session_ref:{id:'same'},messages:[]}}});
  assert.equal(v.ids.get('composer').hidden,true);assert.equal(input.value,'保留这条未发送意见');const count=v.out.length;
  input.fire('keydown',{ctrlKey:true,key:'Enter',preventDefault(){}});v.ids.get('send').fire('click');assert.equal(v.out.length,count);
});

test('UNIT layout switches preserve host-provided dark/high-contrast classes and disconnected state is not duplicated',()=>{
  const v=webview();v.body.className='vscode-high-contrast theme-provided';
  v.receive({type:'state',connection:'APP_NOT_CONFIGURED',trusted:true,notice:'APP_NOT_CONFIGURED',snapshot:{...v.snapshot,error:'APP_NOT_CONFIGURED',scopeToken:null}});
  assert.match(v.body.className,/vscode-high-contrast/);assert.match(v.body.className,/theme-provided/);assert.match(v.body.className,/onboarding-mode/);
  assert.equal(v.ids.get('actions').children.length,2);v.show();assert.match(v.body.className,/conversation-mode/);assert.doesNotMatch(v.body.className,/onboarding-mode/);assert.match(v.body.className,/vscode-high-contrast/);
});

test('UNIT live sidebar locale changes declared controls and fixed statuses while retaining user input, cursor, scroll and operation',()=>{
  const v=webview(),input=v.ids.get('input'),history=v.ids.get('history');
  const session={session_ref:{id:'same'},active_operation_id:'original',messages:[{info:{role:'assistant'},parts:[{type:'text',text:'执行'},{type:'file',filename:'计划.docx'},{type:'tool',tool:'task',state:{status:'running'}}]}]};
  v.show({session});input.value='发送';input.setSelectionRange(1,1);input.fire('input');history.scrollHeight=5000;history.scrollTop=350;history.clientHeight=200;
  const before=v.out.length;v.receive({type:'state',locale:'en',connection:'PAIRED',trusted:true,notice:'',snapshot:{...v.snapshot,session}});
  assert.equal(input.value,'发送');assert.equal(input.selectionStart,1);assert.equal(history.scrollTop,350);assert.equal(v.out.length,before);
  assert.equal(v.ids.get('send').getAttribute('aria-label'),'Send to this conversation');assert.equal(v.ids.get('sync-draft').textContent,'Keep input and sync');
  assert.equal(v.ids.get('connection').textContent,'Connected');assert.match(v.ids.get('operation-status').textContent,/running/i);
  const article=history.children[0];assert.equal(textOf(article.children[1]),'执行');assert.equal(article.children[2].textContent,'计划.docx');assert.deepEqual(descendants(article).filter(e=>(e.className||'').split(' ').includes('tool-state')).map(e=>e.textContent),['Running']);
  v.receive({type:'state',locale:'zh-CN',connection:'PAIRED',trusted:true,notice:'',snapshot:{...v.snapshot,session}});assert.equal(v.ids.get('send').getAttribute('aria-label'),'发送到原会话');assert.equal(input.value,'发送');
});
test('UNIT extension language preference rerenders the same client and private draft without starting, reconnecting or resetting scope',async t=>{
  const f=extensionFixture({language:'en-US'}),dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'cirven-language-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const context={globalStorageUri:{fsPath:dir},extensionUri:'owned-extension',subscriptions:[]};f.api.activate(context);const sidebar=f.provider,posted=[];
  sidebar.view={webview:{postMessage:m=>posted.push(m)}};sidebar.connection='PAIRED';const client={close(){throw Error('Language change must not close original client');}};sidebar.controller.client=client;
  sidebar.controller.selected={project_id:'project',session_ref:{id:'owner',revision:1,sha256:'a'.repeat(64)},generation:1};
  const scope=sidebar.controller.snapshot().scopeToken;sidebar.controller.setDraft(scope,0,'计划');const before=sidebar.connectionEpoch;
  f.changeLanguage('zh-CN');assert.equal(posted.at(-1).locale,'zh-CN');assert.equal(posted.at(-1).snapshot.draft.text,'计划');assert.equal(sidebar.controller.client,client);assert.equal(sidebar.connectionEpoch,before);
  f.changeLanguage('system');assert.equal(posted.at(-1).locale,'en');assert.equal(sidebar.connectionEpoch,before);assert.deepEqual(f.commands,[]);
  await sidebar.installationHelp();assert.equal(f.commands.at(-1)[1].toString(),'owned-extension/README.en.md');sidebar.controller.client=null;sidebar.dispose();
});

test('UNIT known transport and local-save failures have bilingual UI without inventing failure of the original execution',()=>{
  const v=webview();v.receive({type:'state',locale:'en',connection:'PAIRED',trusted:true,notice:'LOCAL_RECEIPT_UNPERSISTED',snapshot:v.snapshot});
  const notice=v.ids.get('actions').children.at(-1);assert.match(notice.textContent,/reply arrived.*local saving failed/i);assert.match(notice.textContent,/not resend/i);assert.doesNotMatch(notice.textContent,/execution failed/i);
  v.receive({type:'state',locale:'zh-CN',connection:'PAIRED',trusted:true,notice:'LOCAL_RECEIPT_UNPERSISTED',snapshot:v.snapshot});assert.match(v.ids.get('actions').children.at(-1).textContent,/回复已收到/);
});

test('UNIT actual HTML labels and live accessible names share the selected locale without touching the draft',async t=>{
  const f=extensionFixture({language:'en-US'}),dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'cirven-at-language-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
  const sidebar=new f.api.Sidebar({globalStorageUri:{fsPath:dir},extensionUri:'owned',subscriptions:[]}),disposable={dispose(){}};
  const view={visible:false,webview:{cspSource:'vscode-webview-resource:',asWebviewUri:uri=>uri.toString(),onDidReceiveMessage:()=>disposable,postMessage(){}},onDidChangeVisibility:()=>disposable,onDidDispose:()=>disposable};
  await sidebar.resolveWebviewView(view);assert.match(view.webview.html,/<label[^>]*for="input"[^>]*data-ui-key="给原会话发消息"/);assert.match(view.webview.html,/<label[^>]*for="agent-mode"[^>]*data-ui-key="原 Agent 模式"/);sidebar.dispose();
  const v=webview(),input=v.ids.get('input');input.value='原 Agent 模式';input.fire('input');const count=v.out.length;
  v.receive({type:'state',locale:'en',connection:'PAIRED',trusted:true,notice:'',snapshot:v.snapshot});
  assert.equal(v.body.dataset.uiLocale,'en');assert.equal(v.ids.get('input-label').textContent,'Message…');assert.equal(v.ids.get('mode-label').textContent,'Mode');
  assert.equal(v.ids.get('send').getAttribute('aria-label'),'Send to this conversation');assert.doesNotMatch(input.getAttribute('placeholder'),/[一-龥]/);assert.equal(input.value,'原 Agent 模式');assert.equal(v.out.length,count);
  v.receive({type:'state',locale:'zh-CN',connection:'PAIRED',trusted:true,notice:'',snapshot:v.snapshot});assert.equal(v.body.dataset.uiLocale,'zh-CN');assert.equal(v.ids.get('input-label').textContent,'发消息…');assert.equal(v.ids.get('mode-label').textContent,'处理模式');assert.equal(input.value,'原 Agent 模式');
});


test('editor title opens the one right-hand chat without changing files or invoking desktop/task actions',async t=>{
 const f=extensionFixture({platform:'win32',trusted:true}),dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'cirven-right-view-'));
 t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));
 const context={globalStorageUri:{fsPath:dir},extensionUri:'owned-extension',subscriptions:[]};f.api.activate(context);
 const sidebar=f.provider,selected={project_id:'original-project',session_ref:{id:'original-session',revision:2,sha256:'a'.repeat(64)},messages:[{text:'User original note'}]};sidebar.controller.selected=selected;
 for(const name of ['start','reveal','connect'])sidebar[name]=()=>assert.fail('Opening chat must not call desktop '+name);
 const snapshot=directory=>fs.readdirSync(directory,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name)).map(entry=>entry.isDirectory()?[entry.name,snapshot(path.join(directory,entry.name))]:[entry.name,fs.readFileSync(path.join(directory,entry.name),'utf8')]);const files=()=>snapshot(dir);const before=files();
 await f.registered.get('eeasCompanion.showChat')();await f.registered.get('eeasCompanion.showChat')();
 assert.deepEqual(f.commands,[['eeasCompanion.chat.focus'],['eeasCompanion.chat.focus']]);
 assert.equal(f.provider,sidebar);assert.equal(sidebar.controller.selected,selected);assert.deepEqual(files(),before);sidebar.dispose();
});

test('chat defaults to the secondary sidebar and has an editor title action for every document',()=>{
 const manifest=JSON.parse(fs.readFileSync(path.join(__dirname,'../package.json'),'utf8'));
 assert.equal(manifest.contributes.viewsContainers.activitybar,undefined);
 assert.equal(manifest.contributes.viewsContainers.secondarySidebar.length,1);
 const container=manifest.contributes.viewsContainers.secondarySidebar[0];
 assert.equal(manifest.contributes.views[container.id].length,1);assert.equal(manifest.contributes.views[container.id][0].id,'eeasCompanion.chat');
 const button=manifest.contributes.menus['editor/title'].find(value=>value.command==='eeasCompanion.showChat');
 assert.ok(button);assert.match(button.group,/^navigation@/);assert.equal(button.when,undefined);
 assert.equal(manifest.engines.vscode,'^1.106.0');
 for(const filename of ['package.nls.json','package.nls.zh-cn.json'])assert.ok(JSON.parse(fs.readFileSync(path.join(__dirname,'..',filename),'utf8'))['command.showChat']);
});


test('context disclosure closes on explicit action or exact scope change without emitting a new draft or model message',()=>{
 const v=webview(),input=v.ids.get('input'),tools=v.ids.get('context-tools');
 input.value='保留这条原稿 😀';input.fire('input');const before=v.out.length;tools.open=true;
 let prevented=false,stopped=false;tools.fire('keydown',{key:'Escape',isComposing:true,preventDefault(){prevented=true;},stopPropagation(){stopped=true;}});assert.equal(tools.open,true);assert.equal(prevented,false);
 tools.fire('keydown',{key:'Escape',isComposing:false,preventDefault(){prevented=true;},stopPropagation(){stopped=true;}});assert.equal(tools.open,false);assert.equal(prevented,true);assert.equal(stopped,true);assert.equal(v.out.length,before);assert.equal(input.value,'保留这条原稿 😀');
 tools.open=true;v.ids.get('attach').fire('click');assert.equal(tools.open,false);assert.equal(v.out.at(-1).type,'attach');assert.equal(input.value,'保留这条原稿 😀');
 tools.open=true;v.show({scopeToken:'b'.repeat(64),draft:{text:'另一个会话原稿',revision:0}});assert.equal(tools.open,false);assert.equal(input.value,'另一个会话原稿');assert.equal(v.out.filter(m=>m.type==='send').length,0);
});

test('visible extension identity is only Cirven and compatibility IDs/right editor action remain unchanged',()=>{
 const manifest=require('../package.json'),product=require('../product.json');assert.equal(product.display_name,'Cirven');assert.equal(manifest.displayName,'Cirven');
 assert.equal(manifest.name,'eeas-companion');assert.equal(manifest.contributes.viewsContainers.secondarySidebar[0].title,'Cirven');
 for(const view of Object.values(manifest.contributes.views).flat())assert.equal(view.name,'Cirven');
 for(const command of manifest.contributes.commands)assert.equal(command.category,'Cirven');
 assert.equal(manifest.contributes.menus['editor/title'][0].command,'eeasCompanion.showChat');assert.equal(manifest.contributes.configuration.title,'Cirven');
});


test('empty recovery panel leaves no duplicated private-draft section, while real recoverable slots remain accessible',()=>{
 const v=webview();assert.equal(v.ids.get('recovery').hidden,true);
 v.show({recoverable:[{id:'old-slot',updated_at:'2026-10-16T01:00:00Z',drafts:2}]});assert.equal(v.ids.get('recovery').hidden,false);
 const entry=v.ids.get('old-drafts').children[0];entry.children[1].fire('click');assert.equal(v.out.at(-1).type,'recover');assert.equal(v.out.at(-1).id,'old-slot');
});

test('actual renderer puts only host-verified report buttons beside their source reply with exact token callback',()=>{
 const v=webview(),token='d'.repeat(64);const session={session_ref:{id:'s'},messages:[{info:{id:'msg_report',role:'assistant'},parts:[{type:'text',text:'已经制作。'},{type:'file',filename:'任意用户文件.docx'}]}],artifacts:[{filename:'<img onerror=alert(1)>长文件名.pptx',source:{message_id:'msg_report'}}],ide_coverage:{artifacts:{unavailable:0}},artifact_channel:'REGISTERED_REPORT_ARTIFACTS'};
 v.show({session,artifactTokens:[token]});const article=v.ids.get('history').children[0],file=article.children.at(-1);assert.equal(file.tag,'button');assert.equal(file.textContent,session.artifacts[0].filename);assert.equal(file.className,'artifact-file');assert.equal(article.children[2].tag,'div');file.fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'artifact',index:0,token});
 const input=v.ids.get('input');input.value='保留这段意见';input.fire('input');v.receive({type:'state',locale:'en',connection:'PAIRED',trusted:true,notice:'',snapshot:{...v.snapshot,session,artifactTokens:[token],draft:{text:'保留这段意见',revision:1}}});assert.equal(input.value,'保留这段意见');assert.equal(v.ids.get('history').children[0].children.at(-1).title,'Open in system app');
});
test('actual artifact message boundary accepts exact index/token only and stale late feedback cannot claim another conversation',async t=>{
 const f=extensionFixture({platform:'win32',trusted:true}),dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'cirven-artifact-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const sidebar=new f.api.Sidebar({globalStorageUri:{fsPath:dir},subscriptions:[]});t.after(()=>sidebar.dispose());
 for(const raw of [{type:'artifact',index:0,token:'a'.repeat(64),path:'C:/private.docx'},{type:'artifact',index:32,token:'a'.repeat(64)},{type:'artifact',index:0,token:'old'}])assert.throws(()=>f.api.parseMessage(raw),/INVALID_WEBVIEW_MESSAGE/);
 assert.equal(f.api.parseMessage({type:'artifact',index:0,token:'a'.repeat(64)}).type,'artifact');let resolve,called=0;sidebar.controller.openArtifact=async()=>{called++;return await new Promise(r=>{resolve=r;});};sidebar.notice='原来的提示';const pending=sidebar.artifact(0,'a'.repeat(64));sidebar.controller.epoch++;sidebar.notice='新话题';resolve({status:'OK'});await pending;assert.equal(called,1);assert.equal(sidebar.notice,'新话题');
});

test('the real sidebar opens host-resolved code/Markdown in native VSCode without writing its dirty document',async t=>{
 const f=extensionFixture({platform:'win32',trusted:true}),dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'cirven-file-editor-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const sidebar=new f.api.Sidebar({globalStorageUri:{fsPath:dir},subscriptions:[]});t.after(()=>sidebar.dispose());let shown=0,read=0;const dirty={isDirty:true,getText:()=> '保留用户手改',save(){assert.fail('must not save');}};
 f.vscode.Uri.parse=value=>({scheme:value.startsWith('file:')?'file':'vscode',fsPath:'D:\\Approved\\notes.md',toString:()=>value});f.vscode.workspace.openTextDocument=async()=>{read++;return dirty;};f.vscode.window.showTextDocument=async document=>{assert.equal(document,dirty);shown++;};sidebar.controller.openArtifact=async()=>({status:'OK',result:{status:'EDITOR_READY',editor_uri:'file:///D:/Approved/notes.md',editor_kind:'markdown'}});
 await sidebar.artifact(0,'a'.repeat(64));assert.equal(read,1);assert.equal(shown,0);assert.equal(f.commands.at(-1)[0],'markdown.showPreview');assert.equal(dirty.getText(),'保留用户手改');
 sidebar.controller.openArtifact=async()=>({status:'OK',result:{status:'EDITOR_READY',editor_uri:'file:///D:/Approved/code.ts',editor_kind:'text'}});await sidebar.artifact(0,'a'.repeat(64));assert.equal(shown,1);assert.equal(dirty.isDirty,true);
 let release;sidebar.controller.openArtifact=async()=>({status:'OK',result:{status:'EDITOR_READY',editor_uri:'file:///D:/Approved/old.ts',editor_kind:'text'}});f.vscode.workspace.openTextDocument=async()=>await new Promise(r=>{release=r;});const late=sidebar.artifact(0,'a'.repeat(64));await untilFixture(()=>release);sidebar.controller.epoch++;release(dirty);await late;assert.equal(shown,1,'late old scope must not reveal the editor');
});

test('actual Markdown renderer makes only host-bound original links clickable and keeps malicious HTML/code/remote references inert',()=>{
 const v=webview(),text='文稿：[**修改稿**](report.docx)；[notes][n]\n\n[n]: notes.md\n\n`[fake](wrong.docx)`\n\n<img src="file:///secret" onerror="evil()"> [remote](https://example.invalid/)';
 const artifacts=[{filename:'report.docx',target:'native',source:{message_id:'msg_links',part_id:'prt_links',link_index:0}},{filename:'notes.md',target:'editor',source:{message_id:'msg_links',part_id:'prt_links',link_index:1}}];
 v.show({session:{session_ref:{id:'same'},messages:[{info:{id:'msg_links',role:'assistant'},parts:[{id:'prt_links',type:'text',text}]}],artifacts},artifactTokens:['b'.repeat(64),'c'.repeat(64)]});
 const descendants=e=>[e,...e.children.flatMap(descendants)],nodes=descendants(v.ids.get('history')),links=nodes.filter(e=>e.className==='artifact-link');assert.equal(links.length,2);assert.equal(nodes.filter(e=>e.className==='artifact-file').length,0);assert.equal(nodes.some(e=>['img','a','script','iframe'].includes(e.tag)),false);links[0].fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'artifact',index:0,token:'b'.repeat(64)});links[1].fire('click');assert.equal(v.out.at(-1).index,1);assert.match(links[1].title,/notes.md/);
 const input=v.ids.get('input');input.value='保留未发内容';input.fire('input');v.receive({type:'state',locale:'en',connection:'PAIRED',trusted:true,notice:'',snapshot:{...v.snapshot,session:{session_ref:{id:'same'},messages:[{info:{id:'msg_links',role:'assistant'},parts:[{id:'prt_links',type:'text',text}]}],artifacts},artifactTokens:['b'.repeat(64),'c'.repeat(64)]}});assert.equal(input.value,'保留未发内容');assert.match(descendants(v.ids.get('history')).find(e=>e.className==='artifact-link').title,/system app/);
});
test('ordinary assistant Markdown without registered artifacts has semantic blocks, full tail and keyboard scroll regions',()=>{
 const v=webview(),tail='完整回复尾部 FINAL_REPLY_TAIL',text='# 核对结果\n\n**重点**与*说明*。\n\n3. 第三项\n4. 第四项\n\n| 任务 | 结果 |\n| --- | --- |\n| T01 | 保留 |\n\n```js\nconst saved = "exact";\n```\n\n'+('完整中文与 English paragraph.\n\n'.repeat(800))+tail;
 v.show({session:{session_ref:{id:'markdown'},messages:[{info:{id:'reply',role:'assistant'},parts:[{id:'body',type:'text',text}]}],artifacts:[]}});
 const prose=v.ids.get('history').children[0].children[1],nodes=descendants(prose);
 for(const tag of ['h3','strong','em','ol','li','table','thead','tbody','tr','th','td','pre','code'])assert.ok(nodes.some(e=>e.tag===tag),tag);
 assert.equal(nodes.find(e=>e.tag==='ol').getAttribute('start'),'3');
 assert.equal(nodes.find(e=>e.className==='prose-table').getAttribute('tabindex'),'0');assert.equal(nodes.find(e=>e.tag==='pre').getAttribute('tabindex'),'0');
 assert.ok(textOf(prose).endsWith(tail));assert.ok(textOf(prose).includes('const saved = "exact";'));
 assert.equal(nodes.some(e=>e.tag==='button'||e.tag==='a'),false);assert.deepEqual(v.out.map(row=>row.type),['ready']);
});
test('user and status text retain literal Markdown; all unregistered assistant links, images and raw HTML remain inert',()=>{
 const v=webview(),literal='## Literal **text** [file](file:///D:/x.docx) <b>raw</b>',unsafe='**Readable** [remote](https://blocked.invalid/) [command](command:run) ![alt](https://blocked.invalid/img) <script>window.evil=1</script> <img src=x onerror=evil()>',session={session_ref:{id:'s'},messages:[{info:{id:'user',role:'user'},parts:[{type:'text',text:literal}]},{info:{id:'status',role:'system'},parts:[{type:'text',text:literal}]},{info:{id:'assistant',role:'assistant'},parts:[{type:'text',text:unsafe}]}],artifacts:[]};
 v.show({session});const history=v.ids.get('history');
 for(const index of [0,1]){const prose=history.children[index].children[1];assert.equal(prose.textContent,literal);assert.equal(prose.children.length,0);}
 const nodes=descendants(history.children[2]);assert.ok(nodes.some(e=>e.tag==='strong'));assert.equal(nodes.some(e=>['a','img','script','iframe','button'].includes(e.tag)),false);
 assert.ok(textOf(history.children[2]).includes('<script>window.evil=1</script>'));assert.ok(textOf(history.children[2]).includes('alt'));assert.deepEqual(v.out.map(row=>row.type),['ready']);
});

test('session changes stay collapsed, send only host tokens, and retain draft/history across observations and scope changes',()=>{
 const v=webview(),diffs={scopeToken:'b'.repeat(64),loaded:false,loading:false,files:[],error:null};v.show({session:{session_ref:{id:'s1'},messages:[]},diffs});const panel=v.ids.get('session-changes');assert.equal(panel.open,false);assert.equal(v.out.some(m=>m.type==='diffs'),false);panel.open=true;panel.fire('toggle');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'diffs',token:diffs.scopeToken});
 const file={path:'src/很长相似文件名-第一份.ts',status:'modified',additions:3,deletions:2,content_scope:'HUNK_EXCERPTS',availability:'AVAILABLE',token:'c'.repeat(64)},loaded={...diffs,loaded:true,files:[file],coverage:{complete:false}};const input=v.ids.get('input');input.value='不改变这份私稿';input.fire('input');v.ids.get('history').scrollTop=42;v.show({session:{session_ref:{id:'s1'},messages:[]},diffs:loaded});assert.equal(panel.open,true);assert.equal(input.value,'不改变这份私稿');const first=v.ids.get('changes-files').children[0];first.fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'sessionDiff',index:0,token:file.token});const prior=v.out.length;v.show({session:{session_ref:{id:'s1'},messages:[]},diffs:loaded});assert.equal(v.ids.get('changes-files').children[0],first);assert.equal(v.out.length,prior);assert.match(first.children[0].children[1].textContent,/改动片段/);
 let prevented=false;v.ids.get('diffs-refresh').fire('click',{preventDefault(){prevented=true;},stopPropagation(){}});assert.equal(prevented,true);assert.equal(panel.open,true);assert.equal(v.out.at(-1).type,'diffs');
 v.show({session:{session_ref:{id:'s1'},messages:[]},diffs:{...loaded,scopeToken:'e'.repeat(64),files:[{...file,availability:'BINARY',content_scope:'UNAVAILABLE'}]}});assert.equal(panel.open,false);assert.equal(v.ids.get('changes-files').children[0].disabled,true);assert.equal(input.value,'不改变这份私稿');
});

test('actual sidebar and readonly provider open exact historical texts once without disk/buffer access or stale reveal',async t=>{
 const {fixture}=require('./diff-fixture.cjs'),f=extensionFixture({platform:'win32',trusted:true}),dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'cirven-task-diff-'));t.after(()=>fs.rmSync(dir,{recursive:true,force:true}));const providers=new Map();f.vscode.workspace.registerTextDocumentContentProvider=(scheme,value)=>{providers.set(scheme,value);return {dispose(){}};};const context={globalStorageUri:{fsPath:dir},subscriptions:[]};f.api.activate(context);const sidebar=f.provider;t.after(()=>sidebar.dispose());f.vscode.Uri.parse=value=>{const u=new URL(value);return {scheme:u.protocol.slice(0,-1),path:decodeURIComponent(u.pathname),authority:u.host,query:u.search.slice(1),fragment:u.hash.slice(1)};};f.vscode.workspace.openTextDocument=()=>assert.fail('must not open or read current file');f.vscode.window.activeTextEditor={document:{isDirty:true,getText:()=>assert.fail('must not read dirty buffer'),save:()=>assert.fail('must not save')}};
 for(const full of [false,true]){const data=fixture({full,sourceGeneration:1});sidebar.controller.selected=data.session;sidebar.controller.client={close(){},rpc:async method=>({status:'OK',result:method==='session.diff'?data.directory:data.file})};await sidebar.handle({type:'diffs',token:sidebar.controller.snapshot().diffs.scopeToken});const token=sidebar.controller.snapshot().diffs.files[0].token;await sidebar.handle({type:'sessionDiff',index:0,token});const command=f.commands.at(-1);assert.equal(command[0],'vscode.diff');const provider=providers.get('cirven-session-diff');assert.equal(provider.provideTextDocumentContent(command[1]),data.file.before);assert.equal(provider.provideTextDocumentContent(command[2]),data.file.after);assert.match(command[3],full?/历史版本.*完整文件/:/改动片段/);}
 const before=f.commands.length,data=fixture();sidebar.controller.selected=data.session;let release;sidebar.controller.client.rpc=async method=>method==='session.diff'?{status:'OK',result:data.directory}:new Promise(r=>release=r);await sidebar.handle({type:'diffs',token:sidebar.controller.snapshot().diffs.scopeToken});const token=sidebar.controller.snapshot().diffs.files[0].token,late=sidebar.handle({type:'sessionDiff',index:0,token});sidebar.controller.epoch++;sidebar.controller.selected={...data.session,generation:3};sidebar.render();release({status:'OK',result:data.file});await late;assert.equal(f.commands.length,before);assert.equal(sidebar.diffDocuments.entries.size,0);
 for(const raw of [{type:'diffs',token:'a'.repeat(64),path:'D:/secret'},{type:'sessionDiff',index:128,token:'a'.repeat(64)},{type:'sessionDiff',index:0,token:'bad'}])assert.throws(()=>f.api.parseMessage(raw),/INVALID_WEBVIEW_MESSAGE/);
 sidebar.controller.selected=data.session;await sidebar.handle({type:'sessionDiff',index:0,token:'0'.repeat(64)});assert.equal(sidebar.controller.snapshot().diffs.error,'DIFF_CHANGED');assert.equal(sidebar.notice,'');assert.equal(f.commands.length,before);assert.throws(()=>providers.get('cirven-session-diff').provideTextDocumentContent(f.vscode.Uri.parse('cirven-session-diff:/'+'0'.repeat(64)+'/before/missing.ts')),/只读快照已释放/);
});

test('history observation updates only the changed message and rebinds an altered artifact token',()=>{
  const v=webview(),token='e'.repeat(64),nextToken='f'.repeat(64),session={session_ref:{id:'same',revision:1,sha256:'c'.repeat(64)},generation:2,messages:[{info:{id:'older',role:'assistant'},parts:[{id:'text',type:'text',text:'Read the retained file'}]},{info:{id:'newer',role:'assistant'},parts:[{id:'tool',type:'tool',tool:'read',state:{status:'running'}}]}],artifacts:[{filename:'retained.md',target:'editor',source:{message_id:'older',part_id:'text'}}]};
  const show=()=>v.show({session:JSON.parse(JSON.stringify(session)),artifactTokens:[token]});show();const h=v.ids.get('history'),older=h.children[0],newer=h.children[1],file=older.children.at(-1);
  session.messages[1].parts[0].state.status='completed';show();assert.equal(h.children[0],older);assert.notEqual(h.children[1],newer);assert.deepEqual(descendants(h.children[1]).filter(e=>(e.className||'').split(' ').includes('tool-state')).map(e=>e.textContent),['已返回']);file.fire('click');assert.equal(v.out.at(-1).token,token);
  const updated=h.children[1];v.show({session,artifactTokens:[nextToken]});assert.notEqual(h.children[0],older);assert.equal(h.children[1],updated);h.children[0].children.at(-1).fire('click');assert.equal(v.out.at(-1).token,nextToken);
});
test('history removes old rows, preserves reordered messages, and releases reuse across exact owner changes',()=>{
  const v=webview(),session={session_ref:{id:'s',revision:1,sha256:'a'.repeat(64)},generation:1,messages:['one','two','three'].map(id=>({info:{id,role:'assistant'},parts:[{type:'text',text:id}]})),artifacts:[]};v.show({session});const h=v.ids.get('history'),old=[...h.children];
  session.messages=[session.messages[2],session.messages[0]];v.show({session});assert.deepEqual(h.children,[old[2],old[0]]);assert.equal(old[1].parentElement,null);
  v.show({session,scopeToken:'b'.repeat(64)});assert.equal(old[0].parentElement,null);assert.equal(old[2].parentElement,null);assert.notEqual(h.children[0],old[2]);
  const prior=[...h.children];v.show({session:{...session,generation:2},scopeToken:'b'.repeat(64)});assert.notEqual(h.children[0],prior[0]);
});

// Real BridgeClient/HTTP/discovery reads; VS Code + Windows installation metadata
// are explicit UNIT boundaries. No live App, real ticket, model or Windows.
async function readonlyRecoveryScene(t,{pairing='PAIRED',onPair=async()=>{},ticketPresent=true}={}){
 const http=require('node:http'),{randomUUID}=require('node:crypto'),client=require('../client.cjs');
 const dir=fs.mkdtempSync(path.join(process.env.TMPDIR||os.tmpdir(),'cirven-read-recovery-')),installed=path.join(dir,'installed'),root=String.raw`D:\Cirven\UnitRecovery`,ticket=client.newTicket(),calls=[],created=[],posted=[];let secretReads=0,discoveryReads=0,sidebar,seenPair;const pairObserved=new Promise(resolve=>{seenPair=resolve;});
 const session={project_id:'p1',session_ref:{id:'s1',revision:1,sha256:'a'.repeat(64)},generation:1,title:'Original task',messages:[{info:{id:'original',role:'assistant'},parts:[{type:'text',text:'Original saved answer'}]}]},instance=randomUUID();
 const envelope=(request_id,result)=>({protocol:client.PROTOCOL.protocol,instance_id:instance,request_id,status:'OK',result,error:null,local_persistence_status:'NOT_JOURNALED_READ_ONLY'});
 const stale=http.createServer((_req,res)=>res.destroy()),fresh=http.createServer(async(req,res)=>{
  res.setHeader('content-type','application/json');const row={method:req.method,path:req.url};calls.push(row);
  if(req.method==='GET'&&req.url==='/v1/pair/status'){seenPair();await onPair(sidebar);res.end(JSON.stringify(envelope(null,{pairing_status:pairing})));return;}
  let raw='';for await(const chunk of req)raw+=chunk;const body=JSON.parse(raw);row.rpc=body.method;
  assert.equal(req.method,'POST');assert.equal(req.url,'/v1/rpc');assert.ok(['status','projects.list','sessions.list','session.get'].includes(body.method),'Only read RPC allowed');
  const value=body.method==='status'?{connected:true}:body.method==='projects.list'?{projects:[{id:'p1',name:'One'},{id:'p2',name:'Two'}]}:body.method==='sessions.list'?{sessions:[session]}:session;
  res.end(JSON.stringify(envelope(body.request_id,value)));
 });
 await new Promise(resolve=>stale.listen(0,'127.0.0.1',resolve));await new Promise(resolve=>fresh.listen(0,'127.0.0.1',resolve));
 const discovery={schema:client.PROTOCOL.discovery_schema,protocol:client.PROTOCOL.protocol,instance_id:instance,nonce:'b'.repeat(64),port:fresh.address().port,pid:process.pid,version:'0.1.12'};const file=path.join(installed,'user-data','ide-companion','discovery.json');fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,JSON.stringify(discovery));
 class ActualClient extends client.BridgeClient{constructor(value){super(value);created.push(this);}}
 const fixture=extensionFixture({platform:'win32',trusted:true,settings:{installDirectory:root},clientBoundary:{BridgeClient:ActualClient,validateInstallation:value=>{assert.equal(value,root);return{protocolSupported:true};},readDiscovery:(value,data)=>{assert.equal(value,root);assert.equal(data,'');discoveryReads++;return client.readDiscovery(installed);}}});
 sidebar=new fixture.api.Sidebar({globalStorageUri:{fsPath:path.join(dir,'storage')},subscriptions:[],secrets:{get:async()=>{secretReads++;return ticketPresent?JSON.stringify(ticket):undefined;},store:async()=>assert.fail('No new ticket may be stored'),delete:async()=>assert.fail('No ticket may be deleted')}});sidebar.view={webview:{postMessage:value=>posted.push(value)}};sidebar.connection='PAIRED';
 const old=new ActualClient({discovery:{...discovery,port:stale.address().port},ticket,timeoutMs:1000});sidebar.controller.attach(old);sidebar.controller.projectId='p1';sidebar.controller.projects=[{id:'p1'},{id:'p2'}];sidebar.controller.sessions=[session];sidebar.controller.selected=session;const key=sidebar.controller.snapshot().scopeToken;sidebar.controller.setDraft(key,0,'Keep my unsent draft');sidebar.state.commit(value=>value.requests.push({request_id:'old-unknown',method:'session.message',scope:{project_id:'p1',session_ref:session.session_ref,generation:1},operation_id:'old-operation',status:'UNKNOWN'}));
 t.after(async()=>{sidebar.dispose();await Promise.all([new Promise(resolve=>stale.close(resolve)),new Promise(resolve=>fresh.close(resolve))]);fs.rmSync(dir,{recursive:true,force:true});});
 return{sidebar,old,calls,created,posted,pairObserved,key,session,currentDiscovery:discovery,get secretReads(){return secretReads;},get discoveryReads(){return discoveryReads;}};
}
test('explicit Refresh recovers one stale read endpoint through real existing-ticket GET and read RPCs only, preserving the draft and UNKNOWN',async t=>{
 let release;const gate=new Promise(resolve=>{release=resolve;}),s=await readonlyRecoveryScene(t,{onPair:()=>gate}),originalUnknown=JSON.stringify(s.sidebar.state.value.requests);const running=s.sidebar.handle({type:'refresh'});await s.pairObserved;await s.sidebar.handle({type:'refresh'});assert.equal(s.calls.filter(x=>x.path==='/v1/pair/status').length,1);release();await running;
 assert.equal(s.discoveryReads,1);assert.equal(s.secretReads,1);assert.equal(s.created.length,2);assert.equal(s.old.closed,true);assert.equal(s.created[1].closed,false);assert.equal(s.sidebar.connection,'PAIRED');assert.equal(s.posted.at(-1).connection,'PAIRED');assert.ok(s.posted.some(x=>x.connection==='APP_NOT_CONNECTED'));
 assert.equal(s.sidebar.controller.snapshot().draft.text,'Keep my unsent draft');assert.equal(s.sidebar.controller.selected.session_ref.id,'s1');assert.equal(JSON.stringify(s.sidebar.state.value.requests),originalUnknown);assert.deepEqual(s.calls.filter(x=>x.rpc).map(x=>x.rpc).sort(),['projects.list','session.get','sessions.list','status']);assert.ok(s.calls.every(x=>x.path!=='/v1/pair'));
});
test('one non-PAIRED existing-ticket result never polls or attaches a candidate and never clears the original directory',async t=>{
 for(const pairing of ['PENDING','UNKNOWN','NOT_FOUND','DENIED','REVOKED']){const s=await readonlyRecoveryScene(t,{pairing}),chosen=s.sidebar.controller.selected;await s.sidebar.handle({type:'refresh'});assert.equal(s.calls.length,1);assert.equal(s.created.length,2);assert.equal(s.created[1].closed,true);assert.equal(s.old.closed,false);assert.equal(s.sidebar.connection,pairing);assert.equal(s.sidebar.controller.selected,chosen);assert.equal(s.sidebar.controller.sessions[0],s.session);assert.equal(s.sidebar.controller.snapshot().draft.text,'Keep my unsent draft');assert.equal(s.sidebar.state.value.requests[0].status,'UNKNOWN');}
});
test('missing existing ticket and durable pending mutation do not pair, store a ticket or replace the old transport',async t=>{
 const missing=await readonlyRecoveryScene(t,{ticketPresent:false});await missing.sidebar.handle({type:'refresh'});assert.equal(missing.calls.length,0);assert.equal(missing.created.length,1);assert.equal(missing.old.closed,false);assert.equal(missing.sidebar.connection,'PAIRING_REQUIRED');
 const pending=await readonlyRecoveryScene(t);pending.sidebar.state.commit(value=>{value.requests[0].status='PENDING';});await pending.sidebar.handle({type:'refresh'});assert.equal(pending.calls.length,0);assert.equal(pending.secretReads,0);assert.equal(pending.old.closed,false);assert.equal(pending.sidebar.state.value.requests[0].status,'PENDING');
});
test('pending mutation, active read or changed project appearing during pairing GET reject and close only the temporary candidate',async t=>{
 for(const change of ['mutation','read','project']){const s=await readonlyRecoveryScene(t,{onPair:async sidebar=>{if(change==='mutation')sidebar.state.commit(value=>{value.requests[0].status='PENDING';});if(change==='read')sidebar.controller.inflight=true;if(change==='project'){sidebar.controller.epoch++;sidebar.controller.projectId='p2';sidebar.controller.selected=null;sidebar.controller.sessions=[];sidebar.controller.error='CURRENT_PROJECT';}}});await s.sidebar.handle({type:'refresh'});assert.equal(s.calls.length,1);assert.equal(s.old.closed,false);assert.equal(s.created[1].closed,true);assert.notEqual(s.sidebar.connection,'PAIRED');assert.equal(s.sidebar.state.value.drafts[s.key].text,'Keep my unsent draft');if(change==='project'){assert.equal(s.sidebar.controller.projectId,'p2');assert.equal(s.sidebar.controller.error,'CURRENT_PROJECT');}}
});
test('nontransport directory errors do not reconnect and transport errors cannot render a green paired status',async t=>{
 const s=await readonlyRecoveryScene(t);s.sidebar.controller.client.rpc=async()=>({status:'ERROR',error:{code:'INVALID_UPSTREAM_RESPONSE'}});await s.sidebar.handle({type:'refresh'});assert.equal(s.calls.length,0);assert.equal(s.created.length,1);assert.equal(s.old.closed,false);
 s.sidebar.controller.error='ECONNREFUSED';s.sidebar.render();assert.equal(s.posted.at(-1).connection,'APP_NOT_CONNECTED');assert.equal(s.sidebar.controller.snapshot().draft.text,'Keep my unsent draft');
});
test('a successful explicit read clears an obsolete transport notice without reconnecting or changing the current draft',async t=>{
 const s=await readonlyRecoveryScene(t);s.old.discovery=s.currentDiscovery;s.sidebar.connection='APP_NOT_CONNECTED';s.sidebar.notice='ECONNREFUSED';await s.sidebar.handle({type:'refresh'});
 assert.equal(s.sidebar.connection,'PAIRED');assert.equal(s.sidebar.notice,'');assert.equal(s.discoveryReads,0);assert.equal(s.secretReads,0);assert.equal(s.created.length,1);assert.equal(s.calls.filter(x=>x.path==='/v1/pair/status').length,0);assert.equal(s.sidebar.controller.snapshot().draft.text,'Keep my unsent draft');assert.equal(s.sidebar.state.value.requests[0].status,'UNKNOWN');
});

test('a stopped-App reload can read-recover an existing ticket before selecting a project, without creating a ticket or changing stored drafts',async t=>{
 const s=await readonlyRecoveryScene(t),saved=JSON.stringify(s.sidebar.state.value);s.sidebar.controller.attach(null);Object.assign(s.sidebar.controller,{projectId:null,projects:[],sessions:[],selected:null});s.sidebar.connection='APP_NOT_RUNNING';await s.sidebar.handle({type:'refresh'});
 assert.equal(s.sidebar.connection,'PAIRED');assert.equal(s.secretReads,1);assert.equal(s.discoveryReads,1);assert.equal(s.calls.filter(x=>x.path==='/v1/pair/status').length,1);assert.deepEqual(s.calls.filter(x=>x.rpc).map(x=>x.rpc).sort(),['projects.list','status']);assert.equal(s.sidebar.controller.selected,null);assert.equal(JSON.stringify(s.sidebar.state.value),saved);assert.ok(s.calls.every(x=>x.path!=='/v1/pair'));
});

test('desktop discussion selection uses its conversation id and shows validated original user text',()=>{
 const v=webview(),rows=[{kind:'NATIVE_CONVERSATION',project_id:'p',conversation_id:'first-one'},{kind:'NATIVE_CONVERSATION',project_id:'p',conversation_id:'second-one'}],session={...rows[1],messages:[{info:{id:'op:request',role:'user'},native_request:true,parts:[{type:'text',text:'用户原话保持可读 😀。'}]},{info:{id:'op:reply',role:'assistant'},native_status:'RETURNED',parts:[{type:'text',text:'A readable answer'}]}]};
 v.show({sessions:rows,session,sessionTokens:['a'.repeat(64),'b'.repeat(64)]});assert.equal(v.ids.get('sessions').value,'1');assert.equal(v.ids.get('session-changes').hidden,true);assert.equal(v.ids.get('agent-mode').hidden,true);
 const article=v.ids.get('history').children[0];assert.equal(descendants(article).some(e=>e.tag==='details'),false);assert.equal(textOf(article),'你用户原话保持可读 😀。');assert.ok(descendants(v.ids.get('history').children[1]).some(e=>e.textContent==='A readable answer'));
});

test('older approved client has one explicit desktop-discussion connection action',()=>{
 const v=webview();v.show({nativePairingRequired:true});const action=v.ids.get('actions').children.find(e=>e.textContent==='同步桌面讨论');assert.ok(action);action.fire('click');assert.equal(v.out.at(-1).type,'repairPair');assert.ok(!v.out.some(m=>m.type==='repairPair'&&m!==v.out.at(-1)));
});

test('native expired owner has a concise bilingual reason while legacy disconnect stays distinct',()=>{
 const v=webview(),row={kind:'NATIVE_CONVERSATION',project_id:'p',conversation_id:'c'},message={info:{id:'expired',role:'assistant'},native_status:'UNKNOWN_OUTCOME',native_execution:{stop:{reason:'OWNER_DISCONNECTED_NO_POST_REPLAY',owner_stop_status:'EXPIRED'}},parts:[]},session={...row,messages:[message]};
 v.show({sessions:[row],session});assert.ok(descendants(v.ids.get('history')).some(e=>e.textContent==='本地执行时限已到，最终回复待核查。'));assert.ok(!v.out.some(m=>m.type==='send'));
 const snapshot={...v.snapshot,session:{...session,messages:[{...message,native_execution:{stop:{reason:'OWNER_DISCONNECTED_NO_POST_REPLAY'}}}]}};v.receive({type:'state',locale:'en',connection:'PAIRED',trusted:true,notice:'',snapshot});assert.ok(descendants(v.ids.get('history')).some(e=>e.textContent==='The execution connection ended. The final reply needs inspection.'));assert.ok(!v.out.some(m=>m.type==='send'));
});
test('native reply offers exact file/diff inline and explicit approval only; repeated observations retain draft and approval DOM',()=>{const v=webview(),token='d'.repeat(64),previewToken='e'.repeat(64),session={kind:'NATIVE_CONVERSATION',project_id:'p1',conversation_id:'c1',messages:[{info:{id:'parent:reply',role:'assistant'},parts:[{type:'text',text:'Stored preview'}],native_parent_operation_id:'parent',native_action_token:token,native_file_source_ref:{path:'tasks/T01.txt'},native_plans:[{record_ref:{id:'plan'}}]}]},native={owner:'owner',methods:['native.file.read','native.file.open','native.rework.preview','native.rework.apply'],context:{parent_operation_id:'parent',token,preview:{token:previewToken,path:'tasks/T01.txt',can_apply:true,already_applied:false}}};v.show({session,native});const history=v.ids.get('history'),article=history.children[0],all=descendants(article),open=all.find(e=>e.textContent==='打开任务文件'),diff=all.find(e=>e.textContent==='查看修改预览'),approve=all.find(e=>e.tag==='input'),apply=all.find(e=>e.textContent==='应用修改');assert.equal(apply.disabled,true);open.fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'nativeOpen',parent:'parent',token});diff.fire('click');assert.equal(v.out.at(-1).type,'nativePreview');approve.checked=true;approve.fire('change');assert.equal(apply.disabled,false);v.show({session,native});assert.equal(history.children[0],article);assert.equal(approve.checked,true);apply.fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'nativeApply',parent:'parent',token,preview:previewToken,approved:true,rebase:false});v.show({session,native:{...native,context:{...native.context,preview:{...native.context.preview,can_apply:false,already_applied:true}}}});assert.equal(descendants(history).some(e=>e.textContent==='应用修改'),false);assert.ok(textOf(history).includes('已应用'));});


test('Workflow draft and parser preserve explicit mode through delayed state and exact-scope changes',()=>{const {api}=extensionFixture();assert.equal(api.parseMessage({type:'send',token:'a'.repeat(64),revision:1,agent:'workflow'}).agent,'workflow');assert.equal(api.parseMessage({type:'draft',token:'a'.repeat(64),revision:1,text:'keep',sequence:1,agent:'workflow'}).agent,'workflow');
 const v=webview(),input=v.ids.get('input'),mode=v.ids.get('agent-mode');v.show({session:{session_ref:{id:'same'},available_agent_modes:['plan','build','workflow'],workflow_mode_supported:true,messages:[]},draft:{text:'Keep 中文 😀',revision:0,agent:'build'}});mode.value='workflow';mode.fire('change');const sent=v.out.at(-1);assert.equal(sent.type,'draft');assert.equal(sent.agent,'workflow');assert.equal(sent.text,'Keep 中文 😀');
 v.show({session:{session_ref:{id:'same'},available_agent_modes:['plan','build','workflow'],workflow_mode_supported:true,messages:[]},draft:{text:'Keep 中文 😀',revision:0,agent:'build'}});assert.equal(mode.value,'workflow','old state cannot replace the pending mode');
 v.receive({type:'draftResult',token:'a'.repeat(64),sequence:sent.sequence,accepted:true,revision:1});v.show({session:{session_ref:{id:'same'},available_agent_modes:['plan','build','workflow'],workflow_mode_supported:true,messages:[]},draft:{text:'Keep 中文 😀',revision:1,agent:'workflow'}});assert.equal(mode.value,'workflow');assert.equal(input.value,'Keep 中文 😀');
 v.show({scopeToken:'b'.repeat(64),session:{session_ref:{id:'other'},available_agent_modes:['plan','build','workflow'],workflow_mode_supported:true,messages:[]},draft:{text:'Other draft',revision:0,agent:'plan'}});assert.equal(mode.value,'plan');v.receive({type:'draftResult',token:'a'.repeat(64),sequence:sent.sequence,accepted:false,revision:9});assert.equal(mode.value,'plan');assert.equal(input.value,'Other draft');});

test('stored Workflow is visible and retained when unavailable; native page keeps its existing hidden mode control',()=>{const v=webview();v.show({session:{session_ref:{id:'same'},messages:[],available_agent_modes:['plan','build']},draft:{text:'Do not change my mode',revision:2,agent:'workflow'}});assert.equal(v.ids.get('agent-mode').value,'workflow');assert.equal(v.ids.get('workflow-mode-option').disabled,true);assert.equal(v.ids.get('send').disabled,true);v.ids.get('send').fire('click');assert.equal(v.out.filter(x=>x.type==='send').length,0);v.show({session:{kind:'NATIVE_CONVERSATION',conversation_id:'topic',messages:[]},draft:{text:'Page note',revision:0}});assert.equal(v.ids.get('agent-mode').hidden,true);});


test('recovery completion closes only the matching current recovery panel and preserves input',()=>{const v=webview(),panel=v.ids.get('recovery'),project=v.ids.get('project-switcher'),input=v.ids.get('input');input.value='Recovered exact 中文 😀';panel.open=true;project.open=true;v.receive({type:'recoveryResult',scopeToken:'b'.repeat(64),revision:0});assert.equal(panel.open,true);v.receive({type:'recoveryResult',scopeToken:'a'.repeat(64),revision:0});assert.equal(panel.open,false);assert.equal(project.open,false);assert.equal(input.value,'Recovered exact 中文 😀');});

test('native original user message is readable in normal history without opening raw-request details',()=>{const v=webview(),raw='只改所选句，保留人工内容 😀。';v.show({session:{kind:'NATIVE_CONVERSATION',project_id:'p',conversation_id:'c',messages:[{info:{id:'request',role:'user'},native_request:true,parts:[{id:'input',type:'text',text:raw}]}],artifacts:[]}});const article=v.ids.get('history').children[0];assert.equal(descendants(article).some(e=>e.tag==='details'),false);assert.equal(textOf(article),'你'+raw);});


test('Source16 a legacy operation observation explains retained input without declaring full confirmation',()=>{
 const v=webview();v.show({pending:[{request_id:'legacy',method:'native.message',operation_id:'original',status:'UNKNOWN',observed_status:'RETURNED',observation_only:true}],draftUnresolved:true});
 const pending=v.ids.get('pending');assert.ok(textOf(pending).includes('已返回，原稿保留'));assert.equal(descendants(pending).filter(e=>e.tag==='button').length,1);assert.equal(v.ids.get('send').disabled,true);assert.equal(v.ids.get('input').value,v.snapshot.draft.text);
});

// AUTHORED_NOT_RUN: child history opens only through its own on-demand control.
test('child changes remain in their record and preserve original input while rendering historical read-only rows',()=>{
 const v=webview(),token='a'.repeat(64),scopeToken='b'.repeat(64),file={token:'c'.repeat(64),path:'src/历史长文件名.py',content_scope:'FULL_FILE',availability:'AVAILABLE'},
 delegation={scopeToken,supported:true,entries:[{title:'Child',token}],detail:{token,value:{observation_is_current:false,messages:[]}},diffs:{scopeToken:'d'.repeat(64),supported:true,loaded:false,loading:false,files:[]}},
 s={session:{session_ref:{id:'s'},messages:[],artifacts:[]},draft:{text:'ROOT_DRAFT_PRESERVED',revision:1},delegation};
 v.show(s);assert.equal(v.out.some(m=>m.type==='childDiffs'),false);v.ids.get('child-diffs-read').fire('click');
 assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'childDiffs',token:delegation.diffs.scopeToken});
 v.show({...s,delegation:{...delegation,diffs:{...delegation.diffs,loaded:true,files:[file]}}});
 const group=v.ids.get('child-changes-files').children[0],row=group.children[0];row.fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'childDiff',index:0,token:file.token});
 assert.match(textOf(row),/完整文件/);assert.equal(v.ids.get('input').value,'ROOT_DRAFT_PRESERVED');
 assert.match(v.ids.get('child-changes-status').textContent,/历史差异/);
});

// AUTHORED_NOT_RUN: explicit historical-review decisions never imply file revert.
test('historical review remains a distinct explicit decision and keeps original drafts',()=>{
 const v=webview(),token='9'.repeat(64),file={path:'src/manual.py',token:'8'.repeat(64),content_scope:'FULL_FILE',availability:'AVAILABLE'},scopeToken='7'.repeat(64),
 s={session:{session_ref:{id:'s'},messages:[],artifacts:[]},draft:{text:'KEEP_USER_TEXT',revision:1},diffs:{scopeToken,loaded:true,files:[file]},diffReview:{supported:true,loading:null,error:null,detail:{token,path:file.path,matches_after:true,recorded:false,unresolved:false}}};
 v.show(s);const row=v.ids.get('changes-files').children[0];row.children[1].fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'diffReview',index:0,token:file.token,child:false});
 v.ids.get('diff-review-reject').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'diffDecision',token,decision:'REJECT'});
 assert.equal(v.ids.get('input').value,'KEEP_USER_TEXT');assert.match(v.ids.get('diff-review-status').textContent,/修改后/);
 v.show({...s,diffReview:{...s.diffReview,detail:{...s.diffReview.detail,unresolved:true}}});assert.equal(v.ids.get('diff-review-accept').disabled,true);assert.match(v.ids.get('diff-review-status').textContent,/待核查/);
});

// AUTHORED_NOT_RUN: token-only actions and in-place pending handoff rendering.
test('native editor actions take only original tokens and pending handoffs never expose file text or clear drafts',()=>{
 const v=webview(),token='3'.repeat(64),entryToken='4'.repeat(64),s={session:{session_ref:{id:'s'},messages:[],artifacts:[]},draft:{text:'ROOT_TEXT_TO_KEEP',revision:1},diffReview:{supported:true,detail:{token,path:'src/original.ts',editorSupported:true,canRestore:true,canApply:false}},editorChanges:{supported:true,busy:false,entries:[{token:entryToken,path:'src/original.ts',stage:'BUFFER_CHANGED',phase:'BUFFER_CHANGED',hasUri:true,ownerEnded:true,actorReady:true,nativeReturned:true}]}};
 v.show(s);v.ids.get('editor-restore').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'editorApply',token,direction:'RESTORE_BEFORE'});
 const article=v.ids.get('editor-change-list').children[0],buttons=descendants(article).filter(e=>e.tag==='button');buttons[0].fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'editorInspect',token:entryToken});
 buttons[2].fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'editorResolve',token:entryToken,action:'RESOLVE_SAVED'});assert.equal(v.ids.get('input').value,'ROOT_TEXT_TO_KEEP');
 assert.match(textOf(article),/原生撤销/);v.show({...s,editorChanges:{...s.editorChanges,busy:true}});assert.equal(v.ids.get('editor-restore').disabled,true);
});
test('webview cannot provide a native URI, new client identity, arbitrary action or content to an editor mutation',()=>{
 const f=extensionFixture(),token='3'.repeat(64);
 for(const m of [{type:'editorApply',token,direction:'RESTORE_BEFORE',editor_uri:'file:///D:/other.txt'},{type:'editorApply',token,direction:'RESTORE_BEFORE',editor_client_id:'injected'},{type:'editorResolve',token,action:'RUN_COMMAND'},{type:'editorResolve',token,action:'RESOLVE_SAVED',content:'OVERWRITE'}])assert.throws(()=>f.api.parseMessage(m),/INVALID_WEBVIEW_MESSAGE/);
 assert.equal(f.api.parseMessage({type:'editorApply',token,direction:'APPLY_AFTER'}).direction,'APPLY_AFTER');
});

// AUTHORED_NOT_RUN: original file lifecycle controls, focus and scope tokens.
test('file creation/deletion labels and exact actions stay in the original diff and handoff',()=>{
 const v=webview(),token='3'.repeat(64),key='5'.repeat(64),first='4'.repeat(64),second='6'.repeat(64),entry={key,token:first,path:'src/original.ts',mode:'FILE_CREATE',fileStatus:'APPLIED',fileExists:true,metadataRecorded:true,fileSupported:true,hasUri:true,stage:'BUFFER_CHANGED',ownerEnded:true,actorReady:true};
 const base={session:{session_ref:{id:'s'},messages:[],artifacts:[]},draft:{text:'KEEP_ROOT_INPUT',revision:1},diffReview:{supported:true,detail:{token,path:entry.path,fileStatus:'deleted',editorSupported:true,canRestore:true,canApply:false}},editorChanges:{supported:true,busy:false,entries:[entry]}};
 v.show(base);assert.equal(v.ids.get('editor-restore').textContent,'恢复已删除文件');assert.equal(v.ids.get('editor-apply').textContent,'删除此文件');assert.match(v.ids.get('editor-effect').textContent,/创建或删除/);
 const article=v.ids.get('editor-change-list').children[0],all=descendants(article),undo=all.find(e=>e.tag==='button'&&e.textContent==='撤销本次创建'),manual=all.find(e=>e.tag==='button'&&e.textContent==='保留当前状态并继续');
 undo.fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'editorFileAction',token:first,action:'UNDO'});
 v.show({...base,editorChanges:{...base.editorChanges,entries:[{...entry,token:second,pending:true}]}});
 assert.equal(v.ids.get('editor-change-list').children[0],article);assert.ok(descendants(article).includes(undo));assert.equal(undo.disabled,true);assert.equal(manual.disabled,false);
 manual.fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'editorFileAction',token:second,action:'MANUAL_RESOLVE'});assert.equal(v.ids.get('input').value,'KEEP_ROOT_INPUT');
 v.show({...base,editorChanges:{...base.editorChanges,entries:[{...entry,token:second,fileStatus:'UNDONE',fileExists:false}]}});assert.equal(undo.hidden,true);assert.equal(all.find(e=>e.tag==='button'&&e.textContent==='打开原文件').hidden,true);
});
test('webview file actions cannot retry COMMIT or inject path, actor, content or original operation identity',()=>{
 const f=extensionFixture(),token='3'.repeat(64);
 for(const m of [{type:'editorFileAction',token,action:'COMMIT'},{type:'editorFileAction',token,action:'UNDO',editor_uri:'file:///C:/other'},{type:'editorFileAction',token,action:'UNDO',operation_id:'new'},{type:'editorFileAction',token,action:'MANUAL_RESOLVE',editor_client_id:'injected'},{type:'editorFileAction',token,action:'UNDO',text:'OVERWRITE'}])assert.throws(()=>f.api.parseMessage(m),/INVALID_WEBVIEW_MESSAGE/);
 assert.equal(f.api.parseMessage({type:'editorFileAction',token,action:'CONFIRM'}).action,'CONFIRM');
});


// AUTHORED_NOT_RUN: observer recovery actions contain no paths or editor state.
test('editor save-state issues stay in the original panel with token-only recovery and preserved input',()=>{
 const v=webview(),token='7'.repeat(64),entry={key:'8'.repeat(64),token,currentScope:true,stage:'UNKNOWN',pending:true,dirtyCount:1,paths:['src/中文.ts'],actorReady:true};
 v.show({draft:{text:'KEEP_ROOT_PRIVATE_INPUT',revision:1},editorObservers:{supported:true,entries:[entry]}});
 const list=v.ids.get('editor-observer-list'),buttons=descendants(list).filter(e=>e.tag==='button');assert.equal(v.ids.get('editor-observers').hidden,false);assert.match(textOf(list),/原内容已保留/);
 buttons[0].fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'observerInspect',token});buttons[1].fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'observerRetire',token});assert.equal(v.ids.get('input').value,'KEEP_ROOT_PRIVATE_INPUT');
 const f=extensionFixture();for(const message of [{type:'observerRetire',token,path:'C:/other'},{type:'observerInspect',token,editor_client_id:'injected'},{type:'observerRetire',token,action:'OBSERVE'},{type:'observerInspect',token,documents:[]}])assert.throws(()=>f.api.parseMessage(message),/INVALID_WEBVIEW_MESSAGE/);
});


// AUTHORED_NOT_RUN. Draft stays editable while a metadata preflight is busy.
test('group capture wait prevents duplicate submit and presents a readable remote dirty result',()=>{
 const v=webview(),token='8'.repeat(64),base={session:{session_ref:{id:'s'},messages:[],artifacts:[]},draft:{text:'KEEP_INPUT_DURING_CAPTURE',revision:1},editorObservers:{busy:true,supported:true,entries:[{key:'9'.repeat(64),token,pending:true,captureState:'WAITING',actorReady:true}]}};
 v.show(base);assert.equal(v.ids.get('input').disabled,false);assert.equal(v.ids.get('send').disabled,true);assert.equal(v.ids.get('input').value,'KEEP_INPUT_DURING_CAPTURE');
 v.show({...base,editorObservers:{...base.editorObservers,busy:false,entries:[{...base.editorObservers.entries[0],pending:false,error:'EDITOR_CAPTURE_DIRTY'}]}});
 assert.match(textOf(v.ids.get('editor-observer-list')),/其他编辑窗口有未保存内容/);assert.equal(v.ids.get('input').value,'KEEP_INPUT_DURING_CAPTURE');assert.doesNotMatch(textOf(v.ids.get('editor-observer-list')),/request_sha256|capture_ref/);
});

// AUTHORED_NOT_RUN: DOM UNIT; host themes/native rendering still unqualified.
test('new pairing recovers an old observer through its existing token actions with clear preservation text',()=>{
 const v=webview(),token='a'.repeat(64),entry={key:'b'.repeat(64),token,stage:'RECOVERED',recovered:true,recoveryRequired:true,actorReady:true,pending:true};
 v.show({draft:{text:'KEEP_REPAIRED_DRAFT',revision:1},editorObservers:{supported:true,busy:false,entries:[entry]}});
 const list=v.ids.get('editor-observer-list'),buttons=descendants(list).filter(e=>e.tag==='button');assert.match(textOf(list),/重新配对后仍保留旧记录/);assert.match(textOf(list),/先保存或保留原内容/);assert.equal(buttons[0].disabled,false);assert.equal(buttons[1].disabled,false);
 buttons[1].fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'observerRetire',token});assert.equal(v.ids.get('input').value,'KEEP_REPAIRED_DRAFT');assert.doesNotMatch(textOf(list),/editor_client_id|preparation_sha256|user_preserved_original/);
});

// AUTHORED_NOT_RUN: source issues reuse the original status area, preserving input.
test('source authority failure appears in existing status without leaking internal selection data',()=>{
 const v=webview();v.show({draft:{text:'KEEP_SOURCE_RECOVERY_DRAFT',revision:1},session:{session_ref:{id:'s'},messages:[],artifacts:[],generation:2,context_authority_issue:{code:'SOURCE_CONTEXT_CHANGED',generation:2,operation_id:'old',scope:'FUTURE_HOST_ADMISSIONS_AND_API_RELAY',original_history_retained:true}}});
 assert.match(textOf(v.ids.get('actions')),/来源授权已变化/);assert.equal(v.ids.get('input').value,'KEEP_SOURCE_RECOVERY_DRAFT');assert.doesNotMatch(textOf(v.ids.get('actions')),/selection_id|registry_ref|request_sha256/);
});

// AUTHORED_NOT_RUN batch29: actual renderer DOM, UNIT extension boundary.
test('source recovery action keeps unsynced input and sends only its exact metadata token',()=>{
 const v=webview(),token='f'.repeat(64),sourceRecovery={needed:true,supported:true,token,busy:false,phase:'READY',can_continue:false};
 v.show({draft:{text:'KEEP_SOURCE_DRAFT',revision:1},sourceRecovery,session:{session_ref:{id:'s'},messages:[],artifacts:[],context_authority_issue:{code:'SOURCE_CONTEXT_CHANGED'}}});
 const input=v.ids.get('input');input.value='UNSYNCED_TEXT_STAYS_HERE';
 const action=descendants(v.ids.get('actions')).find(e=>e.tag==='button'&&e.textContent.includes('停止旧执行'));
 assert.ok(action);assert.equal(action.disabled,false);action.fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'sourceRecoveryStart',token});assert.equal(input.value,'UNSYNCED_TEXT_STAYS_HERE');assert.doesNotMatch(JSON.stringify(v.out.at(-1)),/UNSYNCED_TEXT/);
});

test('an unknown stop only offers inspection and a confirmed creation offers desktop continuation',()=>{
 const v=webview(),token='e'.repeat(64),base={draft:{text:'KEEP_SOURCE_DRAFT',revision:1},sourceRecovery:{needed:true,supported:true,token,busy:false,phase:'STOP_PENDING',can_continue:false}};
 v.show(base);let buttons=descendants(v.ids.get('actions')).filter(e=>e.tag==='button');assert.equal(buttons.some(e=>e.textContent.includes('停止旧执行')),false);const inspect=buttons.find(e=>e.textContent.includes('核查原恢复'));assert.ok(inspect);inspect.fire('click');assert.equal(v.out.at(-1).type,'sourceRecoveryInspect');
 v.show({...base,sourceRecovery:{...base.sourceRecovery,phase:'COMPLETE',desktop_continuation_required:true}});buttons=descendants(v.ids.get('actions')).filter(e=>e.tag==='button');const open=buttons.find(e=>e.textContent==='打开桌面 App');assert.ok(open);open.fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'sourceRecoveryReveal',token});assert.equal(v.ids.get('input').value,'KEEP_SOURCE_DRAFT');assert.match(textOf(v.ids.get('actions')),/桌面会话列表/);
 v.show({...base,sourceRecovery:{...base.sourceRecovery,phase:'COMPLETE',desktop_continuation_required:false}});buttons=descendants(v.ids.get('actions')).filter(e=>e.tag==='button');assert.equal(buttons.some(e=>e.textContent==='打开桌面 App'),false);const local=buttons.find(e=>e.textContent==='打开新对话');assert.ok(local);local.fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'sourceRecoveryReveal',token});assert.equal(v.ids.get('input').value,'KEEP_SOURCE_DRAFT');
});

test('older pairing offers explicit re-pairing instead of issuing an unapproved recovery',()=>{
 const v=webview(),token='e'.repeat(64);v.show({draft:{text:'KEEP_OLD_PAIR_DRAFT',revision:1},sourceRecovery:{needed:true,supported:false,token,busy:false,phase:'READY'}});
 const buttons=descendants(v.ids.get('actions')).filter(e=>e.tag==='button');assert.equal(buttons.some(e=>e.textContent.includes('停止旧执行')),false);assert.ok(buttons.some(e=>e.textContent==='重新配对'));assert.equal(v.ids.get('input').value,'KEEP_OLD_PAIR_DRAFT');assert.equal(v.out.some(e=>e.type==='sourceRecoveryStart'),false);
});

// AUTHORED_NOT_RUN batch40: actual webview with UNIT DOM, no VS Code/layout acceptance.
test('private native recovery scope review updates beside the original message and never sends unsynced input',()=>{
 const v=webview(),token='e'.repeat(64),session={kind:'NATIVE_CONVERSATION',project_id:'project',conversation_id:'native_chat',messages:[{info:{id:'native_parent:reply',role:'assistant'},parts:[{type:'text',text:'KEEP_ORIGINAL_REPLY'}],native_parent_operation_id:'native_parent',native_action_token:'f'.repeat(64)}],artifacts:[]};
 const row={parent_operation_id:'native_parent',token,phase:'READY',supported:true,current:true,busy:false,prepared:false,can_continue:false};
 const base={session,draft:{text:'KEEP_OLD_INPUT',revision:1},native:{methods:[],context:null},nativeSourceRecovery:{supported:true,items:[row]}};
 v.show(base);v.ids.get('input').value='UNSYNCED_NATIVE_INPUT';let buttons=descendants(v.ids.get('history')).filter(e=>e.tag==='button');
 buttons.find(e=>e.textContent==='核对恢复范围').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'nativeRecoveryPrepare',token});assert.equal(v.ids.get('input').value,'UNSYNCED_NATIVE_INPUT');assert.doesNotMatch(JSON.stringify(v.out.at(-1)),/UNSYNCED_NATIVE_INPUT/);
 v.show({...base,nativeSourceRecovery:{supported:true,items:[{...row,prepared:true,target_label:'新项目 / '+'长名称'.repeat(50)}]}});
 buttons=descendants(v.ids.get('history')).filter(e=>e.tag==='button');buttons.find(e=>e.textContent==='停止旧执行并新建对话').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'nativeRecoveryStart',token});
 assert.match(textOf(v.ids.get('history')),/KEEP_ORIGINAL_REPLY/);assert.equal(v.ids.get('input').value,'UNSYNCED_NATIVE_INPUT');
});

test('private native unknown stop only offers original lookup; completed chat opens with current grant',()=>{
 const v=webview(),token='e'.repeat(64),session={kind:'NATIVE_CONVERSATION',project_id:'project',conversation_id:'native_chat',messages:[{info:{id:'native_parent:reply',role:'assistant'},parts:[],native_parent_operation_id:'native_parent',native_action_token:'f'.repeat(64)}],artifacts:[]};
 const row={parent_operation_id:'native_parent',token,phase:'STOP_PENDING',supported:true,current:true,busy:false,can_continue:false,request_id:'original_request'},base={session,draft:{text:'KEEP_RECOVERY_DRAFT',revision:1},native:{methods:[],context:null}};
 v.show({...base,nativeSourceRecovery:{supported:true,items:[row]}});let buttons=descendants(v.ids.get('history')).filter(e=>e.tag==='button');assert.equal(buttons.some(e=>e.textContent.includes('停止旧执行')),false);buttons.find(e=>e.textContent==='核查原恢复结果').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'nativeRecoveryInspect',token});
 v.show({...base,nativeSourceRecovery:{supported:true,items:[{...row,phase:'COMPLETE',can_open:true}]}});buttons=descendants(v.ids.get('history')).filter(e=>e.tag==='button');buttons.find(e=>e.textContent==='打开新对话').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'nativeRecoveryOpen',token});assert.equal(v.ids.get('input').value,'KEEP_RECOVERY_DRAFT');
});

// AUTHORED_NOT_RUN batch41: canonical user-only native source recovery, UNIT DOM.
test('canonical history without an assistant answer still offers exact recovery beside the original question',()=>{
 const v=webview(),token='e'.repeat(64),reference={id:'native:'+'d'.repeat(64),revision:1},row={id:'b'.repeat(64),role:'user',text:'KEEP_UNANSWERED_QUESTION',source_kind:'native',operation_id:'native_parent',native_context_source:{origin:{project_id:null,conversation_id:'unbound_source'}}};
 const session={kind:'CANONICAL_CONVERSATION',organization_project_id:null,conversation_ref:reference,messages:[],canonical_history:[row],artifacts:[]};
 const recovery={parent_operation_id:'native_parent',token,phase:'READY',supported:true,current:true,prepared:false,busy:false};
 const base={session,projectId:'__cirven_chats__',draft:{text:'KEEP_CANONICAL_INPUT',revision:1},canonical:{selected:true,can_send:false},nativeSourceRecovery:{supported:true,items:[recovery]}};
 v.show(base);let buttons=descendants(v.ids.get('history')).filter(e=>e.tag==='button');buttons.find(e=>e.textContent==='核对恢复范围').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'nativeRecoveryPrepare',token});assert.match(textOf(v.ids.get('history')),/KEEP_UNANSWERED_QUESTION/);
 v.show({...base,nativeSourceRecovery:{supported:true,items:[{...recovery,prepared:true,target_label:null}]}});buttons=descendants(v.ids.get('history')).filter(e=>e.tag==='button');buttons.find(e=>e.textContent==='停止旧执行并新建对话').fire('click');assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'nativeRecoveryStart',token});assert.equal(v.ids.get('input').value,'KEEP_CANONICAL_INPUT');
});

test('canonical history with unavailable source grant offers repair without exposing source identifiers',()=>{
 const v=webview(),reference={id:'native:'+'d'.repeat(64),revision:1},row={id:'b'.repeat(64),role:'assistant',text:'KEEP_STORED_REPLY',operation_id:'native_parent',native_context_access_required:true};
 v.show({session:{kind:'CANONICAL_CONVERSATION',organization_project_id:null,conversation_ref:reference,messages:[],canonical_history:[row],artifacts:[]},projectId:'__cirven_chats__',draft:{text:'KEEP_PRIVATE_DRAFT',revision:1},nativeSourceRecovery:{supported:true,items:[]}});
 const history=v.ids.get('history'),buttons=descendants(history).filter(e=>e.tag==='button');assert.ok(buttons.some(e=>e.textContent==='重新配对'));assert.match(textOf(history),/原来源不在当前授权内/);assert.equal(buttons.some(e=>e.textContent.includes('停止旧执行')),false);assert.equal(v.ids.get('input').value,'KEEP_PRIVATE_DRAFT');
});

test('canonical message opens its exact registered input only when the paired view is authorized',()=>{
 const v=webview(),id='b'.repeat(64),token='e'.repeat(64);
 const row={id,role:'user',text:'Word continuity request',input_file:{filename:'review.docx',open_allowed:true}};
 const session={kind:'CANONICAL_CONVERSATION',organization_project_id:'project',
   conversation_ref:{id:'native:'+'d'.repeat(64),revision:1},messages:[],artifacts:[],canonical_history:[row]};
 const snapshot={session,canonical:{selected:true,token},draft:{text:'KEEP_DRAFT',revision:1}};
 v.show(snapshot);
 const history=v.ids.get('history');
 const buttons=()=>descendants(history).filter(e=>e.tag==='button'&&e.textContent==='review.docx');
 buttons()[0].fire('click');
 assert.deepEqual(JSON.parse(JSON.stringify(v.out.at(-1))),{type:'canonicalFileOpen',id,token});
 v.show({...snapshot,session:{...session,canonical_history:[{...row,input_file:{...row.input_file,open_allowed:false}}]}});
 assert.equal(buttons().length,0);
 assert.match(textOf(history),/review\.docx/);
 assert.equal(v.ids.get('input').value,'KEEP_DRAFT');
});

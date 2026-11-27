import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync,existsSync} from 'node:fs';
import {resolve,dirname,extname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';

// Component UNIT: full production InteractionPanels/ObjectDraft/ArtifactActionContext,
// real InteractionState and ModuleRegistry. A Solid universal host and an opaque
// editable fixture renderer are presentation boundaries, not a Main/browser/native
// qualification. Any API access fails; no model/desktop/network is available.
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),req=createRequire(resolve(app,'package.json'));
const ts=req('typescript'),babel=req('@babel/core');
const solidPreset=(await import(pathToFileURL(resolve(app,'node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js')).href)).default;
const solid=await import(pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href);
const solidURL=pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href;
const universal=readFileSync(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8').replace("from 'solid-js'",`from '${solidURL}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universal).toString('base64'));
const webText=readFileSync(resolve(app,'node_modules/solid-js/web/dist/web.js'),'utf8').replaceAll("from 'solid-js'",`from '${solidURL}'`);
const web=await import('data:text/javascript;base64,'+Buffer.from(webText).toString('base64'));
const tick=()=>new Promise(done=>setImmediate(done));
const basis={projectId:'project-one',workspaceId:'workspace-one',sessionId:'session-one'};
const artifact=(type='providers',id='providers')=>({artifactId:id,type,schemaVersion:1,revision:1,contentHash:'a'.repeat(64),scope:{...basis},availability:'IMPLEMENTED',data:{source_ref:{id:'frozen-ref',revision:2,sha256:'b'.repeat(64)}}});

function harness(t,{showRestores,menuOnly=false}={}){
 const saved={};for(const key of ['window','document','navigator','localStorage','eeasDesktop'])saved[key]=Object.getOwnPropertyDescriptor(globalThis,key);
 const values=new Map(),document={documentElement:{lang:'zh-CN'},activeElement:null};
 Object.defineProperty(globalThis,'window',{configurable:true,value:{addEventListener(){},removeEventListener(){}}});
 Object.defineProperty(globalThis,'document',{configurable:true,value:document});
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{language:'zh-CN'}});
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value))}});delete globalThis.eeasDesktop;
 const host={createElement:type=>({type,props:{},children:[],parent:null,scrollTop:0,scrollLeft:0,selectionStart:0,selectionEnd:0,selectionDirection:'none',focus(options){document.activeElement=this;this.focusOptions=options;},setSelectionRange(start,end,direction='none'){this.selectionStart=start;this.selectionEnd=end;this.selectionDirection=direction;}}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:node=>node.type==='#text',replaceText:(node,value)=>node.value=value,
  insertNode(parent,node,anchor){if(node.parent){const i=node.parent.children.indexOf(node);if(i>=0)node.parent.children.splice(i,1);}node.parent=parent;const at=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(at<0?parent.children.length:at,0,node);},removeNode(parent,node){const i=parent.children.indexOf(node);if(i>=0)parent.children.splice(i,1);node.parent=null;},setProperty(node,key,value){node.props[key]=value;if(key==='value'&&node.value!==value){node.value=value;node.selectionStart=node.selectionEnd=String(value??'').length;}},getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
 const renderer=Object.assign(createRenderer(host),{Show:solid.Show,For:solid.For}),modules=new Map(),io=[],callbacks=[],mounts=[],unmounts=[];
 function primitive(type,props){const node=host.createElement(type);renderer.spread(node,props);return node;}
 function evaluate(file,code){const module={exports:{}};modules.set(file,module);
  if(file.endsWith('.tsx')){code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;code=babel.transformSync(code,{filename:file+'.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'source54-renderer'}]],configFile:false,babelrc:false}).code;}
  code=ts.transpileModule(code,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;
  const require=name=>{if(name.endsWith('.css'))return {};if(name==='solid-js')return solid;if(name==='solid-js/web')return web;if(name==='source54-renderer')return renderer;
   if(name==='@opencode-ai/ui/icon')return {Icon:props=>primitive('i',{'data-component':'icon',...props})};
   if(/^\.\/api(?:\.ts)?$/.test(name))return {api:{request(){io.push('API');assert.fail('No API calls in recovery component UNIT');},projectPath:pid=>'/api/projects/'+pid},operationId(){io.push('operation');assert.fail('No new operation during restore');}};
   if(name==='./renderers')return {GenericRenderer:()=>{assert.fail('Fixture has an exact registered renderer');}};
   if(name.startsWith('.'))return load(resolve(dirname(file),name));throw Error('Unexpected unit import '+name);};
  new Function('require','module','exports',code)(require,module,module.exports);return module.exports;
 }
 function load(file){if(!extname(file))file+=existsSync(file+'.tsx')?'.tsx':'.ts';if(modules.has(file))return modules.get(file).exports;if(file.endsWith('.json'))return JSON.parse(readFileSync(file,'utf8'));return evaluate(file,readFileSync(file,'utf8'));}
 const language=load(resolve(app,'src/ui-language.ts')),stopLanguage=language.startUILanguage();
 const {InteractionState,panelKey}=load(resolve(app,'src/interaction-state.ts')),{ModuleRegistry}=load(resolve(app,'src/registry.ts'));
 const [version,setVersion]=solid.createSignal(0),[strip,setStrip]=solid.createSignal(showRestores),[long,setLong]=solid.createSignal('');
 const state=new InteractionState(()=>setVersion(v=>v+1));state.owner({...basis});const original=artifact(),key=state.request(original,'MODELESS_POPOVER','manual',true),registry=new ModuleRegistry();
 const Fixture=evaluate(resolve(app,'tests/opaque-recovery-fixture.tsx'),`import {createSignal,onCleanup} from 'solid-js'; export default function Fixture(props){mounts.push(props.artifact.artifactId);onCleanup(()=>unmounts.push(props.artifact.artifactId));const [text,setText]=createSignal('初始配置'),[tab,setTab]=createSignal('subscription');return <div class="fixture-pane"><button data-fixture-tab onClick={()=>setTab('api')}>API</button><span data-fixture-active>{tab()}</span><textarea data-fixture-field value={text()} onInput={event=>setText(event.currentTarget.value)}/></div>;}`.replace('mounts.push','globalThis.__source54Mounts.push').replace('unmounts.push','globalThis.__source54Unmounts.push')).default;
 const oldMounts=globalThis.__source54Mounts,oldUnmounts=globalThis.__source54Unmounts;globalThis.__source54Mounts=mounts;globalThis.__source54Unmounts=unmounts;
 registry.registerRenderer({descriptor:{moduleId:'fixture.providers',version:'1',sourceHash:'c'.repeat(64),type:'providers',schemaVersion:1,get label(){return long()||language.t('模型连接');},availability:'IMPLEMENTED'},component:Fixture});
 const {default:InteractionPanels,InteractionRestoreMenuItems}=load(resolve(app,'src/InteractionPanels.tsx'));
 const root=host.createElement('root'),trigger=host.createElement('button'),composer=host.createElement('textarea');composer.value='原Main未发送草稿';composer.setSelectionRange(2,6,'backward');composer.scrollTop=19;root.children.push(trigger,composer);trigger.parent=composer.parent=root;
 const all=(node=root)=>[node,...(node.children??[]).flatMap(child=>all(child))],text=(node=root)=>node.type==='#text'?String(node.value):node.children.map(text).join('');
 const onCollapse=value=>{callbacks.push(['collapse',value]);trigger.focus({preventScroll:true});};
 const onRestore=value=>{callbacks.push(['restore',value]);all().find(node=>node.props?.['data-interaction-key']===value)?.children.find(node=>node.type==='header')?.children.find(node=>node.type==='button')?.focus({preventScroll:true});};
 const dispose=renderer.render(()=>[menuOnly?null:InteractionPanels({state,get version(){return version();},registry,context:{dispatch(){io.push('dispatch');assert.fail('Restore cannot dispatch');},open(){io.push('open');assert.fail('Restore cannot open a new artifact');}},get showRestores(){return strip();},onCollapse}),InteractionRestoreMenuItems({state,get version(){return version();},registry,onRestore})],root);
 t.after(()=>{dispose();stopLanguage();globalThis.__source54Mounts=oldMounts;globalThis.__source54Unmounts=oldUnmounts;for(const[key,value]of Object.entries(saved))if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];});
 const find=(kind,filter=()=>true)=>all().find(node=>node.type===kind&&filter(node));
 return {state,key,original,panelKey,all,text,find,io,callbacks,mounts,unmounts,composer,trigger,document,setStrip,setLong,language,section:()=>find('section',node=>node.props['data-interaction-key']===key),menu:()=>find('button',node=>node.props['data-interaction-restore-key']===key),strip:()=>find('nav',node=>node.props.class==='as-interaction-restores'),field:()=>find('textarea',node=>'data-fixture-field'in node.props),collapse:()=>find('button',node=>'data-interaction-collapse'in node.props)};
}
function click(node){assert.ok(node,'visible action is rendered');node.props.onClick?.({currentTarget:node,preventDefault(){}});}
function escape(node,composing=false){const event={key:'Escape',isComposing:composing,prevented:false,stopped:false,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}};node.props.onKeyDown(event);return event;}

test('default workspace strip remains; agent host can suppress it without losing the exact menu restore',async t=>{
 const view=harness(t);view.state.collapse(view.key);await tick();assert.ok(view.strip());assert.match(view.text(view.strip()),/ · v1/);assert.ok(view.menu());
 view.setStrip(false);await tick();assert.equal(view.strip(),undefined);assert.ok(view.menu());click(view.menu());await tick();assert.equal(view.state.records.get(view.key).visible,true);assert.equal(view.menu(),undefined);assert.equal(view.strip(),undefined);assert.deepEqual(view.io,[]);
 view.state.collapse(view.key);view.setStrip(undefined);await tick();assert.ok(view.strip(),'default workbench semantics preserved');
});
test('full production pane stays mounted across collapse/menu resume with draft, tab, range, scroll and original artifact unchanged',async t=>{
 const view=harness(t,{showRestores:false});await tick();const field=view.field(),pane=view.section(),scroll=view.find('div',node=>node.props.class==='as-interaction-content as-inspector');
 click(view.find('button',node=>'data-fixture-tab'in node.props));field.value='保留配置草稿😀\n第二行';field.props.onInput({currentTarget:field});field.setSelectionRange(3,9,'backward');field.scrollTop=31;scroll.scrollTop=143;scroll.scrollLeft=9;view.state.resize(view.key,624);view.state.move(view.key,{x:39,y:28});
 const basisBefore=structuredClone(view.original),composer=view.composer;click(view.collapse());await tick();assert.equal(view.document.activeElement,view.trigger);assert.equal(view.field(),field);assert.equal(view.section(),pane);assert.equal(view.state.records.get(view.key).visible,false);assert.equal(pane.props['aria-hidden'],true);assert.equal(pane.props.inert,true);assert.equal(view.strip(),undefined);
 click(view.menu());await tick();assert.equal(view.field(),field);assert.equal(view.section(),pane);assert.equal(field.value,'保留配置草稿😀\n第二行');assert.deepEqual([field.selectionStart,field.selectionEnd,field.selectionDirection,field.scrollTop],[3,9,'backward',31]);assert.deepEqual([scroll.scrollTop,scroll.scrollLeft],[143,9]);assert.equal(view.text(view.find('span',node=>'data-fixture-active'in node.props)),'api');assert.deepEqual(view.mounts,['providers']);assert.deepEqual(view.unmounts,[]);
 assert.equal(view.document.activeElement,view.collapse());assert.deepEqual(view.callbacks,[['collapse',view.key],['restore',view.key]]);assert.equal(view.state.records.get(view.key).artifact,view.original);assert.deepEqual(view.original,basisBefore);assert.equal(view.state.records.get(view.key).height,624);assert.deepEqual(view.state.records.get(view.key).position,{x:39,y:28});assert.equal(view.composer,composer);assert.deepEqual([composer.value,composer.selectionStart,composer.selectionEnd,composer.scrollTop],['原Main未发送草稿',2,6,19]);assert.deepEqual(view.io,[]);
});
test('Escape ignores IME composition, then collapses once and hands back only through the opt-in user callback',async t=>{
 const view=harness(t,{showRestores:false});const pane=view.section();assert.deepEqual([escape(pane,true).prevented,view.callbacks.length],[false,0]);assert.equal(view.state.records.get(view.key).visible,true);
 const event=escape(pane);await tick();assert.equal(event.prevented,true);assert.equal(event.stopped,true);assert.deepEqual(view.callbacks,[['collapse',view.key]]);assert.equal(view.document.activeElement,view.trigger);assert.ok(view.menu());
});
test('stale menu callbacks cannot restore another project/session or restore the already visible pane twice',async t=>{
 const view=harness(t,{showRestores:false});view.state.collapse(view.key);await tick();const stale=view.menu();click(stale);await tick();assert.equal(view.callbacks.length,1);click(stale);assert.equal(view.callbacks.length,1,'repeat callback rejected after exact pane became visible');
 for(const scope of [{...basis,projectId:'other-project'},{...basis,sessionId:'other-session'}]){view.state.owner({...basis});view.state.collapse(view.key);await tick();const old=view.menu();assert.ok(old);view.state.owner(scope);await tick();assert.equal(view.menu(),undefined);click(old);assert.equal(view.state.records.get(view.key).visible,false);assert.equal(view.callbacks.length,1);}
 assert.deepEqual(view.io,[]);
});
test('workspace changes retain established current() utility semantics without rewriting artifact ownership',async t=>{
 const view=harness(t,{showRestores:false});const originalScope=structuredClone(view.original.scope);view.state.owner({...basis,workspaceId:'workspace-two'});await tick();assert.ok(view.menu());click(view.menu());await tick();assert.equal(view.state.records.get(view.key).visible,true);assert.equal(view.state.records.get(view.key).artifact,view.original);assert.deepEqual(view.original.scope,originalScope);assert.equal(view.original.scope.workspaceId,'workspace-one');assert.equal(view.panelKey(view.original),view.key);assert.deepEqual(view.io,[]);
});
test('restore labels use current localization, omit invented version metadata and retain long labels intact',async t=>{
 const view=harness(t,{showRestores:false});view.state.collapse(view.key);await tick();assert.equal(view.text(view.menu()),'模型连接');assert.doesNotMatch(view.text(view.menu()),/v1/);await view.language.setUILanguagePreference('en');await tick();assert.equal(view.text(view.menu()),view.language.t('模型连接'));
 const long='模型连接 / Model connection '+('很长的名称VeryLongUnbrokenName_'.repeat(20));view.setLong(long);await tick();assert.equal(view.text(view.menu()),long);assert.equal(view.menu().props.type,'button');assert.equal(view.menu().props.class,'as-interaction-restore-menu-item');assert.deepEqual(view.io,[]);
});

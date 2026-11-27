import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {resolve,dirname,extname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';

// Client UNIT: real production Solid components and locale facade. The universal
// DOM host and anchored Popover boundary are isolated. Actual Kobalte positioning,
// focus, layout and native opening require the separate integrated browser/OS QA.
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const universalSource=readFileSync(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8')
 .replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universalSource).toString('base64'));
const tick=()=>new Promise(done=>setImmediate(done));
function harness(t){
 const values=new Map(),storage={getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key)},saved={};
 for(const key of ['window','navigator','localStorage','eeasDesktop'])saved[key]=Object.getOwnPropertyDescriptor(globalThis,key);
 Object.defineProperty(globalThis,'window',{configurable:true,value:{addEventListener(){},removeEventListener(){}}});
 Object.defineProperty(globalThis,'navigator',{configurable:true,value:{language:'zh-CN'}});
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:storage});delete globalThis.eeasDesktop;
 const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:node=>node.type==='#text',replaceText:(node,value)=>{node.value=value;},
  insertNode(parent,node,anchor){if(node.parent){const old=node.parent.children;old.splice(old.indexOf(node),1);}node.parent=parent;const at=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(at<0?parent.children.length:at,0,node);},
  removeNode(parent,node){const at=parent.children.indexOf(node);if(at>=0)parent.children.splice(at,1);node.parent=null;},setProperty:(node,name,value)=>{node.props[name]=value;},getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
 const renderer=createRenderer(host),modules=new Map(),disposes=[];
 function load(file){
  file=resolve(file);if(!extname(file))file+=['NativePageConversation','CompanionFileContext','StudioPopover'].some(name=>file.endsWith('/'+name))?'.tsx':'.ts';
  if(modules.has(file))return modules.get(file).exports;
  if(file.endsWith('.json'))return JSON.parse(readFileSync(file,'utf8'));
  const module={exports:{}};modules.set(file,module);let source=readFileSync(file,'utf8');
  if(file.endsWith('.tsx')){source=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;source=babel.transformSync(source,{filename:file+'.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'test-renderer'}]],configFile:false,babelrc:false}).code;}
  const code=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS,esModuleInterop:true}}).outputText;
  const require=name=>{if(name==='solid-js')return solid;if(name==='test-renderer')return renderer;if(name.endsWith('.css'))return {};
   if(name==='./StudioPopover')return {__esModule:true,default:props=>[props.trigger,solid.createComponent(solid.Show,{get when(){return props.open;},get children(){return props.children;}})]};
   if(name.startsWith('.'))return load(resolve(dirname(file),name));throw Error('Unconfigured test dependency '+name);};
  new Function('require','module','exports',code)(require,module,module.exports);return module.exports;
 }
 const language=load(resolve(app,'src/ui-language.ts')),stop=language.startUILanguage();
 t.after(()=>{for(const dispose of disposes.reverse())dispose();stop();for(const[key,value]of Object.entries(saved))if(value)Object.defineProperty(globalThis,key,value);else delete globalThis[key];});
 function mount(name,props){const Component=load(resolve(app,'src',name+'.tsx')).default,container=host.createElement('root');disposes.push(renderer.render(()=>Component(props),container));
  const nodes=()=>{const list=[];const walk=node=>{list.push(node);node.children.forEach(walk);};walk(container);return list;};
  const text=node=>node.type==='#text'?String(node.value):node.children.map(text).join(' ');
  return {nodes,text:()=>text(container),find:(type,filter=()=>true)=>nodes().find(node=>node.type===type&&filter(node)),all:(type,filter=()=>true)=>nodes().filter(node=>node.type===type&&filter(node)),button:label=>nodes().find(node=>node.type==='button'&&(node.props['aria-label']===label||text(node).trim()===label||node.children.some(child=>child.type==='span'&&text(child.children[0]??child).trim()===label))),textOf:text};
 }
 return {mount,language,host,async locale(locale){await language.setUILanguagePreference(locale);await tick();}};
}
function click(node){assert.ok(node,'button exists');assert.notEqual(node.props.disabled,true,'button must be usable');node.props.onClick?.({preventDefault(){}});}
const sha='a'.repeat(64),source={kind:'LOCAL_FILE',project_id:'p_one',workspace_id:'w_one',binding_revision:1,binding_sha256:sha,path:'材料/长期研究方案与用户保存稿_测试😀.txt',revision:4,sha256:sha};
const selected={file:{source_ref:source,current:true,content:'User content 保存设置 😀',encoding:'utf-8',byte_length:50},workspace:{id:'w_one',project_id:'p_one',root:'D:\\Research',allowed_write_paths:[]}};
function fileProps(calls,overrides={}){return solid.mergeProps({required:false,busy:false,title:'当前页协作',onChoose:()=>calls.push('choose'),onOpen:()=>calls.push('open'),onReadCurrent:()=>calls.push('read'),onRemove:()=>calls.push('remove')},overrides);}
const chip=view=>view.find('button',node=>node.props.class==='as-native-file-chip');
const row=(view,label)=>view.find('button',node=>node.props.class==='as-native-file-menu-row'&&view.textOf(node).includes(label));

test('file context begins collapsed and prior project choices never activate without an explicit action',async t=>{
 const f=harness(t),calls=[],ref={project_id:'p_one',id:'link_old',revision:1,sha256:sha};
 const view=f.mount('CompanionFileContext',fileProps(calls,{previousFilename:'旧文件_不自动采用.docx',onSelectPrevious:()=>calls.push('previous'),legacy:[{fileLinkRef:ref,filename:'旧项目文稿.docx'}],onSelectLegacy:value=>calls.push(value)}));
 await tick();assert.equal(chip(view).props['aria-expanded'],false);assert.equal(view.all('pre').length,0);assert.equal(view.all('button').length,1);assert.deepEqual(calls,[]);
 click(chip(view));await tick();assert.equal(chip(view).props['aria-expanded'],true);assert.match(view.text(),/旧文件_不自动采用/);assert.deepEqual(calls,[]);
 click(row(view,'重新选择上次文件'));await tick();assert.deepEqual(calls,['previous']);assert.equal(chip(view).props['aria-expanded'],false);
 click(chip(view));await tick();click(row(view,'关联至此讨论'));await tick();assert.deepEqual(calls,['previous',ref]);assert.equal(chip(view).props['data-selected'],false);
});

test('linked and direct files dispatch only their exact original callbacks and retain safe diagnostic provenance',async t=>{
 const f=harness(t),calls=[],[value,setValue]=solid.createSignal(selected),[linked,setLinked]=solid.createSignal(undefined),[busy,setBusy]=solid.createSignal(false);
 const link={scope:{projectId:'p_one',conversationId:'c_one'},fileLinkRef:{project_id:'p_one',id:'linked',revision:2,sha256:sha},filename:'保存设置_人工修改.docx'};
 const view=f.mount('CompanionFileContext',fileProps(calls,{get selected(){return value();},get linked(){return linked();},get busy(){return busy();},onOpenLinked:()=>calls.push('open-linked'),onUnlink:()=>calls.push('unlink')}));
 await tick();assert.equal(chip(view).props.title,'长期研究方案与用户保存稿_测试😀.txt');
 click(chip(view));await tick();assert.ok(view.text().includes(f.language.t('未保存的软件内容尚未观测；关联不授予修改权限。')));click(row(view,'原软件打开'));await tick();assert.deepEqual(calls,['open']);
 click(chip(view));await tick();click(row(view,'重新读取当前版本'));await tick();assert.deepEqual(calls,['open','read']);
 setLinked(link);setValue(undefined);await tick();assert.equal(chip(view).props.title,link.filename);assert.equal(chip(view).props['aria-expanded'],false);
 click(chip(view));await tick();assert.equal(row(view,'重新读取当前版本'),undefined);assert.equal(view.find('pre'),undefined);assert.ok(view.text().includes(f.language.t('关联仅用于读取，不代表允许修改文件。')));
 setBusy(true);await tick();assert.equal(row(view,'原软件打开').props.disabled,true);assert.equal(row(view,'移除此文件上下文').props.disabled,true);
 setBusy(false);await tick();click(row(view,'原软件打开'));await tick();click(chip(view));await tick();click(row(view,'移除此文件上下文'));await tick();assert.deepEqual(calls,['open','read','open-linked','unlink']);
});

test('scope and saved-source revision changes close only disclosures; required/error recovery remains visible',async t=>{
 const f=harness(t),calls=[],[scope,setScope]=solid.createSignal('discussion-one'),[value,setValue]=solid.createSignal(selected),[error,setError]=solid.createSignal('来源核对失败：原引用保留');
 const view=f.mount('CompanionFileContext',fileProps(calls,{get scopeKey(){return scope();},get selected(){return value();},get contextError(){return error();},required:true,onRetryContext:()=>calls.push('retry')}));
 await tick();assert.match(view.text(),/来源核对失败/);click(chip(view));await tick();setScope('discussion-two');await tick();assert.equal(chip(view).props['aria-expanded'],false);assert.match(view.text(),/来源核对失败/);assert.deepEqual(calls,[]);
 click(chip(view));await tick();setValue({...selected,file:{...selected.file,source_ref:{...source,revision:5,sha256:'b'.repeat(64)}}});await tick();assert.equal(chip(view).props['aria-expanded'],false);
 click(chip(view));await tick();click(row(view,'重新核对文件关联'));await tick();assert.deepEqual(calls,['retry']);
 setValue(undefined);setError('');await tick();assert.match(view.text(),/先选择需要处理的文件，原文字保留/);assert.deepEqual(calls,['retry']);
});

test('inline references, bounded overflow, removal and scope changes preserve the actual parent textarea',async t=>{
 const f=harness(t),removed=[],[scope,setScope]=solid.createSignal('s_one'),[folded,setFolded]=solid.createSignal(false),[busy,setBusy]=solid.createSignal(false),[attachments,setAttachments]=solid.createSignal([{id:'one',label:'很长的保存设置_用户原始材料_😀.docx',meta:'v4',description:'保留这段用户批注，不翻译。',warning:'当前版本已变化，需要重新定位',onRemove:()=>removed.push('one')},{id:'two',label:'第二个引用'},{id:'three',label:'第三个引用'}]);
 const input=f.host.createElement('textarea');input.props.value='未发送的原始文字 IME 拼音 😀';const toolbar=f.host.createElement('button');toolbar.props['aria-label']='original-send';
 const view=f.mount('NativePageConversation',{input,toolbar,get scopeKey(){return scope();},get folded(){return folded();},get busy(){return busy();},get attachments(){return attachments();}});await tick();
 const reference=()=>view.find('button',node=>node.props.class==='as-composer-attachment-main');
 assert.equal(view.find('textarea'),input);assert.match(view.text(),/当前版本已变化/);assert.doesNotMatch(view.text(),/保留这段用户批注/);assert.deepEqual(removed,[]);
 click(reference());await tick();assert.match(view.text(),/保留这段用户批注/);assert.equal(view.find('pre'),undefined);
 const more=()=>view.find('button',node=>node.props.class==='as-composer-attachment-more');assert.equal(more().props['aria-expanded'],false);click(more());await tick();assert.match(view.text(),/第三个引用/);
 const remove=()=>view.find('button',node=>node.props.class==='as-composer-attachment-remove');setBusy(true);await tick();assert.equal(remove().props.disabled,true);setBusy(false);await tick();click(remove());assert.deepEqual(removed,['one']);
 setScope('s_two');await tick();assert.equal(more().props['aria-expanded'],false);assert.equal(view.find('textarea'),input);assert.equal(input.props.value,'未发送的原始文字 IME 拼音 😀');
 click(more());await tick();setFolded(true);await tick();assert.equal(more().props['aria-expanded'],false);assert.equal(view.find('div',node=>node.props.id==='as-composer-fields').props.hidden,true);assert.equal(view.find('textarea'),input);assert.equal(view.find('button',node=>node.props['aria-label']==='original-send'),toolbar);
 setFolded(false);setAttachments([]);await tick();assert.equal(reference(),undefined);assert.equal(view.find('textarea'),input);assert.deepEqual(removed,['one']);
});

test('locale changes only authored controls; file names, path, original comments and pending textarea stay unchanged',async t=>{
 const f=harness(t),calls=[],view=f.mount('CompanionFileContext',fileProps(calls,{selected}));await tick();const original=chip(view);click(original);await tick();
 await f.locale('en');assert.equal(chip(view),original);assert.equal(original.props.title,'长期研究方案与用户保存稿_测试😀.txt');assert.match(view.text(),/材料\/长期研究方案与用户保存稿_测试😀\.txt/);assert.ok(view.text().includes(f.language.t('原软件打开')));assert.deepEqual(calls,[]);
 await f.locale('zh-CN');assert.equal(chip(view),original);assert.match(view.text(),/原软件打开/);assert.deepEqual(calls,[]);
});

test('input task methods choose only processing intent; session navigation and management are not mixed into this menu',async t=>{
 const f=harness(t),calls=[],view=f.mount('TaskMethodPicker',{mode:'direct',onChoose:value=>calls.push(value),onOpening:()=>calls.push('opening')});click(view.button('选择任务方式'));await tick();assert.doesNotMatch(view.text(),/项目会话|会话管理/);click(view.button('需求梳理'));await tick();assert.deepEqual(calls,['opening','discovery']);assert.equal(view.button('选择任务方式').props['aria-expanded'],false);
});

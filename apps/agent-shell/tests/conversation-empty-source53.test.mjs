import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import * as solid from '../node_modules/solid-js/dist/solid.js';
import {sameQuickScope} from '../src/quick-inbox.ts';
import {deliveryHistoryPlan,originalDeliveryTime,stableDeliveryOrder} from '../src/delivery-history.ts';
import {sameReview} from '../src/native-review.ts';

const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8'),tree=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=tree.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='App');
const predicate=app.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='hasConversationContent');assert.ok(predicate);
let emptyBranch,input;
function visit(node){if(ts.isJsxElement(node)||ts.isJsxSelfClosingElement(node)){const opening=ts.isJsxElement(node)?node.openingElement:node,text=opening.getText(tree);if(opening.tagName.getText(tree)==='Show'&&text.includes('feed().length||!hasConversationContent()'))emptyBranch=node.getText(tree);if(opening.tagName.getText(tree)==='textarea'&&text.includes('描述要完成的工作'))input=node.getText(tree);}ts.forEachChild(node,visit);}visit(app);assert.ok(emptyBranch&&input);
const universal=readFileSync(new URL('../node_modules/solid-js/universal/dist/universal.js',import.meta.url),'utf8').replace("from 'solid-js'",`from '${new URL('../node_modules/solid-js/dist/solid.js',import.meta.url).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universal).toString('base64'));
const ref=id=>({id,revision:1,sha256:'a'.repeat(64)}),scope={projectId:'p',workspaceId:'w',sessionId:undefined,conversationId:'c'};
const turn=()=>new Promise(setImmediate);
const feedSource=readFileSync(new URL('../src/DeliveryHistoryFeed.tsx',import.meta.url),'utf8'),feedTree=ts.createSourceFile('DeliveryHistoryFeed.tsx',feedSource,99,true,4);
const feedBody=feedTree.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='DeliveryHistoryFeed').getText(feedTree).replace('export default ','');

function render(){
 const host={createElement:type=>({type,props:{},children:[],parent:null,querySelector:()=>null,contains:()=>false}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),isTextNode:node=>node.type==='#text',replaceText:(node,value)=>node.value=value,
 insertNode(parent,node,anchor){if(node.parent){const i=node.parent.children.indexOf(node);if(i>=0)node.parent.children.splice(i,1);}node.parent=parent;parent.children.splice(anchor?parent.children.indexOf(anchor):parent.children.length,0,node);},removeNode(parent,node){const i=parent.children.indexOf(node);if(i>=0)parent.children.splice(i,1);node.parent=null;},setProperty:(node,key,value)=>node.props[key]=value,getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
 const renderer=Object.assign(createRenderer(host),{Show:solid.Show,For:solid.For}),initial={scope:{...scope},native:[],quick:[],direct:[],discovery:undefined,original:undefined,operations:[],recovery:[],running:undefined,feed:[],tool:'direct',text:'尚未发送的输入'};
 const [state,setState]=solid.createSignal(initial),calls=[];
 const element=type=>props=>{const node=renderer.createElement(type);renderer.spread(node,props);return node;};
 const bindings={createEffect:solid.createEffect,createMemo:solid.createMemo,createSignal:solid.createSignal,createUniqueId:solid.createUniqueId,onCleanup:solid.onCleanup,deliveryHistoryPlan,originalDeliveryTime,stableDeliveryOrder,t:key=>key,uiLocale:()=> 'zh-CN',deliveryDirectories:()=>({}),renderer,Show:solid.Show,For:solid.For,sameQuickScope,sameReview,threadScope:()=>undefined,threadAccess:()=>'ready',feedById:()=>new Map(state().feed.map(item=>[item.id,item])),DeliveryFeedFeedback:element('section'),inspectDeliveryFeedback(){},
 quickScope:()=>state().scope,nativeMessages:()=>state().native,quickRecords:()=>state().quick,directResults:()=>state().direct,activeSession:()=>state().discovery,originalSession:()=>state().original,originalOperations:()=>state().operations,originalRecovery:()=>state().recovery,activeOperation:()=>state().running,toolMode:()=>state().tool,
 feed:()=>state().feed,busy:()=>'',uiText:key=>key,providersReady:()=>true,providers:()=>[{}],workspace:()=>undefined,directFile:()=>undefined,brandIcon:'brand.svg',brandSignature:'signature.svg',Button:element('button'),Icon:element('i'),BrandLoader:element('i'),TaskStatus:element('small'),DeliveryArtifactCard:element('article'),authoredText:value=>value,deliveryComplete:()=>true,context:{},nativeBridge:undefined,openProviders(){},openFolderDialog(){},inspect(){},
 windowMode:()=> 'workbench',composerView:()=>({focused:true}),composerPresentation:{focus(){}},prompt:()=>state().text,setPrompt:text=>setState(current=>({...current,text})),submitPrompt:()=>calls.push('send')};
 const jsx=feedBody+'\n'+predicate.getText(tree)+`\nfunction Scene(){return <main>${emptyBranch}${input}</main>}`;
 const js=ts.transpileModule(jsx,{compilerOptions:{jsx:ts.JsxEmit.Preserve,target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
 const compiled=babel.transformSync(js,{configFile:false,babelrc:false,presets:[[solidPreset,{generate:'universal',moduleName:'actual-renderer'}]]}).code.replace(/import\s*\{([^}]+)\}\s*from\s*["']actual-renderer["'];?/g,(_match,list)=>'const {'+list.replace(/\s+as\s+/g,':')+'}=bindings.renderer;');
 const Scene=new Function('bindings',`const {${Object.keys(bindings).filter(key=>key!=='renderer').join(',')}}=bindings;${compiled};return Scene;`)(bindings),mount={type:'root',children:[]},dispose=renderer.render(()=>Scene(),mount);
 const all=()=>{const nodes=[];function walk(node){nodes.push(node);for(const child of node.children??[])walk(child);}walk(mount);return nodes;};
 return {set:patch=>setState(current=>({...current,...patch})),all,empty:()=>all().filter(node=>node.props?.class==='as-empty').length,input:()=>all().find(node=>node.type==='textarea'),calls,dispose};
}

test('actual empty-state JSX removes branding for same-scope Quick SEND before native import; current draft/IME node stays mounted',async()=>{
 const view=render();try{const input=view.input();assert.equal(view.empty(),1);input.props.onKeyDown({key:'Enter',ctrlKey:true,isComposing:true,preventDefault(){throw Error('IME consumed');}});assert.equal(view.calls.length,0);
  view.set({quick:[{id:'sent',delivery:'SEND',scope:{...scope},operationId:'a',message:{operation_id:'a',status:'RETURNED',text:'真实历史'}}]});await turn();assert.equal(view.empty(),0);assert.equal(view.input(),input);assert.equal(input.props.value,'尚未发送的输入');
  view.set({text:'后续输入',quick:[]});await turn();assert.equal(view.empty(),1);assert.equal(view.input(),input);assert.equal(input.props.value,'后续输入');
 }finally{view.dispose();}
});
test('unsent Quick drafts and foreign project/workspace/session/conversation records cannot hide the real empty state',async()=>{
 const view=render();try{for(const record of [{delivery:'DRAFT',scope:{...scope}},...['projectId','workspaceId','sessionId','conversationId'].map(key=>({delivery:'SEND',scope:{...scope,[key]:'another'}}))]){view.set({quick:[record]});await turn();assert.equal(view.empty(),1,JSON.stringify(record));}}finally{view.dispose();}
});
test('current native history suppresses branding, including in-flight local receipts; foreign conversation/project histories do not',async()=>{
 const view=render();try{for(const [message,empty] of [[{operation_id:'a',conversation_id:'c',project_id:'internal-project',status:'RETURNED'},0],[{operation_id:'a',status:'REQUESTING'},0],[{operation_id:'a',conversation_id:'other',status:'RETURNED'},1],[{operation_id:'a',project_id:'other',status:'RETURNED'},1]]){view.set({native:[{input:'已发问题',message}]});await turn();assert.equal(view.empty(),empty);}}finally{view.dispose();}
});
test('selected original-session messages and real operations suppress branding; empty or other-session records do not',async()=>{
 const view=render(),original={project_id:'p',session_ref:ref('original'),messages:[],pending_questions:[],pending_permissions:[]};try{view.set({tool:'original',scope:{...scope,sessionId:'original'},original});await turn();assert.equal(view.empty(),1);
  view.set({original:{...original,messages:[{info:{role:'user'},parts:[]}]}});await turn();assert.equal(view.empty(),0);
  view.set({original,operations:[{id:'guide',session_ref:ref('original')}]});await turn();assert.equal(view.empty(),0);
  view.set({operations:[{id:'other',session_ref:ref('another')}]});await turn();assert.equal(view.empty(),1);
  view.set({original:{...original,messages:[{}]},scope:{...scope,sessionId:'another'}});await turn();assert.equal(view.empty(),1);
  view.set({scope:{...scope,sessionId:'original'},original:{...original,project_id:'another',messages:[{}]}});await turn();assert.equal(view.empty(),1);
 }finally{view.dispose();}
});
test('direct receipts and running work count only in their existing project/session scope',async()=>{
 const view=render();try{view.set({direct:[{project_id:'p',operation_id:'edit'}]});await turn();assert.equal(view.empty(),0);view.set({direct:[{project_id:'other'}]});await turn();assert.equal(view.empty(),1);
  view.set({direct:[{project_id:'p'}],scope:{...scope,sessionId:'other-session'}});await turn();assert.equal(view.empty(),1);
  view.set({direct:[],running:{projectId:'p',sessionId:'other-session'}});await turn();assert.equal(view.empty(),0);view.set({running:{projectId:'p',sessionId:'another'}});await turn();assert.equal(view.empty(),1);view.set({running:{projectId:'other'}});await turn();assert.equal(view.empty(),1);
 }finally{view.dispose();}
});
test('selected requirements and existing feed retain their transcript without an empty brand; another requirement cannot suppress a blank discussion',async()=>{
 const view=render();try{view.set({scope:{...scope,sessionId:'requirement'},discovery:{id:'requirement',project_id:'p'}});await turn();assert.equal(view.empty(),0);view.set({discovery:{id:'other',project_id:'p'}});await turn();assert.equal(view.empty(),1);view.set({discovery:{id:'requirement',project_id:'other'}});await turn();assert.equal(view.empty(),1);
  view.set({discovery:undefined,feed:[{id:'actual',title:'已读取资料',body:'原字节已保存',status:'SAVED'}]});await turn();assert.equal(view.empty(),0);assert.equal(view.all().filter(node=>node.props?.class==='as-feed-item').length,1);
 }finally{view.dispose();}
});

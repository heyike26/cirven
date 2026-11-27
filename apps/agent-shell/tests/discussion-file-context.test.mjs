import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import {DiscussionFileContext,sameDiscussionScope,associationScope,fileConversationBasis} from '../src/discussion-file-context.ts';
import {conversationIdFor} from '../src/quick-inbox.ts';
import {nativeMessageInput,nativeInputAttachments} from '../src/native-collaboration.ts';
import {readCompanionFileDraft,saveCompanionFileDraft} from '../src/companion-files.ts';
const scope={projectId:'p',workspaceId:'w',sessionId:'s',conversationId:'c'},ref={project_id:'p',id:'a'.repeat(64),revision:1,sha256:'b'.repeat(64)};
const state=(value=scope,selected=true)=>({scope:value,selected:selected?{scope:value,fileLinkRef:ref,filename:'人工保存.docx'}:null,legacy_candidates:[]});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function deferred(){let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};}
function fixture(bridge){const values=[],errors=[];return{values,errors,controller:new DiscussionFileContext(bridge,value=>values.push(value),value=>errors.push(value))};}

test('exact host projection is read only, coalesces changes, and rejects a cross-discussion response',async()=>{
 const one=deferred(),values=[one.promise,Promise.resolve(state())];let calls=0,notify,unsubscribed=0;
 const f=fixture({getQuickFileContext:()=>{calls++;return values.shift();},onQuickDraftsChanged:fn=>{notify=fn;return()=>unsubscribed++;}});
 f.controller.start();const first=f.controller.setScope(scope);for(let i=0;i<20;i++)notify();assert.equal(calls,1);one.resolve(state());await first;
 assert.equal(calls,2);assert.deepEqual(f.controller.state,state());
 f.controller.stop();notify();await tick();assert.equal(calls,2);assert.equal(unsubscribed,1);
 const wrong=fixture({getQuickFileContext:async()=>state({...scope,conversationId:'other'})});await wrong.controller.setScope(scope);
 assert.equal(wrong.controller.state,undefined);assert.match(wrong.errors.at(-1),/暂未核对/);
});
test('a late old-scope GET and stopped controller cannot replace the selected current discussion',async()=>{
 const one=deferred(),two=state({...scope,conversationId:'two'});let calls=0;const f=fixture({getQuickFileContext:()=>++calls===1?one.promise:Promise.resolve(two)});
 const reading=f.controller.setScope(scope);void f.controller.setScope(two.scope);one.resolve(state());await reading;
 assert.deepEqual(f.controller.state,two);assert.ok(!f.values.some(value=>value?.scope.conversationId==='c'));
 const late=deferred();const stopped=fixture({getQuickFileContext:()=>late.promise});const wait=stopped.controller.setScope(scope);stopped.controller.stop();late.resolve(state());await wait;assert.equal(stopped.controller.state,undefined);
});
test('unknown clear performs one mutation then only reads its outcome, preserving original input and preventing rapid duplicate writes',async()=>{
 const posted=deferred(),calls=[];let current=state();const f=fixture({getQuickFileContext:async()=>{calls.push('GET');return current;},clearQuickFileContext:body=>{calls.push(structuredClone(body));return posted.promise;}});
 await f.controller.setScope(scope);const clearing=f.controller.clear();await assert.rejects(()=>f.controller.clear(),/正在核对/);
 current=state(scope,false);posted.reject(Error('fixture lost reply after commit'));await assert.rejects(()=>clearing,/未重复提交/);
 assert.equal(calls.filter(value=>typeof value==='object').length,1);assert.deepEqual(calls[1],{scope,expectedRef:ref});assert.equal(f.controller.state.selected,null);assert.deepEqual(calls.filter(value=>value==='GET'),['GET','GET']);
});
test('legacy selection requires explicit candidate; late failed selection cannot report into another discussion',async()=>{
 const posted=deferred(),calls=[];let current={...state(scope,false),legacy_candidates:[{fileLinkRef:ref,filename:'旧项目稿.docx'}]};const f=fixture({getQuickFileContext:async()=>current,selectQuickFileContext:body=>{calls.push(body);return posted.promise;}});
 await f.controller.setScope(scope);await assert.rejects(()=>f.controller.select({...ref,id:'f'.repeat(64)}),/明确选择/);assert.equal(calls.length,0);
 const selection=f.controller.select(ref);current=state({...scope,workspaceId:'another'},false);await f.controller.setScope(current.scope);posted.reject(Error('fixture late failure'));await selection;
 assert.equal(calls.length,1);assert.deepEqual(f.controller.state,current);assert.deepEqual(f.errors.filter(Boolean),[]);
});
test('linked saved-file questions keep one file basis and can carry explicit typed screenshot input',()=>{
 const text='保留草稿 😀\r\n 按保存稿继续';const body=nativeMessageInput('question',text,[],undefined,undefined,undefined,'c',ref);
 assert.equal(body.rawText,text);assert.deepEqual(body.fileLinkRef,ref);assert.equal(body.projectId,'p');assert.equal(body.conversationId,'c');assert.equal(body.contextRef,undefined);
 assert.throws(()=>nativeMessageInput('question',text,[],undefined,undefined,{project_id:'p'},'c',ref),/一个文件依据/);
 const frame={id:'saved_frame',revision:1,sha256:'c'.repeat(64)},input=nativeMessageInput('question',text,[{context_ref:frame,label:'Saved screenshot',comment:'My instruction',regions:[],preview_sha256:'d'.repeat(64)}],undefined,undefined,undefined,'c',ref);assert.deepEqual(input.fileLinkRef,ref);assert.equal(input.rawText,text);assert.equal(input.inputAttachments[0].kind,'SCREENSHOT');assert.deepEqual(input.inputAttachments[0].context_ref,frame);assert.equal(input.contextRef,undefined);
});

// Execute the real App callbacks with isolated service/OS boundaries. These tests
// verify selection/navigation, not Windows native-open or a real model answer.
const source=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8'),ast=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=ast.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text==='App');
function actual(name){const node=app.body.statements.find(node=>ts.isFunctionDeclaration(node)&&node.name?.text===name);assert.ok(node);return ts.transpileModule(node.getText(ast),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;}
function callback(name,context){return new Function(...Object.keys(context),actual(name)+'\nreturn '+name)(...Object.values(context));}
test('project-only restored file remains a candidate and does not restore a write grant or read a user file',async()=>{
 const data=new Map(),storage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value),removeItem:key=>data.delete(key)},saved={source_ref:{kind:'local_file',project_id:'p',workspace_id:'w',binding_revision:1,binding_sha256:'c'.repeat(64),path:'report.docx',revision:1,sha256:'d'.repeat(64)},write_requested:true};
 saveCompanionFileDraft(storage,'p',saved);let candidate;
 const restore=callback('restoreSelectedDirectFile',{readCompanionFileDraft,localStorage:storage,project:()=>({id:'p'}),setPreviousDirectFile:value=>candidate=value,adoptDirectSource:()=>assert.fail('restore must not adopt'),setDirectWrite:()=>assert.fail('restore must not grant write')});
 await restore('p');assert.deepEqual(candidate,saved);assert.equal(data.size,1);
});
for(const step of ['before-adoption','during-adoption'])test('actual file open rejects a same-project discussion change '+step,async()=>{
 let current=scope,opened=0,adopted=0;const reading=deferred(),adopting=deferred(),source={project_id:'p',path:'report.docx'};
 const open=callback('openDirectSource',{action:async(_,fn)=>fn(),navigation:{capture:()=>()=>true},quickScope:()=>current,associationScope,sameDiscussionScope,companionFilesApi:{load:()=>reading.promise},project:()=>({id:'p'}),adoptDirectSource:async()=>{adopted++;if(step==='during-adoption')await adopting.promise;},nativeBridge:{openDocument:async()=>{opened++;return{status:'OPEN_REQUESTED'};}},setNotice:()=>{},inspect:()=>assert.fail('late open cannot preview')});
 const pending=open(source),rejected=assert.rejects(pending,/当前工作已切换/);if(step==='before-adoption')current={...scope,conversationId:'new'};reading.resolve({file:{source_ref:source}});await tick();if(step==='during-adoption'){current={...scope,sessionId:'new'};adopting.resolve();}
 await rejected;assert.equal(opened,0);assert.equal(adopted,step==='before-adoption'?0:1);
});
test('exact file open forwards its original scope after all awaits and leaves model/user drafts untouched',async()=>{
 const calls=[],source={project_id:'p',path:'report.docx'},bridge={openDocument:async(...args)=>{calls.push(args);return{status:'OPEN_REQUESTED'};}},open=callback('openDirectSource',{action:async(_,fn)=>fn(),navigation:{capture:()=>()=>true},quickScope:()=>scope,associationScope,sameDiscussionScope,companionFilesApi:{load:async()=>({file:{source_ref:source}})},project:()=>({id:'p'}),adoptDirectSource:async()=>{},nativeBridge:bridge,openFeedbackDocument:(ref,options)=>bridge.openDocument(ref.project_id,ref,options),setNotice:()=>{}});
 await open(source);assert.deepEqual(calls,[['p',source,{scope}]]);
});
test('normal Agent composer uses the same exact file continuation in companion and workbench without changing pure-text session mode',async()=>{
 const preceding={hasComposerFile:()=>true,draftAttachments:()=>[],selectedDiscussion:()=>undefined,threadScope:()=>undefined,threadWriteAllowed:()=>true,submitFeedbackPassage:async()=>false,ordinaryGuidanceParent:()=>undefined,nativePending:()=>false,pageReview:()=>undefined};
 for(const mode of ['companion','workbench']){
  let sent=0;const submit=callback('submitPrompt',{...preceding,toolMode:()=> 'direct',windowMode:()=>mode,linkedFile:()=>({fileLinkRef:ref}),submitCompanion:async()=>{sent++;}});
  await submit();assert.equal(sent,1);
 }
 let plain=0;const submit=callback('submitPrompt',{...preceding,toolMode:()=> 'original',submitOriginalPrompt:async()=>plain++,submitCompanion:()=>assert.fail('pure text must not implicitly attach a file')});await submit();assert.equal(plain,1);
});
test('original-session mode uses the exact original ref only as an isolated local conversation key, without migrating any file authority',()=>{
 const map=new Map(),storage={getItem:key=>map.get(key)??null,setItem:(key,value)=>map.set(key,value)},original={id:'original_a',revision:1,sha256:'c'.repeat(64)};
 const direct=conversationIdFor(storage,fileConversationBasis(scope)),first=conversationIdFor(storage,fileConversationBasis(scope,original));
 for(const next of [null,{...original,id:'original_b'},{...original,revision:2},{...original,sha256:'d'.repeat(64)}])assert.notEqual(conversationIdFor(storage,fileConversationBasis(scope,next)),first);
 assert.notEqual(first,direct);assert.equal(conversationIdFor(storage,fileConversationBasis(scope,original)),first);assert.equal(conversationIdFor(storage,fileConversationBasis(scope)),direct);
 assert.ok([...map.keys()].every(key=>key.startsWith('eeas-conversation:')));assert.ok(![...map.values()].some(value=>value.includes('file_link_ref')));
});

test('native-history file input survives a retained requirements mode; a requirements discussion retains its selected workflow',async()=>{
 for(const native of [false,true]){
  const calls=[];const submit=callback('submitPrompt',{threadWriteAllowed:()=>true,threadScope:()=>undefined,selectedDiscussion:()=>native?{key:{kind:'native'}}:undefined,submitFeedbackPassage:async()=>false,ordinaryGuidanceParent:()=>undefined,nativePending:()=>false,pageReview:()=>undefined,toolMode:()=> 'discovery',draftAttachments:()=>[],nativeDraftAttachments:()=>[],directFile:()=>undefined,nativeFeedbackAvailable:()=>false,linkedFile:()=>({fileLinkRef:ref}),hasComposerFile:()=>true,discoverySendState:()=> 'blocked',submitCompanion:async()=>calls.push('file'),sendNativeMessage:async()=>calls.push('page')});
  await submit();assert.deepEqual(calls,native?['file']:[]);
 }
});
test('workbench requirements mode displays the existing exact file-context projection without opening another view',()=>{
 let expression;
 function walk(node){if(ts.isJsxElement(node)&&node.openingElement.tagName.getText(ast)==='Show'&&node.children.some(child=>ts.isJsxSelfClosingElement(child)&&child.tagName.getText(ast)==='CompanionFileContext'))expression=node.openingElement.attributes.properties.find(value=>value.name?.getText(ast)==='when').initializer.expression.getText(ast);ts.forEachChild(node,walk);}
 walk(ast);assert.ok(expression);
 const context={windowMode:()=> 'workbench',toolMode:()=> 'discovery',directFile:()=>undefined,directFileRequired:()=>false,linkedFile:()=>state().selected,discussionFileState:()=>state(),discussionFileError:()=>''};
 const shown=new Function(...Object.keys(context),'return !!('+expression+')')(...Object.values(context));assert.equal(shown,true);
});
test('saved-file context and typed screenshot use the accepted native input contract together with exact file identity',async()=>{
 const attachments=[{context_ref:{id:'saved_frame',revision:1,sha256:'c'.repeat(64)},label:'Screenshot',comment:'截图中的反馈',regions:[],preview_sha256:'d'.repeat(64)}],calls=[],errors=[];
 const submit=callback('submitCompanion',{threadWriteAllowed:()=>true,submitFeedbackPassage:async()=>false,linkedFile:()=>({fileLinkRef:ref}),nativeDraftAttachments:()=>attachments,prompt:()=> '按当前稿与截图核对，不授权写入',nativeInputAttachments,setError:value=>errors.push(value),sendNativeMessage:async(text,input,file,link)=>calls.push(nativeMessageInput('combined_file_input',text,input,undefined,undefined,file,'c',link))});
 await submit();assert.deepEqual(errors,[]);assert.equal(calls.length,1);assert.deepEqual(calls[0].fileLinkRef,ref);assert.equal(calls[0].conversationId,'c');assert.equal(calls[0].inputAttachments[0].kind,'SCREENSHOT');assert.equal(calls[0].inputAttachments[0].image_sha256,'d'.repeat(64));assert.equal(calls[0].fileSourceRef,undefined);
});

test('a suspended direct source or project-only candidate is never an implicit file input',()=>{
 const active=callback('hasComposerFile',{linkedFile:()=>undefined,nativeFeedbackAvailable:()=>false,directFile:()=>({file:{source_ref:{project_id:'p'}}})});assert.equal(active(),false);
 const link=callback('hasComposerFile',{linkedFile:()=>state().selected,nativeFeedbackAvailable:()=>false,directFile:()=>undefined});assert.equal(link(),true);
});

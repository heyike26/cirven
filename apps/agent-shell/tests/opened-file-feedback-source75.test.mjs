import ts from 'typescript';
import {syncSelectedSource} from '../src/source-watch.ts';
import {discoveryInput,discoverySendGate} from '../src/discovery-pending.ts';
import {DiscoveryApi} from '../src/discovery-api.ts';
import test from 'node:test';
import assert from 'node:assert/strict';
import {controller,appFunction} from './opened-file-feedback-source75.fixture.mjs';
import {exactFileSource,sameSelectedFile,directFileSupported,companionRoute} from '../src/companion-files.ts';
import {sameDiscussionScope,sameFileLink,associationScope} from '../src/discussion-file-context.ts';
import {nativeReference} from '../src/native-collaboration.ts';
const createSignal=value=>[()=>value,next=>value=typeof next==='function'?next(value):next];
const make=()=>controller({createSignal,syncSelectedSource,exactFileSource,sameSelectedFile,directFileSupported,companionRoute,sameDiscussionScope,sameFileLink,associationScope,nativeReference});
const tick=()=>new Promise(r=>setImmediate(r));
test('saved file feedback returns by exact reference after opening a conversation and cannot replace newer UI state',async()=>{
 const reference=id=>({id,revision:1,sha256:'a'.repeat(64)});
 const old={feedback_ref:reference('old'),payload:{project_id:'p'}};
 const latest={feedback_ref:reference('old'),payload:{project_id:'p'},view:'newer'};
 const newlySaved={feedback_ref:reference('new'),payload:{project_id:'p'}};
 let records=[],current=true,project={id:'p'},complete;
 const pending=new Promise(resolve=>{complete=resolve;});
 const calls=[];
 const bindings={
  localStorage:{getItem:key=>{assert.equal(key,'eeas-native-file-feedback:p');return JSON.stringify([old.feedback_ref,reference('missing'),{}]);}},
  project:()=>project,nativeReference,
  nativeFeedbackApi:{get:async(pid,ref)=>{calls.push({pid,ref});if(ref.id==='missing')throw Error('Missing saved record');return pending;}},
  setNativeFileFeedbacks:change=>{records=change(records);},
 };
 const compiled=ts.transpileModule(appFunction('restoreFileFeedbackHistory'),{compilerOptions:{target:99,module:99}}).outputText;
 const restore=new Function(...Object.keys(bindings),compiled+'\nreturn restoreFileFeedbackHistory;')(...Object.values(bindings));
 const history=restore('p',()=>current);
 records=[latest,newlySaved];complete(old);
 await history;
 assert.deepEqual(calls,[{pid:'p',ref:old.feedback_ref},{pid:'p',ref:reference('missing')}]);
 assert.deepEqual(records,[latest,newlySaved]);
 current=false;records=[];
 await restore('p',()=>current);assert.equal(calls.length,2);
 current=true;project={id:'other'};
 await restore('p',()=>current);assert.deepEqual(records,[]);
 project={id:'p'};
 await restore('p',()=>current);assert.deepEqual(records,[old]);
});
test('a ready canonical conversation exposes feedback for its selected project file while keeping original message mode',()=>{
 const q=make(),scope={metadata_source:{kind:'conversation'},project_id:'p',archived:false};
 let file=q.directFile(),thread=scope,access='ready',mode='original',project={id:'p'};
 const bindings={directFile:()=>file,threadScope:()=>thread,threadAccess:()=>access,toolMode:()=>mode,project:()=>project};
 const compiled=ts.transpileModule(appFunction('showNativeFileFeedback'),{compilerOptions:{target:99,module:99}}).outputText;
 const show=new Function(...Object.keys(bindings),compiled+'\nreturn showNativeFileFeedback;')(...Object.values(bindings));
 assert.equal(show(),true);
 assert.equal(mode,'original');
 thread={...scope,archived:true};
 assert.equal(show(),false);
 thread={...scope,project_id:'other'};
 assert.equal(show(),false);
 thread=scope;access='deleted';
 assert.equal(show(),false);
 access='ready';project={id:'other'};
 assert.equal(show(),false);
 project={id:'p'};file=undefined;
 assert.equal(show(),false);
 file=q.directFile();thread=undefined;
 assert.equal(show(),false);
 mode='direct';
 assert.equal(show(),true);
});
test('real delivery actions retain one exact source for host event before or after OPEN_REQUESTED/SAVED, without write permission',async()=>{
 for(const order of ['before','after'])for(const kind of ['deliveryOpen','deliveryCurrent']){
  const q=make(),pending=q[kind]({scope:{projectId:'p'}},{receipt:{report:{artifact:{sha256:'d'.repeat(64)}}},file:{}});await tick();if(order==='before'){q.event();assert.equal(q.nativeFeedbackAvailable(),false);assert.ok(q.directFile());}q.ack();await pending;if(order==='after')q.event();assert.equal(q.nativeFeedbackAvailable(),true);assert.ok(exactFileSource(q.directFile().file.source_ref,q.source()));assert.equal(q.calls.filter(c=>c[0]==='OPEN').length,1);assert.equal(q.writes,false);assert.equal(q.prompt,'Keep the original intent');
 }
});
test('OPEN_REQUESTED alone, non-SAVED context, altered source/link/scope, stale and unsupported source never mint a feedback pair',async()=>{
 const variants=[{file_context_status:'SUPERSEDED'},{file_context_status:'LOCAL_SAVE_UNCONFIRMED'},{file_context_status:'NOT_AVAILABLE'},{status:'FAILED'},{file_source_ref:null},{file_link_ref:{id:'link',revision:1,sha256:'a'.repeat(64),project_id:'other'}},{file_context_scope:{projectId:'p',workspaceId:'w',conversationId:'two'}}];
 for(const extra of variants){const q=make(),p=q.open();q.event();q.ack(q.receipt(extra));await p;assert.equal(q.nativeFeedbackAvailable(),false);assert.equal(q.openedFeedbackFile(),undefined);assert.equal(q.prompt,'Keep the original intent');}
 for(const extra of [{revision:2,sha256:'2'.repeat(64)},{path:'same-name/task.md'},{binding_sha256:'c'.repeat(64)}]){const q=make(),p=q.open();q.event();q.ack(q.receipt({file_source_ref:q.source(1,extra)}));await p;assert.equal(q.nativeFeedbackAvailable(),false);}
 for(const field of [{current:false},{encoding:'binary'},{source_ref:make().source(1,{path:'task.docx'})}]){const q=make();q.directFile().file={...q.directFile().file,...field};const p=q.open();q.event();q.ack();await p;assert.equal(q.nativeFeedbackAvailable(),false);}
});
test('late open cannot attach across project, discussion, workspace, selection or away-and-back leases; new draft remains',async()=>{
 for(const change of ['project','workspace','discussion','file','same-path-version','away-back','navigation']){const q=make(),p=q.open();q.setPrompt('New draft');if(change==='file')q.newSelection();else if(change==='same-path-version')q.refresh();else if(change==='navigation')q.nav();else if(change==='away-back'){q.switchScope({projectId:'p',workspaceId:'w',conversationId:'two'});q.switchScope({projectId:'p',workspaceId:'w',conversationId:'one'});}else q.switchScope({projectId:change==='project'?'other':'p',workspaceId:change==='workspace'?'other':'w',conversationId:change==='discussion'?'two':'one'});const selected=q.directFile();q.event();q.ack();await p;assert.equal(q.openedFeedbackFile(),undefined,change);assert.equal(q.directFile(),selected,change);assert.equal(q.prompt,'New draft',change);assert.equal(q.calls.length,1,change);}
});
test('ordinary same-path links/ref revisions and external newer file cannot reuse volatile source trust; restart is conservative',async()=>{
 const ordinary=make();ordinary.event();assert.equal(ordinary.nativeFeedbackAvailable(),false);
 for(const extra of [{id:'other'},{revision:2},{sha256:'d'.repeat(64)}]){const q=make(),p=q.open();q.ack();await p;q.event(q.link(extra));assert.equal(q.nativeFeedbackAvailable(),false);}
 const q=make(),p=q.open();q.ack();await p;q.event();assert.equal(q.nativeFeedbackAvailable(),true);q.refresh();assert.equal(q.nativeFeedbackAvailable(),false);assert.equal(q.openedFeedbackFile().source.revision,1);q.forgetVolatile();assert.equal(q.nativeFeedbackAvailable(),false);
});
test('only the paired exact-source composer intercepts primary send; unrelated link uses existing linked question route',async()=>{
 const q=make();let previews=0;q.registerNativeFeedbackAction({active:true,pending:false,busy:false,scopeKey:JSON.stringify({projectId:'p',workspaceId:'w',conversationId:'one'}),leaseKey:JSON.stringify([JSON.stringify({projectId:'p',workspaceId:'w',conversationId:'one'}),0]),source:q.source(),submit:async()=>previews++});const p=q.open();q.event();await q.submitPrompt();assert.equal(previews,0);assert.equal(q.calls.at(-1)[0],'LINK_SEND');q.ack();await p;await q.submitPrompt();assert.equal(previews,1);assert.equal(q.prompt,'Keep the original intent');
});
test('explicit unlink clears the paired context and preserves the main draft',async()=>{
 const q=make(),p=q.open();q.ack();await p;q.event();await q.unlinkDiscussionFile();assert.equal(q.directFile(),undefined);assert.equal(q.openedFeedbackFile(),undefined);assert.equal(q.removed.length,1);assert.equal(q.prompt,'Keep the original intent');
});


test('v2 page capability selection with an unrelated link passes link only, never the hidden old file source',async()=>{const q=make();q.event();q.setReview({version:2,selection:{skills:[],mcp_tools:[]}});await q.submitPrompt();const send=q.calls.at(-1);assert.equal(send[0],'LINK_SEND');assert.equal(send[3],undefined);assert.deepEqual(send[4],q.link());assert.equal(q.openedFeedbackFile(),undefined);});


test('ordinary linked tool catalog excludes the retained source, and late explicit file authorization cannot enable writes under a link',async()=>{const q=make();q.setScope({projectId:'p',workspaceId:'w',conversationId:'one',providerProfileRef:{id:'profile',revision:1,sha256:'c'.repeat(64)}});q.event();await q.readPageReview(true);const catalog=q.calls.at(-1);assert.equal(catalog[0],'CATALOG');assert.deepEqual(catalog[1].scope.fileLinkRef,q.link());assert.equal(catalog[1].fileSourceRef,undefined);assert.equal(catalog[1].scope.fileSourceRef,undefined);assert.equal(catalog[1].scope.file_source_ref,undefined);const other=make(),pending=other.changeDirectWrite(true);other.event();await pending;assert.equal(other.writes,false);await other.changeDirectWrite(true);assert.equal(other.writes,false);});


test('unlink of ordinary B cannot revive hidden A, while in-flight unlink cannot clear later C or its new draft',async()=>{const q=make();q.event(q.link({id:'B'}));assert.equal(q.nativeFeedbackAvailable(),false);await q.unlinkDiscussionFile();assert.equal(q.directFile(),undefined);assert.equal(q.currentNativeFeedbackAction(),undefined);assert.equal(q.prompt,'Keep the original intent');const late=make();late.event(late.link({id:'B'}));late.holdClear();const clearing=late.unlinkDiscussionFile();late.newSelection(late.source(1,{path:'C.md'}));const next=late.directFile();late.setPrompt('New C draft');late.completeClear();await clearing;assert.equal(late.directFile(),next);assert.equal(late.prompt,'New C draft');assert.equal(late.removed.length,0);});


test('external removal of unpaired B cannot reactivate hidden A or leak A into ordinary/v2 requests; explicit new selection can recover',async()=>{const q=make();q.event(q.link({id:'B'}));q.clearEvent();assert.equal(q.nativeFeedbackAvailable(),false);await q.submitPrompt();assert.equal(q.calls.at(-1)[0],'LINK_SEND');assert.equal(q.calls.at(-1)[3],undefined);assert.equal(q.calls.at(-1)[4],undefined);q.setReview({version:2});await q.submitPrompt();assert.equal(q.calls.at(-1)[3],undefined);await q.changeDirectWrite(true);assert.equal(q.writes,false);q.newSelection(q.source(1,{path:'C.md'}));assert.equal(q.nativeFeedbackAvailable(),true);});


test('a late A ACK never clears a subsequent B fence, whether B still exists, was cleared, or is followed by a stale A event',async()=>{for(const order of ['B-still-selected','B-cleared-before-ACK','B-cleared-after-ACK','B-then-A-event']){const q=make(),p=q.open();q.event(q.link({id:'B'}));if(order==='B-cleared-before-ACK')q.clearEvent();if(order==='B-then-A-event')q.event();q.ack();await p;if(order!=='B-cleared-before-ACK')q.clearEvent();assert.equal(q.nativeFeedbackAvailable(),false,order);assert.equal(q.openedFeedbackFile(),undefined,order);assert.equal(q.prompt,'Keep the original intent',order);}const own=make(),p=own.open();own.event();own.event();own.ack();await p;assert.equal(own.nativeFeedbackAvailable(),true);});

test('actual source sync keeps user input verbatim while the next actual DiscoveryApi body carries exact typed material refs',async()=>{
 for(const input of ['', 'My own next question 😀\nDo not change this.']){
  const q=make(),source=q.source(),current=q.source(2),document={document_id:'new-document',revision:2,sha256:'d'.repeat(64)},calls=[];let mats=[],syncs=[],promptWrites=0;
  const api={file:async()=>({...q.file(2),source_ref:current}),materials:async(_p,_w,paths,_key,expected)=>{assert.deepEqual(paths,[source.path]);assert.deepEqual(expected,[current]);calls.push('IMPORT');return{materials:[{path:source.path,status:'SAVED',file_source_ref:current,document_ref:document}]};}};
  const discoveryApi=new DiscoveryApi({projectPath:pid=>'/api/projects/'+pid,request:async(path,method,body)=>{calls.push({path,method,body});return{status:'PROCESSING'};}});
  const context={api,discoveryApi,syncSelectedSource,discoveryInput,discoverySendGate,activeOperation:()=>undefined,followDiscoveryReading(){},discoveryWaitSeconds:()=>120,setDiscoveryError:()=>assert.fail('the indexed isolated request must not fail preflight'),navigation:{capture:()=>()=>true},project:()=>({id:'p'}),activeSession:()=>({id:'discussion'}),materials:()=>mats,sourceSyncs:()=>syncs,invalidFeedback:()=>({}),feedbackRecords:()=>[],setMaterials:f=>mats=f(mats),setSourceSyncs:f=>syncs=f(syncs),setInvalidFeedback(){},localStorage:{setItem(){}},setPrompt(){promptWrites++;},fullscreen:()=>false,setFullscreenAgent(){},setAgentHidden(){},setMobileView(){},setNotice(){},workspace:()=>({id:'w',revision:1,binding_sha256:'b'.repeat(64)}),operationId:()=> 'typed-source-review',observeDiscovery(){},providers:()=>[],providerId:()=>'',ApiError:class extends Error{}};
  const code=ts.transpileModule(appFunction('synchronizeSource')+'\n'+appFunction('advanceDiscovery'),{compilerOptions:{target:99,module:99}}).outputText;
  const actual=new Function(...Object.keys(context),code+'\nreturn{synchronizeSource,advanceDiscovery};')(...Object.values(context));await actual.synchronizeSource(source);await actual.advanceDiscovery({project_id:'p',id:'discussion',revision:1},input);
  assert.equal(promptWrites,0);assert.equal(syncs.length,1);assert.deepEqual(mats[0].file_source_ref,current);assert.deepEqual(mats[0].document_ref,document);const post=calls.at(-1);assert.equal(post.method,'POST');assert.equal(post.path,'/api/projects/p/discovery/sessions/discussion/advance');assert.equal(post.body.message,input);assert.deepEqual(post.body.materials,[document]);assert.equal(post.body.expected_revision,1);assert.equal(post.body.operation_id,'typed-source-review');
 }
});


test('actual current thread guard preserves the original draft and sends nothing while connection, creation or organization needs review',async()=>{
 for(const barrier of ['connection','creation','thread']){const q=make();q.setSendingBarrier(barrier);await q.submitPrompt();assert.equal(q.calls.length,0,barrier);assert.equal(q.prompt,'Keep the original intent');assert.ok(q.notices.length+q.errors.length>0,barrier);}
});

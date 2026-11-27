import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import ts from 'typescript';
import {historyDiscoverySource} from '../src/discovery-history-context.ts';
import {DiscussionDraftLease} from '../src/discussion-drafts.ts';
const id='discovery:'+ 'a'.repeat(64),ref={id:'work',revision:1,sha256:'b'.repeat(64)},source={kind:'discovery',project_id:'p',object_ref:ref,workspace_ref:null};
const row={id,kind:'discovery',projectId:'p',readOnly:false,source,title:'Requirements',organization:{revision:0,archived:false}};
const scope={version:1,conversation_ref:{id,revision:1},project_id:'p',archived:false,sources:[{id,identity:{kind:'discovery',project_id:'p',session_id:'work'},original_source:source}],next_message:{project_id:'p',conversation_id:'canonical-input'}};
const empty={text:'',attachments:[]};
test('one exact same-project discovery source can resume its existing workflow',()=>assert.equal(historyDiscoverySource(row,scope,empty),source));
test('a nonempty chat draft or attachment remains in its canonical conversation',()=>{for(const draft of [{text:'Unsent note',attachments:[]},{text:' ',attachments:[]},{text:'',attachments:[{}]}])assert.equal(historyDiscoverySource(row,scope,draft),undefined)});
test('moved, archived and mixed discussions do not gain the old execution context',()=>{for(const [r,s] of [[{...row,projectId:'other'},{...scope,project_id:'other'}],[{...row,readOnly:true},scope],[{...row,organization:{revision:1,archived:true}},scope],[row,{...scope,archived:true}],[row,{...scope,sources:[...scope.sources,{id:'native:'+'c'.repeat(64)}]}]])assert.equal(historyDiscoverySource(r,s,empty),undefined)});
test('aliases and source identity are checked, not just a project title',()=>{for(const sourceValue of [{...scope.sources[0],id:'discovery:'+'c'.repeat(64)},{...scope.sources[0],identity:{...scope.sources[0].identity,session_id:'other'}},{...scope.sources[0],original_source:{...source,object_ref:{...ref,id:'other'}}}])assert.equal(historyDiscoverySource(row,{...scope,sources:[sourceValue]},empty),undefined)});
const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8'),ast=ts.createSourceFile('App.tsx',app,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);let body;
function visit(node){if(ts.isFunctionDeclaration(node)&&node.name?.text==='openHistoryConversation')body=node.getText(ast);ts.forEachChild(node,visit)}visit(ast);assert(body);
const code=ts.transpileModule(body,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
function fixture({draft=empty,thread=scope,selected=row,late=false,brokenStorage=false,historyPage={messages:[]}}={}){
 const calls=[],storage=new Map([['eeas-conversation-draft-v1:'+id,JSON.stringify(draft)]]);let current=true;
 const context={busy:()=>false,creationClient:{pending:undefined},adoptCreatedConversation:async()=>{},historyItems:()=>[selected],sameReview:(a,b)=>JSON.stringify(a)===JSON.stringify(b),navigation:{begin:()=>({signal:new AbortController().signal,current:()=>current})},activityRequest:0,studioDisposed:false,nativeBridge:undefined,
  threadClient:{select:async()=>{calls.push('select');if(late)current=false;return thread},history:async()=>{calls.push('history');return historyPage}},installThread:async()=>{calls.push('canonical');return true;},openLegacyHistoryConversation:async()=>calls.push('discovery'),historyDiscoverySource,DiscussionDraftLease,localStorage:{getItem:k=>{if(brokenStorage)throw Error('quota');return storage.get(k)??null},setItem:()=>{throw Error('This navigation decision must not write drafts')}},uiLocale:()=> 'en',setError:()=>calls.push('error'),refreshHistory:()=>calls.push('refresh')};
 const api=new Function(...Object.keys(context),`${code};return openHistoryConversation;`)(...Object.values(context));return {run:()=>api(selected),calls,storage};
}
test('actual sidebar callback resumes an empty single-source discovery before entering Agent reader',async()=>{const f=fixture();await f.run();assert.deepEqual(f.calls,['select','history','discovery'])});
test('actual sidebar callback retains a nonempty canonical input and the complete history',async()=>{const draft={text:'Keep this unsent chat',attachments:[]},f=fixture({draft});await f.run();assert.deepEqual(f.calls,['select','history','canonical']);assert.equal(f.storage.get('eeas-conversation-draft-v1:'+id),JSON.stringify(draft))});
test('actual sidebar callback preserves mixed-source and moved conversations',async()=>{for(const input of [{thread:{...scope,sources:[...scope.sources,{}]}},{selected:{...row,projectId:'other',readOnly:true},thread:{...scope,project_id:'other'}}]){const f=fixture(input);await f.run();assert.deepEqual(f.calls,['select','history','canonical'])}});
test('late navigation and unreadable target drafts never install a different context',async()=>{const late=fixture({late:true});await late.run();assert.deepEqual(late.calls,['select']);const bad=fixture({brokenStorage:true});await bad.run();assert.deepEqual(bad.calls,['select','error','refresh'])});

const documentRow={role:'assistant',source:{alias_id:id,kind:'discovery',project_id:'p',session_id:'work'},object_ref:ref,discovery_review:{source_role:'DISCOVERY_DOCUMENT_REVISION_NOT_A_CHAT_MODEL_REPLY',claims:[],questions:[]}};
const {threadDiscoveryDocument}=await import('../src/discovery-history-context.ts');
test('a requirements record resolves only from the clicked alias and exact object identity',()=>{
 assert.deepEqual(threadDiscoveryDocument(scope,documentRow),{projectId:'p',objectRef:ref});
 for(const value of [{...documentRow,role:'user'},{...documentRow,source:{...documentRow.source,alias_id:'discovery:'+'c'.repeat(64)}},{...documentRow,source:{...documentRow.source,project_id:'other'}},{...documentRow,object_ref:{...ref,id:'other'}},{...documentRow,object_ref:{...ref,sha256:'bad'}}])assert.equal(threadDiscoveryDocument(scope,value),undefined);
});
let documentBody;
function documentVisit(node){if(ts.isFunctionDeclaration(node)&&node.name?.text==='openThreadDiscoveryDocument')documentBody=node.getText(ast);ts.forEachChild(node,documentVisit)}documentVisit(ast);assert(documentBody);
const documentCode=ts.transpileModule(documentBody,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
function documentFixture({thread=scope,late=false,resultChange={},input=documentRow,saveFailure=false}={}){
 const calls=[];let active=thread;
 const value={id:'work',project_id:'p',revision:1,current_revision:1,sha256:ref.sha256,source_current:true,payload:{workspace_ref:null,candidate:{summary:'Current requirements',claims:[{text:'Preserve originals'}],questions:[{text:'Frequency still unknown'}]}}};
 const context={threadScope:()=>active,threadDiscoveryDocument,navigation:{begin:()=>({current:()=>true,signal:new AbortController().signal})},activityRequest:0,studioDisposed:false,
  api:{projectPath:p=>'/api/projects/'+p,request:async(path,method)=>{calls.push({kind:'read',path,method});if(late)active={...thread,conversation_ref:{id:'discovery:'+'d'.repeat(64)}};return {...value,...resultChange};}},
  saveComposerDraft:()=>{calls.push({kind:'save-current'});if(saveFailure)throw Error('disk full')},setHistoryPreview:value=>calls.push({kind:'preview',value}),
  installDiscussionTarget:async(target,current,signal,presentation)=>{assert(current());calls.push({kind:'install',target,presentation});active=undefined;},showAgentChat:()=>calls.push({kind:'show-chat'}),presentWorkspace:mode=>calls.push({kind:'workspace',mode}),uiLocale:()=> 'en',setError:text=>calls.push({kind:'error',text})};
 const callback=new Function(...Object.keys(context),`${documentCode};return openThreadDiscoveryDocument;`)(...Object.values(context));return {run:()=>callback(input),calls};
}
test('actual document action reads its pinned version and opens the existing requirements input',async()=>{
 const f=documentFixture();await f.run();assert.deepEqual(f.calls.map(x=>x.kind),['read','install','show-chat','workspace']);assert(f.calls[0].path.endsWith('/work?revision=1'));assert.equal(f.calls[0].method,'GET');assert.equal(f.calls[1].presentation,'chat');assert.equal(f.calls[1].target.session.id,'work');
});
test('historical, moved, archived and changed-source records remain read-only and keep the current draft',async()=>{
 for(const options of [{resultChange:{current_revision:2}},{resultChange:{source_current:false}},{thread:{...scope,project_id:'other'}},{thread:{...scope,archived:true}}]){
  const f=documentFixture(options);await f.run();assert.deepEqual(f.calls.map(x=>x.kind),['read','save-current','preview']);assert(f.calls[2].value.answer.includes('Frequency still unknown'));assert(!f.calls.some(x=>x.kind==='install'));
 }
});
test('a late document response or a forged result never changes the current conversation',async()=>{
 const late=documentFixture({late:true});await late.run();assert.deepEqual(late.calls.map(x=>x.kind),['read']);
 const mismatch=documentFixture({resultChange:{sha256:'e'.repeat(64)}});await mismatch.run();assert.deepEqual(mismatch.calls.map(x=>x.kind),['read','error']);
 const other=documentFixture({input:{...documentRow,source:{...documentRow.source,alias_id:'native:'+'f'.repeat(64)}}});await other.run();assert.deepEqual(other.calls,[]);
});
test('a save failure when opening an older record preserves the active input and does not open another view',async()=>{
 const f=documentFixture({resultChange:{current_revision:2},saveFailure:true});await f.run();assert.deepEqual(f.calls.map(x=>x.kind),['read','save-current','error']);
});

const reserved={id:'native:'+'9'.repeat(64),identity:{kind:'native',project_id:'p',conversation_id:'canonical-input'},original_source:null};
const producerScope={...scope,sources:[...scope.sources,reserved]};
const producerPage={messages:[{...documentRow}],nextCursor:null,partial:false,conversationRef:scope.conversation_ref};
test('the real producer reserved empty next-message route is not a second active conversation',()=>{
 assert.equal(historyDiscoverySource(row,producerScope,empty,producerPage),source);
});
test('a reserved alias alone never proves an empty or complete native history',()=>{
 for(const page of [undefined,{...producerPage,partial:true},{...producerPage,nextCursor:'older'},{...producerPage,conversationRef:{id,revision:2}},{...producerPage,messages:[{source:{kind:'native',alias_id:reserved.id}}]}])assert.equal(historyDiscoverySource(row,producerScope,empty,page),undefined);
 for(const extra of [{...reserved,original_source:{}},{...reserved,identity:{...reserved.identity,project_id:'other'}},{...reserved,identity:{...reserved.identity,conversation_id:'different'}}])assert.equal(historyDiscoverySource(row,{...scope,sources:[...scope.sources,extra]},empty,producerPage),undefined);
});

test('actual sidebar callback accepts the real reserved-route producer after complete history inspection',async()=>{
 const f=fixture({thread:producerScope,historyPage:producerPage});await f.run();assert.deepEqual(f.calls,['select','history','discovery']);
 const mixed=fixture({thread:producerScope,historyPage:{...producerPage,messages:[{source:{kind:'native',alias_id:reserved.id}}]}});await mixed.run();assert.deepEqual(mixed.calls,['select','history','canonical']);
});

test('a frozen alias does not pin the selected requirements to the very first revision',()=>{
 const selected={...row,source:{...source,object_ref:{...ref,revision:2,sha256:'8'.repeat(64)}}};
 assert.equal(historyDiscoverySource(selected,producerScope,empty,producerPage),selected.source);
});
let savedBody;function savedVisit(node){if(ts.isFunctionDeclaration(node)&&node.name?.text==='openSavedThread')savedBody=node.getText(ast);ts.forEachChild(node,savedVisit)}savedVisit(ast);assert(savedBody);
const savedCode=ts.transpileModule(savedBody,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
function savedFixture({draft=empty,thread=producerScope,page=producerPage,selected=row,late=false}={}){
 const calls=[],storage=new Map([['eeas-conversation-draft-v1:'+id,JSON.stringify(draft)]]);let current=true;
 const context={historyItems:()=>[selected],sameReview:(a,b)=>JSON.stringify(a)===JSON.stringify(b),setHistoryItems:()=>assert.fail('the already loaded exact row must not need insertion'),threadClient:{scope:async()=>{calls.push('scope');if(late)current=false;return thread},history:async()=>{calls.push('history');return page}},savedThreadRow:()=>selected,DiscussionDraftLease,historyDiscoverySource,localStorage:{getItem:k=>storage.get(k)??null,setItem:()=>{throw Error('No navigation decision writes drafts')}},openLegacyHistoryConversation:async()=>calls.push('discovery'),installThread:async()=>{calls.push('canonical');return true;}};
 const callback=new Function(...Object.keys(context),`${savedCode};return openSavedThread;`)(...Object.values(context));return {run:(minimum=0)=>callback(id,()=>current,undefined,minimum),calls};
}
test('actual restart callback uses the same precise discovery route as the sidebar',async()=>{
 const f=savedFixture();await f.run();assert.deepEqual(f.calls,['scope','history','discovery']);
 const note=savedFixture({draft:{text:'Unsent canonical input',attachments:[]}});await note.run();assert.deepEqual(note.calls,['scope','history','canonical']);
});
test('restart still refuses old organization generations and late responses',async()=>{
 const f=savedFixture();await assert.rejects(()=>f.run(2),/ORGANIZATION_REFRESH_REQUIRED/);assert.deepEqual(f.calls,['scope']);
 const late=savedFixture({late:true});await late.run();assert.deepEqual(late.calls,['scope']);
});

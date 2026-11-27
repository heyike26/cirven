import test from 'node:test';
import assert from 'node:assert/strict';
import {parseHistoryPage,mergeHistoryItems,labelHistoryItems,saveConversationLabel,readConversationLabel} from '../src/conversation-history.ts';
const source={kind:'native',project_id:null,request_project_id:null,conversation_id:'chat',last_operation_id:'op',request_sha256:'a'.repeat(64),record_origin:'CURRENT_HOST',provider_profile_ref:null,workspace_ref:null};
const row=(options={})=>({id:'native:'+ 'b'.repeat(64),kind:'native',project_id:null,project_name:null,title:null,updated_at:'2026-10-19T06:00:00Z',read_only:false,source:{...source},...options});
const page=(items,options={})=>({version:1,execution_authorized:false,items,next_cursor:null,coverage:{model_calls:0,complete:true,partial:false,has_more:false,errors:[]},...options});
test('the actual kind-prefixed directory identity and exact source reach the sidebar unchanged',()=>{
 const raw=row(),parsed=parseHistoryPage(page([raw]),'zh');assert.equal(parsed.items[0].id,raw.id);assert.deepEqual(parsed.items[0].source,raw.source);assert.equal(parsed.items[0].title,'未命名对话');assert.equal(parsed.nextCursor,null);
 assert.equal(parsed.organizationSupported,false);assert.equal(parseHistoryPage(page([],{organization_supported:true}),'en').organizationSupported,true);assert.throws(()=>parseHistoryPage(page([],{organization_supported:'true'}),'en'),/UNAVAILABLE/);
 for(const changed of [{id:'original:'+'b'.repeat(64)},{source:{...source,project_id:'wrong'}},{source:{...source,request_project_id:'other'}}])assert.throws(()=>parseHistoryPage(page([row(changed)]),'en'),/UNAVAILABLE/);
});
test('partial sources remain distinct from more pages, and older duplicate pages cannot replace a newer source',()=>{
 const parsed=parseHistoryPage(page([row()],{next_cursor:'next',coverage:{model_calls:0,complete:false,partial:true,has_more:true,errors:[]}}),'en');assert.equal(parsed.partial,true);assert.equal(parsed.sourceUnavailable,false);
 const older=parseHistoryPage(page([row({updated_at:'2026-10-18T06:00:00Z',source:{...source,last_operation_id:'older'}})]),'en');
 assert.equal(mergeHistoryItems(parsed.items,older.items)[0].source.last_operation_id,'op');
 const failed=parseHistoryPage(page([],{coverage:{model_calls:0,complete:false,partial:true,has_more:false,errors:[{source:'native',code:'INVALID',count:1}]}}),'zh');assert.equal(failed.sourceUnavailable,true);
 assert.throws(()=>mergeHistoryItems(parsed.items,[{...parsed.items[0],id:'another'}],1),/CAPACITY/);
});
test('user-authored chat labels are project-scoped, bounded and never replaced by task output',()=>{
 const data=new Map(),storage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value)};
 saveConversationLabel(storage,null,'chat','真实用户要求\n后续句子');saveConversationLabel(storage,null,'chat','工具内部消息');
 assert.equal(readConversationLabel(storage,null,'chat'),'真实用户要求');assert.equal(readConversationLabel(storage,'project','chat'),undefined);
 saveConversationLabel(storage,'project','chat','🧩'.repeat(200));assert.equal(Array.from(readConversationLabel(storage,'project','chat')).length,100);
});
test('sidebar and management use the same authored label without changing source or organization basis',()=>{
 const data=new Map(),storage={getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value)};saveConversationLabel(storage,null,'chat','项目交付讨论');const original=parseHistoryPage(page([row({organization:{revision:4,archived:true}})]),'en').items[0],named=labelHistoryItems([original],storage)[0];assert.equal(named.title,'项目交付讨论');assert.equal(named.source,original.source);assert.equal(named.organization,original.organization);assert.equal(original.title,'Untitled chat');assert.equal(labelHistoryItems([original],{getItem:()=>{throw Error('unavailable');}})[0],original);
 for(const organization of [{revision:-1,archived:false},{revision:true,archived:false},{revision:0,archived:'false'}])assert.throws(()=>parseHistoryPage(page([row({organization})]),'en'),/UNAVAILABLE/);
});

test('stable organization identity may differ from its exact execution source only with a matching canonical revision',()=>{
 const moved=row({project_id:'target_project',project_name:'目标项目',read_only:true,organization:{revision:2,archived:false},conversation_ref:{id:'native:'+'b'.repeat(64),revision:2}});
 const parsed=parseHistoryPage(page([moved]),'en').items[0];assert.equal(parsed.projectId,'target_project');assert.equal(parsed.source.project_id,null);assert.deepEqual(parsed.conversationRef,moved.conversation_ref);
 for(const corrupt of [{...moved,conversation_ref:{...moved.conversation_ref,id:'original:'+'c'.repeat(64)}},{...moved,conversation_ref:{...moved.conversation_ref,revision:1}},{...moved,conversation_ref:undefined}])assert.throws(()=>parseHistoryPage(page([corrupt]),'en'));
 const next={...moved,id:'original:'+'d'.repeat(64),conversation_ref:{id:'original:'+'d'.repeat(64),revision:2}};assert.equal(parseHistoryPage(page([next]),'en').items[0].kind,'native','an old original-kind stable identity can bind a later native turn without relabeling its actual source kind');
});

test('moving a conversation preserves its original authored title even after a target-project alias receives a new question',()=>{
 const data=new Map(),storage={getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v)};saveConversationLabel(storage,'source_project','original_alias','原来的准确标题');saveConversationLabel(storage,'target_project','new_alias','新的提问不是重命名');
 const raw=row({project_id:'target_project',organization:{revision:2,archived:false},conversation_ref:{id:'native:'+'b'.repeat(64),revision:2},conversation_origin:{kind:'native',project_id:'source_project',conversation_id:'original_alias'},source:{...source,project_id:'target_project',request_project_id:'target_project',conversation_id:'new_alias'}});
 const item=parseHistoryPage(page([raw]),'en').items[0];assert.equal(labelHistoryItems([item],storage)[0].title,'原来的准确标题');assert.equal(item.source.conversation_id,'new_alias');
 assert.throws(()=>parseHistoryPage(page([{...raw,conversation_ref:undefined,conversation_origin:{kind:'native',project_id:'source_project',conversation_id:'original_alias'}}]),'en'));
});

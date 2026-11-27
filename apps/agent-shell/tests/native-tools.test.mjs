import assert from 'node:assert/strict';
import test from 'node:test';
import {NativeToolsClient,assertNativeToolsCatalog,chooseMCPTools,nativeToolsBridge} from '../src/native-tools.ts';
import {AgentCapabilityClient,isCapabilityRejected} from '../src/native-capabilities.ts';
import {AgentSessionsApi} from '../src/agent-sessions.ts';

const ref={id:'review-local',revision:1,sha256:'a'.repeat(64)},probe={id:'b'.repeat(32),sha256:'b'.repeat(64)},binding='c'.repeat(64);
const config={type:'local',command:['D:/original.exe'],files_sha256:{'D:/original.exe':'d'.repeat(64)},enabled:true,timeout:5000};
const row={id:'review-local_read_one',name:'read.one',input_schema_sha256:'e'.repeat(64),selectable:true};
const scope={project_id:'project',session_ref:{id:'f'.repeat(32),revision:1,sha256:'f'.repeat(64)},generation:1};
function catalog(){return {schema:'eeas.native-tools.v1',capability_binding_sha256:binding,configuration_refs:{skills:{},mcp:{[ref.id]:ref}},owner:{scope:null,status:'NOT_SELECTED'},builtin_tools:[],mcp_servers:[{id:ref.id,config_ref:ref,enabled:true,availability:'AVAILABLE',probe_ref:probe,tools:[row],source:'PRIVATE_MCP_PROBE_NOT_ORIGINAL_OWNER'}],read_only:true,model_calls:0,effects_applied:false};}
function saved(revision=2){return {config_ref:{...ref,revision,sha256:'1'.repeat(64)},saved:true,loaded_in_original_session:false,qualified:false};}

test('actual client validates exact owner scope and never substitutes a catalogue for another generation',async()=>{
 const observed={...catalog(),owner:{scope,status:'CURRENT'},builtin_tools:[{id:'read',description:'Read',permission:'ask',input_schema_sha256:'a'.repeat(64),selectable_for_new_session:false,source:'ORIGINAL_OWNER_REGISTRY'}]};
 let calls=0;const bridge={version:2,getAgentTools:async value=>{calls++;assert.deepEqual(value,scope);return observed;}};
 assert.equal(nativeToolsBridge({eeasDesktop:bridge}),bridge);
 assert.equal(await new NativeToolsClient(bridge).get(scope),observed);
 assert.throws(()=>assertNativeToolsCatalog(observed,{...scope,generation:2}),/回执不完整/);
 assert.throws(()=>assertNativeToolsCatalog({...observed,owner:{scope,status:'STALE'}} ,scope),/回执不完整/);
 assert.equal(calls,1);
});
test('choosing real discovered tools produces exact immutable initial values without starting or granting anything',()=>{
 const source=catalog(),choice=chooseMCPTools(source,[row.id]);
 assert.deepEqual(choice,{capability_binding_sha256:binding,items:[{server_id:ref.id,config_ref:ref,probe_ref:probe,tool_id:row.id,name:row.name,input_schema_sha256:row.input_schema_sha256}]});
 choice.items[0].config_ref.revision=99;assert.equal(source.mcp_servers[0].config_ref.revision,1);
 assert.throws(()=>chooseMCPTools(source,[row.id,row.id]),/所选工具/);
 assert.throws(()=>chooseMCPTools({...source,mcp_servers:[{...source.mcp_servers[0],availability:'STALE',tools:[]}]},[row.id]),/所选工具/);
 assert.throws(()=>assertNativeToolsCatalog({...source,mcp_servers:[{...source.mcp_servers[0],config_ref:{...ref,sha256:'0'.repeat(64)}}]}),/回执不完整/);
});
test('explicit new conversation forwards exact selection with ask allowlist and does not silently add effects',async()=>{
 const calls=[],choice=chooseMCPTools(catalog(),[row.id]);
 const client={request:async(path,method,body)=>{calls.push({path,method,body});if(!method)return {capability_binding_sha256:binding,configuration_refs:{skills:{},mcp:{}},permission_granted:false,loaded_in_original_session:false};return {project_id:'project',session_ref:{id:'2'.repeat(32),revision:1,sha256:'2'.repeat(64)},owner_status:'READY',generation:1,connected_owner:true,configuration_current:true,native_status:'idle',messages:[],todo:[],children:[],pending_questions:[],pending_permissions:[],last_event_seq:0,automatic_post_replay:false};}};
 const service=new AgentSessionsApi(client);
 await service.open({projectId:'project',provider:ref,mcpToolAllowlist:[row.id],mcpToolSelection:choice},'explicit-open');
 assert.equal(calls.length,2);assert.deepEqual(calls[1].body.mcp_tool_selection,choice.items);assert.deepEqual(calls[1].body.allowed_effects,[]);
 assert.deepEqual(calls[1].body.mcp_tool_allowlist,[row.id]);assert.equal(calls[1].body.tools,undefined);
 calls.length=0;await assert.rejects(()=>service.open({projectId:'project',provider:ref,mcpToolAllowlist:[],mcpToolSelection:choice}),/所选工具/);assert.equal(calls.length,1);
});
test('controlled MCP read preserves full configuration but refuses a different saved source',async()=>{
 const client=new AgentCapabilityClient({readAgentMCP:async()=>({config_ref:ref,configuration:config,current_configuration:true,integrity:'CURRENT'})});
 assert.deepEqual((await client.mcp(ref)).configuration,config);
 await assert.rejects(()=>client.mcp({...ref,revision:2}),/所选工具/);
});
test('unknown configure resolves only its saved original input, not matching latest revision alone',async()=>{
 let proof={operation_id:'save-once',input_sha256:'a'.repeat(64),receipt:saved(),kind:'mcp',configuration:structuredClone(config)};
 const client=new AgentCapabilityClient({configureAgentMCP:async()=>{throw Error('transport unknown');},inspectAgentCapabilityOperation:async()=>proof});
 await assert.rejects(()=>client.configure(ref.id,config,ref,'save-once'),/transport/);
 proof={...proof,configuration:{...config,timeout:6000}};await assert.rejects(()=>client.operation('save-once'),/回执未绑定/);
 proof={...proof,configuration:structuredClone(config)};assert.deepEqual((await client.operation('save-once')).receipt,saved());
 await assert.rejects(()=>client.operation('save-once'),/回执未绑定/);
});
test('unknown enabled change requires exact old snapshot and no other configuration modification',async()=>{
 let proof={operation_id:'disable-once',input_sha256:'a'.repeat(64),receipt:saved(),kind:'mcp',configuration:{...config,enabled:false},previous_config_ref:ref,previous_configuration:config};
 const client=new AgentCapabilityClient({setAgentMCPEnabled:async()=>{throw Error('unknown');},inspectAgentCapabilityOperation:async()=>proof});
 await assert.rejects(()=>client.enabled('mcp',ref,false,'disable-once'),/unknown/);
 proof={...proof,configuration:{...proof.configuration,timeout:6000}};await assert.rejects(()=>client.operation('disable-once'),/回执未绑定/);
 proof={...proof,configuration:{...config,enabled:false},previous_config_ref:{...ref,sha256:'0'.repeat(64)}};await assert.rejects(()=>client.operation('disable-once'),/回执未绑定/);
 proof={...proof,previous_config_ref:ref};assert.equal((await client.operation('disable-once')).receipt.saved,true);
});
test('unconfirmed saves are bounded and cannot silently evict unresolved intents',async()=>{
 let writes=0;const client=new AgentCapabilityClient({configureAgentMCP:async()=>{writes++;throw Error('unknown');}});
 for(let i=0;i<16;i++)await assert.rejects(()=>client.configure(ref.id,config,ref,'save-'+i),/unknown/);
 await assert.rejects(()=>client.configure(ref.id,config,ref,'save-overflow'),/核查未确认/);assert.equal(writes,16);
});
test('only formal host rejection or local pre-dispatch checks release a failed save',async()=>{
 let writes=0;const client=new AgentCapabilityClient({configureAgentMCP:async()=>{writes++;return {schema:'eeas.capability-rejection.v1',confirmed_rejected:true,status:409,code:'HTTP_REJECTED',message:'Exact version conflict'};}});
 await assert.rejects(()=>client.configure('../invalid',config,undefined,'local-invalid'),error=>isCapabilityRejected(error)&&error.code==='PRE_DISPATCH');assert.equal(writes,0);
 for(let index=0;index<17;index++)await assert.rejects(()=>client.configure(ref.id,config,ref,'known-'+index),error=>isCapabilityRejected(error)&&error.code==='HTTP_REJECTED'&&error.status===409);
 assert.equal(writes,17);
 const unknown=new AgentCapabilityClient({configureAgentMCP:async()=>{throw Error('HTTP 409 in an untrusted message');}});
 await assert.rejects(()=>unknown.configure(ref.id,config,ref,'message-only'),error=>!isCapabilityRejected(error));
});

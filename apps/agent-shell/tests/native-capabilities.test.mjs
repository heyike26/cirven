import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import readline from 'node:readline';
import test from 'node:test';
import { AgentCapabilityClient, MCP_TIMEOUT_DEFAULT, MCP_TIMEOUT_MIN, MCP_TIMEOUT_MAX, agentCapabilityBridge, assertAgentCapabilities, capabilityStatus, mcpConfiguration, planningToolNames, probeStatus, skillText } from '../src/native-capabilities.ts';
import { nativeObservationScope } from '../src/native-collaboration.ts';

const here=dirname(fileURLToPath(import.meta.url)),root=resolve(here,'../../..'),ref={id:'page-review',revision:1,sha256:'a'.repeat(64)};
const skill='---\r\nname: page-review\r\ndescription: Review the exact selected page.\r\n---\r\n保留人工内容 😀\r\n';
const record={id:ref.id,title:'page-review',description:'Review',config_ref:ref,enabled:false,integrity:'CURRENT',loaded_in_original_session:false,qualified:false,native_binding:'NOT_ADAPTED'};
const state={schema:'eeas.native-capabilities.v1',skills:[record],mcp:[]};
function bridge(changes={}) {return {version:2,getAgentCapabilities:async()=>state,readAgentSkill:async()=>({config_ref:ref,content:skill,enabled:false,source:'ORIGINAL_SKILL_MD'}),importAgentSkill:async()=>({config_ref:ref,saved:true,loaded_in_original_session:false,qualified:false}),setAgentSkillEnabled:async()=>{},configureAgentMCP:async()=>{},setAgentMCPEnabled:async()=>{},probeAgentMCP:async()=>{},...changes};}
function fixture(t) {
  const child=spawn(resolve(root,'.venv/bin/python'),[resolve(here,'capability-http-fixture.py')],{cwd:root,stdio:['pipe','pipe','pipe']});
  const waiting=[];let stderr='';child.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-4000);});
  const lines=readline.createInterface({input:child.stdout});lines.on('line',line=>{const next=waiting.shift();if(!next)return;try{next.resolve(JSON.parse(line));}catch(error){next.reject(error);}});
  child.on('error',error=>{for(const next of waiting.splice(0))next.reject(error);});child.on('exit',code=>{for(const next of waiting.splice(0))next.reject(new Error(`API fixture exited ${code}: ${stderr}`));});
  t.after(async()=>{child.stdin.end();await new Promise(resolve=>child.exitCode!==null?resolve():child.once('exit',resolve));lines.close();});
  function raw(command) {return new Promise((resolve,reject)=>{waiting.push({resolve,reject});child.stdin.write(JSON.stringify(command)+'\n');});}
  async function request(method,path,body) {const result=await raw({method,path:'/api/desktop-host/capabilities'+path,body});if(result.status>=400)throw new Error(`HTTP ${result.status}: ${JSON.stringify(result.body)}`);return result.body;}
  return {raw,request,client:new AgentCapabilityClient(bridge({getAgentCapabilities:()=>request('GET','/get'),readAgentSkill:id=>request('GET',`/skills/${id}`),importAgentSkill:body=>request('POST','/skills/import',body),setAgentSkillEnabled:(id,body)=>request('POST',`/skills/${id}/enabled`,body),configureAgentMCP:body=>request('POST','/mcp/configure',body),setAgentMCPEnabled:(id,body)=>request('POST',`/mcp/${id}/enabled`,body),probeAgentMCP:(id,body)=>request('POST',`/mcp/${id}/probe`,body)}))};
}

test('native manager requires all seven private preload v2 methods and validates records instead of inventing an empty catalogue',()=>{
  assert.ok(agentCapabilityBridge({eeasDesktop:bridge()}));assert.equal(agentCapabilityBridge({eeasDesktop:bridge({version:1})}),undefined);assert.equal(agentCapabilityBridge({eeasDesktop:bridge({probeAgentMCP:undefined})}),undefined);
  assert.equal(assertAgentCapabilities(state),state);assert.throws(()=>assertAgentCapabilities({...state,skills:['static-tool']}),/回执不完整/);assert.throws(()=>assertAgentCapabilities({...state,skills:[record,{...record}]}),/重复身份/);
});
test('real private API preserves original Skill bytes, CAS revisions and restart; saving never grants original-session loading',async t=>{
  const f=fixture(t),saved=await f.client.importSkill('page-review',skill,false,undefined,'import-original');assert.equal(saved.saved,true);assert.equal(saved.loaded_in_original_session,false);assert.equal(saved.qualified,false);
  assert.equal((await f.client.skill(saved.config_ref)).content,skill);const enabled=await f.client.enabled('skills',saved.config_ref,true,'enable-original');assert.equal(enabled.config_ref.revision,2);
  await assert.rejects(()=>f.client.enabled('skills',saved.config_ref,false,'stale-enable'),/HTTP 409/);
  await f.raw({fixture:'restart'});const item=(await f.client.get()).skills[0];assert.equal(item.enabled,true);assert.deepEqual(item.config_ref,enabled.config_ref);assert.match(capabilityStatus(item),/声明已启用.*尚未加载原会话.*资格未核实/);
  assert.equal((await f.client.skill(enabled.config_ref)).content,skill);await assert.rejects(()=>f.client.skill(saved.config_ref),/Skill 已变更/);
});
test('real private API rejects absent host, mismatched YAML name, duplicate metadata and unknown fields without saving records',async t=>{
  const f=fixture(t);const denied=await f.raw({method:'GET',path:'/api/desktop-host/capabilities/get',host:false});assert.equal(denied.status,409);
  await assert.rejects(()=>f.client.importSkill('another-name',skill,false,undefined,'wrong-name'),/HTTP 422/);
  await assert.rejects(()=>f.client.importSkill('page-review',skill.replace('description:','name: page-review\r\ndescription:'),false,undefined,'duplicate-meta'),/HTTP 422/);
  const extra=await f.raw({method:'POST',path:'/api/desktop-host/capabilities/skills/import',body:{operation_id:'extra',id:'page-review',content:skill,unexpected_authority:true}});assert.equal(extra.status,422);assert.deepEqual((await f.client.get()).skills,[]);
});
test('real MCP configuration and official stdio initialize/list_tools produce scoped receipts, with no tool invocation or model qualification',async t=>{
  const f=fixture(t),configuration=(await f.raw({fixture:'local-mcp'})).body;
  const saved=await f.client.configure('original-local',configuration,undefined,'save-local');let item=(await f.client.get()).mcp[0];assert.equal(item.probe_permitted,true);assert.deepEqual(item.config_ref,saved.config_ref);
  const receipt=await f.client.probe(item);assert.equal(receipt.status,'INITIALIZED_AND_LISTED');assert.equal(receipt.initialized,true);assert.equal(receipt.tools_invoked,0);assert.equal(receipt.loaded_in_original_session,false);assert.equal(receipt.qualified,false);assert.equal(receipt.cleanup,'OWNED_STDIO_CONTEXT_EXITED');assert.ok(receipt.tools.some(tool=>tool.name==='exact_read'&&tool.invoked===false));
  assert.match(probeStatus(receipt,item.config_ref),/已初始化并读取 1 个工具.*尚未加载/);
  await assert.rejects(()=>f.request('POST','/mcp/original-local/probe',{config_ref:item.config_ref,allowed_effects:[],network_mode:'AS_CONFIGURED'}),/HTTP 409/);
  await assert.rejects(()=>f.request('POST','/mcp/original-local/probe',{config_ref:item.config_ref,allowed_effects:['PROCESS_START'],network_mode:'STRICT_OFFLINE'}),/HTTP 409/);
  await f.client.enabled('mcp',item.config_ref,false,'disable-local');item=(await f.client.get()).mcp[0];assert.equal(item.last_probe.current_configuration,false);assert.match(probeStatus(item.last_probe,item.config_ref),/旧配置/);await assert.rejects(()=>f.client.probe(item),/不可连接探测/);
});
test('remote declaration and actual failing local process are not labelled connected or loaded',async t=>{
  const f=fixture(t);await f.client.configure('remote',{type:'remote',url:'https://server.example/mcp',enabled:true,timeout:2000},undefined,'save-remote');let item=(await f.client.get()).mcp[0];assert.equal(item.probe_permitted,false);await assert.rejects(()=>f.client.probe(item),/不可连接探测/);
  await assert.rejects(()=>f.request('POST','/mcp/remote/probe',{config_ref:item.config_ref,allowed_effects:['PROCESS_START']}),/HTTP 409/);
  const configuration=(await f.raw({fixture:'local-mcp',broken:true})).body;await f.client.configure('broken-local',configuration,undefined,'save-broken');item=(await f.client.get()).mcp.find(item=>item.id==='broken-local');const failed=await f.client.probe(item);assert.equal(failed.status,'FAILED');assert.equal(failed.initialized,false);assert.equal(failed.list_tools_succeeded,false);assert.equal(failed.loaded_in_original_session,false);assert.match(probeStatus(failed,item.config_ref),/连接探测失败/);
});
test('unknown mutation outcomes and unrelated receipts never automatically retry or promote the edited baseline',async()=>{
  let calls=0;const client=new AgentCapabilityClient(bridge({importAgentSkill:async()=>{calls++;throw new Error('IPC disconnected after save');}}));await assert.rejects(()=>client.importSkill('page-review',skill,false,undefined,'once'),/IPC disconnected/);assert.equal(calls,1);
  const wrong=new AgentCapabilityClient(bridge({setAgentSkillEnabled:async()=>({config_ref:{...ref,id:'other',revision:2},saved:true,loaded_in_original_session:false,qualified:false})}));await assert.rejects(()=>wrong.enabled('skills',ref,true,'identity'),/结果未知/);assert.equal(ref.revision,1);
  await assert.rejects(()=>client.importSkill('page-review',skill,false,{...ref,id:'other'}),/身份不一致/);
});
test('probe sends exact config and explicit process-start admission once, and rejects mismatched protocol receipts',async()=>{
  const calls=[],item={...record,id:'local',config_ref:{...ref,id:'local'},type:'local',timeout:1000,enabled:true,probe_permitted:true};
  const client=new AgentCapabilityClient(bridge({probeAgentMCP:async(id,body)=>{calls.push({id,body});return{config_ref:{...body.config_ref,revision:2},status:'INITIALIZED_AND_LISTED',initialized:true,list_tools_succeeded:true,loaded_in_original_session:false,qualified:false,tools_invoked:0};}}));
  await assert.rejects(()=>client.probe(item),/结果未知/);assert.equal(calls.length,1);assert.deepEqual(calls[0],{id:'local',body:{config_ref:item.config_ref,allowed_effects:['PROCESS_START'],network_mode:'AS_CONFIGURED'}});
});
test('explicit selected Skill UTF8 bytes retain BOM and CRLF; invalid and oversized text are rejected before import',()=>{
  const bytes=new TextEncoder().encode('\ufeff'+skill);assert.equal(skillText(bytes,'SKILL.md'),'\ufeff'+skill);assert.throws(()=>skillText(new Uint8Array([0xc0,0x80]),'SKILL.md'),/有效 UTF-8/);assert.throws(()=>skillText(bytes,'other.md'),/原始 SKILL.md/);assert.throws(()=>skillText(new Uint8Array(65537),'SKILL.md'),/65536/);
});
test('MCP form produces bounded exact configurations and preserves validation errors without inventing a command',()=>{
  const input={type:'local',command:'["/actual/python","/actual/server.py"]',files:JSON.stringify({'/actual/python':'b'.repeat(64),'/actual/server.py':'c'.repeat(64)}),enabled:false,timeout:5000,url:''};assert.deepEqual(mcpConfiguration(input),{type:'local',command:['/actual/python','/actual/server.py'],files_sha256:JSON.parse(input.files),enabled:false,timeout:5000});assert.throws(()=>mcpConfiguration({...input,files:'not json'}),/编辑内容已保留/);assert.throws(()=>mcpConfiguration({...input,command:'[]'}),/实际命令/);assert.throws(()=>mcpConfiguration({...input,timeout:NaN}),/100–120000/);assert.deepEqual(mcpConfiguration({...input,type:'remote',url:' https://example/mcp '}),{type:'remote',url:'https://example/mcp',enabled:false,timeout:5000});
});
test('MCP timeout form accepts the 120000 ms boundary and cold default, but rejects bool, zero and overlimit before IPC',()=>{
  assert.equal(MCP_TIMEOUT_DEFAULT,60000);assert.equal(MCP_TIMEOUT_MIN,100);assert.equal(MCP_TIMEOUT_MAX,120000);
  const input={type:'local',command:'["/actual/python","/actual/server.py"]',files:JSON.stringify({'/actual/python':'b'.repeat(64),'/actual/server.py':'c'.repeat(64)}),enabled:false,timeout:MCP_TIMEOUT_DEFAULT,url:''};
  for(const timeout of [MCP_TIMEOUT_MIN,MCP_TIMEOUT_DEFAULT,MCP_TIMEOUT_MAX]){const result=mcpConfiguration({...input,timeout});assert.equal(result.timeout,timeout);assert.equal(result.enabled,false);assert.equal(Object.hasOwn(result,'loaded_in_original_session'),false);}
  for(const timeout of [120001,0,true,false,99,100.5,'60000',Infinity,NaN])assert.throws(()=>mcpConfiguration({...input,timeout}),/100–120000/);
});
test('actual private API saves a 120000 ms MCP configuration without a load claim and rejects invalid deadline types/bounds',async t=>{
  const f=fixture(t),configuration=(await f.raw({fixture:'local-mcp'})).body;
  const saved=await f.client.configure('cold-deadline',{...configuration,timeout:120000},undefined,'deadline-boundary');assert.equal(saved.saved,true);assert.equal(saved.loaded_in_original_session,false);assert.equal(saved.qualified,false);
  const item=(await f.client.get()).mcp.find(item=>item.id==='cold-deadline');assert.equal(item.timeout,120000);assert.equal(item.last_probe,null);
  let counter=0;for(const timeout of [120001,0,true,false]){const reply=await f.raw({method:'POST',path:'/api/desktop-host/capabilities/mcp/configure',body:{operation_id:'invalid-deadline-'+counter++,id:'rejected-deadline',expected_revision:0,configuration:{...configuration,timeout}}});assert.equal(reply.status,422,JSON.stringify(reply));}
  const items=(await f.client.get()).mcp;assert.equal(items.length,1);assert.equal(items[0].id,'cold-deadline');assert.deepEqual(items[0].config_ref,saved.config_ref);
});
test('static planning tools and partial current snapshots never imply installed skills, SDK qualification or input authority',()=>{
  assert.deepEqual(planningToolNames(['source-review',{title:'真实工具'},null,{id:5}]),['source-review','真实工具']);const label=nativeObservationScope({mode:'companion',context_status:{configured:true,status:'CONFIGURED'}},{operation_id:'context',current:true,observation:{status:'PARTIAL',context_ref:ref,coverage:{metadata:true,accessibility:false,visual:false}}});assert.match(label,/仅部分观察.*只读观察，原生输入未启用/);assert.ok(!label.includes('SDK 已就绪'));
});

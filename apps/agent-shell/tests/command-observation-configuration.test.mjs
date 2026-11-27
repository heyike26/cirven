// AUTHORED_NOT_RUN: typed journal/builders, explicit storage/lock/API UNIT boundaries.
import test from 'node:test';
import assert from 'node:assert/strict';
import {ConfigurationDraftJournal,commandObservationPaths,configurationDefaults,configurationOptions,configurationChanged,validConfigurationSettings,flushConfigurationDrafts} from '../src/connection-configuration-draft.ts';
import {configurationHash as hash} from '../src/conversation-configuration.ts';
import {AgentSessionsApi} from '../src/agent-sessions.ts';
const id='native:'+'a'.repeat(64),scope={project_id:'project',conversation_ref:{id,revision:1}},profile={id:'profile',revision:1,sha256:'b'.repeat(64)};
async function scene(){const workspace={project_id:'project',workspace_ref:{id:'c'.repeat(64),revision:1,binding_sha256:'d'.repeat(64)},label:'Readonly project',allowed_write_paths:[]},authority={version:1,...scope,original_source:null,original_request_sha256:null,source_authority:null,capability_binding_sha256:'e'.repeat(64),workspace_catalog_sha256:'f'.repeat(64),tools_catalog_sha256:'0'.repeat(64)},p={kind:'CONVERSATION_CONFIGURATION_PREPARATION',authority,preparation_sha256:await hash(authority),original_request:null,workspaces:[workspace],mcp_servers:[],configuration_refs:{skills:{},mcp:{}},default_execution_project_id:'project',new_owner_capacity:8,original_configuration_current:false};
 const d={scope:structuredClone(scope),preparation_sha256:p.preparation_sha256,settings:configurationDefaults(p)};d.settings.workspace={project_id:'project',workspace_ref:workspace.workspace_ref};d.settings.commands_enabled=true;d.settings.commands_text='python check.py';return {p,d,workspace};}
function storage(){const values=new Map();let reject=false;return {store:{getItem:key=>values.get(key)??null,setItem:(key,value)=>{if(reject)throw Error('UNIT quota');values.set(key,value);}},lock:{request:async(_key,_options,run)=>run()},fail:value=>{reject=value;}};}
test('explicit observation parser distinguishes absent, empty and exact Unicode file names',()=>{
 assert.equal(commandObservationPaths(undefined),undefined);assert.deepEqual(commandObservationPaths(''),[]);assert.deepEqual(commandObservationPaths('reports/测试.xml\r\nspace name.py\ntrailing \n'),['reports/测试.xml','space name.py','trailing ']);
});
test('invalid observation paths cannot become roots, globs or private file reads',()=>{
 for(const value of ['../x','/x','C:/x','back\\x','a//b','a/./b','a/','*.xml','a?','a[b]','.GIT/config','.eeas-runtime/file','.eeas-write-stage/file','a\0b','a\rb','x'.repeat(1025),'same\nsame',Array.from({length:33},(_,i)=>'file'+i).join('\n')])assert.throws(()=>commandObservationPaths(value),/INVALID_OBSERVATION_PATHS/);
});
test('explicit reports need exact commands and a workspace while leaving all write grants empty',async()=>{
 const {p,d,workspace}=await scene();d.settings.observation_paths_text='reports/tests.xml';const options=configurationOptions(p,d,profile);assert.deepEqual(options.commandObservationPaths,['reports/tests.xml']);assert.equal(options.workspaceWrite,false);assert.deepEqual(workspace.allowed_write_paths,[]);
 for(const change of [{workspace:null},{commands_enabled:false},{commands_text:''}])assert.throws(()=>configurationOptions(p,{...d,settings:{...d.settings,...change}},profile));
});
test('legacy settings remain valid and explicit empty survives defaults and change detection',async()=>{
 const {p,d}=await scene();assert(validConfigurationSettings(d.settings));assert.equal(Object.hasOwn(d.settings,'observation_paths_text'),false);d.settings.observation_paths_text='';const opts=configurationOptions(p,d,profile);assert.deepEqual(opts.commandObservationPaths,[]);
 p.original_request={provider_profile_ref:profile,workspace_ref:p.workspaces[0].workspace_ref,allowed_effects:['BACKGROUND_COMMAND'],command_allowlist:['python check.py'],command_observation_paths:[],mcp_tool_allowlist:[],mcp_tool_selection:[],workflow_ref:null,workflow_provider_refs:[],ttl_seconds:600,max_calls:24,max_output_tokens:4096};p.original_configuration_current=true;
 const settings=configurationDefaults(p);assert.equal(settings.observation_paths_text,'');assert.equal(configurationChanged(p,settings,profile),false);settings.observation_enabled=false;assert.equal(configurationChanged(p,settings,profile),true);assert.equal(settings.observation_paths_text,'');
});
test('prepared open packet retains explicit empty and omission without inventing write authority',async()=>{
 const {p,d}=await scene();
 const api=new AgentSessionsApi({request:async()=>({capability_binding_sha256:p.authority.capability_binding_sha256,configuration_refs:{skills:{},mcp:{}},permission_granted:false,loaded_in_original_session:false})});
 const legacy=await api.prepareOpen(configurationOptions(p,d,profile),'legacy');assert.equal(Object.hasOwn(legacy,'command_observation_paths'),false);
 d.settings.observation_paths_text='';const empty=await api.prepareOpen(configurationOptions(p,d,profile),'empty');assert.deepEqual(empty.command_observation_paths,[]);assert.equal(empty.allowed_effects.includes('WORKSPACE_WRITE'),false);
});
test('unfinished observation text survives persisted draft restart without entering execution',async()=>{
 const {p,d}=await scene(),m=storage(),j=new ConfigurationDraftJournal(m.store,id,m.lock);d.settings.observation_paths_text='reports/unfinished/';j.edit(d);await j.flush();const restored=new ConfigurationDraftJournal(m.store,id,m.lock).value();assert.equal(restored.settings.observation_paths_text,d.settings.observation_paths_text);assert.throws(()=>configurationOptions(p,restored,profile),/INVALID_OBSERVATION_PATHS/);restored.settings.observation_enabled=false;j.edit(restored);await j.flush();const disabled=new ConfigurationDraftJournal(m.store,id,m.lock).value();assert.equal(disabled.settings.observation_paths_text,'reports/unfinished/');assert.equal(configurationOptions(p,disabled,profile).commandObservationPaths,undefined);
});
test('two windows retain both conflicting observation drafts rather than overwrite a reviewed scope',async()=>{
 const {d}=await scene(),m=storage(),a=new ConfigurationDraftJournal(m.store,id,m.lock);a.edit(d);await a.flush();const b=new ConfigurationDraftJournal(m.store,id,m.lock),first=a.value(),second=b.value();first.settings.observation_paths_text='first.xml';a.edit(first);await a.flush();second.settings.observation_paths_text='second.xml';b.edit(second);await assert.rejects(()=>b.flush(),/CONFIGURATION_DRAFT_CONFLICT/);const resolved=await b.resolve('LOCAL');assert.equal(resolved.settings.observation_paths_text,'second.xml');assert.equal(resolved.recovery[0].snapshot.settings.observation_paths_text,'first.xml');
});
test('observation draft quota failure blocks closing and retains the latest path text',async()=>{
 const {d}=await scene(),m=storage(),j=new ConfigurationDraftJournal(m.store,id,m.lock);d.settings.observation_paths_text='KEEP_LATEST.xml';j.edit(d);m.fail(true);await assert.rejects(()=>j.flush());assert.equal(j.value().settings.observation_paths_text,'KEEP_LATEST.xml');assert.equal(j.unsaved(),true);await assert.rejects(()=>flushConfigurationDrafts());m.fail(false);await flushConfigurationDrafts();assert.equal(j.unsaved(),false);
});

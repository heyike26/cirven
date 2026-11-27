import test from 'node:test';
import assert from 'node:assert/strict';
import {ObjectDraftSession,artifactStateKey,keyIdentity} from '../src/workbench-state.ts';
import {currentTemplate,defaultDeliveryDirectory,hasTemplatePreference,inheritProposalTemplate,proposalDiscoveryDraftKey} from '../src/proposal-delivery-preferences.ts';

const ref={kind:'local_file',project_id:'p',workspace_id:'w',binding_revision:2,binding_sha256:'b'.repeat(64),path:'templates/用户模板.docx',revision:3,sha256:'c'.repeat(64)};
const proposal={id:'proposal',project_id:'p',revision:1,sha256:'a'.repeat(64),payload:{discovery_ref:{id:'discussion',revision:4,sha256:'d'.repeat(64)}}};
const artifact={type:'proposal',artifactId:proposal.id,revision:1,contentHash:proposal.sha256,scope:{projectId:'p',sessionId:'discussion'},data:proposal};
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return{promise,resolve,reject}};
const snapshot=(key,fields={},revision=1)=>({status:'SAVED',schema_version:1,key,state_revision:revision,fields,view:{},dirty:false});
function store(initial=[]){const records=new Map(initial.map(([key,fields])=>[keyIdentity(key),snapshot(key,fields)])),calls=[];const remote={load:async key=>{calls.push(['load',structuredClone(key)]);return structuredClone(records.get(keyIdentity(key))??snapshot(key));},save:async(key,revision,fields,view,dirty,id)=>{calls.push(['save',structuredClone(key)]);const result={...snapshot(key,structuredClone(fields),revision+1),view,dirty,operation_id:id};records.set(keyIdentity(key),result);return structuredClone(result)}};return{records,calls,remote};}
function setup(fields={templateRef:ref,templateName:'用户模板.docx'}){const sourceKey=proposalDiscoveryDraftKey(artifact),s=store([[sourceKey,fields]]),target=new ObjectDraftSession(artifactStateKey(artifact),s.remote),source=key=>new ObjectDraftSession(key,s.remote);return{...s,target,source};}
const inherit=(s,changes={})=>inheritProposalTemplate({artifact,target:s.target,source:s.source,readFile:async()=>({source_ref:ref,current:true}),isCurrent:()=>true,...changes});

test('exact predecessor key rejects changed/cross-project scope rather than searching recent drafts',()=>{
 assert.deepEqual(proposalDiscoveryDraftKey(artifact),{project_id:'p',session_id:'discussion',kind:'discovery',object_id:'discussion',revision:4,sha256:'d'.repeat(64)});
 for(const change of [{scope:{projectId:'other'}},{scope:{projectId:'p',sessionId:'another'}},{revision:2},{artifactId:'other'},{contentHash:'f'.repeat(64)},{data:{...proposal,payload:{discovery_ref:{id:'discussion',revision:0,sha256:'d'.repeat(64)}}}}])assert.equal(proposalDiscoveryDraftKey({...artifact,...change}),undefined);
});
test('directory default is inside exactly one existing grant; empty, ambiguous, file-only and foreign grants do not guess or widen authority',()=>{
 const workspace={id:'w',project_id:'p',allowed_write_paths:['delivery/']};assert.equal(defaultDeliveryDirectory(workspace,'p'),'delivery');
 assert.equal(defaultDeliveryDirectory({...workspace,allowed_write_paths:['*']},'p'),'eeas-harness');
 assert.equal(defaultDeliveryDirectory({...workspace,allowed_write_paths:['delivery/','delivery/']},'p'),'delivery');
 for(const paths of [[],['a/','b/'],['delivery/','delivery/sub/'],['report.docx'],['../delivery/'],['/delivery/'],['D:/delivery/'],['delivery\\sub/'],['delivery//']])assert.equal(defaultDeliveryDirectory({...workspace,allowed_write_paths:paths},'p'),'',JSON.stringify(paths));
 assert.equal(defaultDeliveryDirectory(workspace,'foreign'),'');assert.equal(defaultDeliveryDirectory(undefined,'p'),'');
});
test('both actual ObjectDraft sessions load before inheritance and only the full current template ref is persisted',async()=>{
 const gate=deferred(),s=setup();s.target=new ObjectDraftSession(artifactStateKey(artifact),{...s.remote,load:()=>gate.promise});const reads=[];const task=inherit(s,{readFile:async actual=>{reads.push(actual);return{current:true,source_ref:ref}}});await Promise.resolve();assert.equal(reads.length,0);assert.equal(s.calls.length,1,'source draft is not opened before target restoration');
 gate.resolve(snapshot(s.target.key));assert.equal(await task,'INHERITED');assert.deepEqual(reads,[ref]);assert.deepEqual(s.target.fields,{templateRef:ref,templateName:'用户模板.docx',templatePath:''});await s.target.finish();assert.deepEqual(new ObjectDraftSession(s.target.key,s.remote).key,s.target.key);
});
test('legacy proposal choice and explicit empty choice are not replaced, including a new session after restart',async()=>{
 const s=setup();await s.target.ready;s.target.set('fields','templateName','');s.target.set('fields','templatePath','');await s.target.finish();
 const restarted=new ObjectDraftSession(s.target.key,s.remote);let reads=0;assert.equal(await inherit({...s,target:restarted},{readFile:async()=>{reads++;throw Error('must not read')}}),'UNCHANGED');assert.equal(reads,0);assert.equal(hasTemplatePreference(restarted.fields),true);assert.equal(restarted.fields.templateRef,undefined);
 for(const fields of [{templatePath:'manual.dotx'},{templateRef:ref},{templateName:''}]){const t=setup();await t.target.ready;for(const [name,value]of Object.entries(fields))t.target.set('fields',name,value);assert.equal(await inherit(t),'UNCHANGED');await t.target.finish();assert.deepEqual(t.target.fields,fields);}
});
test('clear or pick while the template read is pending wins; a late previous-scope response cannot write',async()=>{
 for(const action of ['clear','choose','navigate']){const s=setup(),gate=deferred(),entered=deferred();let live=true;const task=inherit(s,{isCurrent:()=>live,readFile:async()=>{entered.resolve();return gate.promise}});await entered.promise;
  if(action==='clear'){s.target.set('fields','templateName','');s.target.set('fields','templatePath','');}
  if(action==='choose'){s.target.set('fields','templateRef',{...ref,path:'chosen.dotx'});s.target.set('fields','templateName','chosen.dotx');}
  if(action==='navigate')live=false;
  const before=structuredClone(s.target.fields);gate.resolve({current:true,source_ref:ref});assert.equal(await task,'UNCHANGED');assert.deepEqual(s.target.fields,before);await s.target.finish();
 }
});
test('source clearing during a read and all stale/binding/project mismatches reject without seeding fallback',async()=>{
 const s=setup(),origin=new ObjectDraftSession(proposalDiscoveryDraftKey(artifact),s.remote),gate=deferred(),entered=deferred();const task=inherit(s,{source:()=>origin,readFile:async()=>{entered.resolve();return gate.promise}});await entered.promise;origin.set('fields','templateRef',undefined);gate.resolve({current:true,source_ref:ref});assert.equal(await task,'TEMPLATE_UNAVAILABLE');assert.deepEqual(s.target.fields,{});await origin.finish();
 for(const difference of [{project_id:'other'},{workspace_id:'other'},{binding_revision:9},{binding_sha256:'e'.repeat(64)},{path:'same-name.docx'},{revision:4},{sha256:'f'.repeat(64)}]){const value=setup();assert.equal(await inherit(value,{readFile:async()=>({current:true,source_ref:{...ref,...difference}})}),'TEMPLATE_UNAVAILABLE');assert.deepEqual(value.target.fields,{});}
 assert.equal(currentTemplate({current:false,source_ref:ref},ref),false);
 const foreign=setup({templateRef:{...ref,project_id:'other'}});let reads=0;assert.equal(await inherit(foreign,{readFile:async()=>{reads++;throw Error()}}),'TEMPLATE_UNAVAILABLE');assert.equal(reads,0);
});
test('failed restoration or failed exact read stays explicit and never grants template inheritance',async()=>{
 const s=setup();s.target=new ObjectDraftSession(artifactStateKey(artifact),{...s.remote,load:async()=>{throw Error('offline')}});assert.equal(await inherit(s),'DRAFT_UNAVAILABLE');assert.deepEqual(s.target.fields,{});
 const badOrigin=setup();assert.equal(await inherit(badOrigin,{source:key=>new ObjectDraftSession(key,{...badOrigin.remote,load:async()=>{throw Error('offline')}})}),'DRAFT_UNAVAILABLE');
 const badRead=setup();assert.equal(await inherit(badRead,{readFile:async()=>{throw Error('missing file')}}),'TEMPLATE_UNAVAILABLE');assert.deepEqual(badRead.target.fields,{});
});

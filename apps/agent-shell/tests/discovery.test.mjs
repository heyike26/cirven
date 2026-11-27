import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import cytoscape from 'cytoscape';
import { ApiClient, ApiError } from '../src/api.ts';
import { DiscoveryApi } from '../src/discovery-api.ts';
import { semanticElements } from '../src/semantic-graph.ts';
import { quotedEvidence, verifyQuotedEvidence } from '../src/source-evidence.ts';

const reply=(data,status=200)=>new Response(JSON.stringify(data),{status});
const session={id:'work',project_id:'p',revision:4,sha256:'a'.repeat(64),payload:{candidate:{graphs:[],claims:[]}}};
test('problem entry sends no invented documents or workspace; task file carries exact selected refs',async()=>{
  const calls=[];const client=new DiscoveryApi(new ApiClient(async(url,options)=>{calls.push({url,options});return reply(url==='/api/bootstrap'?{csrf_token:'t'}:session);}));
  await client.create('p',{title:'Find a problem',entry_mode:'PROBLEM',statement:'Observed difficulty',materials:[]},'start-once');
  assert.deepEqual(JSON.parse(calls.at(-1).options.body),{operation_id:'start-once',title:'Find a problem',entry_mode:'PROBLEM',statement:'Observed difficulty',materials:[]});
  const materials=[{document_id:'actual',revision:3,sha256:'b'.repeat(64)}],workspace={id:'c'.repeat(64),revision:2,binding_sha256:'d'.repeat(64),root:'/actual'};
  await client.create('p',{title:'Imported task',entry_mode:'TASK_FILE',statement:'Review first',materials,workspace},'task-once');
  assert.deepEqual(JSON.parse(calls.at(-1).options.body).workspace_ref,{id:workspace.id,revision:2,binding_sha256:workspace.binding_sha256});assert.deepEqual(JSON.parse(calls.at(-1).options.body).materials,materials);
});
test('CAS edit/answer/explicit confirmation preserve version and acceptance; advancement is a separate request',async()=>{
  const writes=[];const client=new DiscoveryApi(new ApiClient(async(url,options)=>{if(url==='/api/bootstrap')return reply({csrf_token:'t'});writes.push({url,body:options.body?JSON.parse(options.body):undefined});return reply(session);}));
  const candidate={title:'Exact edit',claims:[],graphs:[]};await client.edit(session,candidate,'User changed scope','edit-once');await client.answer(session,'q','Saved, not accepted','answer-once');
  assert.equal(writes.length,2);assert.deepEqual(writes[0].body,{operation_id:'edit-once',expected_revision:4,candidate,change_reason:'User changed scope'});assert.equal(writes[1].body.user_confirmed,undefined);
  const resolutions=[{question_id:'q',mode:'ANSWER_ACCEPTED',answer_id:'answer-actual',reason:'Explicit reason'}];await client.confirm(session,['required-claim'],['diagram'],resolutions,'confirm-once');
  assert.deepEqual(writes[2].body,{operation_id:'confirm-once',expected_revision:4,session_sha256:'a'.repeat(64),user_confirmed:true,accepted_claim_ids:['required-claim'],accepted_graph_ids:['diagram'],resolutions});
  await client.advance(session,'Next action',{profile:{id:'provider',revision:2,sha256:'e'.repeat(64)}},'advance-once');assert.equal(writes[3].body.expected_revision,4);assert.equal(writes[3].body.workspace_ref,undefined);assert.equal(writes[3].body.materials,undefined);assert.equal(writes[3].body.provider_profile_ref.id,'provider');
});
test('operation resumption/cancellation reads exact owned key and never resends a model call; conflicts stay failures',async()=>{
  const requests=[];const client=new DiscoveryApi(new ApiClient(async(url,options)=>{if(url==='/api/bootstrap')return reply({csrf_token:'t'});requests.push({url,method:options.method});if(options.method==='PUT')return reply({error:{code:'REVISION_CONFLICT',message:'Reload current revision'}},409);return reply({operation_id:'old-op',kind:'ADVANCE',status:'PROCESSING',session_id:'work',events:[]});}));
  await client.operation('p','old-op');await client.cancel('p','old-op');assert.deepEqual(requests,[{url:'/api/projects/p/discovery/operations/old-op',method:'GET'},{url:'/api/projects/p/discovery/operations/old-op/cancel',method:'POST'}]);await assert.rejects(()=>client.edit(session,{},'reason'),error=>error instanceof ApiError&&error.code==='REVISION_CONFLICT');
});
test('actual Cytoscape semantic flow retains cycles/self-loops and namespaces node/edge identities',()=>{
  const graph={kind:'FLOW',nodes:[{id:'same',label:'Control',origin:'USER_STATEMENT'},{id:'b',label:'Observe',origin:'SOURCE_FACT'}],edges:[{id:'same',source:'same',target:'b',relation:'send'},{id:'back',source:'b',target:'same',relation:'feedback'},{id:'retry',source:'same',target:'same',relation:'retry'}]};
  const core=cytoscape({headless:true,elements:semanticElements(graph)});assert.equal(core.nodes().length,2);assert.equal(core.edges().length,3);assert.equal(core.getElementById('edge:same').data('original_id'),'same');assert.equal(core.getElementById('edge:retry').source().id(),core.getElementById('edge:retry').target().id());core.destroy();
  assert.throws(()=>semanticElements({...graph,edges:[{id:'bad',source:'absent',target:'b'}]}),/不存在/);assert.throws(()=>semanticElements({...graph,nodes:[...graph.nodes,graph.nodes[0]]}),/重复/);
});
test('source excerpts use Python code point offsets across later chunks; untrimmed CRLF and exact SHA are verified',async()=>{
  const content='甲😀\r\n乙',hash=text=>createHash('sha256').update(text).digest('hex');const anchor={document_id:'doc',revision:3,raw_sha256:'f'.repeat(64),chunk_index:8,chunk_sha256:hash(content),char_start:101,char_end:104,quote_sha256:hash('😀\r\n')};
  const receipt={documents:[{document_id:'doc',revision:3,raw_sha256:anchor.raw_sha256,filename:'真实资料.txt',chunks:[{index:8,content,sha256:hash(content),char_start:100,char_end:105}]}]};
  assert.equal(quotedEvidence(receipt,anchor).text,'😀\r\n');assert.equal((await verifyQuotedEvidence(receipt,anchor)).text,'😀\r\n');
  await assert.rejects(()=>verifyQuotedEvidence(receipt,{...anchor,quote_sha256:'0'.repeat(64)}),/摘录 SHA/);assert.throws(()=>quotedEvidence(receipt,{...anchor,revision:2}),/精确版本/);assert.throws(()=>quotedEvidence(receipt,{...anchor,char_start:99}),/范围/);
  const changed=structuredClone(receipt);changed.documents[0].chunks[0].content='丙😀\r\n乙';await assert.rejects(()=>verifyQuotedEvidence(changed,anchor),/片段 SHA/);
});

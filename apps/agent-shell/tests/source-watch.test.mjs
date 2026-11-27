import test from 'node:test';
import assert from 'node:assert/strict';
import {ApiClient} from '../src/api.ts';
import {selectedSources,SelectedSourceWatcher,sourceIdentity,syncSelectedSource} from '../src/source-watch.ts';
const ref={kind:'local_file',project_id:'p',workspace_id:'w',binding_revision:1,binding_sha256:'a'.repeat(64),path:'actual.txt',revision:1,sha256:'b'.repeat(64)};
const row=(source,status='CURRENT')=>({source_ref:source,status,live_sha256:status==='MISSING'?null:status==='CURRENT'?source.sha256:'c'.repeat(64)});
const turn=()=>new Promise(resolve=>setImmediate(resolve));
test('only opened/explicit material sources are selected; cross-project refs and exact duplicates cannot expand the watch scope',()=>{
 const actual={path:ref.path,status:'SAVED',file_source_ref:ref};assert.deepEqual(selectedSources('p',ref,[actual,{...actual,file_source_ref:{...ref,project_id:'other'}}]),[ref]);const next={...ref,revision:2,sha256:'c'.repeat(64)};assert.equal(selectedSources('p',ref,[{...actual,file_source_ref:next}]).length,2);assert.notEqual(sourceIdentity(ref),sourceIdentity(next));
});
test('serial low-frequency watch discards late project results and stops all requests on cleanup',async()=>{
 const requests=[],received=[],pending=[];let active=0,max=0;const watcher=new SelectedSourceWatcher(async(pid,wid,refs,signal)=>{active++;max=Math.max(max,active);requests.push({pid,wid,refs,signal});return await new Promise(resolve=>pending.push(value=>{active--;resolve(value);}));},(pid,values)=>received.push({pid,values}),()=>assert.fail('no failed poll expected'),10000);
 try{watcher.update('p',[ref,ref]);assert.equal(requests.length,1);await watcher.poll();assert.equal(requests.length,1);const next={...ref,project_id:'q'};watcher.update('q',[next]);assert.equal(requests[0].signal.aborted,true);pending.shift()([row(ref)]);await turn();await new Promise(resolve=>setTimeout(resolve,5));assert.equal(received.length,0);assert.equal(requests.length,2);pending.shift()([row(next,'STALE')]);await turn();assert.equal(received[0].pid,'q');assert.equal(received[0].values[0].status,'STALE');assert.equal(max,1);watcher.stop();await watcher.poll();assert.equal(requests.length,2);}finally{watcher.stop();}
});
test('each workspace is checked serially; observation never sends bytes or uses a model endpoint',async()=>{
 const calls=[],client=new ApiClient(async(url,options)=>{if(url==='/api/bootstrap')return new Response(JSON.stringify({csrf_token:'t'}));const body=JSON.parse(options.body);calls.push({url,body});return new Response(JSON.stringify({sources:body.sources.map(source=>row(source))}));});let finish;const done=new Promise(resolve=>finish=resolve);const watcher=new SelectedSourceWatcher((pid,wid,refs,signal)=>client.sourceCheck(pid,wid,refs,signal),(pid,values)=>finish(values),()=>assert.fail('source poll failed'),10000);
 try{watcher.update('p',[ref,{...ref,workspace_id:'w2',path:'other.md'}]);assert.equal((await done).length,2);assert.deepEqual(calls.map(item=>item.url),['/api/projects/p/local-workspaces/w/source-check','/api/projects/p/local-workspaces/w2/source-check']);assert.ok(calls.every(item=>Object.keys(item.body).join()==='sources'));}finally{watcher.stop();}
});
test('explicit sync reads and imports matching current FileSource before returning a context update, retaining old provenance',async()=>{
 const newer={...ref,revision:2,sha256:'c'.repeat(64)},material={path:ref.path,status:'SAVED',file_source_ref:newer,document_ref:{document_id:'doc',revision:2,sha256:newer.sha256}},calls=[];const client={file:async(...args)=>{calls.push(['read',...args]);return{current:true,source_ref:newer};},materials:async(...args)=>{calls.push(['material',...args]);return{materials:[material]};}};const update=await syncSelectedSource(client,ref);assert.equal(update.previous,ref);assert.equal(update.current,newer);assert.deepEqual(calls[1].slice(1),['p','w',['actual.txt'],undefined,[newer]]);assert.equal(calls.length,2);assert.equal(update.material.document_ref.revision,2);
 const changed={...client,file:async()=>({current:false,source_ref:newer})};await assert.rejects(()=>syncSelectedSource(changed,ref),/当前精确版本/);await assert.rejects(()=>syncSelectedSource({...client,materials:async()=>({materials:[]})},ref),/匹配的资料来源/);
});

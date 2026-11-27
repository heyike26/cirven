import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {FolderConnection} from '../src/project-folder.ts';
import {ApiClient} from '../src/api.ts';

const canonical=v=>v&&typeof v==='object'?Array.isArray(v)?'['+v.map(canonical).join(',')+']':'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical(v[k])).join(',')+'}':JSON.stringify(v);
const digest=v=>createHash('sha256').update(canonical(v)).digest('hex');
function storage(){const values=new Map();return{values,fail:()=>false,getItem:k=>values.get(k)??null,setItem(k,v){if(this.fail(JSON.parse(v)))throw new Error('Local disk full');values.set(k,v);}};}
// CLIENT_UNIT boundary: models Web Locks' shared-name exclusive queue and actual
// AbortSignal cancellation; it is not native browser/Windows qualification.
function serialLocks(){
  const queues=new Map(),held=new Set(),events=[];
  function pump(name){if(held.has(name))return;const queue=queues.get(name)??[],entry=queue.shift();if(!entry)return;
    if(entry.signal.aborted){entry.reject(new DOMException('Aborted','AbortError'));pump(name);return;}
    held.add(name);entry.signal.removeEventListener('abort',entry.abort);events.push(['ACQUIRED',name]);
    const release=()=>{held.delete(name);events.push(['RELEASED',name]);pump(name);};
    Promise.resolve().then(()=>entry.callback({name,mode:'exclusive'})).then(value=>{release();entry.resolve(value);},error=>{release();entry.reject(error);});
  }
  return {events,request(name,options,callback){assert.equal(options.mode,'exclusive');assert.equal(options.steal,undefined);return new Promise((resolve,reject)=>{
    const entry={signal:options.signal,callback,resolve,reject,abort:null};
    entry.abort=()=>{const queue=queues.get(name)??[],index=queue.indexOf(entry);if(index>=0)queue.splice(index,1);reject(new DOMException('Aborted','AbortError'));};
    if(entry.signal.aborted)return entry.abort();entry.signal.addEventListener('abort',entry.abort,{once:true});
    const queue=queues.get(name)??[];queue.push(entry);queues.set(name,queue);events.push(['QUEUED',name]);queueMicrotask(()=>pump(name));
  });}};
}
function fixture(local=storage()){
  const calls=[],projects=new Map(),bindings=new Map();let projectMode='ok',bindingMode='ok';
  const client={
    async createProject(name,operationId){calls.push(['CREATE',name,operationId]);const p={id:'project-new',name,description:''};projects.set(operationId,{operation_id:operationId,request_sha256:digest({name,description:''}),project:p});if(projectMode==='lost')throw new Error('Response lost after commit');return p;},
    async projectOperation(key){calls.push(['GET_PROJECT',key]);if(!projects.has(key))throw new Error('404; absent does not establish nonexecution');return projects.get(key);},
    async bindWorkspace(pid,path,writable,key){calls.push(['BIND',pid,path,writable,key]);const root=path.replace(/[\\/]+$/,'');const w={id:digest({project_id:pid,key}),project_id:pid,revision:1,root,binding_sha256:'c'.repeat(64),allowed_write_paths:writable?['*']:[],native_location:{directory:root},request_sha256:digest({root,allowed_write_paths:writable?['*']:[]})};bindings.set(key,{operation_id:key,workspace:w});if(bindingMode==='lost')throw new Error('Binding response lost');return w;},
    async workspaceBindingOperation(pid,key){calls.push(['GET_BIND',pid,key]);if(!bindings.has(key))throw new Error('404');return bindings.get(key);}
  };
  let counter=0;const options={operationId:()=>`folder-${++counter}`,locks:serialLocks()};const connection=new FolderConnection(client,local,options);
  return {connection,client,calls,projects,bindings,local,options,setProjectMode:v=>projectMode=v,setBindingMode:v=>bindingMode=v};
}
const writes=f=>f.calls.filter(c=>['CREATE','BIND'].includes(c[0]));

test('NEW creates and binds its new actual project once; old project/drafts survive and identical prepare recovers the same intent',async()=>{
  const f=fixture();f.local.values.set('eeas-composer:old-project','人工草稿不可丢');
  const prepared=await f.connection.prepare('NEW','新工程','D:\\实际 工作\\新工程\\',true,{id:'old-project',name:'旧工程'});
  assert.equal(writes(f).length,0);assert.ok(f.local.getItem('eeas-folder-connections:v1').includes(prepared.operation_id));
  const actual=await f.connection.run();assert.equal(actual.stage,'CONNECTED');assert.equal(actual.project.id,'project-new');assert.equal(actual.workspace.project_id,'project-new');assert.deepEqual(actual.workspace.allowed_write_paths,['*']);assert.equal(writes(f).length,2);
  assert.equal((await f.connection.prepare('NEW','新工程','D:\\实际 工作\\新工程\\',true,{id:'another-old',name:'别的工作'})).operation_id,prepared.operation_id);
  await f.connection.run();assert.equal(writes(f).length,2);assert.equal(f.local.getItem('eeas-composer:old-project'),'人工草稿不可丢');
  actual.project.id='caller-mutated';assert.equal(f.connection.current().project.id,'project-new');
});

test('CURRENT freezes the chosen project identity and does not imply project rename or write permission',async()=>{
  const f=fixture(),p={id:'actual-current',name:'当前工程',description:'原说明'};
  const row=await f.connection.prepare('CURRENT','不应创建这个名称','D:\\资料',false,p);p.id='changed-later';
  assert.equal(row.request.name,'当前工程');assert.equal(row.project.id,'actual-current');assert.equal(row.stage,'PROJECT_SAVED');
  const actual=await f.connection.run();assert.equal(actual.stage,'CONNECTED');assert.equal(f.calls.some(c=>c[0]==='CREATE'),false);assert.equal(actual.workspace.project_id,'actual-current');assert.deepEqual(actual.workspace.allowed_write_paths,[]);
  await assert.rejects(f.connection.prepare('CURRENT','缺项目','D:\\资料',false),/明确的原项目/);
});

test('lost project response survives restart; inspect only GETs and explicit later run binds without repeating project creation',async()=>{
  const f=fixture();f.setProjectMode('lost');await f.connection.prepare('NEW','新工程','D:\\新工程',false);
  assert.equal((await f.connection.run()).stage,'UNKNOWN');await f.connection.run();assert.equal(writes(f).length,1);
  const restored=new FolderConnection(f.client,f.local,f.options);const found=await restored.inspect();assert.equal(found.stage,'PROJECT_SAVED');assert.equal(writes(f).length,1);assert.equal(f.calls.at(-1)[0],'GET_PROJECT');
  const final=await restored.run();assert.equal(final.stage,'CONNECTED');assert.equal(f.calls.filter(c=>c[0]==='CREATE').length,1);assert.equal(f.calls.filter(c=>c[0]==='BIND').length,1);
});

test('lost binding retains exact scope; 404 or foreign path/hash/key cannot become connected or cause replay',async()=>{
  const f=fixture();f.setBindingMode('lost');const prepared=await f.connection.prepare('NEW','新工程','D:\\新工程\\',true);const unknown=await f.connection.run();assert.equal(unknown.stage,'UNKNOWN');assert.equal(unknown.project.id,'project-new');
  const exact=structuredClone(f.bindings.get(prepared.binding_operation_id));
  for(const changed of [null,{...exact,operation_id:'foreign'}, {...exact,workspace:{...exact.workspace,root:'D:\\别的项目'}},{...exact,workspace:{...exact.workspace,allowed_write_paths:[]}},{...exact,workspace:{...exact.workspace,request_sha256:'d'.repeat(64)}}]){
    if(changed===null)f.bindings.delete(prepared.binding_operation_id);else f.bindings.set(prepared.binding_operation_id,changed);
    assert.equal((await f.connection.inspect()).stage,'UNKNOWN');await f.connection.run();assert.equal(writes(f).length,2);
  }
  f.bindings.set(prepared.binding_operation_id,exact);const actual=await f.connection.inspect();assert.equal(actual.stage,'CONNECTED');assert.equal(writes(f).length,2);assert.equal(actual.workspace.root,'D:\\新工程');
});

test('write-before-send persistence failure sends no POST and preserves the same prepared intent for recovery',async()=>{
  const f=fixture();await f.connection.prepare('NEW','工程','D:\\工程',false);f.local.fail=()=>true;
  const notSent=await f.connection.run();assert.equal(notSent.stage,'CREATING');assert.equal(notSent.local_persistence_status,'FAILED');assert.equal(writes(f).length,0);
  f.local.fail=()=>false;const actual=await f.connection.run();assert.equal(actual.stage,'CONNECTED');assert.equal(actual.operation_id,notSent.operation_id);assert.equal(writes(f).length,2);
});

test('known project receipt survives local persistence failure and cannot be mislabeled unknown or recreated',async()=>{
  const f=fixture();await f.connection.prepare('NEW','工程','D:\\工程',false);f.local.fail=j=>j.records.some(r=>r.stage==='PROJECT_SAVED');
  const actual=await f.connection.run();assert.equal(actual.stage,'PROJECT_SAVED');assert.equal(actual.project.id,'project-new');assert.equal(actual.local_persistence_status,'FAILED');assert.equal(writes(f).length,1);
  f.local.fail=()=>false;assert.equal((await f.connection.run()).stage,'CONNECTED');assert.equal(f.calls.filter(c=>c[0]==='CREATE').length,1);
});

test('known binding result remains connected when local save fails; inspect repairs local journal with no POST',async()=>{
  const f=fixture();await f.connection.prepare('NEW','工程','D:\\工程',false);f.local.fail=j=>j.records.some(r=>r.stage==='CONNECTED');
  const known=await f.connection.run();assert.equal(known.stage,'CONNECTED');assert.equal(known.local_persistence_status,'FAILED');assert.ok(known.workspace.id);assert.equal(writes(f).length,2);
  f.local.fail=()=>false;const saved=await f.connection.inspect();assert.equal(saved.stage,'CONNECTED');assert.equal(saved.local_persistence_status,'SAVED');assert.equal(saved.reason,undefined);assert.equal(writes(f).length,2);
  assert.equal(new FolderConnection(f.client,f.local,f.options).current().stage,'CONNECTED');
});

test('parallel clicks share one write; late completion does not replace a different newly prepared active intent',async()=>{
  const f=fixture();let release;const wait=new Promise(resolve=>release=resolve),original=f.client.createProject;
  f.client.createProject=async(...args)=>{await wait;return original(...args);};const old=await f.connection.prepare('NEW','原工程','D:\\原工程',false);
  const a=f.connection.run(old.operation_id),b=f.connection.run(old.operation_id);assert.equal(a,b);
  const preparing=f.connection.prepare('CURRENT','无关名称','D:\\当前工作',false,{id:'current-project',name:'当前工作'});release();const newer=await preparing;
  assert.equal((await a).stage,'CONNECTED');await b;assert.equal(writes(f).length,2);assert.equal(f.connection.current().operation_id,newer.operation_id);assert.equal(f.connection.current().project.id,'current-project');
});

test('interrupted write at restart becomes unknown and only original GET may recover; absence never authorizes repost',async()=>{
  const f=fixture();await f.connection.prepare('NEW','工程','D:\\工程',false);const key='eeas-folder-connections:v1',saved=JSON.parse(f.local.getItem(key));saved.records[0].project_attempted=true;f.local.setItem(key,JSON.stringify(saved));
  const before=f.local.getItem(key);let storageWrites=0;const set=f.local.setItem;f.local.setItem=function(...args){storageWrites++;return set.apply(this,args);};
  const restored=new FolderConnection(f.client,f.local,f.options);assert.equal(restored.current().stage,'UNKNOWN');assert.equal(storageWrites,0);assert.equal(f.local.getItem(key),before);assert.equal((await restored.inspect()).stage,'UNKNOWN');await restored.run();assert.equal(writes(f).length,0);assert.equal(f.calls[0][0],'GET_PROJECT');
});

test('incorrect project receipt, corrupted journal/fingerprint and external refresh preserve connection records',async()=>{
  const f=fixture();f.setProjectMode('lost');const row=await f.connection.prepare('NEW','工程','D:\\工程',false);await f.connection.run();const exact=f.projects.get(row.project_operation_id);f.projects.set(row.project_operation_id,{...exact,request_sha256:'f'.repeat(64)});assert.equal((await f.connection.inspect()).stage,'UNKNOWN');assert.equal(writes(f).length,1);
  const raw=f.local.getItem('eeas-folder-connections:v1');f.local.values.set('eeas-folder-connections:v1','broken-json');assert.throws(()=>new FolderConnection(f.client,f.local,f.options),/原记录保留/);await assert.rejects(f.connection.refresh(),/原记录保留/);assert.equal(f.connection.current().operation_id,row.operation_id);f.local.values.set('eeas-folder-connections:v1',raw);
  const second=new FolderConnection(f.client,f.local,f.options);const added=await second.prepare('CURRENT','现在','D:\\现在',false,{id:'other',name:'现在'});const preserved=f.local.getItem('eeas-folder-connections:v1');assert.equal((await f.connection.refresh()).operation_id,added.operation_id);assert.equal(f.local.getItem('eeas-folder-connections:v1'),preserved);assert.equal(f.connection.list().length,2);
  const tampered=JSON.parse(preserved);tampered.records[0].request.path='D:\\换过的目录';f.local.values.set('eeas-folder-connections:v1',JSON.stringify(tampered));const bad=new FolderConnection(f.client,f.local,f.options);await assert.rejects(bad.run(tampered.records[0].operation_id),/要求已变化/);assert.equal(writes(f).length,1);
});

test('real ApiClient write methods send one POST on 401, retain operation keys, and recovery routes are GET-only',async()=>{
  const calls=[];const api=new ApiClient(async(url,options)=>{calls.push([url,options]);if(url==='/api/bootstrap')return new Response(JSON.stringify({csrf_token:'fixture'}));return new Response(JSON.stringify({error:{code:'AUTH_REQUIRED',message:'401'}}),{status:401});});
  await assert.rejects(api.createProject('工程','same-project-op'),/401/);assert.equal(calls.filter(c=>c[1].method==='POST').length,1);assert.equal(JSON.parse(calls.find(c=>c[1].method==='POST')[1].body).operation_id,'same-project-op');
  await assert.rejects(api.bindWorkspace('p','D:\\工程',false,'same-binding-op'),/401/);assert.equal(calls.filter(c=>c[1].method==='POST').length,2);
  const routes=[];const reader=new ApiClient(async(url,options)=>{routes.push([url,options]);return new Response(JSON.stringify(url==='/api/bootstrap'?{csrf_token:'fixture'}:{operation_id:'op'}));});
  await reader.projectOperation('op');await reader.workspaceBindingOperation('p','op');assert.deepEqual(routes.slice(1).map(c=>[c[0],c[1].method]),[['/api/project-operations/op','GET'],['/api/projects/p/local-workspace-operations/op','GET']]);
});

test('more than32 successful connections roll only local connected quick indexes while all backend projects/workspaces stay available',async()=>{
  const f=fixture(),original=f.client.createProject;f.local.values.set('eeas-composer:existing','人工草稿');
  f.client.createProject=async(name,key)=>{const actual=await original(name,key),p={...actual,id:'project-'+key};f.projects.get(key).project=p;return p;};
  let first;
  for(let i=0;i<40;i++){const intent=await f.connection.prepare('NEW','工程'+i,'D:\\实际工程'+i,false);first??=intent;assert.equal((await f.connection.run()).stage,'CONNECTED');}
  assert.equal(f.connection.list().length,32);assert.equal(f.connection.list().every(row=>row.stage==='CONNECTED'),true);assert.equal(f.connection.get(first.operation_id),undefined);
  assert.equal(f.projects.size,40);assert.equal(f.bindings.size,40);assert.equal((await f.client.projectOperation(first.project_operation_id)).project.name,'工程0');assert.equal(f.local.getItem('eeas-composer:existing'),'人工草稿');
  assert.equal(new FolderConnection(f.client,f.local,f.options).list().length,32);
});

test('32 unresolved intents remain intact and reject only a new connection without a single hidden POST or history deletion',async()=>{
  const f=fixture();for(let i=0;i<32;i++)await f.connection.prepare('NEW','未决工程'+i,'D:\\未决工程'+i,false);
  const before=f.local.getItem('eeas-folder-connections:v1');await assert.rejects(f.connection.prepare('NEW','额外工程','D:\\额外工程',false),/32条未决/);
  assert.equal(f.connection.list().length,32);assert.equal(f.local.getItem('eeas-folder-connections:v1'),before);assert.equal(writes(f).length,0);
});

test('two independently constructed instances serialize identical preparation and writes using the same journal lock',async()=>{
  const f=fixture(),second=new FolderConnection(f.client,f.local,f.options);
  const [a,b]=await Promise.all([f.connection.prepare('NEW','同一工程','D:\\同一工程',false),second.prepare('NEW','同一工程','D:\\同一工程',false)]);
  assert.equal(a.operation_id,b.operation_id);assert.equal(f.connection.list().length,1);
  const [left,right]=await Promise.all([f.connection.run(a.operation_id),second.run(b.operation_id)]);
  assert.equal(left.stage,'CONNECTED');assert.equal(right.stage,'CONNECTED');assert.equal(right.current_binding_status,'AVAILABLE');
  assert.equal(f.calls.filter(c=>c[0]==='CREATE').length,1);assert.equal(f.calls.filter(c=>c[0]==='BIND').length,1);assert.equal(f.calls.filter(c=>c[0]==='GET_BIND').length,1);
  const names=new Set(f.options.locks.events.map(event=>event[1]));assert.deepEqual([...names],['eeas.folder-connections:eeas-folder-connections:v1']);
});

test('distinct concurrent preparations preserve both records and the external active selection when an older operation completes',async()=>{
  const f=fixture(),second=new FolderConnection(f.client,f.local,f.options);
  const [a,b]=await Promise.all([f.connection.prepare('NEW','工程一','D:\\工程一',false),second.prepare('CURRENT','工程二','D:\\工程二',false,{id:'second-project',name:'工程二'})]);
  assert.notEqual(a.operation_id,b.operation_id);assert.equal((await f.connection.refresh()).operation_id,b.operation_id);assert.equal(f.connection.list().length,2);
  assert.equal((await f.connection.run(a.operation_id)).stage,'CONNECTED');assert.equal(f.connection.current().operation_id,b.operation_id);
  await second.refresh();assert.equal(second.get(a.operation_id).stage,'CONNECTED');assert.equal(second.current().operation_id,b.operation_id);
});

test('unavailable platform lock rejects every controlled operation without modifying storage or issuing requests',async()=>{
  const f=fixture(),row=await f.connection.prepare('NEW','工程','D:\\工程',false),before=f.local.getItem('eeas-folder-connections:v1');
  const unavailable=new FolderConnection(f.client,f.local,{...f.options,locks:null});
  for(const call of [()=>unavailable.prepare('NEW','其他','D:\\其他',false),()=>unavailable.run(row.operation_id),()=>unavailable.inspect(row.operation_id),()=>unavailable.refresh()])await assert.rejects(call(),/跨窗口安全锁/);
  assert.equal(unavailable.lockStatus().status,'UNAVAILABLE');assert.equal(f.calls.length,0);assert.equal(f.local.getItem('eeas-folder-connections:v1'),before);
});

test('lock wait times out and removes the queued request; an acquired slow request stays exclusive until its actual result',async()=>{
  const f=fixture();let release,entered;const gate=new Promise(resolve=>release=resolve),started=new Promise(resolve=>entered=resolve),original=f.client.createProject;
  f.client.createProject=async(...args)=>{entered();await gate;return original(...args);};
  const holder=new FolderConnection(f.client,f.local,{...f.options,lockWaitMs:10}),waiting=new FolderConnection(f.client,f.local,{...f.options,lockWaitMs:15});
  const row=await holder.prepare('NEW','慢工程','D:\\慢工程',false),running=holder.run(row.operation_id);await started;
  const attempt=waiting.prepare('NEW','不能迟到执行','D:\\不能迟到执行',false);assert.equal(waiting.lockStatus().status,'WAITING');
  try{await assert.rejects(attempt,/等待已结束/);assert.equal(waiting.lockStatus().status,'TIMED_OUT');assert.equal(holder.lockStatus().status,'HELD');assert.equal(writes(f).length,0);}
  finally{release();}
  assert.equal((await running).stage,'CONNECTED');await new Promise(resolve=>setTimeout(resolve,20));
  assert.equal(writes(f).length,2);assert.equal(JSON.parse(f.local.getItem('eeas-folder-connections:v1')).records.length,1);assert.equal(holder.lockStatus().status,'IDLE');
  assert.equal((await waiting.refresh()).operation_id,row.operation_id);assert.equal(f.calls.length,2);
});

test('a second call timing out on the same instance cannot claim that its still-running owner released the lock',async()=>{
  const f=fixture();let release,entered;const gate=new Promise(resolve=>release=resolve),started=new Promise(resolve=>entered=resolve),original=f.client.createProject;
  f.client.createProject=async(...args)=>{entered();await gate;return original(...args);};
  const connection=new FolderConnection(f.client,f.local,{...f.options,lockWaitMs:10}),row=await connection.prepare('NEW','工程','D:\\工程',false),running=connection.run(row.operation_id);await started;
  try{await assert.rejects(connection.prepare('NEW','后来的','D:\\后来的',false),/等待已结束/);assert.equal(connection.lockStatus().status,'HELD');}
  finally{release();}
  assert.equal((await running).stage,'CONNECTED');assert.equal(connection.lockStatus().status,'IDLE');assert.equal(writes(f).length,2);
});

test('refresh merges a locally known project receipt after another window adds an intent without discarding receipt or new active',async()=>{
  const f=fixture(),row=await f.connection.prepare('NEW','原工程','D:\\原工程',false);
  f.local.fail=j=>j.records.some(r=>r.operation_id===row.operation_id&&r.stage==='PROJECT_SAVED');
  assert.equal((await f.connection.run()).local_persistence_status,'FAILED');
  const second=new FolderConnection(f.client,f.local,f.options),newer=await second.prepare('NEW','后来工程','D:\\后来工程',false);
  const before=f.local.getItem('eeas-folder-connections:v1');assert.equal((await f.connection.refresh()).operation_id,newer.operation_id);assert.equal(f.local.getItem('eeas-folder-connections:v1'),before);
  const retained=f.connection.get(row.operation_id);assert.equal(retained.stage,'PROJECT_SAVED');assert.equal(retained.project.id,'project-new');assert.equal(retained.local_persistence_status,'FAILED');
  f.local.fail=()=>false;assert.equal((await f.connection.inspect(row.operation_id)).stage,'PROJECT_SAVED');assert.equal(writes(f).length,1);
  assert.equal((await f.connection.run(row.operation_id)).stage,'CONNECTED');assert.equal(f.connection.current().operation_id,newer.operation_id);assert.equal(writes(f).length,2);
});

test('known binding survives failed persistence and external changes; refresh alone never claims saved or currently available',async()=>{
  const f=fixture(),row=await f.connection.prepare('NEW','原工程','D:\\原工程',false);f.local.fail=j=>j.records.some(r=>r.stage==='CONNECTED');
  const known=await f.connection.run();assert.equal(known.stage,'CONNECTED');assert.equal(known.local_persistence_status,'FAILED');
  const second=new FolderConnection(f.client,f.local,f.options),newer=await second.prepare('NEW','另一个工程','D:\\另一个工程',false);
  await f.connection.refresh();const retained=f.connection.get(row.operation_id);assert.equal(retained.workspace.id,known.workspace.id);assert.equal(retained.stage,'CONNECTED');assert.equal(retained.local_persistence_status,'FAILED');assert.equal(retained.current_binding_status,'NOT_CHECKED');
  f.local.fail=()=>false;const saved=await f.connection.inspect(row.operation_id);assert.equal(saved.local_persistence_status,'SAVED');assert.equal(saved.current_binding_status,'AVAILABLE');assert.equal(f.connection.current().operation_id,newer.operation_id);assert.equal(writes(f).length,2);
});

test('missing disk journal does not hide an unsaved known binding or retain cached availability',async()=>{
  const f=fixture();await f.connection.prepare('NEW','工程','D:\\工程',false);f.local.fail=j=>j.records.some(r=>r.stage==='CONNECTED');const known=await f.connection.run();
  f.local.values.delete('eeas-folder-connections:v1');const retained=await f.connection.refresh();
  assert.equal(retained.operation_id,known.operation_id);assert.deepEqual(retained.workspace,known.workspace);assert.equal(retained.local_persistence_status,'FAILED');assert.equal(retained.current_binding_status,'NOT_CHECKED');assert.equal(writes(f).length,2);
  f.local.fail=()=>false;assert.equal((await f.connection.inspect()).current_binding_status,'AVAILABLE');assert.equal(writes(f).length,2);
});

test('merged attempted flags cannot replay binding when a different owner already submitted it after project receipt persistence failed',async()=>{
  const f=fixture(),row=await f.connection.prepare('NEW','工程','D:\\工程',false);f.local.fail=j=>j.records.some(r=>r.stage==='PROJECT_SAVED');await f.connection.run();f.local.fail=()=>false;
  const second=new FolderConnection(f.client,f.local,f.options);assert.equal(second.current().stage,'UNKNOWN');assert.equal((await second.inspect()).stage,'PROJECT_SAVED');
  f.setBindingMode('lost');assert.equal((await second.run()).stage,'UNKNOWN');
  const retained=await f.connection.run(row.operation_id);assert.equal(retained.stage,'UNKNOWN');assert.equal(retained.unknown_stage,'BIND_WORKSPACE');assert.equal(retained.binding_attempted,true);assert.equal(writes(f).length,2);
  assert.equal((await f.connection.inspect(row.operation_id)).stage,'CONNECTED');assert.equal(writes(f).length,2);
});

test('conflicting external receipts do not overwrite a locally known server fact or trigger another POST',async()=>{
  const f=fixture(),row=await f.connection.prepare('NEW','工程','D:\\工程',false);f.local.fail=j=>j.records.some(r=>r.stage==='PROJECT_SAVED');await f.connection.run();
  const key='eeas-folder-connections:v1',changed=JSON.parse(f.local.getItem(key));changed.records[0].project={id:'foreign-project',name:'工程',description:''};changed.records[0].stage='PROJECT_SAVED';f.local.values.set(key,JSON.stringify(changed));const before=f.local.getItem(key);
  await assert.rejects(f.connection.refresh(),/已知回执.*冲突/);await assert.rejects(f.connection.run(row.operation_id),/已知回执.*冲突/);
  assert.equal(f.connection.get(row.operation_id).project.id,'project-new');assert.equal(f.connection.get(row.operation_id).local_persistence_status,'FAILED');assert.equal(f.local.getItem(key),before);assert.equal(writes(f).length,1);
});

test('a prepared intent whose first local save failed survives refresh under its original operation without silent recreation',async()=>{
  const f=fixture();f.local.fail=()=>true;await assert.rejects(f.connection.prepare('NEW','工程','D:\\工程',false),/本机保存/);const known=f.connection.current();
  assert.equal((await f.connection.refresh()).operation_id,known.operation_id);assert.equal(f.connection.current().local_persistence_status,'FAILED');assert.equal(f.local.getItem('eeas-folder-connections:v1'),null);assert.equal(f.calls.length,0);
  f.local.fail=()=>false;assert.equal((await f.connection.run(known.operation_id)).stage,'CONNECTED');assert.equal(writes(f).length,2);
});

test('connected history is not live-root authority: restart and refresh clear availability; explicit run and inspect use original GET only',async()=>{
  const f=fixture();await f.connection.prepare('NEW','工程','D:\\工程',false);const connected=await f.connection.run();assert.equal(connected.current_binding_status,'AVAILABLE');
  const raw=f.local.getItem('eeas-folder-connections:v1'),restored=new FolderConnection(f.client,f.local,f.options);assert.equal(restored.current().current_binding_status,'NOT_CHECKED');assert.equal(f.local.getItem('eeas-folder-connections:v1'),raw);
  await f.connection.refresh();assert.equal(f.connection.current().current_binding_status,'NOT_CHECKED');
  assert.equal((await restored.run()).current_binding_status,'AVAILABLE');assert.equal((await restored.inspect()).current_binding_status,'AVAILABLE');assert.equal(writes(f).length,2);assert.equal(f.calls.filter(c=>c[0]==='GET_BIND').length,2);
});

test('deleted, drifted or mismatched live binding preserves known connected receipt with UNAVAILABLE and never POSTs',async()=>{
  const f=fixture(),row=await f.connection.prepare('NEW','工程','D:\\工程',false),known=await f.connection.run(),original=f.client.workspaceBindingOperation;
  const exact=f.bindings.get(row.binding_operation_id);
  for(const changed of [null,{...exact,workspace:{...exact.workspace,root:'D:\\被替换'}},{...exact,workspace:{...exact.workspace,binding_sha256:'d'.repeat(64)}},{...exact,workspace:{...exact.workspace,revision:2}}]){
    if(changed===null)f.client.workspaceBindingOperation=async()=>{throw new Error('WORKSPACE_IDENTITY_DRIFT_OR_MISSING');};else{f.client.workspaceBindingOperation=original;f.bindings.set(row.binding_operation_id,changed);}
    const result=await f.connection.run(row.operation_id);assert.equal(result.stage,'CONNECTED');assert.equal(result.current_binding_status,'UNAVAILABLE');assert.match(result.current_binding_reason,/历史回执保留/);assert.deepEqual(result.workspace,known.workspace);assert.equal(result.project.id,known.project.id);assert.equal(writes(f).length,2);
  }
  f.client.workspaceBindingOperation=original;f.bindings.set(row.binding_operation_id,exact);assert.equal((await f.connection.inspect()).current_binding_status,'AVAILABLE');assert.equal(writes(f).length,2);
});

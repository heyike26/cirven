'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),{EventEmitter}=require('node:events');
const {createTaskNotifications}=require('../task-notifications.cjs');
const base=require('node:os').tmpdir();fs.mkdirSync(base,{recursive:true});
function clock(){let now=0,sequence=0;const timers=new Map();return {now:()=>now,setTimeout(fn,delay){const id=++sequence;timers.set(id,{at:now+delay,fn});return id;},clearTimeout:id=>timers.delete(id),advance(ms){now+=ms;for(const [id,item]of [...timers])if(item.at<=now){timers.delete(id);item.fn();}},get pending(){return timers.size;}};}
function alert({id='alert-1',revision=1,generation=1,category='WAITING_USER',acknowledged=false,...rest}={}){
  const sha256=String(revision%10).repeat(64);return {id,revision,sha256,alert_ref:{id,revision,sha256},kind:'TASK_STATE_CHANGED',
    key:{kind:'agent-session',project_id:'project-A',object_id:'owner-1',operation_id:'operation-1',generation},category,
    terminal:['EXECUTION_RETURNED','EXECUTION_ERROR','CANCELLED','TIMED_OUT'].includes(category),acknowledged,
    label:'PRIVATE PROMPT MUST NEVER APPEAR',created_at:'2026-10-15T00:00:00Z',...rest};
}
function fixture(options={}){
  const notices=[],opened=[],errors=[],time=clock();const journalPath=options.journalPath??path.join(fs.mkdtempSync(path.join(base,'run-')),'notifications.json');
  class Notification extends EventEmitter{static isSupported(){return options.supported!==false;}
    constructor(input){super();this.input=input;this.closeCalls=0;notices.push(this);}
    show(){this.showCalls=(this.showCalls??0)+1;if(options.throwShow)throw Error('OS failed');if(options.shown)this.emit('show');if(options.failed)this.emit('failed');}
    close(){this.closeCalls++;this.emit('close');}}
  const owner=createTaskNotifications({Notification,journalPath,clock:time,isActive:options.isActive??(()=>false),getVisibleTask:options.getVisibleTask??(()=>null),
    onOpenTask:item=>opened.push(item),onError:error=>errors.push(error.name),...options});
  const observe=(alerts,enabled=true)=>owner.observe({alerts,settings:{notifications_enabled:enabled}});
  return {owner,notices,opened,errors,time,journalPath,observe};
}
test('only genuine useful exact changes notify; a silent merged body never copies prompts or filenames',()=>{
  const f=fixture();try{
    f.observe([alert(),alert({id:'returned',category:'EXECUTION_RETURNED'}),alert({id:'running',category:'RUNNING',elapsed_seconds:999999})]);
    assert.equal(f.notices.length,0);f.time.advance(400);assert.equal(f.notices.length,1);const n=f.notices[0];
    assert.equal(n.input.silent,true);assert.match(n.input.body,/2 项任务/);assert.doesNotMatch(JSON.stringify(n.input),/PRIVATE|project-A|owner-1/);
    assert.equal(f.owner.get().records.every(row=>row.status==='REQUESTED'),true);assert.equal(f.owner.get().show_is_delivery_confirmation,false);
    f.time.advance(8000);assert.equal(f.owner.get().records.every(row=>row.status==='DELIVERY_UNCONFIRMED'),true);
    n.emit('click');n.emit('click');assert.equal(f.opened.length,1);assert.equal(f.opened[0].related.length,2);
    assert.deepEqual(f.opened[0].key,alert().key);assert.deepEqual(f.opened[0].alert_ref,alert().alert_ref);
  }finally{f.owner.close();}
});
test('repeated polling and an actual FS restart never repeat an attempted or acknowledged exact alert',()=>{
  const f=fixture({shown:true});f.observe([alert()]);f.time.advance(400);f.observe([alert()]);f.time.advance(400);assert.equal(f.notices.length,1);f.owner.close();
  const restart=fixture({journalPath:f.journalPath});try{
    restart.observe([alert()]);restart.time.advance(400);assert.equal(restart.notices.length,0);assert.equal(restart.owner.get().records[0].status,'SYSTEM_SHOWN');
    restart.observe([alert({id:'ack',acknowledged:true})]);restart.time.advance(400);
    restart.observe([alert({id:'ack'})]);restart.time.advance(400);assert.equal(restart.notices.length,0);
    restart.observe([alert({revision:2,generation:2})]);restart.time.advance(400);assert.equal(restart.notices.length,1);
  }finally{restart.owner.close();}
});
test('currently visible exact task is local only, while another task or observer event is eligible',()=>{
  const current=alert(),f=fixture({isActive:()=>true,getVisibleTask:()=>current.key});try{
    f.observe([current]);f.time.advance(400);assert.equal(f.notices.length,0);assert.equal(f.owner.get().records[0].status,'LOCAL_ONLY');
    f.observe([alert({id:'other',key:{...current.key,operation_id:'other'}}),alert({id:'observer',kind:'OBSERVER_STATE_CHANGED',key:null,category:'OBSERVATION_UNAVAILABLE'})]);
    f.time.advance(400);assert.equal(f.notices.length,1);assert.match(f.notices[0].input.body,/2 项任务/);
  }finally{f.owner.close();}
});
test('notification disable records local-only without changing software unread; re-enable never replays the quiet batch',()=>{
  const f=fixture();try{f.observe([alert()],false);f.time.advance(1000);assert.equal(f.notices.length,0);assert.equal(f.owner.get().local_count,1);
    f.observe([alert()]);f.observe([alert()],false);f.time.advance(1000);assert.equal(f.notices.length,0);
    f.observe([alert()]);f.time.advance(400);assert.equal(f.notices.length,0);
    f.observe([alert({revision:2})]);f.time.advance(400);assert.equal(f.notices.length,1);
    f.observe([alert()],false);assert.equal(f.notices[0].closeCalls,1);assert.equal(f.time.pending,0);
    f.observe([alert()]);f.notices[0].emit('click');assert.equal(f.opened.length,0,'retired notifications cannot open a task after re-enable');
  }finally{f.owner.close();}
});
test('prime durably baselines first-scan history without acknowledging software alerts, then accepts only genuinely new changes',()=>{
  const f=fixture(),history=[alert({category:'EXECUTION_RETURNED'}),alert({id:'old-error',category:'EXECUTION_ERROR'}),alert({id:'old-wait'})];try{
    f.owner.prime({alerts:history,settings:{enabled:true,notifications_enabled:true}});f.time.advance(10000);
    assert.equal(f.notices.length,0);assert.equal(f.owner.get().baseline_status,'READY');assert.equal(history.every(item=>item.acknowledged===false),true);
    assert.equal(f.owner.get().records.every(item=>item.status==='BASELINED'),true);
    f.observe(history);f.time.advance(400);assert.equal(f.notices.length,0);
    f.observe([...history,alert({id:'new-wait'})]);f.time.advance(400);assert.equal(f.notices.length,1);
    assert.deepEqual(f.notices[0].input.body,'有任务等待你的回答。点击查看准确任务。');
  }finally{f.owner.close();}
  const restart=fixture({journalPath:f.journalPath});try{restart.observe(history);restart.time.advance(400);assert.equal(restart.notices.length,0);}finally{restart.owner.close();}
});
test('disabled-period history stays quiet across a real restart and only a new ref notifies after enable',()=>{
  const f=fixture();f.observe([alert()],false);f.owner.close();
  const restart=fixture({journalPath:f.journalPath});try{
    restart.observe([alert()]);restart.time.advance(400);assert.equal(restart.notices.length,0);
    restart.observe([alert({revision:2,generation:2})]);restart.time.advance(400);assert.equal(restart.notices.length,1);
  }finally{restart.owner.close();}
});
test('failed baseline persistence remains pending and fail-closed until that same history is saved',()=>{
  let fail=true;const io={...fs,renameSync(...args){if(fail)throw Error('fixture ENOSPC');return fs.renameSync(...args);}},f=fixture({io});try{
    f.owner.prime({alerts:[alert()],settings:{notifications_enabled:true}});assert.equal(f.owner.get().baseline_status,'PENDING');
    f.observe([alert()]);f.time.advance(400);assert.equal(f.notices.length,0);
    fail=false;f.observe([alert()]);f.time.advance(400);assert.equal(f.owner.get().baseline_status,'READY');assert.equal(f.notices.length,0);
    f.observe([alert({id:'new'})]);f.time.advance(400);assert.equal(f.notices.length,1);
  }finally{f.owner.close();}
});
test('unsupported, native show failure, unknown delivery and OS show acknowledgement remain distinct',()=>{
  for(const [options,expected]of [[{supported:false},'UNSUPPORTED'],[{throwShow:true},'DISPLAY_FAILED'],[{failed:true},'DISPLAY_FAILED'],[{shown:true},'SYSTEM_SHOWN'],[{},'REQUESTED']]){
    const f=fixture(options);try{f.observe([alert()]);f.time.advance(400);assert.equal(f.owner.get().records[0].status,expected);
      assert.equal(f.owner.get().system_blocking,'NOT_OBSERVABLE');assert.equal(f.owner.get().native_windows_qualification,'NOT_RUN');
    }finally{f.owner.close();}
  }
});
test('malformed references, ordinary running elapsed and stale ack cannot become invented timeout notifications',()=>{
  const f=fixture();try{
    const bad=alert();bad.alert_ref.sha256='f'.repeat(64);
    f.observe([bad,alert({id:'running',category:'RUNNING',elapsed_seconds:1e12}),alert({id:'ack',acknowledged:true})]);f.time.advance(1000);
    assert.equal(f.notices.length,0);assert.equal(f.errors.length,1);
    f.observe([alert({id:'timeout',category:'TIMED_OUT'})]);f.time.advance(400);assert.match(f.notices[0].input.body,/明确超时/);
  }finally{f.owner.close();}
});
test('journal CAS rejects a second stale owner before a native show and preserves the first owner evidence',()=>{
  const first=fixture(),stale=fixture({journalPath:first.journalPath});try{
    first.observe([alert()]);first.time.advance(400);const bytes=fs.readFileSync(first.journalPath,'utf8');
    stale.observe([alert({id:'different'})]);stale.time.advance(400);assert.equal(stale.notices.length,0);assert.equal(stale.owner.get().journal_status,'FAILED');
    assert.equal(fs.readFileSync(first.journalPath,'utf8'),bytes);assert.equal(fs.existsSync(first.journalPath+'.lock'),false);
  }finally{first.owner.close();stale.owner.close();}
});
test('failed disk admission never shows or claims delivery; late callback and disposal affect only owned notifications',()=>{
  const io={...fs,renameSync(){throw Error('fixture ENOSPC');}},failed=fixture({io});
  failed.observe([alert()]);failed.time.advance(400);assert.equal(failed.notices.length,0);assert.equal(failed.owner.get().journal_status,'FAILED');failed.owner.close();
  const f=fixture();f.observe([alert()]);f.time.advance(400);const n=f.notices[0];n.emit('close');f.owner.close();
  assert.equal(n.closeCalls,1);assert.equal(f.time.pending,0);n.emit('click');n.emit('show');assert.equal(f.opened.length,0);
  const pending=fixture();pending.observe([alert()]);pending.owner.close();pending.time.advance(10000);assert.equal(pending.notices.length,0);
});
test('known OS show acknowledgement survives a later local write failure, and restart deduplicates the durable attempt',()=>{
  let fail=false;const io={...fs,renameSync(...args){if(fail)throw Error('fixture ENOSPC');return fs.renameSync(...args);}};
  const f=fixture({io});f.observe([alert()]);f.time.advance(400);fail=true;f.notices[0].emit('show');
  assert.equal(f.owner.get().records[0].status,'SYSTEM_SHOWN');assert.equal(f.owner.get().journal_status,'FAILED');f.owner.close();
  const restart=fixture({journalPath:f.journalPath});try{restart.observe([alert()]);restart.time.advance(400);assert.equal(restart.notices.length,0);}finally{restart.owner.close();}
});
test('corrupt journal and existing lock fail closed without rewriting evidence or showing notifications',()=>{
  const f=fixture();f.observe([alert()]);f.time.advance(400);f.owner.close();
  const corrupt=JSON.parse(fs.readFileSync(f.journalPath,'utf8'));corrupt.revision++;const bytes=JSON.stringify(corrupt);fs.writeFileSync(f.journalPath,bytes);
  const restart=fixture({journalPath:f.journalPath});restart.observe([alert({id:'new'})]);restart.time.advance(400);
  assert.equal(restart.notices.length,0);assert.equal(restart.owner.get().journal_status,'FAILED');assert.equal(fs.readFileSync(f.journalPath,'utf8'),bytes);restart.owner.close();
  const locked=fixture();fs.writeFileSync(locked.journalPath+'.lock','orphaned fixture; never silently stolen');locked.observe([alert()]);locked.time.advance(400);
  assert.equal(locked.notices.length,0);assert.equal(fs.readFileSync(locked.journalPath+'.lock','utf8'),'orphaned fixture; never silently stolen');locked.owner.close();
});

test('changing language never replays a notification; only a genuinely new alert uses the next locale',()=>{
  const f=fixture();try{
    f.observe([alert()]);f.time.advance(400);assert.equal(f.notices.length,1);const old=f.notices[0].input.body;
    const records=f.owner.get().records;f.owner.setLocale('en');assert.equal(f.notices.length,1);assert.equal(f.notices[0].input.body,old);assert.deepEqual(f.owner.get().records,records);
    f.observe([alert()]);f.time.advance(400);assert.equal(f.notices.length,1);
    f.observe([alert({id:'new-error',category:'EXECUTION_ERROR'})]);f.time.advance(400);
    assert.equal(f.notices.length,2);assert.equal(f.notices[1].input.title,'Cirven · Task update');assert.doesNotMatch(f.notices[1].input.body,/[一-龥]/);
    assert.equal(f.opened.length,0);assert.doesNotMatch(f.notices[1].input.body,/PRIVATE/);
  }finally{f.owner.close();}
});

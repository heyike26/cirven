'use strict';
const fs=require('node:fs'),path=require('node:path'),{createHash,randomUUID}=require('node:crypto');
const {DISPLAY_NAME}=require('./product-brand.cjs');
const {t,resolveLocale}=require('../../packages/i18n/index.cjs');
const CATEGORIES=Object.freeze({WAITING_USER:'有任务等待你的回答',EXECUTION_ERROR:'有任务执行出错',NEEDS_CHECK:'有任务需要核查',
  TIMED_OUT:'有任务已明确超时',OBSERVATION_UNAVAILABLE:'任务状态暂无法读取',EXECUTION_RETURNED:'有任务已返回结果',CANCELLED:'有任务已取消'});
const PRIORITY=Object.keys(CATEGORIES),SHA=/^[a-f0-9]{64}$/;
function canonical(value){return JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);}
const digest=value=>createHash('sha256').update(typeof value==='string'?value:canonical(value)).digest('hex');
const text=value=>typeof value==='string'&&value.length>0&&value.length<=200&&!/[\x00-\x1f]/.test(value);
function taskKey(value){
  if(value===null)return null;
  if(!value||!text(value.kind)||!text(value.object_id)||!(value.project_id===null||text(value.project_id))||
      !(value.operation_id===null||text(value.operation_id))||!Number.isSafeInteger(value.generation)||value.generation<0)throw Error('Invalid supervisor task identity');
  return {kind:value.kind,project_id:value.project_id,object_id:value.object_id,operation_id:value.operation_id,generation:value.generation};
}
function alert(value){
  const ref=value?.alert_ref;
  if(!ref||!text(ref.id)||!Number.isSafeInteger(ref.revision)||ref.revision<1||!SHA.test(ref.sha256)||
      ref.id!==value.id||ref.revision!==value.revision||ref.sha256!==value.sha256||typeof value.acknowledged!=='boolean'||
      typeof value.terminal!=='boolean'||!['TASK_STATE_CHANGED','OBSERVER_STATE_CHANGED'].includes(value.kind))throw Error('Invalid exact supervisor alert');
  const key=taskKey(value.key);
  return {key,alert_ref:{id:ref.id,revision:ref.revision,sha256:ref.sha256},category:value.category,
    acknowledged:value.acknowledged,id:digest([key,ref])};
}

class NotificationJournal{
  constructor(filename,io=fs){
    if(!path.isAbsolute(filename))throw Error('Notification journal requires an explicit absolute local path');
    this.path=filename;this.io=io;this.entries={};this.revision=0;this.expected=null;
    io.mkdirSync(path.dirname(filename),{recursive:true});const raw=this.read();
    if(raw!==null){const data=JSON.parse(raw);
      if(data.schema!=='eeas.task-notifications.v1'||!Number.isSafeInteger(data.revision)||data.revision<0||
          !data.entries||Array.isArray(data.entries)||Object.keys(data.entries).length>2048||
          Object.entries(data.entries).some(([key,value])=>!SHA.test(key)||!value||typeof value.status!=='string'||!Number.isFinite(value.at))||
          data.sha256!==digest({schema:data.schema,revision:data.revision,entries:data.entries}))throw Error('Notification journal integrity failed');
      this.entries=data.entries;this.revision=data.revision;this.expected=digest(raw);
    }
  }
  read(){
    if(!this.io.existsSync(this.path))return null;
    if(this.io.lstatSync(this.path).isSymbolicLink())throw Error('Notification journal cannot be a symbolic link');
    const raw=this.io.readFileSync(this.path,'utf8');if(Buffer.byteLength(raw)>512*1024)throw Error('Notification journal is too large');return raw;
  }
  commit(changes){
    if(!changes.length)return;
    const lock=this.path+'.lock',temporary=this.path+'.'+randomUUID()+'.next';let held=false,fd;
    try{
      fd=this.io.openSync(lock,'wx',0o600);held=true;this.io.closeSync(fd);fd=undefined;
      const raw=this.read();if((raw===null?null:digest(raw))!==this.expected)throw Error('Notification journal CAS changed');
      const entries={...this.entries};for(const [key,value]of changes)entries[key]=value;
      if(Object.keys(entries).length>2048)throw Error('Notification journal capacity reached; history was not evicted');
      const next={schema:'eeas.task-notifications.v1',revision:this.revision+1,entries};next.sha256=digest(next);
      const bytes=canonical(next)+'\n';fd=this.io.openSync(temporary,'wx',0o600);this.io.writeFileSync(fd,bytes,'utf8');this.io.fsyncSync(fd);this.io.closeSync(fd);fd=undefined;
      this.io.renameSync(temporary,this.path);this.entries=entries;this.revision=next.revision;this.expected=digest(bytes);
    }finally{
      if(fd!==undefined)this.io.closeSync(fd);
      if(held){if(this.io.existsSync(temporary))this.io.unlinkSync(temporary);this.io.unlinkSync(lock);}
    }
  }
}

class TaskNotifications{
  constructor({Notification,journalPath,locale='zh-CN',isActive=()=>false,getVisibleTask=()=>null,onOpenTask,onError=()=>{},clock,mergeMs=400,deliveryMs=8000,io}){
    if(typeof Notification!=='function'||typeof onOpenTask!=='function')throw Error('Native task notifications require explicit host callbacks');
    Object.assign(this,{Notification,isActive,getVisibleTask,onOpenTask,onError});this.locale=resolveLocale(locale,locale);
    this.clock=clock??{now:()=>Date.now(),setTimeout,clearTimeout};this.mergeMs=Math.max(100,Math.min(3000,mergeMs));this.deliveryMs=Math.max(1000,Math.min(30000,deliveryMs));
    this.pending=new Map();this.records=new Map();this.current=new Map();this.local=new Set();this.quietPending=new Map();this.baselinePending=false;this.primed=false;this.owned=new Set();this.timers=new Set();this.timer=null;this.closed=false;this.enabled=false;this.supported=null;this.journalStatus='READY';
    try{this.journal=new NotificationJournal(journalPath,io);}catch(error){this.journalStatus='FAILED';this.error(error);}
  }
  setLocale(locale){this.locale=resolveLocale(locale,locale);return this.get();}
  now(){return typeof this.clock.now==='function'?this.clock.now():this.clock.now;}
  error(error){try{Promise.resolve(this.onError(error)).catch(()=>{});}catch{}}
  save(changes){
    if(!this.journal)return false;
    try{this.journal.commit(changes);this.journalStatus='READY';return true;}catch(error){if(this.journalStatus!=='FAILED')this.error(error);this.journalStatus='FAILED';return false;}
  }
  localOnly(item){
    try{if(!item.key||!this.isActive())return false;
      const visible=this.getVisibleTask();return !!visible&&canonical(taskKey(visible))===canonical(item.key);
    }catch{this.error(Error('Visible task identity could not be checked'));return false;}
  }
  prime(snapshot){return this.observe(snapshot,{baseline:true});}
  observe(snapshot,{baseline=false}={}){
    if(this.closed)return this.get();
    if(typeof baseline!=='boolean')throw Error('Explicit notification baseline mode required');
    if(baseline)this.baselinePending=true;
    baseline=baseline||this.baselinePending;
    this.enabled=snapshot?.settings?.enabled!==false&&snapshot?.settings?.notifications_enabled===true;
    if(!Array.isArray(snapshot?.alerts)||snapshot.alerts.length>2048)throw Error('Bounded supervisor alerts are required');
    const current=new Map();
    for(const raw of snapshot.alerts){
      let item;try{item=alert(raw);}catch{this.error(Error('Supervisor alert identity was rejected'));continue;}
      current.set(item.id,item);
      if(item.acknowledged){this.pending.delete(item.id);if(this.journal?.entries[item.id]?.status!=='ACKNOWLEDGED')this.quietPending.set(item.id,{status:'ACKNOWLEDGED',at:this.now()});continue;}
      if(baseline||!this.enabled){
        this.pending.delete(item.id);
        if(!this.journal?.entries[item.id]&&!this.records.has(item.id)){
          const status=baseline?'BASELINED':'LOCAL_ONLY';this.quietPending.set(item.id,{status,at:this.now()});
          this.records.set(item.id,{key:item.key,alert_ref:item.alert_ref,status});
        }
        continue;
      }
      if(!Object.hasOwn(CATEGORIES,item.category))continue;
      const saved=this.journal?.entries[item.id];
      if(saved){if(!this.records.has(item.id))this.records.set(item.id,{key:item.key,alert_ref:item.alert_ref,status:saved.status,historical:true});continue;}
      if(this.records.has(item.id))continue;
      this.pending.set(item.id,item);
    }
    this.current=current;this.local=new Set(current.keys());
    // Keep quiet decisions in memory until their CAS succeeds. Turning the
    // setting back on must not replay a batch whose first disk write failed.
    const quietSaved=this.save([...this.quietPending]);
    if(quietSaved){this.quietPending.clear();if(baseline){this.baselinePending=false;this.primed=true;}}
    for(const id of this.pending.keys())if(!current.has(id))this.pending.delete(id);
    if(baseline||!this.enabled||!quietSaved){if(this.timer!==null)this.clock.clearTimeout(this.timer);this.timer=null;this.pending.clear();if(baseline||!this.enabled)this.dismissOwned();return this.get();}
    if(this.pending.size&&this.timer===null){this.timer=this.clock.setTimeout(()=>{this.timer=null;this.flush();},this.mergeMs);}
    return this.get();
  }
  flush(){
    if(this.closed||!this.enabled)return;
    const items=[...this.pending.values()].filter(item=>!this.current.get(item.id)?.acknowledged);this.pending.clear();if(!items.length)return;
    const local=items.filter(item=>this.localOnly(item)),remote=items.filter(item=>!local.includes(item));
    const changes=local.map(item=>[item.id,{status:'LOCAL_ONLY',at:this.now()}]);
    try{this.supported=this.Notification.isSupported();}catch{this.supported=null;}
    const status=this.supported===true?'REQUESTED':this.supported===false?'UNSUPPORTED':'SUPPORT_UNKNOWN';
    for(const item of remote)changes.push([item.id,{status,at:this.now()}]);
    // Durable admission precedes the sole show() attempt. Even a crash between
    // admission and show must not produce duplicate toasts on the next poll.
    if(!this.save(changes))return;
    for(const item of local)this.records.set(item.id,{key:item.key,alert_ref:item.alert_ref,status:'LOCAL_ONLY'});
    for(const item of remote)this.records.set(item.id,{key:item.key,alert_ref:item.alert_ref,status});
    if(!remote.length||this.supported!==true)return;
    remote.sort((a,b)=>PRIORITY.indexOf(a.category)-PRIORITY.indexOf(b.category));
    const target=()=>JSON.parse(canonical({key:remote[0].key,alert_ref:remote[0].alert_ref,related:remote.map(item=>({key:item.key,alert_ref:item.alert_ref}))}));
    const body=remote.length===1?t('host',CATEGORIES[remote[0].category],this.locale)+t('host','。点击查看准确任务。',this.locale):t('host','{count} 项任务有需要关注的更新。点击查看本次任务。',this.locale,{count:remote.length});
    let notice,delivery,opened=false;
    const record=status=>{
      if(this.closed)return;
      for(const item of remote)this.records.set(item.id,{...this.records.get(item.id),status});
      this.save(remote.map(item=>[item.id,{status,at:this.now()}]));
    };
    try{
      notice=new this.Notification({title:t('common','notification.title',this.locale,{product:DISPLAY_NAME}),body,silent:true,urgency:'normal',timeoutType:'default'});this.owned.add(notice);
      notice.on('show',()=>{if(delivery!==undefined){this.clock.clearTimeout(delivery);this.timers.delete(delivery);}record('SYSTEM_SHOWN');});
      notice.on('failed',()=>{if(delivery!==undefined){this.clock.clearTimeout(delivery);this.timers.delete(delivery);}record('DISPLAY_FAILED');});
      notice.on('click',()=>{if(this.closed||!this.enabled||!this.owned.has(notice)||opened)return;opened=true;record('CLICKED');try{Promise.resolve(this.onOpenTask(target())).catch(error=>this.error(error));}catch(error){this.error(error);}});
      // Keep this exact object until close(): a timeout/close event can leave a
      // Windows toast in Action Center, where our object can still remove it.
      delivery=this.clock.setTimeout(()=>{this.timers.delete(delivery);record('DELIVERY_UNCONFIRMED');},this.deliveryMs);this.timers.add(delivery);
      notice.show();
    }catch{if(delivery!==undefined){this.clock.clearTimeout(delivery);this.timers.delete(delivery);}record('DISPLAY_FAILED');}
  }
  get(){return JSON.parse(canonical({locale:this.locale,enabled:this.enabled,closed:this.closed,supported:this.supported,journal_status:this.journalStatus,
    local_count:this.local.size,pending_count:this.pending.size,baseline_status:this.baselinePending?'PENDING':this.primed?'READY':'NOT_PRIMED',records:[...this.records.values()],
    system_blocking:'NOT_OBSERVABLE',native_windows_qualification:'NOT_RUN',show_is_delivery_confirmation:false}));}
  dismissOwned(){
    for(const timer of this.timers)this.clock.clearTimeout(timer);this.timers.clear();
    for(const notice of this.owned){try{notice.close();}catch{}}this.owned.clear();
  }
  close(){
    if(this.closed)return;this.closed=true;if(this.timer!==null)this.clock.clearTimeout(this.timer);this.timer=null;this.pending.clear();
    this.dismissOwned();
  }
}
const createTaskNotifications=options=>new TaskNotifications(options);
module.exports={createTaskNotifications,TaskNotifications,taskKey};

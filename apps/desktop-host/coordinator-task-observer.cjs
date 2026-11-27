'use strict';
const {ownedSupervisorOperation}=require('./supervisor-host-integration.cjs');

const ACTIVE=new Set(['CLAIMED','UNKNOWN','PROCESSING','REQUESTING','RUNNING']);
const RUNNING=new Set(['CLAIMED','PROCESSING','REQUESTING','RUNNING','QUEUED','PENDING','ACCEPTED','CANCEL_REQUESTED']);
const LIMIT=128,CONCURRENCY=2,BASIS_LIMIT=16;
const candidate=task=>task?.key?.kind==='native_message'&&task.key.project_id==null&&
  Number.isSafeInteger(task.key.generation)&&task.key.generation>=0&&
  task.observation_status==='CURRENT'&&typeof task.source_status==='string'&&!RUNNING.has(task.source_status);
const witness=task=>JSON.stringify([task.key.generation,task.source_ref.sha256]);
const copyTask=task=>({key:{...task.key},source_status:task.source_status,source_ref:{...task.source_ref},observation_status:task.observation_status});

// TaskActivity owns wakeups. This observer only inspects existing operations.
class CoordinatorTaskObserver {
  constructor({store,onError=()=>{}}){
    if(typeof store?.snapshot!=='function'||typeof store.inspect!=='function'||typeof onError!=='function')throw new Error('COORDINATOR_OBSERVER_OPTIONS_INVALID');
    this.store=store;this.onError=onError;this.closed=false;this.entries=new Map();this.queue=new Map();this.flights=new Map();this.closePending=null;
  }
  get(){return {closed:this.closed,active:this.flights.size,queued:this.queue.size,tracked:this.entries.size};}
  _error(error){try{this.onError(error);}catch{}}
  observe(snapshot){
    if(this.closed)return this.get();
    if(!snapshot||!Array.isArray(snapshot.tasks)||snapshot.tasks.length>10000)throw new Error('COORDINATOR_OBSERVER_SNAPSHOT_INVALID');
    const state=this.store.snapshot(),cached={snapshot:()=>state};
    if(!Array.isArray(state?.operations)||state.operations.length>LIMIT)throw new Error('COORDINATOR_OBSERVER_STORE_INVALID');
    for(const task of snapshot.tasks){
      if(!candidate(task))continue;
      const operation=ownedSupervisorOperation(cached,task);
      if(!operation||!ACTIVE.has(operation.status))continue;
      const id=operation.operation_id,basis=witness(task);let entry=this.entries.get(id);
      if(!entry){if(this.entries.size>=LIMIT)continue;entry={seen:new Set(),desired:null};this.entries.set(id,entry);}
      if(entry.seen.has(basis)||entry.desired?.basis===basis)continue;
      if(entry.seen.size>=BASIS_LIMIT){
        if(!entry.blocked){entry.blocked=true;this._error(Object.assign(new Error('COORDINATOR_OBSERVER_BASIS_LIMIT'),{code:'COORDINATOR_OBSERVER_BASIS_LIMIT'}));}
        continue;
      }
      entry.desired={basis,task:copyTask(task)};
      if(!this.flights.has(id))this.queue.set(id,entry);
    }
    this._pump();return this.get();
  }
  _pump(){
    while(!this.closed&&this.flights.size<CONCURRENCY&&this.queue.size){
      const [id,entry]=this.queue.entries().next().value;this.queue.delete(id);
      const desired=entry.desired;entry.desired=null;
      if(!desired||entry.seen.has(desired.basis)||entry.seen.size>=BASIS_LIMIT)continue;
      let operation;
      try{operation=ownedSupervisorOperation(this.store,desired.task);}catch(error){this._error(error);continue;}
      if(!operation||!ACTIVE.has(operation.status))continue;
      entry.seen.add(desired.basis);
      let resolve,reject;
      const pending=new Promise((yes,no)=>{resolve=yes;reject=no;});
      const flight=pending.catch(error=>{this._error(error);}).finally(()=>{
        this.flights.delete(id);
        if(!this.closed&&entry.desired)this.queue.set(id,entry);
        this._pump();
      });
      this.flights.set(id,flight);
      try{Promise.resolve(this.store.inspect(id)).then(resolve,reject);}catch(error){reject(error);}
    }
  }
  async flush(){while(this.flights.size)await Promise.allSettled([...this.flights.values()]);}
  close(){
    if(this.closePending)return this.closePending;
    this.closed=true;this.queue.clear();for(const entry of this.entries.values())entry.desired=null;
    this.closePending=Promise.allSettled([...this.flights.values()]).then(()=>{});return this.closePending;
  }
}
module.exports={CoordinatorTaskObserver};

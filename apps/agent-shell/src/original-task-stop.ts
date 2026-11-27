import {operationId} from './api.ts';
import {agentSessionsApi,originalSessionCanStop,type AgentSession,type AgentSessionOperation} from './agent-sessions.ts';
import type {ControlLocks} from './original-pending-controls.ts';

export type TaskStopStorage=Pick<Storage,'getItem'|'setItem'>;
export type TaskStopClient=Pick<typeof agentSessionsApi,'interruptTask'|'operation'|'get'>;
export type TaskStopAttempt={version:1;scope:string;target:string;key:string;status:string;acknowledged:boolean;targetStatus?:string};
const running=new Set<string>();
const scope=(s:AgentSession)=>JSON.stringify([s.project_id,[s.session_ref.id,s.session_ref.revision,s.session_ref.sha256],s.generation]);
export const taskStopStorageKey=(s:AgentSession,target:string)=>'eeas-original-task-stop-v1:'+JSON.stringify([scope(s),target]);

/** A persisted intent precedes the only POST. Re-entry and recovery only GET its key. */
export class OriginalTaskStops{
 private storage:TaskStopStorage;private client:TaskStopClient;private locks?:ControlLocks;
 constructor(storage:TaskStopStorage,client:TaskStopClient=agentSessionsApi,locks?:ControlLocks){this.storage=storage;this.client=client;this.locks=locks;}
 read(s:AgentSession,target:string):TaskStopAttempt|undefined{
  const raw=this.storage.getItem(taskStopStorageKey(s,target));if(raw===null)return;
  if(raw.length>16384)throw Error('停止记录暂未核实，未重复请求');
  const a=JSON.parse(raw) as TaskStopAttempt;
  if(a.version!==1||a.scope!==scope(s)||a.target!==target||typeof a.key!=='string'||!a.key||typeof a.status!=='string'||typeof a.acknowledged!=='boolean')throw Error('停止记录暂未核实，未重复请求');
  return a;
 }
 private save(s:AgentSession,a:TaskStopAttempt){this.storage.setItem(taskStopStorageKey(s,a.target),JSON.stringify(a));}
 private async exclusive<T>(s:AgentSession,target:string,work:()=>Promise<T>){
  const key=taskStopStorageKey(s,target);
  const run=async()=>{if(running.has(key))throw Error('正在核查原停止请求');running.add(key);try{return await work();}finally{running.delete(key);}};
  return this.locks?this.locks.request(key,{mode:'exclusive',signal:new AbortController().signal},run):run();
 }
 async stop(s:AgentSession,target:string){
  return this.exclusive(s,target,async()=>{
   if(this.read(s,target))return;
   if(!originalSessionCanStop(s)||s.active_operation_id!==target)throw Error('当前任务已变化，未请求停止');
   const a:TaskStopAttempt={version:1,scope:scope(s),target,key:operationId(),status:'ATTEMPTED',acknowledged:false};
   this.save(s,a);
   let result:AgentSessionOperation;
   try{result=await this.client.interruptTask(s,target,a.key);}
   catch(error){
    // Even a failed result observation must retain ATTEMPTED if storage fails.
    this.save(s,{...a,status:'UNKNOWN_OUTCOME'});throw error;
   }
   this.save(s,{...a,status:result.status,acknowledged:result.original_abort_acknowledged===true});
  });
 }
 async inspect(s:AgentSession,target:string){
  return this.exclusive(s,target,async()=>{
   const a=this.read(s,target);if(!a)throw Error('停止记录暂未核实，未重复请求');
   const control=await this.client.operation(s,a.key);
   // The session read observes native idle and settles its original task receipt.
   const session=await this.client.get(s.project_id,s.session_ref);
   const task=await this.client.operation(s,target);
   this.save(s,{...a,status:control.status,acknowledged:control.original_abort_acknowledged===true,targetStatus:task.status});
   return session;
  });
 }
}

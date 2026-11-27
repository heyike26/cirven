import type { Project, Workspace } from './contracts';

export type FolderStage = 'CREATING' | 'PROJECT_SAVED' | 'BINDING' | 'CONNECTED' | 'UNKNOWN';
type Phase = 'CREATE_PROJECT' | 'BIND_WORKSPACE';
type FolderRequest = { target:'NEW'|'CURRENT'; name:string; path:string; writable:boolean; current_project_id:string|null };
export type FolderConnectionIntent = {
  operation_id:string; project_operation_id:string; binding_operation_id:string; fingerprint:string;
  request:FolderRequest; stage:FolderStage; project?:Project; workspace?:Workspace;
  project_attempted:boolean; binding_attempted:boolean; unknown_stage?:Phase;
  local_persistence_status:'SAVED'|'FAILED'; reason?:string;
  /** CONNECTED records historical submission; cached availability is never authority. */
  current_binding_status?:'NOT_CHECKED'|'AVAILABLE'|'UNAVAILABLE'; current_binding_reason?:string;
};
type WorkspaceReceipt = Workspace & {request_sha256?:string};
export type FolderConnectionClient = {
  /** Write methods must disable automatic unauthorized POST replay. */
  createProject(name:string,operationId:string):Promise<Project>;
  projectOperation(operationId:string):Promise<{operation_id:string;request_sha256:string;project:Project}>;
  bindWorkspace(projectId:string,path:string,writable:boolean,key:string):Promise<Workspace>;
  workspaceBindingOperation(projectId:string,key:string):Promise<{operation_id:string;workspace:WorkspaceReceipt}>;
};
type Journal = {schema:'eeas.folder-connections.v1'; records:FolderConnectionIntent[]; active:string|null};
type StorageLike = Pick<Storage,'getItem'|'setItem'>;
export type FolderLockStatus = {status:'IDLE'|'WAITING'|'HELD'|'UNAVAILABLE'|'TIMED_OUT';reason?:string};
type FolderOptions = {storageKey?:string;operationId?:()=>string;locks?:Pick<LockManager,'request'>|null;lockWaitMs?:number};
const id=(value:unknown):value is string=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value);
const hash=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const clone=<T>(value:T):T=>JSON.parse(JSON.stringify(value));
function canonical(value:unknown):string {
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
  if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical((value as Record<string,unknown>)[key])).join(',')+'}';
  return JSON.stringify(value);
}
async function digest(value:unknown):Promise<string>{return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(value))))].map(v=>v.toString(16).padStart(2,'0')).join('');}
function absolute(value:string):boolean{return !!value&&!/[\x00-\x1f]/.test(value)&&(value.startsWith('/')||/^[A-Za-z]:[\\/]/.test(value)||/^\\\\[^\\]+\\[^\\]+(?:\\|$)/.test(value))&&!value.split(/[\\/]/).includes('..');}
const comparable=(path:string)=>path.replace(/[\\/]+$/,'')||'/';
function project(value:unknown,name:string):Project {
  const p=value as Project|undefined;
  if(!p||!id(p.id)||p.name!==name||p.description!==undefined&&p.description!=='')throw new Error('项目回执与原连接要求不一致；请核查原操作');
  return clone(p);
}
function readJournal(raw:string|null):Journal {
  if(raw===null)return {schema:'eeas.folder-connections.v1',records:[],active:null};
  let saved:Journal;try{saved=JSON.parse(raw);}catch{throw new Error('目录连接记录无法读取，原记录保留；未发送请求');}
  if(!saved||typeof saved!=='object'||saved.schema!=='eeas.folder-connections.v1'||!Array.isArray(saved.records)||saved.records.length>32||saved.active!==null&&!id(saved.active))throw new Error('目录连接记录格式不一致，未发送请求');
  const seen=new Set<string>();
  for(const row of saved.records){
    if(!row||!id(row.operation_id)||seen.has(row.operation_id)||row.project_operation_id!==row.operation_id+'_project'||row.binding_operation_id!==row.operation_id+'_binding'||!hash(row.fingerprint)||
      !['CREATING','PROJECT_SAVED','BINDING','CONNECTED','UNKNOWN'].includes(row.stage)||typeof row.project_attempted!=='boolean'||typeof row.binding_attempted!=='boolean'||!row.request||
      !['NEW','CURRENT'].includes(row.request.target)||typeof row.request.name!=='string'||!row.request.name||typeof row.request.path!=='string'||!absolute(row.request.path)||typeof row.request.writable!=='boolean'||
      !['SAVED','FAILED'].includes(row.local_persistence_status))throw new Error('目录连接记录不完整，原记录保留；未发送请求');
    seen.add(row.operation_id);
    if(row.stage==='CREATING'&&row.project_attempted||row.stage==='BINDING'&&row.binding_attempted){row.unknown_stage=row.stage==='CREATING'?'CREATE_PROJECT':'BIND_WORKSPACE';row.stage='UNKNOWN';row.reason='上次目录连接中断；先核查原操作，不会重新创建';}
    row.current_binding_status='NOT_CHECKED';delete row.current_binding_reason;
  }
  if(saved.active!==null&&!seen.has(saved.active))throw new Error('目录连接当前操作缺失，未发送请求');
  return saved;
}

/** A durable two-step connection intent. It never creates directories or switches UI scope. */
export class FolderConnection {
  private readonly client:FolderConnectionClient; private readonly storage:StorageLike;
  private readonly key:string; private readonly makeId:()=>string;
  private readonly locks:Pick<LockManager,'request'>|null; private readonly lockWaitMs:number;
  private lockState:FolderLockStatus={status:'IDLE'};
  private heldLocks=0; private waitingLocks=0;
  private journal:Journal; private raw:string|null; private running=new Map<string,Promise<FolderConnectionIntent>>();
  constructor(client:FolderConnectionClient,storage:StorageLike,options:FolderOptions={}){
    this.client=client;this.storage=storage;this.key=options.storageKey??'eeas-folder-connections:v1';this.makeId=options.operationId??(()=>crypto.randomUUID());
    this.locks=Object.prototype.hasOwnProperty.call(options,'locks')?options.locks??null:typeof navigator!=='undefined'?navigator.locks??null:null;
    this.lockWaitMs=options.lockWaitMs??5000;if(!Number.isSafeInteger(this.lockWaitMs)||this.lockWaitMs<1||this.lockWaitMs>30000)throw new Error('目录连接等待时间须为1至30000毫秒');
    // Synchronous construction is read-only, including interrupted operation recovery.
    this.raw=storage.getItem(this.key);this.journal=readJournal(this.raw);
  }
  lockStatus():FolderLockStatus{return this.heldLocks?{status:'HELD'}:this.waitingLocks?{status:'WAITING',reason:'正在等待另一窗口完成目录连接；不会重复提交'}:{...this.lockState};}
  current():FolderConnectionIntent|undefined{return this.journal.active?this.get(this.journal.active):undefined;}
  list():FolderConnectionIntent[]{return clone(this.journal.records);}
  get(operationId:string):FolderConnectionIntent|undefined{const value=this.journal.records.find(row=>row.operation_id===operationId);return value?clone(value):undefined;}
  private row(operationId=this.journal.active??''):FolderConnectionIntent{const row=this.journal.records.find(value=>value.operation_id===operationId);if(!row)throw new Error('请先确认目录连接要求');return row;}
  private async reload(){
    const raw=this.storage.getItem(this.key),saved=readJournal(raw);
    for(const row of saved.records)await this.verify(row);
    // A failed local write may hold a real server receipt newer than disk. Never
    // replace that fact with an older UNKNOWN, nor roll back attempted markers.
    for(const local of this.journal.records.filter(row=>row.local_persistence_status==='FAILED')){
      await this.verify(local);const index=saved.records.findIndex(row=>row.operation_id===local.operation_id),disk=saved.records[index];
      if(disk&&(disk.fingerprint!==local.fingerprint||canonical(disk.request)!==canonical(local.request)||
        disk.project&&local.project&&canonical(disk.project)!==canonical(local.project)||disk.workspace&&local.workspace&&canonical(disk.workspace)!==canonical(local.workspace)))throw new Error('原连接的已知回执与另一窗口冲突；内存结果和原记录保留，未发送请求');
      if(!disk){if(saved.records.length>=32)throw new Error('目录连接索引已满；未保存的已知结果仍保留，请先核查原操作');const retained=clone(local);retained.current_binding_status='NOT_CHECKED';delete retained.current_binding_reason;saved.records.push(retained);continue;}
      const merged={...disk,project:disk.project??local.project,workspace:disk.workspace??local.workspace,
        project_attempted:disk.project_attempted||local.project_attempted,binding_attempted:disk.binding_attempted||local.binding_attempted,
        local_persistence_status:'FAILED' as const,current_binding_status:'NOT_CHECKED' as const,reason:local.reason};
      delete merged.current_binding_reason;
      if(merged.workspace){merged.stage='CONNECTED';delete merged.unknown_stage;}
      else if(merged.project&&merged.binding_attempted){merged.stage='UNKNOWN';merged.unknown_stage='BIND_WORKSPACE';}
      else if(merged.project){merged.stage='PROJECT_SAVED';delete merged.unknown_stage;}
      else if(merged.project_attempted){merged.stage='UNKNOWN';merged.unknown_stage='CREATE_PROJECT';}
      else {merged.stage='CREATING';delete merged.unknown_stage;}
      saved.records[index]=merged;
    }
    if((raw===this.raw||saved.active===null)&&this.journal.active&&saved.records.some(row=>row.operation_id===this.journal.active))saved.active=this.journal.active;
    this.raw=raw;this.journal=saved;
  }
  private async locked<T>(work:()=>Promise<T>):Promise<T>{
    if(!this.locks||typeof this.locks.request!=='function'){this.lockState={status:'UNAVAILABLE',reason:'当前环境不支持跨窗口安全锁；未发送请求，请在桌面软件或受支持浏览器重试'};throw new Error(this.lockState.reason);}
    const abort=new AbortController();let acquired=false;
    this.waitingLocks++;
    this.lockState={status:'WAITING',reason:'正在等待另一窗口完成目录连接；不会重复提交'};
    const timeout=setTimeout(()=>abort.abort(),this.lockWaitMs);
    try{return await this.locks.request('eeas.folder-connections:'+this.key,{mode:'exclusive',signal:abort.signal},async lock=>{
      if(!lock||abort.signal.aborted)throw new Error('目录连接锁未取得；未发送请求');
      acquired=true;this.waitingLocks--;this.heldLocks++;clearTimeout(timeout);this.lockState={status:'HELD'};
      try{await this.reload();return await work();}finally{this.heldLocks--;this.lockState={status:'IDLE'};}
    });}catch(error){
      if(!acquired)this.waitingLocks--;
      if(!acquired&&abort.signal.aborted){this.lockState={status:'TIMED_OUT',reason:'另一窗口仍在处理目录连接；等待已结束，未发送请求，可稍后重新读取'};throw new Error(this.lockState.reason);}
      if(!acquired)this.lockState={status:'UNAVAILABLE',reason:'跨窗口安全锁暂不可用；未发送请求'};
      throw error;
    }finally{clearTimeout(timeout);}
  }
  /** Re-read another window's changes without requests or discarding unsaved receipts. */
  refresh():Promise<FolderConnectionIntent|undefined>{return this.locked(async()=>this.current());}
  private persist(){
    if(this.storage.getItem(this.key)!==this.raw)throw new Error('另一窗口已更新目录连接，原记录保留；请重新读取');
    const next=JSON.stringify(this.journal);this.storage.setItem(this.key,next);
    if(this.storage.getItem(this.key)!==next)throw new Error('目录连接记录未能保存，未发送后续请求');
    this.raw=next;
  }
  private save(row:FolderConnectionIntent):boolean{
    if(row.reason==='连接进度的本机保存未成功；已知结果保留，请勿重新创建'){if(row.stage==='UNKNOWN')row.reason='原连接结果尚未确认；只核查这个原操作，不会重新创建';else delete row.reason;}
    row.local_persistence_status='SAVED';try{this.persist();return true;}catch{row.local_persistence_status='FAILED';row.reason='连接进度的本机保存未成功；已知结果保留，请勿重新创建';return false;}
  }
  private async verify(row:FolderConnectionIntent){
    if(await digest(row.request)!==row.fingerprint||row.request.target==='NEW'&&row.request.current_project_id!==null||row.request.target==='CURRENT'&&(!id(row.request.current_project_id)||row.project?.id!==row.request.current_project_id))throw new Error('目录连接的原项目或要求已变化；未发送请求');
    if(row.project&&(!id(row.project.id)||row.project.name!==row.request.name))throw new Error('目录连接的项目回执不匹配，未发送请求');
  }
  prepare(target:'NEW'|'CURRENT',name:string,path:string,writable:boolean,currentProject?:Project):Promise<FolderConnectionIntent>{
    const selected=currentProject?clone(currentProject):undefined;
    return this.locked(()=>this.prepareLocked(target,name,path,writable,selected));
  }
  private async prepareLocked(target:'NEW'|'CURRENT',name:string,path:string,writable:boolean,currentProject?:Project):Promise<FolderConnectionIntent>{
    if(target!=='NEW'&&target!=='CURRENT'||typeof name!=='string'||typeof path!=='string'||typeof writable!=='boolean')throw new Error('请选择新项目或当前项目及实际文件夹');
    path=path.trim();name=name.trim();if(!absolute(path))throw new Error('请选择实际本机绝对目录，目录由系统选择窗口创建');
    if(target==='CURRENT'&&(!currentProject||!id(currentProject.id)||typeof currentProject.name!=='string'||!currentProject.name.trim()))throw new Error('关联当前项目需要明确的原项目');
    const request:FolderRequest={target,name:target==='CURRENT'?currentProject!.name:name,path,writable,current_project_id:target==='CURRENT'?currentProject!.id:null};
    if(!request.name||request.name.length>200)throw new Error('请输入不超过200字的工作名称');
    const fingerprint=await digest(request);let row=this.journal.records.find(value=>value.fingerprint===fingerprint);
    if(row){await this.verify(row);this.journal.active=row.operation_id;if(!this.save(row))throw new Error(row.reason);return clone(row);}
    if(this.journal.records.length>=32){
      // This is a local quick index, not the backend project/operation history.
      // Only durably connected records may roll off; unresolved intent stays.
      const completed=this.journal.records.filter(value=>value.stage==='CONNECTED'&&value.local_persistence_status==='SAVED');
      const oldest=completed.find(value=>value.operation_id!==this.journal.active)??completed[0];
      if(!oldest)throw new Error('已有32条未决目录连接，请先核查原操作；未覆盖记录或删除项目');
      this.journal.records=this.journal.records.filter(value=>value.operation_id!==oldest.operation_id);
    }
    const operationId=this.makeId();if(!id(operationId)||operationId.length>80||this.journal.records.some(value=>value.operation_id===operationId))throw new Error('目录连接操作编号无效');
    row={operation_id:operationId,project_operation_id:operationId+'_project',binding_operation_id:operationId+'_binding',fingerprint,request,
      stage:target==='NEW'?'CREATING':'PROJECT_SAVED',...(target==='CURRENT'?{project:clone(currentProject!)}:{}),project_attempted:false,binding_attempted:false,local_persistence_status:'SAVED',current_binding_status:'NOT_CHECKED'};
    this.journal.records.push(row);this.journal.active=operationId;if(!this.save(row))throw new Error(row.reason);return clone(row);
  }
  private exclusive(operationId:string,work:()=>Promise<FolderConnectionIntent>):Promise<FolderConnectionIntent>{
    const pending=this.running.get(operationId);if(pending)return pending;
    const result=work().finally(()=>{if(this.running.get(operationId)===result)this.running.delete(operationId);});this.running.set(operationId,result);return result;
  }
  private unknown(row:FolderConnectionIntent,phase:Phase,error:unknown){row.stage='UNKNOWN';row.unknown_stage=phase;row.reason='原连接结果尚未确认；只核查这个原操作，不会重新创建。'+(error instanceof Error?error.message.slice(0,300):'');this.save(row);return clone(row);}
  private async workspace(row:FolderConnectionIntent,value:WorkspaceReceipt,requireRequestHash=false):Promise<Workspace>{
    const expectedId=await digest({project_id:row.project!.id,key:row.binding_operation_id});
    if(!value||value.id!==expectedId||value.project_id!==row.project!.id||typeof value.root!=='string'||comparable(value.root)!==comparable(row.request.path)||
      !value.native_location||typeof value.native_location.directory!=='string'||comparable(value.native_location.directory)!==comparable(value.root)||
      !Array.isArray(value.allowed_write_paths)||canonical(value.allowed_write_paths)!==canonical(row.request.writable?['*']:[])||!Number.isSafeInteger(value.revision)||value.revision<1||!hash(value.binding_sha256)||
      (requireRequestHash||value.request_sha256!==undefined)&&value.request_sha256!==await digest({root:value.root,allowed_write_paths:value.allowed_write_paths}))throw new Error('目录绑定回执与原项目、路径或写范围不一致');
    return clone(value);
  }
  private async checkBinding(row:FolderConnectionIntent):Promise<FolderConnectionIntent>{
    try{
      if(!row.project||!row.workspace)throw new Error('原绑定的历史回执缺失');
      const receipt=await this.client.workspaceBindingOperation(row.project.id,row.binding_operation_id);
      if(receipt.operation_id!==row.binding_operation_id)throw new Error('目录原操作编号不一致');
      const actual=await this.workspace(row,receipt.workspace,true);
      if(actual.binding_sha256!==row.workspace.binding_sha256||actual.revision!==row.workspace.revision)throw new Error('原目录绑定版本已经变化，请重新确认');
      row.current_binding_status='AVAILABLE';delete row.current_binding_reason;
    }catch{row.current_binding_status='UNAVAILABLE';row.current_binding_reason='原连接已提交，但当前目录不可用或依据已变化；历史回执保留，请核对原目录';}
    this.save(row);return clone(row);
  }
  run(operationId=this.journal.active??''):Promise<FolderConnectionIntent>{return this.exclusive(operationId,async()=>{
    return this.locked(async()=>{
    const row=this.row(operationId);await this.verify(row);
    if(row.stage==='CONNECTED')return this.checkBinding(row);
    if(row.stage==='UNKNOWN'){if(row.local_persistence_status==='FAILED')this.save(row);return clone(row);}
    if(row.stage==='CREATING'){
      if(row.project_attempted)return this.unknown(row,'CREATE_PROJECT',new Error('原项目创建已经发送'));
      row.project_attempted=true;if(!this.save(row)){row.project_attempted=false;return clone(row);}
      let actual:Project;try{actual=project(await this.client.createProject(row.request.name,row.project_operation_id),row.request.name);}catch(error){return this.unknown(row,'CREATE_PROJECT',error);}
      row.project=actual;row.stage='PROJECT_SAVED';delete row.reason;if(!this.save(row))return clone(row);
    }
    if(row.stage==='PROJECT_SAVED'){
      if(!row.project)throw new Error('原项目回执尚未确认');
      if(row.binding_attempted)return this.unknown(row,'BIND_WORKSPACE',new Error('原目录绑定已经发送'));
      row.stage='BINDING';row.binding_attempted=true;if(!this.save(row)){row.stage='PROJECT_SAVED';row.binding_attempted=false;return clone(row);}
      let binding:Workspace;try{binding=await this.workspace(row,await this.client.bindWorkspace(row.project.id,row.request.path,row.request.writable,row.binding_operation_id));}catch(error){return this.unknown(row,'BIND_WORKSPACE',error);}
      row.workspace=binding;row.stage='CONNECTED';row.current_binding_status='AVAILABLE';delete row.current_binding_reason;delete row.reason;this.save(row);
    }
    return clone(row);
    });
  });}
  inspect(operationId=this.journal.active??''):Promise<FolderConnectionIntent>{return this.exclusive(operationId,async()=>{
    return this.locked(async()=>{
    const row=this.row(operationId);await this.verify(row);if(row.stage==='CONNECTED')return this.checkBinding(row);if(row.stage!=='UNKNOWN'){if(row.local_persistence_status==='FAILED')this.save(row);return clone(row);}
    const phase=row.unknown_stage;
    try{
      if(phase==='CREATE_PROJECT'){
        const receipt=await this.client.projectOperation(row.project_operation_id);
        if(receipt.operation_id!==row.project_operation_id||receipt.request_sha256!==await digest({name:row.request.name,description:''}))throw new Error('项目原操作回执与提交内容不一致');
        row.project=project(receipt.project,row.request.name);row.stage='PROJECT_SAVED';
      }else if(phase==='BIND_WORKSPACE'){
        if(!row.project)throw new Error('原目录缺少已确认项目');
        const receipt=await this.client.workspaceBindingOperation(row.project.id,row.binding_operation_id);
        if(receipt.operation_id!==row.binding_operation_id)throw new Error('目录原操作编号不一致');
        row.workspace=await this.workspace(row,receipt.workspace,true);row.stage='CONNECTED';row.current_binding_status='AVAILABLE';delete row.current_binding_reason;
      }else throw new Error('原操作阶段缺失');
      delete row.unknown_stage;delete row.reason;this.save(row);return clone(row);
    }catch(error){return this.unknown(row,phase??'CREATE_PROJECT',error);}
    });
  });}
}

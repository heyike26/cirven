import {api, ApiError, type ApiClient} from './api.ts';

export interface SupervisorRef {id:string;revision:number;sha256:string}
export interface SupervisorSettings {enabled:boolean;notifications_enabled:boolean;interval_seconds:number;revision:number;sha256:string}
export interface SupervisorTaskKey {kind:string;project_id?:string|null;object_id:string;operation_id?:string|null;generation:number}
export type SupervisorClassification='RUNNING'|'WAITING_USER'|'EXECUTION_RETURNED'|'EXECUTION_ERROR'|'TIMED_OUT'|'CANCEL_REQUESTED'|'CANCELLED'|'NEEDS_CHECK';
export interface SupervisorDisplayContext {project_label?:string;workspace_label?:string;conversation_created_at?:string;task_created_at?:string}
export interface SupervisorTask {
  key:SupervisorTaskKey;label:string;source_status:string;observed_at:string;actual_error_code:string|null;
  execution_returned:boolean|null;validation_status:'NOT_OBSERVED'|'NOT_RUN'|'PENDING'|'PASSED'|'FAILED';
  wait_reason:'USER_INPUT'|'PERMISSION'|null;coverage:'SOURCE_SNAPSHOT'|'PARTIAL'|'UNKNOWN';source_ref?:SupervisorRef;
  fresh_for_seconds:number;classification:SupervisorClassification;observation_status:'CURRENT'|'STALE'|'NEEDS_CHECK_AFTER_RESTART';last_checked_at:string|null;
  display_context?:SupervisorDisplayContext;
}
export interface SupervisorAlert extends SupervisorRef {
  alert_ref:SupervisorRef;kind:'TASK_STATE_CHANGED'|'OBSERVER_STATE_CHANGED';key:SupervisorTaskKey|null;label:string;
  category:SupervisorClassification|'OBSERVATION_UNAVAILABLE';source_status:string|null;actual_error_code:string|null;created_at:string;terminal:boolean;
  acknowledged:boolean;acknowledged_at:string|null;
  validation_status?:SupervisorTask['validation_status'];observation_status?:SupervisorTask['observation_status'];
}
export interface SupervisorSnapshot {
  settings:SupervisorSettings;observer:{status:'INITIAL'|'READY'|'PARTIAL'|'OBSERVATION_UNAVAILABLE'|'DISABLED'|'CLOSED';last_attempt_at:string|null;
    last_success_at:string|null;actual_error_code:string|null;running:boolean;unavailable_codes?:string[]};
  tasks:SupervisorTask[];alerts:SupervisorAlert[];alerts_total:number;alerts_has_more?:boolean;coverage:'ONLY_COLLECTED_EXISTING_TASKS';
}
export interface SupervisorDraft {enabled:boolean;notifications_enabled:boolean;interval_seconds:string}
export interface SupervisorMutation {outcome:'ACKNOWLEDGED'|'OBSERVED_MATCH'|'UNKNOWN'|'REJECTED';snapshot?:SupervisorSnapshot;settings?:SupervisorSettings;error_code?:string}
const classifications:SupervisorClassification[]=['RUNNING','WAITING_USER','EXECUTION_RETURNED','EXECUTION_ERROR','TIMED_OUT','CANCEL_REQUESTED','CANCELLED','NEEDS_CHECK'];
const clone=<T>(value:T):T=>JSON.parse(JSON.stringify(value));
function invalid():never{throw new Error('SUPERVISOR_RESPONSE_INVALID');}
function object(raw:unknown):Record<string,unknown>{if(!raw||typeof raw!=='object'||Array.isArray(raw))return invalid();return raw as Record<string,unknown>;}
function string(raw:unknown,limit=200):string {if(typeof raw!=='string'||[...raw].length>limit||/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(raw))return invalid();return raw;}
function id(raw:unknown,limit=200):string{const value=string(raw,limit);if(!/^[A-Za-z0-9_-]+$/.test(value))return invalid();return value;}
function label(raw:unknown):string{const value=string(raw);if(!value.trim()||/[\u0000-\u001f]/.test(value)||/(?:bearer\s+\S+|sk-[a-z0-9_-]{12,}|(?:api[_-]?key|password|access[_-]?token)\s*[:=])/i.test(value))return invalid();return value;}
function integer(raw:unknown,min=0,max=Number.MAX_SAFE_INTEGER):number{if(typeof raw!=='number'||!Number.isSafeInteger(raw)||raw<min||raw>max)return invalid();return raw;}
function boolean(raw:unknown):boolean{if(typeof raw!=='boolean')return invalid();return raw;}
function date(raw:unknown):string{const value=string(raw,64);if(!/(Z|[+-]\d{2}:\d{2})$/.test(value)||!Number.isFinite(Date.parse(value)))return invalid();return value;}
function maybeDate(raw:unknown):string|null{return raw==null?null:date(raw);}
function code(raw:unknown):string|null{if(raw==null)return null;const value=string(raw,120);return /^[A-Za-z0-9_.:-]+$/.test(value)?value:invalid();}
function choice<T extends string>(raw:unknown,values:readonly T[]):T{if(typeof raw!=='string'||!values.includes(raw as T))return invalid();return raw as T;}
function ref(raw:unknown):SupervisorRef{const v=object(raw),sha=string(v.sha256,64);if(!/^[a-f0-9]{64}$/.test(sha))return invalid();return {id:id(v.id),revision:integer(v.revision,1),sha256:sha};}
function key(raw:unknown):SupervisorTaskKey{const v=object(raw),kind=string(v.kind,64);if(!/^[A-Za-z][A-Za-z0-9_-]*$/.test(kind))return invalid();return {kind,object_id:id(v.object_id),generation:integer(v.generation),
  ...Object.hasOwn(v,'project_id')?{project_id:v.project_id==null?null:id(v.project_id,100)}:{},
  ...Object.hasOwn(v,'operation_id')?{operation_id:v.operation_id==null?null:id(v.operation_id,100)}:{}};}
function displayContext(raw:unknown):SupervisorDisplayContext{
  const v=object(raw);return {...v.project_label!=null?{project_label:label(v.project_label)}:{},
    ...v.workspace_label!=null?{workspace_label:label(v.workspace_label)}:{},
    ...v.conversation_created_at!=null?{conversation_created_at:date(v.conversation_created_at)}:{},
    ...v.task_created_at!=null?{task_created_at:date(v.task_created_at)}:{}};
}
export function supervisorSettings(raw:unknown):SupervisorSettings{const v=object(raw),r=ref({id:'settings',revision:v.revision,sha256:v.sha256});return {
  enabled:boolean(v.enabled),notifications_enabled:boolean(v.notifications_enabled),interval_seconds:integer(v.interval_seconds,5,3600),revision:r.revision,sha256:r.sha256};}
export function supervisorSnapshot(raw:unknown):SupervisorSnapshot{
  const v=object(raw),o=object(v.observer);if(!Array.isArray(v.tasks)||v.tasks.length>10000||!Array.isArray(v.alerts)||v.alerts.length>1000)return invalid();
  const tasks=v.tasks.map(raw=>{const t=object(raw);return {key:key(t.key),label:label(t.label),source_status:string(t.source_status,120),observed_at:date(t.observed_at),
    actual_error_code:code(t.actual_error_code),execution_returned:t.execution_returned==null?null:boolean(t.execution_returned),
    validation_status:choice(t.validation_status,['NOT_OBSERVED','NOT_RUN','PENDING','PASSED','FAILED'] as const),
    wait_reason:t.wait_reason==null?null:choice(t.wait_reason,['USER_INPUT','PERMISSION'] as const),coverage:choice(t.coverage,['SOURCE_SNAPSHOT','PARTIAL','UNKNOWN'] as const),
    ...t.source_ref?{source_ref:ref(t.source_ref)}:{},fresh_for_seconds:integer(t.fresh_for_seconds,5,86400),classification:choice(t.classification,classifications),
    observation_status:choice(t.observation_status,['CURRENT','STALE','NEEDS_CHECK_AFTER_RESTART'] as const),last_checked_at:maybeDate(t.last_checked_at),
    ...t.display_context!=null?{display_context:displayContext(t.display_context)}:{}};});
  if(new Set(tasks.map(task=>supervisorTaskIdentity(task.key))).size!==tasks.length)return invalid();
  const alerts=v.alerts.map(raw=>{const a=object(raw),r=ref(a),exact=ref(a.alert_ref);if(JSON.stringify(r)!==JSON.stringify(exact))return invalid();return {...r,alert_ref:exact,
    kind:choice(a.kind,['TASK_STATE_CHANGED','OBSERVER_STATE_CHANGED'] as const),key:a.key==null?null:key(a.key),label:label(a.label),
    category:choice(a.category,[...classifications,'OBSERVATION_UNAVAILABLE'] as const),source_status:a.source_status==null?null:string(a.source_status,120),actual_error_code:code(a.actual_error_code),
    created_at:date(a.created_at),terminal:boolean(a.terminal),acknowledged:boolean(a.acknowledged),acknowledged_at:maybeDate(a.acknowledged_at),
    ...a.validation_status!==undefined?{validation_status:choice(a.validation_status,['NOT_OBSERVED','NOT_RUN','PENDING','PASSED','FAILED'] as const)}:{},
    ...a.observation_status!==undefined?{observation_status:choice(a.observation_status,['CURRENT','STALE','NEEDS_CHECK_AFTER_RESTART'] as const)}:{}};});
  if(o.unavailable_codes!==undefined&&(!Array.isArray(o.unavailable_codes)||o.unavailable_codes.length>100))return invalid();
  return {settings:supervisorSettings(v.settings),observer:{status:choice(o.status,['INITIAL','READY','PARTIAL','OBSERVATION_UNAVAILABLE','DISABLED','CLOSED'] as const),
    last_attempt_at:maybeDate(o.last_attempt_at),last_success_at:maybeDate(o.last_success_at),actual_error_code:code(o.actual_error_code),running:boolean(o.running),
    ...Array.isArray(o.unavailable_codes)?{unavailable_codes:o.unavailable_codes.map(v=>code(v)??invalid())}:{}},
    tasks,alerts,alerts_total:integer(v.alerts_total,alerts.length),...v.alerts_has_more!==undefined?{alerts_has_more:boolean(v.alerts_has_more)}:{},coverage:choice(v.coverage,['ONLY_COLLECTED_EXISTING_TASKS'] as const)};
}
export function supervisorError(error:unknown):string{return error instanceof ApiError&&/^[A-Za-z0-9_.:-]{1,120}$/.test(error.code)?error.code:'SUPERVISOR_UNAVAILABLE';}
function matches(settings:SupervisorSettings,body:SupervisorSettingsInput){return settings.enabled===body.enabled&&settings.notifications_enabled===body.notifications_enabled&&settings.interval_seconds===body.interval_seconds;}
export interface SupervisorSettingsInput {enabled:boolean;notifications_enabled:boolean;interval_seconds:number;expected_revision:number}
export function supervisorSettingsInput(draft:SupervisorDraft,revision:number):SupervisorSettingsInput{
  if(!/^\d{1,4}$/.test(draft.interval_seconds))throw new Error('检查间隔应为 5–3600 的整数秒');
  const seconds=Number(draft.interval_seconds);if(seconds<5||seconds>3600)throw new Error('检查间隔应为 5–3600 的整数秒');
  return {enabled:boolean(draft.enabled),notifications_enabled:boolean(draft.notifications_enabled),interval_seconds:seconds,expected_revision:integer(revision,1)};
}
export class GlobalSupervisorClient {
  private writing=false;private unknownSettings?:SupervisorSettingsInput;private unknownAlerts=new Map<string,SupervisorRef>();
  private readonly http:Pick<ApiClient,'request'>;
  constructor(http:Pick<ApiClient,'request'>=api){this.http=http;}
  get settingsUnresolved(){return !!this.unknownSettings;}
  alertUnresolved(value:SupervisorRef){return this.unknownAlerts.has(JSON.stringify(ref(value)));}
  async refresh(signal?:AbortSignal):Promise<SupervisorSnapshot>{
    const snapshot=supervisorSnapshot(await this.http.request('/api/supervisor','GET',undefined,signal));
    if(this.unknownSettings&&matches(snapshot.settings,this.unknownSettings))this.unknownSettings=undefined;
    for(const [id,value]of this.unknownAlerts)if(snapshot.alerts.some(a=>a.acknowledged&&JSON.stringify(a.alert_ref)===JSON.stringify(value)))this.unknownAlerts.delete(id);
    return snapshot;
  }
  async settings(body:SupervisorSettingsInput):Promise<SupervisorMutation>{
    if(this.writing||this.unknownSettings)throw new Error('设置结果仍需读取核对，不会重复提交');
    supervisorSettingsInput({...body,interval_seconds:String(body.interval_seconds)},body.expected_revision);
    this.writing=true;let accepted:SupervisorSettings|undefined,errorCode:string|undefined,rejected=false;
    try{
      try{accepted=supervisorSettings(await this.http.request('/api/supervisor/settings','POST',body,undefined,false));if(!matches(accepted,body))throw new Error('SUPERVISOR_SETTINGS_RECEIPT_DIFFERS');}
      catch(error){errorCode=supervisorError(error);rejected=error instanceof ApiError&&[400,401,403,409,422].includes(error.status);if(!rejected)this.unknownSettings=clone(body);}
      let snapshot:SupervisorSnapshot|undefined;try{snapshot=await this.refresh();}catch(error){errorCode??=supervisorError(error);}
      return {outcome:accepted?'ACKNOWLEDGED':rejected?'REJECTED':snapshot&&matches(snapshot.settings,body)?'OBSERVED_MATCH':'UNKNOWN',settings:accepted,snapshot,error_code:errorCode};
    }finally{this.writing=false;}
  }
  async acknowledge(value:SupervisorRef):Promise<SupervisorMutation>{
    const exact=ref(value),id=JSON.stringify(exact);
    if(this.writing||this.unknownAlerts.has(id))throw new Error('提醒结果仍需读取核对，不会重复提交');
    this.writing=true;let accepted=false,errorCode:string|undefined,rejected=false;
    try{
      try{const result=object(await this.http.request('/api/supervisor/alerts/ack','POST',exact,undefined,false));
        if(JSON.stringify(ref(result.alert_ref))!==id||result.acknowledged!==true)throw new Error('SUPERVISOR_ACK_RECEIPT_DIFFERS');date(result.acknowledged_at);accepted=true;}
      catch(error){errorCode=supervisorError(error);rejected=error instanceof ApiError&&[400,401,403,409,422].includes(error.status);if(!rejected)this.unknownAlerts.set(id,exact);}
      let snapshot:SupervisorSnapshot|undefined;try{snapshot=await this.refresh();}catch(error){errorCode??=supervisorError(error);}
      return {outcome:accepted?'ACKNOWLEDGED':rejected?'REJECTED':snapshot?.alerts.some(a=>a.acknowledged&&JSON.stringify(a.alert_ref)===id)?'OBSERVED_MATCH':'UNKNOWN',snapshot,error_code:errorCode};
    }finally{this.writing=false;}
  }
  async check():Promise<SupervisorMutation>{
    if(this.writing)throw new Error('正在核对，请稍后');this.writing=true;
    try{try{return {outcome:'ACKNOWLEDGED',snapshot:supervisorSnapshot(await this.http.request('/api/supervisor/check','POST',{},undefined,false))};}
      catch(error){let snapshot:SupervisorSnapshot|undefined;try{snapshot=await this.refresh();}catch{}return {outcome:'UNKNOWN',snapshot,error_code:supervisorError(error)};}}
    finally{this.writing=false;}
  }
}
export const globalSupervisor=new GlobalSupervisorClient();
export type SupervisorGroup='attention'|'running'|'recent';
export function supervisorGroup(task:SupervisorTask):SupervisorGroup{
  if(task.observation_status!=='CURRENT'||task.actual_error_code||task.validation_status==='FAILED'||task.wait_reason||['WAITING_USER','EXECUTION_ERROR','TIMED_OUT','NEEDS_CHECK'].includes(task.classification))return 'attention';
  return ['RUNNING','CANCEL_REQUESTED'].includes(task.classification)?'running':'recent';
}
/** Presentation identity only; never an execution or acknowledgement grant. */
export function supervisorTaskIdentity(value:SupervisorTaskKey):string{
  return JSON.stringify([value.kind,value.project_id??null,value.object_id,value.operation_id??null,value.generation]);
}
export interface SupervisorAlertGroup {identity:string;key:SupervisorTaskKey|null;alerts:SupervisorAlert[]}
export function supervisorActivity(alerts:SupervisorAlert[],unresolved:(ref:SupervisorRef)=>boolean=()=>false){
  const history=new Map<string,SupervisorAlertGroup>(),attention=new Map<string,SupervisorAlertGroup>();
  const add=(map:Map<string,SupervisorAlertGroup>,identity:string,alert:SupervisorAlert)=>{
    let group=map.get(identity);if(!group){group={identity,key:alert.key,alerts:[]};map.set(identity,group);}group.alerts.push(alert);
  };
  for(const alert of alerts){
    const identity=alert.key?supervisorTaskIdentity(alert.key):JSON.stringify(['observer',alert.alert_ref.id]);
    add(history,identity,alert);
    // Missing/unknown evidence stays prominent. A later success never clears an old warning.
    if(unresolved(alert.alert_ref)||!alert.acknowledged&&(alert.kind==='OBSERVER_STATE_CHANGED'||!alert.key||!!alert.actual_error_code||
      ['WAITING_USER','EXECUTION_ERROR','TIMED_OUT','NEEDS_CHECK','OBSERVATION_UNAVAILABLE'].includes(alert.category)||
      alert.validation_status==='FAILED'||alert.observation_status!==undefined&&alert.observation_status!=='CURRENT'))add(attention,identity,alert);
  }
  return {history:[...history.values()],attention:[...attention.values()]};
}
export function supervisorTaskContext(task:SupervisorTask,peers:SupervisorTask[]):{labels:string[];created_kind?:'task'|'conversation';created_at?:string}{
  const labelsFor=(value:SupervisorTask)=>[...new Set([value.display_context?.project_label,value.display_context?.workspace_label].filter((label):label is string=>!!label))];
  const labels=labelsFor(task),ambiguous=peers.some(other=>supervisorTaskIdentity(other.key)!==supervisorTaskIdentity(task.key)&&other.label===task.label&&JSON.stringify(labelsFor(other))===JSON.stringify(labels));
  if(ambiguous&&task.display_context?.task_created_at)return {labels,created_kind:'task',created_at:task.display_context.task_created_at};
  if(ambiguous&&task.display_context?.conversation_created_at)return {labels,created_kind:'conversation',created_at:task.display_context.conversation_created_at};
  return {labels};
}
export function supervisorAlertStatus(alert:SupervisorAlert,translate:(message:string)=>string=value=>value):string{
  if(alert.actual_error_code)return alert.actual_error_code;
  if(alert.validation_status==='FAILED')return translate('验证失败');
  if(alert.observation_status&&alert.observation_status!=='CURRENT')return translate('观察待核查');
  const labels:Record<SupervisorAlert['category'],string>={RUNNING:'运行中',WAITING_USER:'等待用户处理',EXECUTION_RETURNED:'执行返回',EXECUTION_ERROR:'执行报错',
    TIMED_OUT:'超时，需核查',CANCEL_REQUESTED:'取消中',CANCELLED:'已取消',NEEDS_CHECK:'需核查',OBSERVATION_UNAVAILABLE:'观察不可用'};
  return translate(labels[alert.category]);
}
export function supervisorCreatedAt(value:string,locale='zh-CN'){
  return new Intl.DateTimeFormat(locale,{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value));
}
export function supervisorCounts(snapshot?:SupervisorSnapshot){const tasks=snapshot?.tasks??[];return {running:tasks.filter(t=>supervisorGroup(t)==='running').length,
  attention:tasks.filter(t=>supervisorGroup(t)==='attention').length,unacknowledged:snapshot?.alerts.filter(a=>!a.acknowledged).length??0};}
export function supervisorTaskStatus(task:SupervisorTask,translate:(message:string)=>string=value=>value){const labels:Record<SupervisorClassification,string>={RUNNING:'运行中',WAITING_USER:task.wait_reason==='PERMISSION'?'等待授权':'等待补充',
  EXECUTION_RETURNED:'执行返回',EXECUTION_ERROR:'执行报错',TIMED_OUT:'超时，需核查',CANCEL_REQUESTED:'取消中',CANCELLED:'已取消',NEEDS_CHECK:'需核查'};
  return translate(labels[task.classification])+(task.observation_status==='STALE'?translate(' · 观察过期'):task.observation_status==='NEEDS_CHECK_AFTER_RESTART'?translate(' · 重启后待核对'):'');}
export function supervisorObservedAt(value:string|null,locale='zh-CN'){if(!value)return '尚未观察';return new Intl.DateTimeFormat(locale,{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value));}

export interface SupervisorPanelState {snapshot?:SupervisorSnapshot;draft:SupervisorDraft;basis?:SupervisorSettings;dirty:boolean;busy:string;error:string;notice:string;unknown:boolean;conflict:boolean}
export class GlobalSupervisorPanelModel {
  private disposed=false;private generation=0;private active=false;
  readonly client:GlobalSupervisorClient;private readonly changed:(state:SupervisorPanelState)=>void;
  private readonly opened:(task:SupervisorTask)=>unknown;private readonly observed?: (snapshot:SupervisorSnapshot)=>void;
  state:SupervisorPanelState={draft:{enabled:true,notifications_enabled:false,interval_seconds:'30'},dirty:false,busy:'',error:'',notice:'',unknown:false,conflict:false};
  constructor(client:GlobalSupervisorClient,changed:(state:SupervisorPanelState)=>void,opened:(task:SupervisorTask)=>unknown,observed?:(snapshot:SupervisorSnapshot)=>void){
    this.client=client;this.changed=changed;this.opened=opened;this.observed=observed;
  }
  private publish(){if(!this.disposed)this.changed({...this.state,draft:{...this.state.draft}});}
  setActive(value:boolean){if(this.active!==value){this.active=value;if(!this.state.busy||this.state.busy==='读取'){this.generation++;this.state.busy='';}}this.publish();}
  accept(snapshot:SupervisorSnapshot,notify=false){
    if(this.disposed)return;let value:SupervisorSnapshot;
    try{value=supervisorSnapshot(snapshot);}catch{this.state.error='任务动态格式尚未确认 · SUPERVISOR_RESPONSE_INVALID';this.publish();return;}
    if(value.settings.revision<Math.max(this.state.snapshot?.settings.revision??0,this.state.basis?.revision??0))return;
    const recovered=this.state.unknown&&!this.client.settingsUnresolved;
    if(recovered){
      try{if(matches(value.settings,supervisorSettingsInput(this.state.draft,value.settings.revision))){this.state.dirty=false;this.state.notice='当前设置已读取核对';}}
      catch{/* Preserve invalid/newer user input rather than silently replace it. */}
    }
    this.state.snapshot=value;
    if(!this.state.dirty){this.state.basis=value.settings;this.state.draft={enabled:value.settings.enabled,notifications_enabled:value.settings.notifications_enabled,interval_seconds:String(value.settings.interval_seconds)};}
    this.state.unknown=this.client.settingsUnresolved;this.publish();if(notify)this.observed?.(clone(value));
  }
  edit(change:Partial<SupervisorDraft>){if(this.disposed)return;this.state.draft={...this.state.draft,...change};this.state.dirty=true;this.state.notice='';this.publish();}
  async refresh(){
    if(this.disposed||!this.active||this.state.busy)return;
    const owner=++this.generation;this.state.busy='读取';this.state.error='';this.publish();
    try{const value=await this.client.refresh();if(owner===this.generation&&!this.disposed){this.accept(value,true);this.state.error='';}}
    catch(error){if(owner===this.generation&&!this.disposed)this.state.error='任务动态暂不可读 · '+supervisorError(error);}
    finally{if(!this.disposed&&owner===this.generation){this.state.busy='';this.publish();}}
  }
  async save(){
    if(this.disposed||!this.active||this.state.busy||this.client.settingsUnresolved)return;
    let body:SupervisorSettingsInput;
    try{body=supervisorSettingsInput(this.state.draft,(this.state.conflict?this.state.snapshot?.settings:this.state.basis)?.revision??0);}
    catch(error){this.state.error=error instanceof Error?error.message:'检查输入';this.publish();return;}
    await this.mutate('保存',()=>this.client.settings(body),result=>{
      if(result.outcome==='ACKNOWLEDGED'||result.outcome==='OBSERVED_MATCH'){
        const actual=result.settings??result.snapshot?.settings;if(actual){this.state.basis=actual;this.state.dirty=false;this.state.conflict=false;}
        this.state.notice=result.outcome==='ACKNOWLEDGED'?'设置已确认':'当前设置已读取核对';
        if(!result.snapshot)this.state.error='设置已确认，列表暂未刷新 · '+(result.error_code??'SUPERVISOR_UNAVAILABLE');
      }else{this.state.conflict=result.outcome==='REJECTED';this.state.error=result.outcome==='UNKNOWN'?'设置结果待核对；输入保留，仅读取，不重复提交。':'设置未保存；输入保留，请核对当前版本后再保存。';}
    });
  }
  async acknowledge(alert:SupervisorAlert){
    if(this.disposed||!this.active||this.state.busy||this.client.alertUnresolved(alert.alert_ref))return;
    await this.mutate('确认提醒',()=>this.client.acknowledge(alert.alert_ref),result=>{
      this.state.notice=['ACKNOWLEDGED','OBSERVED_MATCH'].includes(result.outcome)?'提醒已确认；任务本身的状态保持原样。':'';
      if(['UNKNOWN','REJECTED'].includes(result.outcome))this.state.error='提醒结果待读取核对，未重复提交。';
    });
  }
  async check(){if(this.disposed||!this.active||this.state.busy)return;await this.mutate('本地检查',()=>this.client.check(),result=>{
    this.state.notice=result.outcome==='ACKNOWLEDGED'?'本地状态已检查':'';if(result.outcome==='UNKNOWN')this.state.error='本次检查回执未确认，已仅读取现有状态。';});}
  private async mutate(label:string,run:()=>Promise<SupervisorMutation>,settled:(result:SupervisorMutation)=>void){
    const owner=++this.generation;this.state.busy=label;this.state.error='';this.state.notice='';this.publish();
    try{const result=await run();if(this.disposed||owner!==this.generation)return;if(result.snapshot)this.accept(result.snapshot,true);settled(result);this.state.unknown=this.client.settingsUnresolved;}
    catch(error){if(!this.disposed&&owner===this.generation)this.state.error='结果需核对 · '+supervisorError(error);}
    finally{if(!this.disposed&&owner===this.generation){this.state.busy='';this.publish();}}
  }
  async open(task:SupervisorTask){
    if(this.disposed||!this.active)return;
    try{await this.opened(clone(task));}catch(error){if(!this.disposed){this.state.error='任务详情暂未打开 · '+supervisorError(error);this.publish();}}
  }
  dispose(){this.disposed=true;this.generation++;}
}

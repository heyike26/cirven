import type {SupervisorCenterConversationMessage,SupervisorCenterReply} from './SupervisorCommandCenter';

export interface SupervisorConversationClientSnapshot {
  revision:number;state_sha256?:string;conversation_id:string;draft:{text:string;revision:number};messages:(SupervisorCenterConversationMessage&{created_at?:string})[];
  operations:{operation_id:string;status:string;request_sha256:string;created_at:string;followup_ref?:SupervisorConversationFollowupRef}[];
  storage_bytes?:number;storage_limit_bytes?:number;
  history?:{basis_sha256:string;total_records:number;archived_records:number;archived_operations:number;archive_bytes:number;archive_limit_bytes:number;segments:number};
}
export type SupervisorConversationOperation=SupervisorConversationClientSnapshot['operations'][number];
export interface SupervisorConversationHistoryView {messages:SupervisorConversationClientSnapshot['messages'];operations:SupervisorConversationOperation[];hasMore:boolean;loading:boolean;changed:boolean;windowed:boolean;error?:'UNAVAILABLE'|'INVALID'|'CHANGED'}
export interface SupervisorConversationHistoryPage {conversation_id:string;basis_sha256:string;messages:SupervisorConversationClientSnapshot['messages'];operations:SupervisorConversationOperation[];next_cursor:string|null;record_count:number;total_records:number;archived_records:number}
export interface SupervisorDiscussionSelection {operation_id:string;request_sha256:string;question:string;answer_text:string;status:SupervisorCenterReply['status']}
export interface SupervisorSearchHit {id:string;question:string;answer_text:string;text_truncated:boolean;reference?:SupervisorConversationFollowupRef}
export interface SupervisorHistorySearch {query:string;results:SupervisorSearchHit[];scanned_records:number;total_records:number;matched_records:number;hasMore:boolean;loading:boolean;changed:boolean;windowed:boolean;coverage:'SAVED_TEXT';error?:'UNAVAILABLE'|'INVALID'|'CHANGED'}
export interface SupervisorConversationFollowupRef {operation_id:string;request_sha256:string}
export interface SupervisorConversationAsk {operationId:string;text:string;providerProfileRef:{id:string;revision:number;sha256:string};followupRef?:SupervisorConversationFollowupRef}
export interface SupervisorConversationLocal {question:string;answer:string|{text:string}}
export type SupervisorAskAdmission={allowed:true}|{allowed:false;code:'SUPERVISOR_OPERATION_PENDING'|'SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED';operation_id:string};
export interface SupervisorConversationClientBridge {
  getSnapshot:()=>Promise<unknown>;saveDraft:(body:{text:string;expectedRevision:number})=>Promise<unknown>;
  ask:(body:SupervisorConversationAsk)=>Promise<unknown>;inspect:(operationId:string)=>Promise<unknown>;
  persistLocal:(body:SupervisorConversationLocal)=>Promise<unknown>;
  onChange?:(listener:(snapshot:unknown)=>void)=>(()=>void);
  readHistoryPage?:(body:{cursor:string|null;limit:number})=>Promise<unknown>;
  readOperation?:(body:SupervisorConversationFollowupRef)=>Promise<unknown>;
}
export interface SupervisorConversationClientView {snapshot?:SupervisorConversationClientSnapshot;draft:string;dirty:boolean;saving:boolean;draftConflict:boolean;draftBlocked:boolean;history:SupervisorConversationHistoryView;search?:SupervisorHistorySearch;searchBusy:boolean;error?:{code:string;operation_id?:string}}
interface Clock {setTimeout:(callback:()=>void,milliseconds:number)=>unknown;clearTimeout:(handle:unknown)=>void}
type SaveOwner={text:string;edit:number;revision:number;basisText:string};
type FailedSave={owner:SaveOwner;code:string;rejected:boolean;checked:boolean};
type ClearLease={question:string;revision:number};
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/;
const PENDING=new Set(['CLAIMED','PROCESSING','REQUESTING','RUNNING']);
const TERMINAL=new Set(['RETURNED','FAILED','CANCELLED','REJECTED']);
const clone=<T>(value:T):T=>JSON.parse(JSON.stringify(value));
const immutable=<T>(value:T):T=>{if(value&&typeof value==='object'){Object.values(value).forEach(immutable);Object.freeze(value);}return value;};
const fail=(code:string):never=>{throw Object.assign(new Error(code),{code});};
function check(value:unknown,code:string):asserts value{if(!value)fail(code);}
function boundedText(value:unknown,max=8000):string{check(typeof value==='string'&&Array.from(value).length<=max&&!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value),'SUPERVISOR_CLIENT_TEXT_INVALID');return value;}
function object(value:unknown):Record<string,unknown>{check(value&&typeof value==='object'&&!Array.isArray(value),'SUPERVISOR_CLIENT_RESPONSE_INVALID');return value as Record<string,unknown>;}
function integer(value:unknown,max=Number.MAX_SAFE_INTEGER):number{check(typeof value==='number'&&Number.isSafeInteger(value)&&value>=0&&value<=max,'SUPERVISOR_CLIENT_RESPONSE_INVALID');return value;}
function ident(value:unknown):string{check(typeof value==='string'&&ID.test(value),'SUPERVISOR_CLIENT_RESPONSE_INVALID');return value;}
const canonicalInput=(value:unknown):string=>value===null||typeof value!=='object'?JSON.stringify(value):Array.isArray(value)?'['+value.map(canonicalInput).join(',')+']':'{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonicalInput((value as Record<string,unknown>)[key])).join(',')+'}';
async function inputDigest(body:SupervisorConversationAsk){const value={operation_id:body.operationId,text:body.text,provider_profile_ref:body.providerProfileRef,...body.followupRef?{followup_ref:body.followupRef}:{}};return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonicalInput(value))))].map(n=>n.toString(16).padStart(2,'0')).join('');}
function profile(value:unknown){const raw=object(value);check(SHA.test(String(raw.sha256))&&typeof raw.revision==='number'&&raw.revision>0,'SUPERVISOR_CLIENT_RESPONSE_INVALID');return {id:ident(raw.id),revision:integer(raw.revision),sha256:String(raw.sha256)};}
function sameProfile(a:unknown,b:unknown){if(!a||!b)return false;try{const x=profile(a),y=profile(b);return x.id===y.id&&x.revision===y.revision&&x.sha256===y.sha256;}catch{return false;}}
function followup(value:unknown):SupervisorConversationFollowupRef|undefined{
  if(value===undefined)return;
  check(value&&typeof value==='object'&&!Array.isArray(value),'SUPERVISOR_CLIENT_FOLLOWUP_INVALID');const raw=value as Record<string,unknown>;
  check(Object.keys(raw).sort().join(',')==='operation_id,request_sha256'&&typeof raw.operation_id==='string'&&ID.test(raw.operation_id)&&typeof raw.request_sha256==='string'&&SHA.test(raw.request_sha256),'SUPERVISOR_CLIENT_FOLLOWUP_INVALID');
  return {operation_id:raw.operation_id,request_sha256:raw.request_sha256};
}
function sameFollowup(a:SupervisorConversationFollowupRef|undefined,b:SupervisorConversationFollowupRef|undefined){return a?.operation_id===b?.operation_id&&a?.request_sha256===b?.request_sha256;}
function snapshotAskAdmission(snapshot:SupervisorConversationClientSnapshot|undefined,text:string,chosen:unknown,settled:ReadonlySet<string>):SupervisorAskAdmission{
  const operations=(snapshot?.operations??[]).filter(row=>!settled.has(row.operation_id)),pending=operations.find(row=>PENDING.has(row.status));
  if(pending)return {allowed:false,code:'SUPERVISOR_OPERATION_PENDING',operation_id:pending.operation_id};
  for(const row of operations.filter(row=>row.status==='UNKNOWN')){
    const question=snapshot?.messages.find(message=>message.operation_id===row.operation_id&&message.role==='user');
    if(question&&question.text===text&&sameProfile(question.provider_profile_ref,chosen))return {allowed:false,code:'SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED',operation_id:row.operation_id};
  }
  return {allowed:true};
}
export function supervisorClientSnapshot(value:unknown):SupervisorConversationClientSnapshot{
  const raw=object(value),draft=object(raw.draft);check(Array.isArray(raw.messages)&&raw.messages.length<=256&&Array.isArray(raw.operations)&&raw.operations.length<=128,'SUPERVISOR_CLIENT_RESPONSE_INVALID');
  const messages=raw.messages.map(value=>{const row=object(value);check(row.role==='user'||row.role==='assistant','SUPERVISOR_CLIENT_RESPONSE_INVALID');
    check(row.status===undefined||['RETURNED','UNKNOWN','REJECTED','PROCESSING','REQUESTING','RUNNING','FAILED','CANCELLED'].includes(String(row.status)),'SUPERVISOR_CLIENT_RESPONSE_INVALID');
    check(typeof row.id==='string'&&/^[A-Za-z0-9_-]{1,112}$/.test(row.id),'SUPERVISOR_CLIENT_RESPONSE_INVALID');
    return {id:row.id,role:row.role as 'user'|'assistant',text:boundedText(row.text,48000),
      ...row.operation_id!==undefined?{operation_id:ident(row.operation_id)}:{},...row.status!==undefined?{status:row.status as SupervisorCenterReply['status']}:{},
      ...row.error!==undefined?{error:row.error===null?null:boundedText(row.error,120)}:{},...row.text_truncated===true?{text_truncated:true}:{},
      ...row.provider_profile_ref?{provider_profile_ref:profile(row.provider_profile_ref)}:{},
      ...typeof row.created_at==='string'&&Number.isFinite(Date.parse(row.created_at))?{created_at:row.created_at}:{}};
  });
  check(new Set(messages.map(row=>row.id)).size===messages.length,'SUPERVISOR_CLIENT_RESPONSE_INVALID');
  const operations=raw.operations.map(value=>{const row=object(value);check(SHA.test(String(row.request_sha256))&&typeof row.status==='string'&&['CLAIMED','RETURNED','UNKNOWN','REJECTED','PROCESSING','REQUESTING','RUNNING','FAILED','CANCELLED'].includes(row.status)&&typeof row.created_at==='string'&&Number.isFinite(Date.parse(row.created_at)),'SUPERVISOR_CLIENT_RESPONSE_INVALID');const reference=followup(row.followup_ref);return {operation_id:ident(row.operation_id),status:row.status,request_sha256:String(row.request_sha256),created_at:row.created_at,...reference?{followup_ref:reference}:{}};});
  check(new Set(operations.map(row=>row.operation_id)).size===operations.length,'SUPERVISOR_CLIENT_RESPONSE_INVALID');
  let history:SupervisorConversationClientSnapshot['history'];if(raw.history!==undefined){const row=object(raw.history);
    check(typeof row.basis_sha256==='string'&&SHA.test(row.basis_sha256),'SUPERVISOR_CLIENT_RESPONSE_INVALID');
    history={basis_sha256:row.basis_sha256,total_records:integer(row.total_records,8384),archived_records:integer(row.archived_records,8192),archived_operations:integer(row.archived_operations,8192),archive_bytes:integer(row.archive_bytes,32*1024*1024),archive_limit_bytes:integer(row.archive_limit_bytes,32*1024*1024),segments:integer(row.segments,256)};
    check(history.total_records>=history.archived_records&&history.archived_records>=history.archived_operations&&history.archive_bytes<=history.archive_limit_bytes,'SUPERVISOR_CLIENT_RESPONSE_INVALID');}
  check(raw.state_sha256===undefined||typeof raw.state_sha256==='string'&&SHA.test(raw.state_sha256),'SUPERVISOR_CLIENT_RESPONSE_INVALID');
  return {revision:integer(raw.revision),...raw.state_sha256?{state_sha256:String(raw.state_sha256)}:{},conversation_id:ident(raw.conversation_id),draft:{text:boundedText(draft.text),revision:integer(draft.revision)},messages,operations,...history?{history}:{},
    ...raw.storage_bytes!==undefined?{storage_bytes:integer(raw.storage_bytes,256*1024)}:{},...raw.storage_limit_bytes!==undefined?{storage_limit_bytes:integer(raw.storage_limit_bytes,256*1024)}:{}};
}
export function supervisorHistoryPage(value:unknown):SupervisorConversationHistoryPage{
  const raw=object(value);check(Array.isArray(raw.messages)&&raw.messages.length<=48&&Array.isArray(raw.operations)&&raw.operations.length<=24&&typeof raw.basis_sha256==='string'&&SHA.test(raw.basis_sha256)&&
    (raw.next_cursor===null||typeof raw.next_cursor==='string'&&raw.next_cursor.length<=512&&/^[A-Za-z0-9_-]+$/.test(raw.next_cursor)),'SUPERVISOR_CLIENT_HISTORY_INVALID');
  const parsed=supervisorClientSnapshot({revision:0,conversation_id:raw.conversation_id,draft:{text:'',revision:0},messages:raw.messages,operations:raw.operations});
  const count=integer(raw.record_count,24),total=integer(raw.total_records,8384),archived=integer(raw.archived_records,8192);
  check(parsed.messages.length===count*2&&total>=count&&total>=archived,'SUPERVISOR_CLIENT_HISTORY_INVALID');
  for(let i=0;i<parsed.messages.length;i+=2){const a=parsed.messages[i],b=parsed.messages[i+1];check(a.role==='user'&&b.role==='assistant'&&a.operation_id===b.operation_id,'SUPERVISOR_CLIENT_HISTORY_INVALID');
    if(a.operation_id)check(parsed.operations.some(row=>row.operation_id===a.operation_id),'SUPERVISOR_CLIENT_HISTORY_INVALID');}
  return {conversation_id:parsed.conversation_id,basis_sha256:raw.basis_sha256,messages:parsed.messages,operations:parsed.operations,next_cursor:raw.next_cursor as string|null,record_count:count,total_records:total,archived_records:archived};
}
function receipt(raw:unknown,operationId:string):SupervisorCenterReply{
  const value=object(raw);check(value.operation_id===operationId&&['RETURNED','UNKNOWN','REJECTED','PROCESSING','REQUESTING','RUNNING','FAILED','CANCELLED'].includes(String(value.status)),'SUPERVISOR_CLIENT_OPERATION_MISMATCH');
  return {operation_id:operationId,status:value.status as SupervisorCenterReply['status'],...value.text!==undefined?{text:boundedText(value.text,48000)}:{},
    ...value.error!==undefined?{error:value.error===null?null:boundedText(value.error,120)}:{},...value.text_truncated===true?{text_truncated:true}:{}};
}
const HOST_ERRORS=new Set(['SUPERVISOR_DRAFT_CONFLICT','SUPERVISOR_STORAGE_CONFLICT','SUPERVISOR_STORAGE_FULL','SUPERVISOR_CLOSED','SUPERVISOR_OPERATION_PENDING','SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED','SUPERVISOR_OPERATION_CONFLICT','SUPERVISOR_PROFILE_INVALID','SUPERVISOR_TEXT_INVALID','SUPERVISOR_FOLLOWUP_REFERENCE_INVALID','SUPERVISOR_QUESTION_REFERENCE_CHANGED']);
const IPC_METHODS=new Set(['eeas:supervisor-draft-save','eeas:supervisor-conversation-get','eeas:supervisor-ask','eeas:supervisor-inspect','eeas:supervisor-local-save','eeas:supervisor-history-read','eeas:supervisor-operation-read']);
export function supervisorClientErrorCode(error:unknown,fallback:string){
  const value=error as {code?:unknown;message?:unknown}|undefined,code=value?.code;
  if(typeof code==='string'&&(/^(?:SUPERVISOR_CLIENT_[A-Z_]+)$/.test(code)||HOST_ERRORS.has(code)))return code;
  if(typeof value?.message==='string'){
    const exact=value.message.match(/^Error invoking remote method '([^']+)': Error: ([A-Z_]+)$/);
    if(exact&&IPC_METHODS.has(exact[1])&&HOST_ERRORS.has(exact[2]))return exact[2];
  }
  return fallback;
}
const errorCode=supervisorClientErrorCode;
const CAS_ERRORS=new Set(['SUPERVISOR_DRAFT_CONFLICT','SUPERVISOR_CLIENT_DRAFT_CONFLICT']);
export function supervisorDraftFailureKind(code:string|undefined):'CONFLICT'|'FAILED'|'UNKNOWN'|undefined{
  if(code&&CAS_ERRORS.has(code))return 'CONFLICT';
  if(code==='SUPERVISOR_STORAGE_FULL')return 'FAILED';
  if(code&&['SUPERVISOR_STORAGE_CONFLICT','SUPERVISOR_CLIENT_DRAFT_SAVE_UNKNOWN','SUPERVISOR_CLIENT_SAVE_RECEIPT_MISMATCH','SUPERVISOR_CLIENT_DRAFT_RECOVERY_READ_UNAVAILABLE'].includes(code))return 'UNKNOWN';
}


export class SupervisorConversationClient {
  private readonly bridge:SupervisorConversationClientBridge;private readonly changed:(view:SupervisorConversationClientView)=>void;private readonly clock:Clock;
  private snapshotValue?:SupervisorConversationClientSnapshot;private draftValue='';private editVersion=0;private savedEdit=0;private basis?:number;
  private errorValue?:SupervisorConversationClientView['error'];private conflict=false;private casConflict=false;private failedSave?:FailedSave;private draftInspection?:Promise<SupervisorConversationClientView>;private timer?:unknown;private started=false;private closed=false;private closing=false;
  private load?:Promise<void>;private release?:()=>void;private saving?:SaveOwner;private savePending?:Promise<void>;
  private localMutation?:{done:Promise<void>;release:()=>void};
  private clearLeases:ClearLease[]=[];private asks=new Map<string,{body:SupervisorConversationAsk;promise:Promise<SupervisorCenterReply>;result?:SupervisorCenterReply;dispatched?:boolean;requestSha?:string}>();
  private historyValue:SupervisorConversationHistoryView={messages:[],operations:[],hasMore:false,loading:false,changed:false,windowed:false};
  private historyCursor:string|null=null;private historyBasis?:string;private historyGeneration=0;private historyFlight?:Promise<SupervisorConversationClientView>;
  private searchValue?:SupervisorHistorySearch;private searchCursor:string|null=null;private searchBasis?:string;private searchGeneration=0;private searchFlight?:Promise<SupervisorConversationClientView>;
  private inspections=new Map<string,Promise<SupervisorCenterReply>>();
  private observation=0;private readVersion=0;
  constructor(bridge:SupervisorConversationClientBridge,changed:(view:SupervisorConversationClientView)=>void,options:{clock?:Clock}={}){
    this.bridge=bridge;this.changed=changed;this.clock=options.clock??{setTimeout:(callback,milliseconds)=>setTimeout(callback,milliseconds),clearTimeout:handle=>clearTimeout(handle as ReturnType<typeof setTimeout>)};
  }
  get view():SupervisorConversationClientView{return {snapshot:this.snapshotValue?clone(this.snapshotValue):undefined,draft:this.draftValue,dirty:this.editVersion>this.savedEdit,saving:!!this.saving,draftConflict:this.casConflict,draftBlocked:this.conflict||!!this.errorValue&&!this.errorValue.operation_id,history:{...this.historyValue},searchBusy:!!this.searchFlight,...this.searchValue?{search:clone(this.searchValue)}:{},...this.errorValue?{error:{...this.errorValue}}:{}};}
  private get dirty(){return this.editVersion>this.savedEdit;}
  private publish(){if(!this.closed)this.changed(this.view);}
  private cancelTimer(){if(this.timer!==undefined)this.clock.clearTimeout(this.timer);this.timer=undefined;}
  private schedule(){
    this.cancelTimer();if(this.closed||this.closing||this.localMutation||this.conflict||this.errorValue&&!this.errorValue.operation_id||!this.dirty)return;
    this.timer=this.clock.setTimeout(()=>{this.timer=undefined;void this.saveOnce().catch(()=>{});},300);
  }
  private markDraftConflict(code='SUPERVISOR_CLIENT_DRAFT_CONFLICT'){this.conflict=true;this.casConflict=true;this.failedSave=undefined;this.setError(code);}
  private setError(code:string,operationId?:string){this.errorValue={code,...operationId?{operation_id:operationId}:{}};this.cancelTimer();this.publish();}
  start(){
    if(this.load)return this.load;if(this.closed)return Promise.resolve();this.started=true;
    this.release=this.bridge.onChange?.(raw=>{if(this.closed)return;try{this.accept(supervisorClientSnapshot(raw));}catch{this.setError('SUPERVISOR_CLIENT_RESPONSE_INVALID');}});
    this.load=this.getSnapshot().then(()=>{});return this.load;
  }
  async getSnapshot(){
    if(this.closed)return this.view;
    const owner=++this.readVersion,observation=this.observation;
    try{const incoming=supervisorClientSnapshot(await this.bridge.getSnapshot());if(!this.closed){this.accept(incoming);if(this.errorValue?.code==='SUPERVISOR_CLIENT_READ_UNAVAILABLE'&&this.snapshotValue?.revision===incoming.revision){this.errorValue=undefined;this.publish();this.schedule();}}}
    catch(error){if(!this.closed&&owner===this.readVersion&&observation===this.observation)this.setError(errorCode(error,'SUPERVISOR_CLIENT_READ_UNAVAILABLE'));}
    return this.view;
  }
  private accept(incoming:SupervisorConversationClientSnapshot){
    if(this.closed)return;
    const previous=this.snapshotValue;
    if(previous){
      if(incoming.conversation_id!==previous.conversation_id){this.setError('SUPERVISOR_CLIENT_CONVERSATION_CHANGED');return;}
      if(incoming.revision<previous.revision)return;
      if(incoming.revision===previous.revision&&JSON.stringify(incoming)!==JSON.stringify(previous)){this.setError('SUPERVISOR_CLIENT_REVISION_CONFLICT');return;}
      if(incoming.draft.revision<previous.draft.revision){this.setError('SUPERVISOR_CLIENT_DRAFT_REVISION_INVALID');return;}
      if(incoming.draft.revision===previous.draft.revision&&incoming.draft.text!==previous.draft.text){this.setError('SUPERVISOR_CLIENT_DRAFT_REVISION_INVALID');return;}
    }
    this.snapshotValue=clone(incoming);this.observation++;let settledError=false;
    for(const [id,entry]of this.asks){
      if(!entry.dispatched)continue;const op=incoming.operations.find(row=>row.operation_id===id);if(!op)continue;
      const question=incoming.messages.find(row=>row.operation_id===id&&row.role==='user');
      if(!entry.requestSha&&question?.text===entry.body.text&&sameProfile(question.provider_profile_ref,entry.body.providerProfileRef)&&sameFollowup(op.followup_ref,entry.body.followupRef))entry.requestSha=op.request_sha256;
      if(entry.requestSha!==op.request_sha256||!TERMINAL.has(op.status))continue;
      const answer=incoming.messages.find(row=>row.operation_id===id&&row.role==='assistant');
      entry.result={operation_id:id,status:op.status as SupervisorCenterReply['status'],text:answer?.text??'',...answer?.error?{error:answer.error}:{}};
      if(this.errorValue?.operation_id===id){this.errorValue=undefined;settledError=true;}
    }
    if(this.searchBasis&&incoming.history?.basis_sha256&&incoming.history.basis_sha256!==this.searchBasis&&this.searchValue)this.searchValue.changed=true;
    if(this.historyBasis&&incoming.history?.basis_sha256&&incoming.history.basis_sha256!==this.historyBasis){this.historyValue.changed=true;}
    if(!this.historyValue.messages.length&&!this.historyFlight)this.historyValue.hasMore=!!this.bridge.readHistoryPage&&(incoming.history?.total_records??0)>incoming.messages.length/2;
    const draft=incoming.draft,owner=this.saving??this.failedSave?.owner,ownSave=owner&&draft.revision===owner.revision+1&&draft.text===owner.text;
    const ownClear=draft.text===''&&this.clearLeases.some(lease=>draft.revision===lease.revision+1);
    if(this.basis===undefined){
      this.basis=draft.revision;
      if(this.dirty&&draft.text&&draft.text!==this.draftValue){this.markDraftConflict();return;}
    }
    if(!this.dirty){this.draftValue=draft.text;this.basis=draft.revision;}
    else if(ownClear){this.basis=draft.revision;this.schedule();}
    else if(draft.revision>this.basis&&!ownSave){this.markDraftConflict();return;}
    this.publish();if(settledError)this.schedule();
  }
  resetHistory(){
    this.historyGeneration++;this.historyCursor=null;this.historyBasis=undefined;
    this.historyValue={messages:[],operations:[],hasMore:!!this.bridge.readHistoryPage&&(this.snapshotValue?.history?.total_records??0)>(this.snapshotValue?.messages.length??0)/2,loading:!!this.historyFlight,changed:false,windowed:false};
    this.publish();return this.view;
  }
  readEarlier():Promise<SupervisorConversationClientView>{
    if(this.historyFlight)return this.historyFlight;
    if(this.closed||this.closing||!this.bridge.readHistoryPage)return Promise.resolve(this.view);
    if(this.historyValue.messages.length&&!this.historyValue.hasMore&&!this.historyValue.changed&&!this.historyValue.error)return Promise.resolve(this.view);
    const generation=this.historyGeneration,reset=this.historyValue.changed||this.historyValue.error==='CHANGED';
    this.historyValue.loading=true;this.historyValue.error=undefined;this.publish();
    let pending!:Promise<SupervisorConversationClientView>;pending=Promise.resolve().then(async()=>{
      try{
        if(!this.snapshotValue)await this.start();check(this.snapshotValue,'SUPERVISOR_CLIENT_READ_UNAVAILABLE');
        const conversationId=this.snapshotValue.conversation_id,cursor=reset?null:this.historyCursor;
        const page=supervisorHistoryPage(await this.bridge.readHistoryPage!({cursor,limit:24}));
        if(this.closed||generation!==this.historyGeneration)return this.view;
        check(page.conversation_id===conversationId&&this.snapshotValue?.conversation_id===conversationId,'SUPERVISOR_CLIENT_HISTORY_INVALID');
        check((!this.snapshotValue.history||page.basis_sha256===this.snapshotValue.history.basis_sha256)&&(!cursor||page.basis_sha256===this.historyBasis),'SUPERVISOR_HISTORY_CHANGED');
        const prior=reset?[]:this.historyValue.messages,seen=new Set<string>(),combined=[...page.messages,...prior].filter(row=>!seen.has(row.id)&&!!seen.add(row.id));
        const retained=combined.slice(0,240),ids=new Set(retained.map(row=>row.operation_id).filter(Boolean)),operations=new Map<string,SupervisorConversationOperation>();
        for(const op of [...page.operations,...(reset?[]:this.historyValue.operations)])if(ids.has(op.operation_id)&&!operations.has(op.operation_id))operations.set(op.operation_id,op);
        this.historyBasis=page.basis_sha256;this.historyCursor=page.next_cursor;
        this.historyValue={messages:immutable(clone(retained)),operations:immutable([...operations.values()].map(clone)),hasMore:page.next_cursor!==null,loading:true,changed:false,windowed:combined.length>240||!reset&&this.historyValue.windowed};
      }catch(error){if(!this.closed&&generation===this.historyGeneration){const code=String((error as {code?:string})?.code??(error as Error)?.message??'');
        this.historyValue.error=code.includes('HISTORY_CHANGED')?'CHANGED':code.includes('HISTORY_INVALID')||code.includes('RESPONSE_INVALID')?'INVALID':'UNAVAILABLE';
        if(this.historyValue.error==='CHANGED')this.historyValue.changed=true;
      }}
      finally{if(this.historyFlight===pending)this.historyFlight=undefined;if(!this.closed){this.historyValue.loading=false;this.publish();}}
      return this.view;
    });this.historyFlight=pending;return pending;
  }
  resetSearch(){
    this.searchGeneration++;this.searchValue=undefined;this.searchCursor=null;this.searchBasis=undefined;this.publish();return this.view;
  }
  searchEarlier(value:string):Promise<SupervisorConversationClientView>{
    const query=boundedText(value,120).trim();check(query,'SUPERVISOR_CLIENT_SEARCH_INVALID');
    if(this.searchFlight)return this.searchFlight;
    if(this.closed||this.closing||!this.bridge.readHistoryPage)return Promise.resolve(this.view);
    const reset=this.searchValue?.query!==query||this.searchValue.changed||this.searchValue.error==='CHANGED';
    if(!reset&&this.searchValue&&!this.searchValue.hasMore&&!this.searchValue.error)return Promise.resolve(this.view);
    if(reset){this.searchGeneration++;this.searchCursor=null;this.searchBasis=undefined;this.searchValue={query,results:[],scanned_records:0,total_records:0,matched_records:0,hasMore:true,loading:false,changed:false,windowed:false,coverage:'SAVED_TEXT'};}
    const generation=this.searchGeneration;this.searchValue!.loading=true;this.searchValue!.error=undefined;this.publish();
    let pending!:Promise<SupervisorConversationClientView>;pending=Promise.resolve().then(async()=>{
      try{
        if(!this.snapshotValue)await this.start();check(this.snapshotValue,'SUPERVISOR_CLIENT_READ_UNAVAILABLE');
        if(this.closed||generation!==this.searchGeneration||!this.searchValue)return this.view;
        const conversation=this.snapshotValue.conversation_id,cursor=this.searchCursor;
        // One explicit action reads one existing 24-record page. No scan loop,
        // index copy, model request, write, or background archive traversal.
        const page=supervisorHistoryPage(await this.bridge.readHistoryPage!({cursor,limit:24}));
        if(this.closed||generation!==this.searchGeneration||!this.searchValue)return this.view;
        check(page.conversation_id===conversation&&this.snapshotValue?.conversation_id===conversation,'SUPERVISOR_CLIENT_HISTORY_INVALID');
        check((!this.snapshotValue.history||page.basis_sha256===this.snapshotValue.history.basis_sha256)&&(!cursor||page.basis_sha256===this.searchBasis),'SUPERVISOR_HISTORY_CHANGED');
        check(!page.next_cursor||page.record_count>0&&page.next_cursor!==cursor,'SUPERVISOR_CLIENT_HISTORY_INVALID');
        check(!cursor||page.total_records===this.searchValue.total_records,'SUPERVISOR_CLIENT_HISTORY_INVALID');
        const folded=(text:string)=>text.normalize('NFKC').toLowerCase(),needle=folded(query),hits:SupervisorSearchHit[]=[],seen=new Set<string>();
        for(let index=0;index<page.messages.length;index+=2){
          const question=page.messages[index],answer=page.messages[index+1];
          check(question.role==='user'&&answer.role==='assistant'&&question.operation_id===answer.operation_id,'SUPERVISOR_CLIENT_HISTORY_INVALID');
          const key=question.operation_id??question.id.replace(/_user$/,'');
          check(!seen.has(key)&&!!key&&(question.operation_id||question.id===key+'_user'&&answer.id===key+'_assistant'),'SUPERVISOR_CLIENT_HISTORY_INVALID');seen.add(key);
          if(!folded(question.text).includes(needle)&&!folded(answer.text).includes(needle))continue;
          const operation=question.operation_id?page.operations.find(row=>row.operation_id===question.operation_id):undefined;
          check(!question.operation_id||operation,'SUPERVISOR_CLIENT_HISTORY_INVALID');
          hits.push({id:key,question:Array.from(question.text).slice(0,800).join(''),answer_text:Array.from(answer.text).slice(0,1200).join(''),text_truncated:answer.text_truncated===true||Array.from(question.text).length>800||Array.from(answer.text).length>1200,...operation?{reference:{operation_id:operation.operation_id,request_sha256:operation.request_sha256}}:{}});
        }
        const old=this.searchValue,combined=[...old.results,...hits],scanned=old.scanned_records+page.record_count;
        check(scanned<=page.total_records,'SUPERVISOR_CLIENT_HISTORY_INVALID');
        this.searchBasis=page.basis_sha256;this.searchCursor=page.next_cursor;
        this.searchValue={query,results:combined.slice(-24),scanned_records:scanned,total_records:page.total_records,matched_records:old.matched_records+hits.length,hasMore:page.next_cursor!==null,loading:true,changed:false,windowed:old.windowed||combined.length>24,coverage:'SAVED_TEXT'};
      }catch(error){if(!this.closed&&generation===this.searchGeneration&&this.searchValue){const code=String((error as {code?:string})?.code??(error as Error)?.message??'');this.searchValue.error=code.includes('HISTORY_CHANGED')?'CHANGED':code.includes('HISTORY_INVALID')||code.includes('RESPONSE_INVALID')?'INVALID':'UNAVAILABLE';if(this.searchValue.error==='CHANGED')this.searchValue.changed=true;}}
      finally{if(this.searchFlight===pending)this.searchFlight=undefined;if(!this.closed){if(generation===this.searchGeneration&&this.searchValue)this.searchValue.loading=false;this.publish();}}
      return this.view;
    });this.searchFlight=pending;this.publish();return pending;
  }
  async readDiscussion(reference:SupervisorConversationFollowupRef):Promise<SupervisorDiscussionSelection>{
    const ref=followup(reference)!;if(!this.snapshotValue)await this.start();check(!this.closed&&!this.closing&&this.snapshotValue,'SUPERVISOR_CLIENT_READ_UNAVAILABLE');
    const conversation=this.snapshotValue.conversation_id;
    let raw:Record<string,unknown>;
    if(this.bridge.readOperation)raw=object(await this.bridge.readOperation(ref));
    else{const saved=this.snapshotValue,operation=saved.operations.find(row=>row.operation_id===ref.operation_id),question=saved.messages.find(row=>row.operation_id===ref.operation_id&&row.role==='user'),answer=saved.messages.find(row=>row.operation_id===ref.operation_id&&row.role==='assistant');check(operation&&question&&answer,'SUPERVISOR_CLIENT_FOLLOWUP_CHANGED');raw={conversation_id:conversation,operation,question:question.text,answer_text:answer.text};}
    check(!this.closed&&!this.closing&&this.snapshotValue?.conversation_id===conversation&&raw.conversation_id===conversation,'SUPERVISOR_CLIENT_FOLLOWUP_CHANGED');
    const question=boundedText(raw.question),answer=boundedText(raw.answer_text,48000),operation=supervisorClientSnapshot({revision:0,conversation_id:conversation,draft:{text:'',revision:0},messages:[],operations:[raw.operation]}).operations[0];
    check(operation.operation_id===ref.operation_id&&operation.request_sha256===ref.request_sha256&&operation.status!=='CLAIMED','SUPERVISOR_CLIENT_FOLLOWUP_CHANGED');
    return {...ref,question,answer_text:answer,status:operation.status as SupervisorCenterReply['status']};
  }
  async readOperation(reference:SupervisorConversationFollowupRef):Promise<SupervisorConversationOperation>{
    const ref=followup({operation_id:reference.operation_id,request_sha256:reference.request_sha256})!;
    if(!this.snapshotValue)await this.start();check(!this.closed&&this.snapshotValue,'SUPERVISOR_CLIENT_READ_UNAVAILABLE');
    const current=this.snapshotValue.operations.find(row=>row.operation_id===ref.operation_id);
    if(current){check(current.request_sha256===ref.request_sha256,'SUPERVISOR_CLIENT_FOLLOWUP_CHANGED');return clone(current);}
    check(this.bridge.readOperation,'SUPERVISOR_CLIENT_FOLLOWUP_CHANGED');const conversationId=this.snapshotValue.conversation_id;
    const raw=object(await this.bridge.readOperation(ref));check(!this.closed&&this.snapshotValue?.conversation_id===conversationId&&raw.conversation_id===conversationId,'SUPERVISOR_CLIENT_FOLLOWUP_CHANGED');
    boundedText(raw.question);boundedText(raw.answer_text,48000);
    const parsed=supervisorClientSnapshot({revision:0,conversation_id:raw.conversation_id,draft:{text:'',revision:0},messages:[],operations:[raw.operation]}).operations[0];
    check(parsed.operation_id===ref.operation_id&&parsed.request_sha256===ref.request_sha256,'SUPERVISOR_CLIENT_FOLLOWUP_CHANGED');return parsed;
  }
  editDraft(value:string){
    if(this.closed||this.closing)fail('SUPERVISOR_CLIENT_CLOSED');boundedText(value);this.draftValue=value;this.editVersion++;this.publish();this.schedule();
  }
  edit(value:string){this.editDraft(value);}
  private async saveOnce(){
    if(this.savePending)return this.savePending;
    if(this.closed||this.localMutation||this.conflict||this.errorValue&&!this.errorValue.operation_id||!this.dirty)return;
    if(!this.snapshotValue){await (this.load??this.getSnapshot());if(this.conflict||this.errorValue&&!this.errorValue.operation_id||!this.snapshotValue)return;}
    if(this.savePending)return this.savePending;if(this.localMutation)return;
    const owner:SaveOwner={text:this.draftValue,edit:this.editVersion,revision:this.basis!,basisText:this.snapshotValue!.draft.text};this.saving=owner;this.publish();
    const pending=Promise.resolve().then(async()=>{
      try{
        const incoming=supervisorClientSnapshot(await this.bridge.saveDraft({text:owner.text,expectedRevision:owner.revision}));
        check(incoming.conversation_id===this.snapshotValue?.conversation_id&&incoming.draft.revision===owner.revision+1&&incoming.draft.text===owner.text,'SUPERVISOR_CLIENT_SAVE_RECEIPT_MISMATCH');
        if(this.closed)return;
        this.savedEdit=Math.max(this.savedEdit,owner.edit);this.basis=Math.max(this.basis??0,incoming.draft.revision);
        if(!this.snapshotValue||incoming.revision>=this.snapshotValue.revision)this.accept(incoming);
        else if(this.snapshotValue.draft.revision>incoming.draft.revision){
          if(this.dirty){this.markDraftConflict();}
          else{this.draftValue=this.snapshotValue.draft.text;this.basis=this.snapshotValue.draft.revision;}
        }
      }catch(error){if(!this.closed){
        const code=errorCode(error,'SUPERVISOR_CLIENT_DRAFT_SAVE_UNKNOWN');
        if(CAS_ERRORS.has(code))this.markDraftConflict(code);
        else{this.conflict=true;this.failedSave={owner,code,rejected:code==='SUPERVISOR_STORAGE_FULL',checked:false};this.setError(code);}
        await this.getSnapshot();
        if(!this.closed&&(CAS_ERRORS.has(code)||!this.casConflict))this.setError(code);
      }}
      finally{if(this.saving===owner)this.saving=undefined;this.savePending=undefined;if(!this.closed){this.publish();this.schedule();}}
    });this.savePending=pending;return pending;
  }
  async flush(){
    this.cancelTimer();if(this.closed)return this.view;
    if(!this.dirty&&!this.savePending)return this.view;
    if(this.localMutation)await this.localMutation.done;
    if(!this.started)await this.start();else if(this.load)await this.load;
    if(this.savePending)await this.savePending;
    while(this.dirty&&!this.conflict&&(!this.errorValue||!!this.errorValue.operation_id)){if(this.localMutation)await this.localMutation.done;await this.saveOnce();if(this.savePending)await this.savePending;}
    if(this.dirty||this.conflict||this.errorValue&&!this.errorValue.operation_id)fail(this.errorValue?.code??'SUPERVISOR_CLIENT_DRAFT_NOT_SAVED');
    return this.view;
  }
  resolveDraftConflict(choice:'keep-local'|'use-remote'){
    check(!this.closed&&!this.closing&&!this.saving&&this.snapshotValue,'SUPERVISOR_CLIENT_NOT_READY');
    check(choice==='keep-local'||choice==='use-remote','SUPERVISOR_CLIENT_CHOICE_INVALID');
    check(this.casConflict,'SUPERVISOR_CLIENT_DRAFT_RECOVERY_REQUIRED');
    this.basis=this.snapshotValue.draft.revision;this.conflict=false;this.casConflict=false;this.failedSave=undefined;this.errorValue=undefined;
    if(choice==='use-remote'){this.draftValue=this.snapshotValue.draft.text;this.savedEdit=this.editVersion;}
    this.publish();return this.view;
  }
  draftSaveRecovery():'CHECK_REQUIRED'|'RETRY_ALLOWED'|undefined{
    const failure=this.failedSave;if(!failure||this.casConflict)return;
    const draft=this.snapshotValue?.draft;
    return failure.rejected&&failure.checked&&draft?.revision===failure.owner.revision&&draft.text===failure.owner.basisText?'RETRY_ALLOWED':'CHECK_REQUIRED';
  }
  inspectDraftSave():Promise<SupervisorConversationClientView>{
    check(!this.closed&&!this.closing&&!this.saving,'SUPERVISOR_CLIENT_NOT_READY');
    if(this.draftInspection)return this.draftInspection;
    const failure=this.failedSave;check(failure&&!this.casConflict,'SUPERVISOR_CLIENT_DRAFT_RECOVERY_REQUIRED');failure.checked=false;
    const pending=(async()=>{
      try{
        const incoming=supervisorClientSnapshot(await this.bridge.getSnapshot());
        if(this.closed)return this.view;const observed=this.observation;this.accept(incoming);
        if(this.observation===observed)return this.view;
        if(this.failedSave!==failure||this.casConflict||incoming.conversation_id!==this.snapshotValue?.conversation_id)return this.view;
        const draft=incoming.draft,current=this.snapshotValue.draft,owner=failure.owner;
        if(draft.revision!==current.revision||draft.text!==current.text){failure.checked=false;this.setError(failure.code);return this.view;}
        if(draft.revision===owner.revision+1&&draft.text===owner.text){
          this.savedEdit=Math.max(this.savedEdit,owner.edit);this.basis=draft.revision;this.failedSave=undefined;this.conflict=false;this.casConflict=false;this.errorValue=undefined;
        }else if(draft.revision===owner.revision&&draft.text===owner.basisText){failure.checked=failure.rejected;this.setError(failure.code);}
        else this.markDraftConflict();
      }catch{if(!this.closed&&this.failedSave===failure&&!this.casConflict){failure.checked=false;this.setError('SUPERVISOR_CLIENT_DRAFT_RECOVERY_READ_UNAVAILABLE');}}
      if(!this.closed)this.publish();return this.view;
    })();
    this.draftInspection=pending;pending.finally(()=>{if(this.draftInspection===pending)this.draftInspection=undefined;}).catch(()=>{});return pending;
  }
  retryDraftSave():Promise<SupervisorConversationClientView>{
    check(!this.closed&&!this.closing&&!this.saving&&!this.draftInspection&&!this.localMutation,'SUPERVISOR_CLIENT_NOT_READY');
    check(!this.casConflict,'SUPERVISOR_CLIENT_DRAFT_CONFLICT');
    check(this.draftSaveRecovery()==='RETRY_ALLOWED','SUPERVISOR_CLIENT_DRAFT_RECOVERY_REQUIRED');
    this.basis=this.failedSave!.owner.revision;this.failedSave=undefined;this.conflict=false;this.errorValue=undefined;this.publish();
    return this.flush();
  }
  private rememberClear(question:string){const draft=this.snapshotValue?.draft;if(draft&&draft.text===question){const lease={question,revision:draft.revision};this.clearLeases.push(lease);if(this.clearLeases.length>128)this.clearLeases.shift();return lease;}}
  private memoryAskAdmission(text:string,chosen:unknown,excluding?:string):SupervisorAskAdmission{
    for(const [operationId,entry] of this.asks){
      if(operationId===excluding)continue;
      const observed=this.snapshotValue?.operations.find(row=>row.operation_id===operationId),question=this.snapshotValue?.messages.find(row=>row.operation_id===operationId&&row.role==='user');
      if(observed&&entry.requestSha===observed.request_sha256&&TERMINAL.has(observed.status)&&(!question||question.text===entry.body.text&&sameProfile(question.provider_profile_ref,entry.body.providerProfileRef)))continue;
      if(!entry.result&&entry.dispatched||entry.result&&PENDING.has(entry.result.status))return {allowed:false,code:'SUPERVISOR_OPERATION_PENDING',operation_id:operationId};
      if(entry.result?.status==='UNKNOWN'&&entry.body.text===text&&sameProfile(entry.body.providerProfileRef,chosen))return {allowed:false,code:'SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED',operation_id:operationId};
    }
    return {allowed:true};
  }
  private settledAskOperations():ReadonlySet<string>{
    const settled=new Set<string>();
    for(const [operationId,entry]of this.asks){
      if(!entry.result||!TERMINAL.has(entry.result.status))continue;
      const question=this.snapshotValue?.messages.find(row=>row.operation_id===operationId&&row.role==='user');
      if(!question||question.text===entry.body.text&&sameProfile(question.provider_profile_ref,entry.body.providerProfileRef))settled.add(operationId);
    }
    return settled;
  }
  askAdmission(text:string,providerProfileRef:SupervisorConversationAsk['providerProfileRef']):SupervisorAskAdmission{
    boundedText(text);const chosen=profile(providerProfileRef),memory=this.memoryAskAdmission(text,chosen);
    return memory.allowed?snapshotAskAdmission(this.snapshotValue,text,chosen,this.settledAskOperations()):memory;
  }
  ask(body:SupervisorConversationAsk):Promise<SupervisorCenterReply>{
    check(!this.closed&&!this.closing&&typeof body.operationId==='string'&&ID.test(body.operationId)&&boundedText(body.text).trim(),'SUPERVISOR_CLIENT_ASK_INVALID');const chosen=profile(body.providerProfileRef),reference=followup(body.followupRef);
    const prior=this.asks.get(body.operationId);
    if(prior){check(prior.body.text===body.text&&sameProfile(prior.body.providerProfileRef,chosen)&&sameFollowup(prior.body.followupRef,reference),'SUPERVISOR_CLIENT_OPERATION_MISMATCH');return prior.result?Promise.resolve(clone(prior.result)):prior.promise;}
    if(this.asks.size>=128)for(const [id,entry]of this.asks){if(entry.result&&TERMINAL.has(entry.result.status))this.asks.delete(id);if(this.asks.size<64)break;}
    check(this.asks.size<128,'SUPERVISOR_CLIENT_OPERATION_LIMIT');
    const original={operationId:body.operationId,text:body.text,providerProfileRef:chosen,...reference?{followupRef:reference}:{}};
    const pending=(async()=>{
      const verifyFollowup=async()=>{if(original.followupRef)await this.readOperation(original.followupRef);};
      if(original.followupRef){if(!this.snapshotValue)await this.start();await verifyFollowup();}
      await this.flush();if(!this.snapshotValue)await this.start();check(this.snapshotValue,'SUPERVISOR_CLIENT_READ_UNAVAILABLE');check(!this.closed&&!this.closing,'SUPERVISOR_CLIENT_CLOSED');
      await verifyFollowup();
      const memoryAdmission=this.memoryAskAdmission(original.text,chosen,original.operationId);check(memoryAdmission.allowed,memoryAdmission.allowed?'':memoryAdmission.code);
      const operations=this.snapshotValue?.operations??[];
      const existing=operations.find(row=>row.operation_id===original.operationId);
      if(existing){
        const question=this.snapshotValue?.messages.find(row=>row.role==='user'&&row.operation_id===original.operationId),answer=this.snapshotValue?.messages.find(row=>row.role==='assistant'&&row.operation_id===original.operationId);
        check(question&&question.text===original.text&&sameProfile(question.provider_profile_ref,chosen)&&sameFollowup(existing.followup_ref,original.followupRef),'SUPERVISOR_CLIENT_OPERATION_MISMATCH');
        return {operation_id:original.operationId,status:answer?.status??'UNKNOWN',text:answer?.text??''};
      }
      const snapshotAdmission=snapshotAskAdmission(this.snapshotValue,original.text,chosen,this.settledAskOperations());check(snapshotAdmission.allowed,snapshotAdmission.allowed?'':snapshotAdmission.code);
      if(this.errorValue?.code==='SUPERVISOR_CLIENT_ASK_NOT_DISPATCHED'){this.errorValue=undefined;this.publish();}
      const clearLease=this.rememberClear(original.text),dispatchBasis=this.snapshotValue;
      const owner=this.asks.get(original.operationId);if(owner)owner.dispatched=true;
      let result:SupervisorCenterReply;
      try{const raw=object(await this.bridge.ask(original));
        if(raw.dispatch_proof!==undefined||raw.error==='SUPERVISOR_STORAGE_FULL'){
          const proof=object(raw.dispatch_proof),basis=object(proof.basis),exact=(row:Record<string,unknown>,keys:string[])=>Object.keys(row).sort().join(',')===keys.sort().join(',');
          check(exact(raw,['operation_id','status','error','dispatch_proof'])&&raw.status==='REJECTED'&&raw.error==='SUPERVISOR_STORAGE_FULL'&&exact(proof,['version','outcome','conversation_id','operation_id','input_sha256','basis'])&&exact(basis,['revision','sha256'])&&proof.version===1&&proof.outcome==='NOT_DISPATCHED'&&proof.operation_id===original.operationId&&proof.conversation_id===dispatchBasis.conversation_id&&typeof dispatchBasis.state_sha256==='string'&&basis.sha256===dispatchBasis.state_sha256&&basis.revision===dispatchBasis.revision&&proof.input_sha256===await inputDigest(original),'SUPERVISOR_CLIENT_ASK_UNCONFIRMED');
          check(!this.closed&&!this.conflict&&this.snapshotValue?.conversation_id===dispatchBasis.conversation_id&&this.snapshotValue.revision===dispatchBasis.revision&&this.snapshotValue.state_sha256===dispatchBasis.state_sha256,'SUPERVISOR_CLIENT_ASK_UNCONFIRMED');
          this.clearLeases=this.clearLeases.filter(lease=>lease!==clearLease);
        }
        result=receipt(raw,original.operationId);const bound=this.asks.get(original.operationId);
        if(raw.request_sha256!==undefined){check(typeof raw.request_sha256==='string'&&SHA.test(raw.request_sha256)&&(!bound?.requestSha||bound.requestSha===raw.request_sha256),'SUPERVISOR_CLIENT_ASK_UNCONFIRMED');if(bound)bound.requestSha=raw.request_sha256;}
      }
      catch{result={operation_id:original.operationId,status:'UNKNOWN',error:'SUPERVISOR_CLIENT_ASK_UNCONFIRMED'};}
      const entry=this.asks.get(original.operationId);if(entry){if(entry.result&&TERMINAL.has(entry.result.status))result=entry.result;else entry.result=result;}
      if(!this.closed){await this.getSnapshot();if(entry?.result&&TERMINAL.has(entry.result.status))result=entry.result;if(result.status==='UNKNOWN')this.setError('SUPERVISOR_CLIENT_ASK_UNCONFIRMED',original.operationId);else if(result.error==='SUPERVISOR_STORAGE_FULL')this.setError('SUPERVISOR_CLIENT_ASK_NOT_DISPATCHED',original.operationId);}
      return result;
    })();
    this.asks.set(original.operationId,{body:clone(original),promise:pending});pending.catch(()=>{const owner=this.asks.get(original.operationId);if(owner&&!owner.dispatched&&owner.promise===pending)this.asks.delete(original.operationId);});return pending;
  }
  inspect(operationId:string):Promise<SupervisorCenterReply>{
    check(!this.closed&&ID.test(operationId),'SUPERVISOR_CLIENT_ASK_INVALID');
    if(this.inspections.has(operationId))return this.inspections.get(operationId)!;
    check(this.asks.has(operationId)||this.snapshotValue?.operations.some(row=>row.operation_id===operationId),'SUPERVISOR_CLIENT_OPERATION_NOT_FOUND');
    const pending=(async()=>{
      let result:SupervisorCenterReply;
      try{result=receipt(await this.bridge.inspect(operationId),operationId);}catch{result={operation_id:operationId,status:'UNKNOWN',error:'SUPERVISOR_CLIENT_INSPECT_UNCONFIRMED'};}
      const entry=this.asks.get(operationId);if(entry){if(entry.result&&TERMINAL.has(entry.result.status))result=entry.result;else entry.result=result;}
      if(!this.closed){await this.getSnapshot();if(entry?.result&&TERMINAL.has(entry.result.status))result=entry.result;if(result.status!=='UNKNOWN'&&this.errorValue?.operation_id===operationId){this.errorValue=undefined;this.publish();}}
      return result;
    })();
    this.inspections.set(operationId,pending);pending.finally(()=>{if(this.inspections.get(operationId)===pending)this.inspections.delete(operationId);}).catch(()=>{});return pending;
  }
  async persistLocal(body:SupervisorConversationLocal){
    check(!this.closed&&!this.closing,'SUPERVISOR_CLIENT_CLOSED');boundedText(body.question);boundedText(typeof body.answer==='string'?body.answer:body.answer.text,24000);
    await this.flush();if(!this.snapshotValue)await this.start();check(this.snapshotValue,'SUPERVISOR_CLIENT_READ_UNAVAILABLE');check(!this.closed&&!this.closing,'SUPERVISOR_CLIENT_CLOSED');check(!this.localMutation,'SUPERVISOR_OPERATION_PENDING');this.rememberClear(body.question);
    let release!:()=>void;const mutation={done:new Promise<void>(resolve=>{release=resolve;}),release:()=>release()};this.localMutation=mutation;this.cancelTimer();
    try{const value=supervisorClientSnapshot(await this.bridge.persistLocal(clone(body)));if(!this.closed)this.accept(value);return this.view;}
    catch(error){this.setError(errorCode(error,'SUPERVISOR_CLIENT_LOCAL_SAVE_UNCONFIRMED'));throw error;}
    finally{if(this.localMutation===mutation)this.localMutation=undefined;mutation.release();this.schedule();}
  }
  async close(){
    if(this.closed)return;this.closing=true;this.cancelTimer();
    try{if(this.dirty||this.savePending)await this.flush();this.closed=true;this.searchGeneration++;this.searchValue=undefined;this.historyGeneration++;this.historyValue.messages=[];this.historyValue.operations=[];this.release?.();this.release=undefined;this.cancelTimer();}
    catch(error){this.closing=false;throw error;}
  }
}

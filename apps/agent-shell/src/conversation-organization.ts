import type {HistoryItem,HistorySource} from './conversation-history';

export type OrganizationIntent={action:'archive'|'restore'}|{action:'move';targetProjectId:string|null}|{action:'delete';confirmed:true};
type Action=OrganizationIntent['action'];
type Body={operation_id:string;action:Action;expected_revision:number;source:HistorySource;target_project_id?:string|null;delete_scope?:'CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES'};
type Pending={conversationId:string;title:string;projectId?:string|null;archived?:boolean;request:Body;checksum:string;phase:'ATTEMPTED'|'NOT_FOUND'};
export type OrganizationReceipt={version:1|2;operation_id:string;request_sha256:string;conversation_id:string;action:Action;organization:{revision:number;archived:boolean};project_id:string|null;execution_authorized:false;transcript_changed:false;files_changed:false;updated_at:string;source_project_id?:string|null;conversation_removed?:boolean;execution_records_retained?:true;project_files_retained?:true;existing_execution_scope_changed?:false;execution_stop_requested?:false};
type Transport={request<T>(path:string,method?:string,body?:unknown,signal?:AbortSignal,retryUnauthorized?:boolean):Promise<T>};
type StorageLike=Pick<Storage,'getItem'|'setItem'>;
const KEY='eeas-conversation-organization-v1',IDENT=/^(native|original|discovery):[a-f0-9]{64}$/;
const projectId=(value:unknown)=>value===null||typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value);
function validRequest(p:Pending){
 const r=p.request,move=r.action==='move',remove=r.action==='delete';
 return (move===Object.hasOwn(r,'target_project_id'))&&(!move||projectId(r.target_project_id)&&r.target_project_id!==p.projectId)&&
  (remove===Object.hasOwn(r,'delete_scope'))&&(!remove||r.delete_scope==='CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES')&&
  (!move&&!remove||Object.hasOwn(p,'projectId')&&typeof p.archived==='boolean');
}
function canonical(value:unknown):string{
 if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
 if(value&&typeof value==='object')return '{'+Object.keys(value).sort().map(key=>JSON.stringify(key)+':'+canonical((value as Record<string,unknown>)[key])).join(',')+'}';
 return JSON.stringify(value);
}
async function checksum(value:unknown){return [...new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(canonical(value))))].map(v=>v.toString(16).padStart(2,'0')).join('');}
const clone=<T>(value:T):T=>JSON.parse(JSON.stringify(value));
export class ConversationOrganizationClient{
 pending:Pending|null=null;error='';busy=false;private raw:string|null=null;
 constructor(private api:Transport,private storage:StorageLike,private changed:()=>void=()=>{},private locks:Pick<LockManager,'request'>|null=globalThis.navigator?.locks??null,private operation=()=>crypto.randomUUID(),private timeoutMs=15000){this.reload();}
 reload(){try{this.hydrate();this.error='';}catch{this.error='ORGANIZATION_STORAGE_UNAVAILABLE';}this.changed();}
 private hydrate(){
  const raw=this.storage.getItem(KEY);if(raw!==null&&raw.length>32768)throw Error('ORGANIZATION_STORAGE_UNAVAILABLE');
  const value=raw===null?{version:1,pending:null}:JSON.parse(raw),p=value.pending;
  if(value.version!==1||p!==null&&(!p||!IDENT.test(p.conversationId)||typeof p.title!=='string'||p.title.length>500||!p.request||!['archive','restore','move','delete'].includes(p.request.action)||!Number.isSafeInteger(p.request.expected_revision)||p.request.expected_revision<0||typeof p.request.operation_id!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(p.request.operation_id)||!p.request.source||!['native','original','discovery','conversation'].includes(p.request.source.kind)||Object.hasOwn(p,'projectId')&&!projectId(p.projectId)||!validRequest(p)||!['ATTEMPTED','NOT_FOUND'].includes(p.phase)||typeof p.checksum!=='string'||!/^[a-f0-9]{64}$/.test(p.checksum)))throw Error('ORGANIZATION_STORAGE_UNAVAILABLE');
  this.raw=raw;this.pending=p;
 }
 private save(pending:Pending|null){
  if(this.storage.getItem(KEY)!==this.raw)throw Error('ORGANIZATION_STORAGE_CONFLICT');
  const next=JSON.stringify({version:1,pending});if(next.length>32768)throw Error('ORGANIZATION_STORAGE_UNAVAILABLE');
  this.storage.setItem(KEY,next);if(this.storage.getItem(KEY)!==next)throw Error('ORGANIZATION_STORAGE_CONFLICT');this.raw=next;this.pending=pending;this.changed();
 }
 private async locked<T>(work:()=>Promise<T>):Promise<T>{
  if(this.busy)throw Error('ORGANIZATION_BUSY');if(!this.locks)throw Error('ORGANIZATION_LOCK_UNAVAILABLE');
  this.busy=true;this.error='';this.changed();const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),5000);
  try{return await this.locks.request(KEY,{mode:'exclusive',signal:controller.signal},async()=>{clearTimeout(timer);this.hydrate();return work();});}
  catch(error){this.error=error instanceof Error&&error.message.startsWith('ORGANIZATION_')?error.message:'ORGANIZATION_CHECK_REQUIRED';this.changed();throw error;}
  finally{clearTimeout(timer);this.busy=false;this.changed();}
 }
 private async validate(value:unknown,p:Pending):Promise<OrganizationReceipt>{
  const r=value as OrganizationReceipt,expected=await checksum({conversation_id:p.conversationId,...p.request});
  const advanced=p.request.action==='move'||p.request.action==='delete',target=p.request.action==='move'?p.request.target_project_id:Object.hasOwn(p,'projectId')?p.projectId:p.request.source.project_id;
  const archived=advanced?p.archived:p.request.action==='archive';
  if(expected!==p.checksum||!r||r.version!==(advanced?2:1)||r.operation_id!==p.request.operation_id||r.request_sha256!==expected||r.conversation_id!==p.conversationId||r.action!==p.request.action||r.project_id!==target||r.organization?.revision!==p.request.expected_revision+1||r.organization.archived!==archived||r.execution_authorized!==false||r.transcript_changed!==false||r.files_changed!==false||typeof r.updated_at!=='string'||!Number.isFinite(Date.parse(r.updated_at))||advanced&&(r.source_project_id!==p.request.source.project_id||r.conversation_removed!==(p.request.action==='delete')||r.execution_records_retained!==true||r.project_files_retained!==true||r.existing_execution_scope_changed!==false||r.execution_stop_requested!==false))throw Error('ORGANIZATION_RECEIPT_MISMATCH');
  return r;
 }
 private async send(p:Pending){
  let value:unknown;
  try{value=await this.bounded(signal=>this.api.request('/api/conversations/'+encodeURIComponent(p.conversationId)+'/organization','POST',p.request,signal,false));}
  catch(error){const status=(error as {status?:number})?.status;if(typeof status==='number'&&[400,401,403,404,409,413,422].includes(status)){this.save(null);throw Error(status===409?'ORGANIZATION_REFRESH_REQUIRED':'ORGANIZATION_REJECTED');}throw error;}
  return this.finish(await this.validate(value,p));
 }
 private async bounded(work:(signal:AbortSignal)=>Promise<unknown>){
  const controller=new AbortController();let timer:ReturnType<typeof setTimeout>|undefined;
  try{return await Promise.race([work(controller.signal),new Promise<never>((_resolve,reject)=>{timer=setTimeout(()=>{controller.abort();reject(Error('ORGANIZATION_CHECK_REQUIRED'));},this.timeoutMs);})]);}
  finally{if(timer!==undefined)clearTimeout(timer);}
 }
 private finish(result:OrganizationReceipt){try{this.save(null);}catch{this.error='ORGANIZATION_RECOVERY_SAVE_FAILED';this.changed();}return result;}
 change(item:HistoryItem,intent:OrganizationIntent|'archive'|'restore'){return this.locked(async()=>{
  if(this.pending)throw Error('ORGANIZATION_CHECK_REQUIRED');
  const choice=typeof intent==='string'?{action:intent}:intent,action=choice.action;
  if(!IDENT.test(item.id)||!item.organization||!Number.isSafeInteger(item.organization.revision)||item.organization.revision<0||!['archive','restore','move','delete'].includes(action)||['archive','restore'].includes(action)&&item.organization.archived!==(action==='restore'))throw Error('ORGANIZATION_REFRESH_REQUIRED');
  if(action==='move'&&(!('targetProjectId'in choice)||!projectId(choice.targetProjectId)||choice.targetProjectId===(item.projectId??null))||action==='delete'&&(!('confirmed'in choice)||choice.confirmed!==true))throw Error('ORGANIZATION_CONFIRM_REQUIRED');
  const request:Body={operation_id:this.operation(),action,expected_revision:item.organization.revision,source:clone(item.source)};
  if(choice.action==='move')request.target_project_id=choice.targetProjectId;
  if(choice.action==='delete')request.delete_scope='CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES';
  const p:Pending={conversationId:item.id,title:item.title.slice(0,500),projectId:item.projectId??null,archived:item.organization.archived,request,checksum:await checksum({conversation_id:item.id,...request}),phase:'ATTEMPTED'};
  this.save(p);return this.send(p);
 });}
 inspect(){return this.locked(async()=>{
  const p=this.pending;if(!p)throw Error('ORGANIZATION_NOT_PENDING');
  let value:unknown;
  try{value=await this.bounded(signal=>this.api.request('/api/conversation-organization/operations/'+encodeURIComponent(p.request.operation_id),'GET',undefined,signal));}
  catch(error){if((error as {status?:number})?.status===404){this.save({...p,phase:'NOT_FOUND'});throw Error('ORGANIZATION_NOT_FOUND');}throw error;}
  return this.finish(await this.validate(value,p));
 });}
 retry(){return this.locked(async()=>{const p=this.pending;if(!p||p.phase!=='NOT_FOUND')throw Error('ORGANIZATION_CHECK_REQUIRED');if(await checksum({conversation_id:p.conversationId,...p.request})!==p.checksum)throw Error('ORGANIZATION_RECEIPT_MISMATCH');this.save({...p,phase:'ATTEMPTED'});return this.send(p);});}
}

import {operationId} from './api.ts';
import {AgentSessionOutcomeError,originalSessionReady,type AgentSession,type AgentSessionOperation,type PendingRequest} from './agent-sessions.ts';
import {DiscussionDraftLease,chooseDraft,type DiscussionDraftKey} from './discussion-drafts.ts';
import {sameNativeReference} from './native-collaboration.ts';

type StoragePort=Pick<Storage,'getItem'|'setItem'>;
export type ControlLocks={request<T>(name:string,options:{mode:'exclusive';signal:AbortSignal},work:()=>Promise<T>):Promise<T>};
export type ControlClient={pendingReply(session:AgentSession,item:PendingRequest,choice:string,answers:string[][]|undefined,key:string):Promise<AgentSessionOperation>;operation(session:AgentSession,key:string):Promise<AgentSessionOperation>};
export type ControlIdentity={project:string;session:AgentSession['session_ref'];generation:number;parent:string;request:PendingRequest['request_ref'];kind:string};
export type ControlAttempt={identity:ControlIdentity;operationId:string;choice:string;status:string;sourceChanged:boolean;recoveryPrepared?:boolean};
type ControlIndex={version:1;attempts:ControlAttempt[]};
const canonical=(v:unknown):string=>Array.isArray(v)?'['+v.map(canonical).join(',')+']':v&&typeof v==='object'?'{'+Object.keys(v).sort().map(k=>JSON.stringify(k)+':'+canonical((v as Record<string,unknown>)[k])).join(',')+'}':JSON.stringify(v);
export const controlIdentity=(s:AgentSession,p:PendingRequest):ControlIdentity=>({project:s.project_id,session:{...s.session_ref},generation:s.generation,parent:s.active_operation_id??'',request:{...p.request_ref},kind:p.kind});
export const controlKey=(i:ControlIdentity)=>canonical(i);
const terminal=(status:string)=>['RETURNED','COMPLETED','FAILED','CANCELLED','REJECTED'].includes(status);
const indexKey=(s:Pick<AgentSession,'project_id'|'session_ref'>)=>'eeas-original-controls-v1:'+canonical([s.project_id,s.session_ref]);
function current(s:AgentSession,p:PendingRequest){
 if(!originalSessionReady(s)||!s.active_operation_id||!['QUESTION','PERMISSION'].includes(p.kind)||p.request_ref.generation!==s.generation||!/^([a-f0-9]{64})$/.test(p.request_ref.sha256)||![...s.pending_permissions,...s.pending_questions].some(v=>v.kind===p.kind&&canonical(v.request_ref)===canonical(p.request_ref)))throw Error('CONTROL_REQUEST_CHANGED');
}
/** A durable client observation index, never permission authority. The original
 * API remains the sole executor. Unknown results only query the original key. */
export class OriginalControlDecisions{
 private storage:StoragePort;private client:ControlClient;private locks:ControlLocks|undefined;private changed:()=>void;private running=new Set<string>();
 constructor(storage:StoragePort,client:ControlClient,locks:ControlLocks|undefined,changed:()=>void=()=>{}){this.storage=storage;this.client=client;this.locks=locks;this.changed=changed;}
 private read(s:AgentSession){const raw=this.storage.getItem(indexKey(s));if(raw!==null&&raw.length>262144)throw Error('CONTROL_STORAGE_UNAVAILABLE');const value:ControlIndex=raw===null?{version:1,attempts:[]}:JSON.parse(raw);if(value.version!==1||!Array.isArray(value.attempts)||value.attempts.length>128)throw Error('CONTROL_STORAGE_UNAVAILABLE');for(const a of value.attempts){if(!a||typeof a.operationId!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(a.operationId)||typeof a.status!=='string'||typeof a.choice!=='string'||typeof a.sourceChanged!=='boolean'||a.recoveryPrepared!==undefined&&typeof a.recoveryPrepared!=='boolean'||a.identity?.project!==s.project_id||!sameNativeReference(a.identity?.session,s.session_ref)||!Number.isSafeInteger(a.identity.generation)||a.identity.generation<1||typeof a.identity.parent!=='string'||!a.identity.parent||typeof a.identity.request?.id!=='string'||!a.identity.request.id||!['QUESTION','PERMISSION'].includes(a.identity.kind)||a.identity.request?.generation!==a.identity.generation||!/^([a-f0-9]{64})$/.test(a.identity.request?.sha256))throw Error('CONTROL_STORAGE_UNAVAILABLE');}return {raw,value};}
 private save(s:AgentSession,basis:string|null,value:ControlIndex){const raw=JSON.stringify(value);if(raw.length>262144||this.storage.getItem(indexKey(s))!==basis)throw Error('CONTROL_STORAGE_CONFLICT');this.storage.setItem(indexKey(s),raw);if(this.storage.getItem(indexKey(s))!==raw)throw Error('CONTROL_STORAGE_UNCONFIRMED');this.changed();}
 attempts(s:AgentSession){return this.read(s).value.attempts;}
 latest(s:AgentSession,p:PendingRequest){const key=controlKey(controlIdentity(s,p));return this.attempts(s).filter(a=>controlKey(a.identity)===key).at(-1);}
 unresolved(s:AgentSession){return this.attempts(s).filter(a=>!terminal(a.status));}
 recoveries(s:AgentSession){const groups=new Map<string,ControlAttempt[]>();for(const a of this.attempts(s)){const key=controlKey(a.identity);groups.set(key,[...groups.get(key)??[],a]);}return [...groups.values()].flatMap(rows=>{const last=rows.at(-1)!;return last.identity.generation===s.generation&&last.choice==='reject'&&last.status==='RETURNED'&&!last.recoveryPrepared&&rows.some(a=>a.sourceChanged)?[last]:[];});}
 async recoveryPrepared(s:AgentSession,a:ControlAttempt){return this.locked(s,async()=>{const old=this.read(s);if(!old.value.attempts.some(v=>v.operationId===a.operationId&&controlKey(v.identity)===controlKey(a.identity)))throw Error('CONTROL_STORAGE_CONFLICT');this.save(s,old.raw,{version:1,attempts:old.value.attempts.map(v=>v.operationId===a.operationId?{...v,recoveryPrepared:true}:v)});});}
 busy(s:AgentSession){return this.running.has(indexKey(s));}
 private async locked<T>(s:AgentSession,work:()=>Promise<T>){const key=indexKey(s);if(!this.locks||this.running.has(key))throw Error('CONTROL_BUSY_OR_LOCK_UNAVAILABLE');this.running.add(key);this.changed();const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),5000);try{return await this.locks.request(key,{mode:'exclusive',signal:abort.signal},async()=>{clearTimeout(timer);return work();});}finally{clearTimeout(timer);this.running.delete(key);this.changed();}}
 async decide(session:AgentSession,item:PendingRequest,choice:string,answers?:string[][]){
  const s={...session,session_ref:{...session.session_ref}},p=JSON.parse(JSON.stringify(item)) as PendingRequest,selected=answers?.map(row=>[...row]);current(s,p);
  if(!p.allowed_choices.includes(choice)||p.kind==='QUESTION'&&!['reply','reject'].includes(choice)||p.kind==='PERMISSION'&&!['once','always','reject'].includes(choice))throw Error('CONTROL_CHOICE_INVALID');
  return this.locked(s,async()=>{current(s,p);const old=this.read(s),identity=controlIdentity(s,p),key=controlKey(identity),prior=old.value.attempts.filter(a=>controlKey(a.identity)===key).at(-1);
   if(prior&&(!terminal(prior.status)||['RETURNED','COMPLETED'].includes(prior.status)||prior.sourceChanged&&choice!=='reject'))throw Error('CONTROL_CHECK_OR_REJECT_REQUIRED');
   const entries=old.value.attempts.length<128?old.value.attempts:old.value.attempts.filter(a=>!terminal(a.status));if(entries.length>=128)throw Error('CONTROL_STORAGE_CAPACITY');
   const attempt:ControlAttempt={identity,operationId:operationId(),choice,status:'ATTEMPTED',sourceChanged:false};this.save(s,old.raw,{version:1,attempts:[...entries,attempt]});
   try{const receipt=await this.client.pendingReply(s,p,choice,selected,attempt.operationId);this.finish(s,attempt,receipt.status,false);return receipt;}
   catch(error){if(error instanceof AgentSessionOutcomeError){const changed=error.failureCode==='WORKSPACE_SOURCE_CHANGED';if(error.receipt||changed)this.finish(s,attempt,error.receipt?.status??'ATTEMPTED',changed);if(error.receipt&&['RETURNED','COMPLETED'].includes(error.receipt.status))return error.receipt;}throw error;}
  });
 }
 private finish(s:AgentSession,a:ControlAttempt,status:string,sourceChanged:boolean){const old=this.read(s),found=old.value.attempts.find(v=>v.operationId===a.operationId);if(!found||controlKey(found.identity)!==controlKey(a.identity))throw Error('CONTROL_STORAGE_CONFLICT');this.save(s,old.raw,{version:1,attempts:old.value.attempts.map(v=>v.operationId===a.operationId?{...v,status,sourceChanged}:v)});}
 async inspect(session:AgentSession,attempt:ControlAttempt){return this.locked(session,async()=>{const saved=this.attempts(session).find(v=>v.operationId===attempt.operationId);if(!saved||controlKey(saved.identity)!==controlKey(attempt.identity))throw Error('CONTROL_STORAGE_CONFLICT');const receipt=await this.client.operation(session,attempt.operationId);this.finish(session,saved,receipt.status,saved.sourceChanged||receipt.error_code==='WORKSPACE_SOURCE_CHANGED');return receipt;});}
}

export type QuestionItem={question:string;multiple?:boolean;custom?:boolean;options:{label:string;description?:string}[]};
export function controlQuestions(p:PendingRequest):QuestionItem[]{const q=p.native_request.questions;if(!Array.isArray(q)||!q.length||q.length>128||q.some(v=>!v||typeof v.question!=='string'||!Array.isArray(v.options)||v.options.some((o:{label?:unknown;description?:unknown})=>!o||typeof o.label!=='string'||o.description!==undefined&&typeof o.description!=='string')))return [];return q;}
export type ControlDraft={answers:string[][];custom:string[]};
const draftRegistry=new Set<ControlQuestionDraft>();
const draftListeners=new Set<()=>void>();
export function onControlQuestionDraftChange(listener:()=>void){draftListeners.add(listener);return()=>{draftListeners.delete(listener);};}
const draftChanged=()=>{for(const listener of draftListeners)listener();};
export function controlDraftScope(i:ControlIdentity):DiscussionDraftKey{
 const hash=btoa(String.fromCharCode(...i.request.sha256.match(/../g)!.map(h=>parseInt(h,16)))).replaceAll('+','-').replaceAll('/','_').replaceAll('=','');
 return {kind:'original',projectId:i.project,id:`ctl_${i.session.id}_${i.generation}_${i.parent}_${hash}`};
}
export class ControlQuestionDraft{
 private storage:StoragePort;private lease:DiscussionDraftLease;private scope:DiscussionDraftKey;private identity:ControlIdentity;private labels:string[];private draft:ControlDraft={answers:[],custom:[]};dirty=false;
 constructor(storage:StoragePort,i:ControlIdentity,labels:string[]=[]){this.storage=storage;this.identity=JSON.parse(JSON.stringify(i));this.labels=[...labels];this.scope=controlDraftScope(i);this.lease=new DiscussionDraftLease(storage,this.scope);this.draft=this.decode(this.lease.read().text);}
 private decode(raw:string){if(!raw)return {answers:[],custom:[]};const saved=JSON.parse(raw),v=saved.draft as ControlDraft;if(canonical(saved.identity)!==canonical(this.identity)||!v||!Array.isArray(v.answers)||!Array.isArray(v.custom)||v.answers.some(a=>!Array.isArray(a)||a.some(t=>typeof t!=='string'))||v.custom.some(t=>typeof t!=='string'))throw Error('CONTROL_DRAFT_UNAVAILABLE');if(!this.labels.length&&Array.isArray(saved.questions)&&saved.questions.length<=128&&saved.questions.every((s:unknown)=>typeof s==='string'))this.labels=[...saved.questions];return v;}
 private encoded(){return {text:JSON.stringify({identity:this.identity,draft:this.draft,...this.labels.length?{questions:this.labels}:{}}),attachments:[]};}
 read(){return structuredClone(this.draft);}
 set(value:ControlDraft){this.draft={answers:Array.from({length:value.answers.length},(_,i)=>i in value.answers?structuredClone(value.answers[i]):[]),custom:Array.from({length:value.custom.length},(_,i)=>i in value.custom?value.custom[i]:'')};this.dirty=true;draftRegistry.add(this);this.flush();}
 flush(){if(!this.dirty)return;this.lease.save(this.encoded());this.dirty=false;draftRegistry.delete(this);draftChanged();}
 inspect(){return this.decode(new DiscussionDraftLease(this.storage,this.scope).read().text);}
 resolve(choice:'local'|'saved'){const latest=new DiscussionDraftLease(this.storage,this.scope);const chosen=chooseDraft(this.storage,latest,this.encoded(),choice);this.draft=this.decode(chosen.text);this.lease=latest;this.dirty=false;draftRegistry.delete(this);draftChanged();return this.read();}
}
export function flushOriginalControlDrafts(){for(const draft of draftRegistry)draft.flush();}
export function questionAnswers(p:PendingRequest,draft:ControlDraft){const q=controlQuestions(p);if(!q.length)throw Error('CONTROL_QUESTIONS_UNAVAILABLE');return q.map((item,index)=>{const labels=draft.answers[index]??[],custom=draft.custom[index]??'';if(labels.some(v=>!item.options.some(o=>o.label===v))||new Set(labels).size!==labels.length||item.custom===false&&!!custom.trim())throw Error('CONTROL_ANSWERS_INVALID');const answer=custom.trim()?(item.multiple?[...new Set([...labels,custom])]:[custom]):labels;if(!answer.length||!item.multiple&&answer.length!==1)throw Error('CONTROL_ANSWERS_REQUIRED');return answer;});}
export type PendingPatch={path:string;patch:string};
export function pendingPatches(item:PendingRequest):PendingPatch[]{const m=item.native_request.metadata as {files?:unknown[];diff?:unknown;filepath?:unknown}|undefined;if(!m)return [];if(Array.isArray(m.files)&&m.files.length){if(m.files.some(v=>!v||typeof(v as {patch?:unknown}).patch!=='string'||typeof((v as {relativePath?:unknown;filePath?:unknown}).relativePath??(v as {filePath?:unknown}).filePath)!=='string'))return [];return m.files.map(v=>{const f=v as {patch:string;relativePath?:string;filePath?:string};return {path:f.relativePath??f.filePath!,patch:f.patch};});}return typeof m.diff==='string'&&typeof m.filepath==='string'?[{path:m.filepath,patch:m.diff}]:[];}

/** Keep the full original scope available while showing the actual changed lines first. */
export const pendingFileName=(path:string)=>path.split(/[\\/]/).filter(Boolean).at(-1)??path;
export function pendingActionTitle(item:PendingRequest,locale:string):string{
 const labels:Record<string,[string,string]>={edit:['确认文件修改','Review file changes'],read:['读取文件内容','Read file contents'],glob:['查找文件','Find files'],grep:['搜索文件内容','Search file contents'],list:['查看目录','List directory'],bash:['运行命令','Run command']};
 const label=item.kind==='QUESTION'?['需要你的回答','Your answer is needed']:labels[String(item.native_request.permission??'')]??['确认工具操作','Review tool action'];
 return label[locale.startsWith('en')?1:0];
}
export function pendingDiffBody(patch:string){const lines=patch.split('\n'),hunk=lines.findIndex(line=>/^@@ -\d/.test(line));return hunk<0?patch:lines.slice(hunk).join('\n');}

export function pendingDiffPreview(patch:string){const body=pendingDiffBody(patch);if(!/^@@ -\d/.test(body))return body;const changed=body.split('\n').filter(line=>/^[+-]/.test(line));return changed.length?changed.join('\n'):body;}

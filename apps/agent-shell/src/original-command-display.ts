import type {AgentSession,OriginalMessage} from './agent-sessions.ts';
import type {OriginalCommandDisplayCoverage} from './original-command-observations.ts';
import {originalFileScope} from './original-session-files.ts';

export function sameCommandDisplayScope(a:OriginalCommandDisplayCoverage,b:OriginalCommandDisplayCoverage):boolean{
 const x=a.scope,y=b.scope;
 return a.contextKey===b.contextKey&&a.original_session_id===b.original_session_id&&x.project_id===y.project_id&&x.generation===y.generation&&x.session_ref.id===y.session_ref.id&&x.session_ref.revision===y.session_ref.revision&&x.session_ref.sha256===y.session_ref.sha256&&x.workspace_ref.id===y.workspace_ref.id&&x.workspace_ref.revision===y.workspace_ref.revision&&x.workspace_ref.binding_sha256===y.workspace_ref.binding_sha256;
}

/** Presentation ownership only: a visible checked card replaces its caption. */
export function displayedCommandKeys(value:OriginalCommandDisplayCoverage|undefined,session:AgentSession|undefined,contextKey:string):ReadonlySet<string>{
 const keys=new Set<string>(),scope=session&&originalFileScope(session);
 if(!value||!session||!scope||typeof session.original_session_id!=='string'||!sameCommandDisplayScope(value,{contextKey,scope,original_session_id:session.original_session_id,entries:[]}))return keys;
 const wantedMessages=new Set(value.entries.map(row=>row.message_id)),wantedCalls=new Set(value.entries.map(row=>row.call_id));
 const messages=new Map<string,OriginalMessage>(),messageCounts=new Map<string,number>(),callCounts=new Map<string,number>();
 for(const message of session.messages){
  if(message.info.role!=='assistant'||message.info.sessionID!==value.original_session_id)continue;
  if(wantedMessages.has(message.info.id)){messages.set(message.info.id,message);messageCounts.set(message.info.id,(messageCounts.get(message.info.id)??0)+1);}
  for(const part of message.parts)if(part.type==='tool'&&part.tool==='bash'&&typeof part.callID==='string'&&wantedCalls.has(part.callID))callCounts.set(part.callID,(callCounts.get(part.callID)??0)+1);
 }
 for(const row of value.entries){
  const message=messages.get(row.message_id),key=JSON.stringify([value.original_session_id,row.message_id,row.part_id,row.call_id]);
  if(message&&messageCounts.get(row.message_id)===1&&callCounts.get(row.call_id)===1&&originalToolHasDisplayedResult(message,row.part_id,new Set([key])))keys.add(key);
 }
 return keys;
}

export function originalToolHasDisplayedResult(message:OriginalMessage,partId:string|undefined,keys:ReadonlySet<string>|undefined):boolean{
 if(!keys?.size||message.info.role!=='assistant'||!partId)return false;
 const parts=message.parts.filter(part=>part.id===partId);
 if(parts.length!==1)return false;
 const part=parts[0],status=(part.state as {status?:unknown}|undefined)?.status;
 if(part.type!=='tool'||part.tool!=='bash'||!['completed','error'].includes(String(status))||typeof part.callID!=='string'||part.sessionID!==message.info.sessionID||part.messageID!==message.info.id)return false;
 if(message.parts.filter(value=>value.type==='tool'&&value.tool==='bash'&&value.callID===part.callID).length!==1)return false;
 return keys.has(JSON.stringify([message.info.sessionID,message.info.id,part.id,part.callID]));
}

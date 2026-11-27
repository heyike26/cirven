import type {NativeRef} from './native-collaboration';
import type {QuickRecord,QuickScope} from './quick-inbox';
import {layoutReadingOffset} from './chat-workbench-layout.ts';

export type QuickReplyTarget={kind:'QUICK_REPLY';operationId:string;draftRef:NativeRef;scope:Pick<QuickScope,'projectId'|'workspaceId'|'sessionId'|'conversationId'>};
const id=(value:unknown):value is string=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value);
const object=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
export function quickReplyTarget(value:unknown):QuickReplyTarget|undefined{
 if(!object(value)||Object.keys(value).sort().join(',')!=='draftRef,kind,operationId,scope'||value.kind!=='QUICK_REPLY'||!id(value.operationId))return;
 const ref=value.draftRef,scope=value.scope;
 if(!object(ref)||Object.keys(ref).sort().join(',')!=='id,revision,sha256'||!id(ref.id)||!Number.isSafeInteger(ref.revision)||Number(ref.revision)<1||typeof ref.sha256!=='string'||!/^[a-f0-9]{64}$/.test(ref.sha256)||!object(scope)||!id(scope.conversationId))return;
 if(Object.keys(scope).some(key=>!['projectId','workspaceId','sessionId','conversationId'].includes(key)||!id(scope[key]))||(scope.workspaceId||scope.sessionId)&&!scope.projectId)return;
 return {kind:'QUICK_REPLY',operationId:value.operationId,draftRef:{id:ref.id,revision:Number(ref.revision),sha256:ref.sha256},scope:{...scope}} as QuickReplyTarget;
}
export const sameReplyScope=(a:QuickScope,b:QuickScope)=>['projectId','workspaceId','sessionId','conversationId'].every(key=>a[key as keyof QuickScope]===b[key as keyof QuickScope]);
type Options={root:()=>HTMLElement|undefined;scope:()=>QuickScope;records:()=>QuickRecord[];refresh:()=>Promise<unknown>;importRecord:(record:QuickRecord)=>void|Promise<void>;present:(ready:()=>void)=>void;unavailable:()=>void};

/** One explicit reading intent. Observation and result updates never call open. */
export class QuickReplyNavigation{
 private epoch=0;private release?:()=>void;private disposed=false;
 private options:Options;
 constructor(options:Options){this.options=options;}
 cancel(){this.epoch++;this.release?.();this.release=undefined;}
 dispose(){this.disposed=true;this.cancel();}
 async open(raw:unknown){
  this.cancel();const target=quickReplyTarget(raw),epoch=this.epoch;
  if(!target||this.disposed||!sameReplyScope(target.scope,this.options.scope()))return;
  const root=this.options.root();if(!root)return;
  const cancel=()=>this.cancel();
  for(const event of ['wheel','touchstart','pointerdown','keydown','compositionstart'])root.addEventListener(event,cancel,{capture:true,passive:true});
  this.release=()=>{for(const event of ['wheel','touchstart','pointerdown','keydown','compositionstart'])root.removeEventListener(event,cancel,true);};
  const current=()=>{const valid=!this.disposed&&epoch===this.epoch&&root===this.options.root()&&sameReplyScope(target.scope,this.options.scope());if(!valid&&epoch===this.epoch)this.cancel();return valid;};
  const record=()=>this.options.records().find(item=>item.delivery==='SEND'&&item.id===target.draftRef.id&&item.revision===target.draftRef.revision&&item.sha256===target.draftRef.sha256&&item.operationId===target.operationId&&item.message?.operation_id===target.operationId&&sameReplyScope(item.scope,target.scope));
  try{
   let item=record();if(!item){await this.options.refresh();if(!current())return;item=record();}
   if(!item){this.cancel();this.options.unavailable();return;}
   if(!current())return;await this.options.importRecord(item);if(!current())return;
   const expected=item.message?.original_source as QuickScope['originalSource'];
   const canonical=item.message?.canonical_original===true;
   this.options.present(()=>{
    if(!current())return;
    const conversation=root.querySelector<HTMLElement>('.as-conversation');
    const row=canonical?[...root.querySelectorAll<HTMLElement>('.as-thread-message[data-operation-id]')].find(node=>node.dataset.operationId===target.operationId&&node.dataset.role==='assistant'&&expected&&node.dataset.sourceProjectId===expected.project_id&&node.dataset.sourceSessionId===expected.session_ref.id):
      [...root.querySelectorAll<HTMLElement>('.as-native-message[data-operation-id]')].find(node=>node.dataset.operationId===target.operationId&&node.dataset.conversationId===target.scope.conversationId);
    const answer=canonical?row:row?.querySelector<HTMLElement>('.as-native-agent-message');
    if(!conversation?.isConnected||!conversation.clientHeight||!answer||!conversation.contains(answer)){this.cancel();this.options.unavailable();return;}
    const viewport=conversation.getBoundingClientRect(),composer=root.querySelector<HTMLElement>('.as-composer')?.getBoundingClientRect();
    let top=viewport.top+conversation.clientTop,bottom=Math.min(viewport.bottom,composer?.height?composer.top:Infinity);
    for(let parent=conversation.parentElement;parent;parent=parent.parentElement){const rect=parent.getBoundingClientRect();if(parent.clientHeight>0&&rect.height>0){top=Math.max(top,rect.top);bottom=Math.min(bottom,rect.bottom);}if(parent===root)break;}
    if(bottom-top<32){this.cancel();this.options.unavailable();return;}
    const start=viewport.top+layoutReadingOffset(answer,conversation);
    if(start<top+16||start>bottom-32)conversation.scrollTop=Math.max(0,Math.min(conversation.scrollHeight-conversation.clientHeight,conversation.scrollTop+start-top-16));
    this.cancel();
   });
  }catch{if(current()){this.cancel();this.options.unavailable();}}
 }
}

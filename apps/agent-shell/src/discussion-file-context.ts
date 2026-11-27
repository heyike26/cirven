import {nativeReference,type NativeCollaborationBridge,type NativeRef,type FileAssociationScope,type FileLinkRef,type QuickFileContextState} from './native-collaboration.ts';
const fields=['projectId','workspaceId','sessionId','conversationId'] as const;
const identity=(scope:FileAssociationScope)=>JSON.stringify(fields.map(key=>scope[key]??null));
export const sameDiscussionScope=(a:FileAssociationScope,b:FileAssociationScope)=>identity(a)===identity(b);
export const associationScope=(scope:FileAssociationScope):FileAssociationScope=>Object.fromEntries(fields.filter(key=>scope[key]!==undefined).map(key=>[key,scope[key]]));
export const sameFileLink=(a:FileLinkRef,b:FileLinkRef)=>a.project_id===b.project_id&&a.id===b.id&&a.revision===b.revision&&a.sha256===b.sha256;
/** A local conversation-index key, never an original-session execution route.
 * undefined keeps the existing direct key; null isolates unselected session mode. */
export function fileConversationBasis(scope:FileAssociationScope,originalSession?:NativeRef|null):FileAssociationScope{
 if(originalSession===undefined)return {projectId:scope.projectId,sessionId:scope.sessionId};
 return {projectId:scope.projectId,sessionId:'original:'+JSON.stringify(originalSession?[originalSession.id,originalSession.revision,originalSession.sha256]:[])};
}
function link(value:unknown,project?:string):value is FileLinkRef{const item=value as FileLinkRef;return nativeReference(item)&&typeof item.project_id==='string'&&item.project_id===project;}
function assertState(value:QuickFileContextState,expected:FileAssociationScope){
 if(!value||!sameDiscussionScope(value.scope??{},expected)||!Array.isArray(value.legacy_candidates)||value.legacy_candidates.length>1)throw new Error('文稿关联尚未与当前讨论核对，原稿和草稿保留。');
 const valid=(item:{fileLinkRef:FileLinkRef;filename?:string})=>item&&link(item.fileLinkRef,expected.projectId)&&(item.filename===undefined||typeof item.filename==='string'&&Array.from(item.filename).length<=240&&!/[\u0000-\u001f\u007f]/.test(item.filename));
 if(value.selected&&(!valid(value.selected)||!sameDiscussionScope(value.selected.scope,expected))||value.legacy_candidates.some(item=>!valid(item)))throw new Error('文稿关联回执不完整，原选择保留。');
 return value;
}
/** A projection of Main's existing journal, never a second file authority.
 * Mutations occur only on explicit clear/select. Unknown writes perform GET,
 * not POST replay. Late replies cannot change another discussion or draft. */
export class DiscussionFileContext{
 private scope:FileAssociationScope={};private generation=0;private disposed=false;private release?:()=>void;private flight?:Promise<void>;private dirty=false;private writing=false;
 state?:QuickFileContextState;
 private bridge:NativeCollaborationBridge;private changed:(state:QuickFileContextState|undefined)=>void;private failed:(message:string)=>void;
 constructor(bridge:NativeCollaborationBridge,changed:(state:QuickFileContextState|undefined)=>void,failed:(message:string)=>void){this.bridge=bridge;this.changed=changed;this.failed=failed;}
 start(){this.release=this.bridge.onQuickDraftsChanged?.(()=>void this.refresh());}
 setScope(scope:FileAssociationScope,read=true){const next=associationScope(scope);if(!sameDiscussionScope(next,this.scope)){this.generation++;this.scope=next;this.state=undefined;this.changed(undefined);}return read?this.refresh():Promise.resolve();}
 async refresh():Promise<void>{
  if(this.disposed||!this.bridge.getQuickFileContext)return;
  this.dirty=true;if(this.flight)return this.flight;
  this.flight=(async()=>{while(this.dirty&&!this.disposed){this.dirty=false;const generation=this.generation,scope=this.scope;
   try{const value=await this.bridge.getQuickFileContext!();if(!this.disposed&&generation===this.generation){this.state=assertState(value,scope);this.changed(this.state);this.failed('');}}
   catch{if(!this.disposed&&generation===this.generation)this.failed('文稿关联暂未核对，原选择和草稿保留。');}
  }})().finally(()=>{this.flight=undefined;});return this.flight;
 }
 async clear(){
  if(this.disposed||this.writing)throw new Error('正在核对此讨论的文稿选择，请稍候。');
  const state=this.state,scope=this.scope,generation=this.generation;if(!state?.selected||!this.bridge.clearQuickFileContext)throw new Error('当前讨论没有可清除的文稿关联。');
  this.writing=true;
  try{const value=await this.bridge.clearQuickFileContext({scope,expectedRef:state.selected.fileLinkRef});if(!this.disposed&&generation===this.generation){this.state=assertState(value,scope);this.changed(this.state);}}
  catch{if(!this.disposed&&generation===this.generation){await this.refresh();throw new Error('解除结果尚未确认；已核查原选择，未重复提交。');}}
  finally{this.writing=false;}
 }
 async select(reference:FileLinkRef){
  if(this.disposed||this.writing)throw new Error('正在核对此讨论的文稿选择，请稍候。');
  const state=this.state,scope=this.scope,generation=this.generation;
  if(!this.bridge.selectQuickFileContext||!state?.legacy_candidates.some(item=>sameFileLink(item.fileLinkRef,reference)))throw new Error('请从当前讨论可核对的文稿中明确选择。');
  this.writing=true;
  try{const value=await this.bridge.selectQuickFileContext({scope,fileLinkRef:reference});if(!this.disposed&&generation===this.generation){this.state=assertState(value,scope);this.changed(this.state);}}
  catch{if(!this.disposed&&generation===this.generation){await this.refresh();throw new Error('选择结果尚未确认；已核查原选择，未重复提交。');}}
  finally{this.writing=false;}
 }
 stop(){this.disposed=true;this.generation++;this.release?.();this.release=undefined;}
}

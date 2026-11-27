import {exactFileSource} from './companion-files.ts';
import {nativeReference,sameNativeReference,type NativeMessage,type NativeRef} from './native-collaboration.ts';
import {assertReworkRecord,reworkSource,type NativeReworkApi} from './native-rework.ts';
import type {NativeFeedbackApi} from './native-feedback-api.ts';
import type {SourceRef} from './contracts';

export type NativePreviewTarget={operationId:string;projectId:string;conversationId:string;source:SourceRef;planRef:NativeRef;mappingRef:NativeRef;feedbackRef:NativeRef};
// A stored PLAN is a view target, never an apply grant. NativeMessages already
// binds these records to this message; fresh reads below verify the full chain.
export function nativePreviewTargets(message:NativeMessage,pid?:string,cid?:string):NativePreviewTarget[]{
 if(!pid||!cid||typeof message.operation_id!=='string'||!/^([A-Za-z0-9_-]{1,100})$/.test(message.operation_id)||message.project_id!==pid||message.conversation_id!==cid||!reworkSource(message.file_source_ref)||message.file_source_ref.project_id!==pid||!Array.isArray(message.rework_previews))return [];
 const targets=new Map<string,NativePreviewTarget>();
 for(const value of message.rework_previews.slice(-24))try{const plan=assertReworkRecord(value,pid);if(plan.kind!=='PLAN'||!nativeReference(plan.payload.mapping_ref))continue;targets.set(JSON.stringify(plan.record_ref),{operationId:message.operation_id,projectId:pid,conversationId:cid,source:message.file_source_ref,planRef:plan.record_ref,mappingRef:plan.payload.mapping_ref,feedbackRef:plan.payload.native_feedback_ref});}catch{}
 return [...targets.values()];
}
export function sameNativePreview(a:NativePreviewTarget,b:NativePreviewTarget){return a.operationId===b.operationId&&a.projectId===b.projectId&&a.conversationId===b.conversationId&&exactFileSource(a.source,b.source)&&sameNativeReference(a.planRef,b.planRef)&&sameNativeReference(a.mappingRef,b.mappingRef)&&sameNativeReference(a.feedbackRef,b.feedbackRef);}
export async function readNativePreview(target:NativePreviewTarget,rework:Pick<NativeReworkApi,'get'>,feedbackApi:Pick<NativeFeedbackApi,'get'>){
 const plan=assertReworkRecord(await rework.get(target.projectId,'PLAN',target.planRef,target.feedbackRef),target.projectId,target.feedbackRef,target.planRef);
 if(plan.kind!=='PLAN'||!sameNativeReference(plan.payload.mapping_ref,target.mappingRef))throw new Error('返工持久引用不一致');
 const mapping=assertReworkRecord(await rework.get(target.projectId,'MAPPING',target.mappingRef,target.feedbackRef),target.projectId,target.feedbackRef,target.mappingRef);
 if(mapping.kind!=='MAPPING'||mapping.status!=='MAPPED'||!sameNativeReference(mapping.payload.proposal_ref,plan.payload.proposal_ref)||!exactFileSource(mapping.payload.baseline_file_source_ref,target.source))throw new Error('原生预览与反馈文件身份不一致');
 const feedback=await feedbackApi.get(target.projectId,mapping.payload.native_feedback_ref);
 if(!sameNativeReference(feedback.feedback_ref,target.feedbackRef)||feedback.payload.project_id!==target.projectId||!exactFileSource(feedback.payload.baseline_file_source_ref,target.source)||feedback.payload.write_authorized!==false||feedback.payload.model_dispatched!==false||feedback.execution_authorized!==false)throw new Error('原生预览与反馈文件身份不一致');
 return {feedback,records:[mapping,plan],plan};
}

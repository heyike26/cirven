import type {RevisionRef,FeedbackView} from './workbench-contracts';
export type ComposerDraft={text:string;feedback_refs:RevisionRef[]};
export const draftKey=(projectId:string,sessionId?:string)=>`eeas-composer:${encodeURIComponent(projectId)}:${encodeURIComponent(sessionId??'new')}`;
export function appendFeedbackDraft(text:string,existing:FeedbackView[],feedback:FeedbackView){if(existing.some(item=>item.feedback_ref.id===feedback.feedback_ref.id))return{text,feedback:existing};if(existing.length>=20)throw new Error('一条消息最多携带20个批注');return{text:text?text+'\n\n'+feedback.payload.comment:feedback.payload.comment,feedback:[...existing,feedback]};}
export function readComposerDraft(storage:Pick<Storage,'getItem'>,projectId:string,sessionId?:string):ComposerDraft{try{const value=JSON.parse(storage.getItem(draftKey(projectId,sessionId))??'null');if(!value||typeof value.text!=='string'||!Array.isArray(value.feedback_refs))return{text:'',feedback_refs:[]};return{text:value.text,feedback_refs:value.feedback_refs.filter((ref:RevisionRef)=>typeof ref?.id==='string'&&ref.revision===1&&/^[a-f0-9]{64}$/.test(ref.sha256)).slice(0,20)};}catch{return{text:'',feedback_refs:[]};}}
export function hydrateDraftRefs(refs:RevisionRef[],actual:FeedbackView[]){return refs.flatMap(ref=>{const found=actual.find(item=>item.feedback_ref.id===ref.id&&item.feedback_ref.revision===ref.revision&&item.feedback_ref.sha256===ref.sha256);return found?[found]:[];});}
export type DraftRestore={key:string;generation:number;projectId:string;sessionId?:string;draft:ComposerDraft};
/** A scope change cannot save under another session; pending refs survive text edits. */
export class ComposerDraftPersistence{
 private serial=0;private owner?:{lease:DraftRestore;initialized:boolean;pending:RevisionRef[]};
 private readonly storage:Pick<Storage,'getItem'|'setItem'>;
 constructor(storage:Pick<Storage,'getItem'|'setItem'>){this.storage=storage;}
 begin(projectId:string,sessionId?:string,carry?:ComposerDraft):DraftRestore{const draft=carry??readComposerDraft(this.storage,projectId,sessionId),lease={key:draftKey(projectId,sessionId),generation:++this.serial,projectId,sessionId,draft};this.owner={lease,initialized:false,pending:[...draft.feedback_refs]};return lease;}
 pause(){this.serial++;this.owner=undefined;}
 current(lease:DraftRestore){return this.owner?.lease.generation===lease.generation&&this.owner.lease.key===lease.key;}
 initialize(lease:DraftRestore){if(!this.current(lease))return false;this.owner!.initialized=true;return true;}
 hydrated(lease:DraftRestore){if(!this.current(lease))return false;this.owner!.pending=[];return true;}
 hasPending(projectId:string,sessionId?:string){return this.owner?.lease.key===draftKey(projectId,sessionId)&&!!this.owner.pending.length;}
 save(projectId:string,sessionId:string|undefined,text:string,refs:RevisionRef[]){const owner=this.owner;if(!owner?.initialized||owner.lease.key!==draftKey(projectId,sessionId))return false;const combined=[...owner.pending];for(const ref of refs){if(!combined.some(old=>old.id===ref.id&&old.revision===ref.revision&&old.sha256===ref.sha256))combined.push(ref);}this.storage.setItem(owner.lease.key,JSON.stringify({text,feedback_refs:combined.slice(0,20)}));return true;}
}

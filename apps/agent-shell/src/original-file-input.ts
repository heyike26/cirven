import {nativeReference,type FileLinkRef} from './native-collaboration.ts';
import {deliverySource} from './planning-delivery.ts';
import type {SourceRef} from './contracts';

export type OriginalFileInput={kind:'LINKED_FILE';link_ref:FileLinkRef}|{kind:'WORKSPACE_FILE';source_ref:SourceRef};
export function originalFileInput(value:unknown):OriginalFileInput|undefined{
 if(value===undefined)return undefined;
 const input=value as OriginalFileInput;
 if(input?.kind==='LINKED_FILE'&&Object.keys(input).length===2&&nativeReference(input.link_ref)&&/^[a-f0-9]{64}$/.test(input.link_ref.id)&&typeof input.link_ref.project_id==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(input.link_ref.project_id))return {kind:input.kind,link_ref:{project_id:input.link_ref.project_id,id:input.link_ref.id,revision:input.link_ref.revision,sha256:input.link_ref.sha256}};
 if(input?.kind==='WORKSPACE_FILE'&&Object.keys(input).length===2&&deliverySource(input.source_ref)){
  const source=input.source_ref;return {kind:input.kind,source_ref:{kind:source.kind,project_id:source.project_id,workspace_id:source.workspace_id,binding_revision:source.binding_revision,binding_sha256:source.binding_sha256,path:source.path,revision:source.revision,sha256:source.sha256}};
 }
 throw Error('文件上下文暂未核对，原稿保留。');
}
export function sameOriginalFileInput(a:unknown,b:unknown){return JSON.stringify(originalFileInput(a))===JSON.stringify(originalFileInput(b));}
/** Refreshing saved bytes may advance revision/SHA, but may never silently
 * select another file or move the task to another workspace binding. */
export function sameOriginalFileTarget(a:unknown,b:unknown){
 const first=originalFileInput(a),second=originalFileInput(b);
 if(!first||!second||first.kind!==second.kind)return first===second;
 if(first.kind==='LINKED_FILE'||second.kind==='LINKED_FILE')return sameOriginalFileInput(first,second);
 return ['kind','project_id','workspace_id','binding_revision','binding_sha256','path'].every(key=>Reflect.get(first.source_ref,key)===Reflect.get(second.source_ref,key));
}
/** A new unassigned chat has neither a project nor a file. A previous
 * project's hidden selection must never become its implicit attachment. */
export function originalFileChoice(projectId:string|undefined,linked:FileLinkRef|undefined,selected:SourceRef|undefined):OriginalFileInput|undefined{
 if(linked)return originalFileInput({kind:'LINKED_FILE',link_ref:linked});
 if(!selected||!projectId||selected.project_id!==projectId)return undefined;
 return originalFileInput({kind:'WORKSPACE_FILE',source_ref:selected});
}

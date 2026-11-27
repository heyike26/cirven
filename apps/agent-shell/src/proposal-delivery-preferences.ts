import type {ArtifactEnvelope,FileContent,PlanningProposal,SourceRef,Workspace} from './contracts';
import {deliveryDirectoryAllowed,deliverySource} from './planning-delivery.ts';
import {artifactStateKey,keyIdentity,type ObjectDraftSession,type WorkbenchKey} from './workbench-state.ts';

const templateFields=['templateRef','templatePath','templateName'] as const;
const ready=(draft:ObjectDraftSession)=>['ready','saving','saved'].includes(draft.status);
export function hasTemplatePreference(fields:Record<string,unknown>){return templateFields.some(key=>Object.hasOwn(fields,key));}

/** The predecessor is an exact immutable basis, never the most recent discussion. */
export function proposalDiscoveryDraftKey(artifact:ArtifactEnvelope):WorkbenchKey|undefined{
 const proposal=artifact.data as PlanningProposal,ref=proposal?.payload?.discovery_ref;
 if(artifact.type!=='proposal'||artifact.scope.projectId!==proposal?.project_id||artifact.artifactId!==proposal.id||artifact.revision!==proposal.revision||artifact.contentHash!==proposal.sha256||!ref||typeof ref.id!=='string'||!ref.id||!Number.isSafeInteger(ref.revision)||ref.revision<1||!/^[a-f0-9]{64}$/.test(ref.sha256)||artifact.scope.sessionId&&artifact.scope.sessionId!==ref.id)return;
 return{project_id:proposal.project_id,session_id:ref.id,kind:'discovery',object_id:ref.id,revision:ref.revision,sha256:ref.sha256};
}

/** A UI suggestion inside existing authority; ambiguous or file-only grants stay empty. */
export function defaultDeliveryDirectory(workspace:Workspace|undefined,projectId:string):string{
 if(!workspace||workspace.project_id!==projectId||!Array.isArray(workspace.allowed_write_paths))return '';
 if(workspace.allowed_write_paths.includes('*'))return 'eeas-harness';
 const grants=[...new Set(workspace.allowed_write_paths)];
 if(grants.length!==1||!grants[0].endsWith('/'))return '';
 const directory=grants[0].slice(0,-1);
 return deliveryDirectoryAllowed(workspace,directory)?directory:'';
}

export function currentTemplate(file:FileContent,expected:SourceRef):boolean{
 return file.current===true&&deliverySource(expected)&&deliverySource(file.source_ref)&&/\.(docx|dotx)$/i.test(expected.path)&&(['kind','project_id','workspace_id','binding_revision','binding_sha256','path','revision','sha256'] as const).every(key=>file.source_ref[key]===expected[key]);
}

export type TemplateInheritanceResult='INHERITED'|'UNCHANGED'|'DRAFT_UNAVAILABLE'|'TEMPLATE_UNAVAILABLE';
/** One bounded read chain plus existing exact-key draft persistence; no binding,
 * execution, automatic retry or global draft search. */
export async function inheritProposalTemplate(options:{artifact:ArtifactEnvelope;target:ObjectDraftSession;source:(key:WorkbenchKey)=>ObjectDraftSession;readFile:(ref:SourceRef)=>Promise<FileContent>;isCurrent:()=>boolean}):Promise<TemplateInheritanceResult>{
 const {artifact,target,isCurrent}=options,targetKey=artifactStateKey(artifact),sourceKey=proposalDiscoveryDraftKey(artifact);
 if(!targetKey||keyIdentity(target.key)!==keyIdentity(targetKey)||!sourceKey)return 'UNCHANGED';
 await target.ready;
 if(!isCurrent())return 'UNCHANGED';
 if(!ready(target))return 'DRAFT_UNAVAILABLE';
 if(hasTemplatePreference(target.fields))return 'UNCHANGED';
 const source=options.source(sourceKey);await source.ready;
 if(!isCurrent()||hasTemplatePreference(target.fields))return 'UNCHANGED';
 if(!ready(target)||!ready(source)||keyIdentity(source.key)!==keyIdentity(sourceKey))return 'DRAFT_UNAVAILABLE';
 const ref=source.fields.templateRef;
 if(ref===undefined)return 'UNCHANGED';
 if(!deliverySource(ref)||ref.project_id!==artifact.scope.projectId||!/\.(docx|dotx)$/i.test(ref.path))return 'TEMPLATE_UNAVAILABLE';
 const expected=structuredClone(ref),sourceChoice=JSON.stringify(templateFields.map(key=>source.fields[key]));
 let file:FileContent;try{file=await options.readFile(expected);}catch{return isCurrent()&&!hasTemplatePreference(target.fields)?'TEMPLATE_UNAVAILABLE':'UNCHANGED';}
 if(!isCurrent()||hasTemplatePreference(target.fields))return 'UNCHANGED';
 if(!ready(target)||!ready(source))return 'DRAFT_UNAVAILABLE';
 // A removal/change while the read was pending cannot revive the old selection.
 if(sourceChoice!==JSON.stringify(templateFields.map(key=>source.fields[key])))return 'TEMPLATE_UNAVAILABLE';
 if(!currentTemplate(file,expected))return 'TEMPLATE_UNAVAILABLE';
 target.set('fields','templateRef',expected);
 target.set('fields','templateName',typeof source.fields.templateName==='string'&&source.fields.templateName?source.fields.templateName:expected.path.split('/').at(-1)!);
 target.set('fields','templatePath','');
 return 'INHERITED';
}

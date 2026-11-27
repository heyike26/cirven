import type {ArtifactEnvelope} from './contracts';
export type InteractionMode='NONE'|'INLINE_QUESTION'|'INLINE_CHOICE'|'MODELESS_POPOVER'|'FULL_WORKSPACE';
export type PresentationCause='EXPLICIT_OPEN'|'TOOL_RESULT'|'ACTIVE_REQUEST';
export type InteractionRequest={id:string;tool_id:string;artifact_ref:{id:string;revision:number;sha256?:string};reason:string;mode:InteractionMode;required:boolean;status:'REQUESTED'|'DISPLAYED'|'ANSWERED'|'DISMISSED';question_refs?:string[]};
export type Presentation={destination:'CHAT'|'WORKSPACE'|'INTERACTION'|'AGENT_TOOL'|'DIALOG';mode:InteractionMode;reason:string};
const simpleViews=new Set(['capabilities','materials','competition','experience']);
const interactiveViews=new Set(['discovery','proposal','harness','taskbook']);
/** Output alone never requests human interaction. No project-type profiles. */
export function presentationFor(artifact:ArtifactEnvelope,cause:PresentationCause,request?:InteractionRequest):Presentation{
 if(cause==='TOOL_RESULT')return{destination:'CHAT',mode:'NONE',reason:'普通产物留在聊天；用户可主动查看'};
 if(cause==='ACTIVE_REQUEST'){
  if(!request||request.status!=='REQUESTED'||request.artifact_ref.id!==artifact.artifactId||request.artifact_ref.revision!==artifact.revision||request.artifact_ref.sha256!==artifact.contentHash)return{destination:'CHAT',mode:'NONE',reason:'没有当前版本的活跃交互请求'};
  if(['NONE','INLINE_QUESTION','INLINE_CHOICE'].includes(request.mode))return{destination:'CHAT',mode:request.mode,reason:request.reason};
  return{destination:'INTERACTION',mode:request.mode,reason:request.reason};
 }
 if(artifact.type==='providers')return{destination:'DIALOG',mode:'NONE',reason:'按需配置模型，关闭后继续原会话'};
 if(artifact.type==='local-file'||artifact.type==='report'||artifact.type==='word-report'||artifact.type==='image')return{destination:'WORKSPACE',mode:'NONE',reason:'主动打开实际文件或预览；主工作面保留原软件路径'};
 if(simpleViews.has(artifact.type))return{destination:'AGENT_TOOL',mode:'MODELESS_POPOVER',reason:'轻量内置操作，不阻断其余工作区'};
 if(interactiveViews.has(artifact.type))return{destination:'INTERACTION',mode:'FULL_WORKSPACE',reason:'主动审阅当前结构化产物，使用内置互动工具'};
 return{destination:'CHAT',mode:'NONE',reason:'该产物没有声明交互能力'};
}
export const panelMode=(value:InteractionMode):value is 'MODELESS_POPOVER'|'FULL_WORKSPACE'=>value==='MODELESS_POPOVER'||value==='FULL_WORKSPACE';

import {nativeReference,type NativeRef} from './native-collaboration';
import {experienceScope,type ExperienceAction,type ExperienceContext,type ExperienceRequests,type ExperienceKind} from './task-experience';
import type {ExperienceDraft} from './task-experience-journal';
import {mcpConfiguration} from './native-capabilities';

export type ExperienceCaseDraft={id:string;kind:ExperienceKind;label:string;purpose:'POSITIVE'|'NEGATIVE';prompt:string;required_text:string;forbidden_text:string;tool_name:string;input_json:string;content_json:string;structured_json:string;is_error:boolean};
export const newExperienceCase=(kind:ExperienceKind,purpose:'POSITIVE'|'NEGATIVE'):ExperienceCaseDraft=>({id:crypto.randomUUID(),kind,purpose,label:'',prompt:'',required_text:'',forbidden_text:'',tool_name:'',input_json:'{}',content_json:'[]',structured_json:'null',is_error:purpose==='NEGATIVE'});
export function experienceDraft(context:ExperienceContext,rootSha:string,action:ExperienceAction,candidate:NativeRef|null=null,review:NativeRef|null=null,fields:Record<string,string>={}):ExperienceDraft{return {action,generation:context.generation,root_sha256:rootSha,candidate_ref:candidate,review_ref:review,fields};}
export function proposalFields(){return {kind:'SKILL',id:'',title:'',observation:'',proposed_use:'',limitations:'',content:'',transport:'local',command_json:'[]',files_json:'{}',url:'',timeout:'60000',quotes:'[]'};}
export function reviewFields(kind:ExperienceKind){return {statement:'',cases:JSON.stringify([newExperienceCase(kind,'POSITIVE'),newExperienceCase(kind,'NEGATIVE')]),max_model_calls:kind==='SKILL'?'8':'0',max_output_tokens:'2048',timeout_seconds:'120'};}
function check(v:unknown):asserts v{if(!v)throw Error('EXPERIENCE_FIELDS_REQUIRED');}
export function experienceCases(d:ExperienceDraft):ExperienceCaseDraft[]{const rows=JSON.parse(d.fields.cases??'[]');check(Array.isArray(rows)&&rows.length<=8);return rows;}
export function buildExperienceProposal(context:ExperienceContext,root:string,d:ExperienceDraft,operation_id:string):ExperienceRequests['PROPOSE']{
 const f=d.fields;check(d.action==='PROPOSE'&&d.generation===context.generation&&f.title.trim()&&f.observation.trim()&&f.proposed_use.trim()&&f.limitations.trim());const kind=f.kind;
 const proposal=kind==='SKILL'?{kind:'SKILL' as const,id:f.id,content:f.content}:{kind:'MCP_TOOL' as const,id:f.id,configuration:{...mcpConfiguration({type:f.transport==='remote'?'remote':'local',command:f.command_json,files:f.files_json,url:f.url,timeout:Number(f.timeout),enabled:false}),enabled:false as const}};
 const quotes=JSON.parse(f.quotes??'[]');check(Array.isArray(quotes)&&quotes.length<=8);const sources=quotes.map(({source,char_start,char_end,quote_sha256}:{source:ExperienceRequests['PROPOSE']['sources'][number]['source'];char_start:number;char_end:number;quote_sha256:string})=>({source,char_start,char_end,quote_sha256}));
 return {...experienceScope(context,root),root_sha256:d.root_sha256,operation_id,title:f.title,observation:f.observation,proposed_use:f.proposed_use,limitations:f.limitations,proposal,sources};
}
/** Keep user-authored JSON numbers as text until the server decodes them. */
export function buildExperienceReviewJSON(d:ExperienceDraft,operation_id:string,effect:'PROCESS_START'|'REMOTE_MCP_CONNECT',confirmed:boolean):string{
 check(d.action==='REVIEW'&&nativeReference(d.candidate_ref)&&confirmed&&d.fields.statement.trim());const rows=experienceCases(d);check(rows.length>=2&&rows.some(c=>c.purpose==='POSITIVE')&&rows.some(c=>c.purpose==='NEGATIVE'));
 const cases=rows.map(c=>{
  const common={kind:c.kind,id:c.id,label:c.label,purpose:c.purpose};check(c.label.trim());
  if(c.kind==='SKILL'){check(c.prompt.trim());const required_text=c.required_text.split('\n').filter(v=>v!==''),forbidden_text=c.forbidden_text.split('\n').filter(v=>v!=='');check(required_text.length+forbidden_text.length>0);return JSON.stringify({...common,prompt:c.prompt,required_text,forbidden_text});}
  const input=JSON.parse(c.input_json),content=JSON.parse(c.content_json),structured=JSON.parse(c.structured_json);check(input&&typeof input==='object'&&!Array.isArray(input)&&Array.isArray(content)&&(structured===null||typeof structured==='object'&&!Array.isArray(structured))&&/^[A-Za-z0-9_-]{1,100}$/.test(c.tool_name));
  const head=JSON.stringify({...common,tool_name:c.tool_name});return head.slice(0,-1)+',"input":'+c.input_json+',"expected_result":{"content":'+c.content_json+',"structuredContent":'+c.structured_json+',"isError":'+JSON.stringify(c.is_error)+'}}';
 });
 const head=JSON.stringify({operation_id,candidate_ref:d.candidate_ref,review_confirmed:true,statement:d.fields.statement,allowed_effects:[effect],max_model_calls:Number(d.fields.max_model_calls),max_output_tokens:Number(d.fields.max_output_tokens),timeout_seconds:Number(d.fields.timeout_seconds),process_boundary_acknowledged:true});
 return head.slice(0,-1)+',"cases":['+cases.join(',')+']}';
}

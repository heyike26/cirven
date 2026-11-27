import {verifyQuotedEvidence} from './source-evidence.ts';

export type PlanningAnchor={document_id:string;revision:number;raw_sha256:string;chunk_index:number;chunk_sha256:string;char_start:number;char_end:number;quote_sha256:string};
export type PlanningSource={document_id:string;revision:number;raw_sha256:string;filename:string;chunks:{index:number;content:string;sha256:string;char_start:number;char_end:number}[]};
export class PlanningSourceError extends Error {}
const hash=(value:unknown):value is string=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
const integer=(value:unknown,min=0):value is number=>Number.isSafeInteger(value)&&(value as number)>=min;
export function planningAnchor(value:unknown):value is PlanningAnchor {
  const v=value as PlanningAnchor|null;
  return !!v&&typeof v.document_id==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(v.document_id)&&integer(v.revision,1)&&hash(v.raw_sha256)&&integer(v.chunk_index)&&hash(v.chunk_sha256)&&integer(v.char_start)&&integer(v.char_end,1)&&v.char_end>v.char_start&&hash(v.quote_sha256);
}
export function planningSources(receipt:unknown):PlanningSource[] {
  const documents=(receipt as {documents?:unknown[]}|null)?.documents;
  if(!Array.isArray(documents))return [];
  return documents.filter((item):item is PlanningSource=>{
    const value=item as PlanningSource|null;
    return !!value&&typeof value.document_id==='string'&&integer(value.revision,1)&&hash(value.raw_sha256)&&typeof value.filename==='string'&&!!value.filename.trim()&&Array.isArray(value.chunks)&&value.chunks.every(chunk=>!!chunk&&integer(chunk.index)&&typeof chunk.content==='string'&&hash(chunk.sha256)&&integer(chunk.char_start)&&integer(chunk.char_end,1)&&chunk.char_end>chunk.char_start);
  });
}
export function planningSourceDocument(receipt:unknown,value:unknown) {
  if(!planningAnchor(value))return;
  return planningSources(receipt).find(source=>source.document_id===value.document_id&&source.revision===value.revision&&source.raw_sha256===value.raw_sha256);
}
export function planningSourceName(receipt:unknown,value:unknown) {return planningSourceDocument(receipt,value)?.filename;}
export function samePlanningAnchor(a:unknown,b:unknown) {
  if(!planningAnchor(a)||!planningAnchor(b))return false;
  return (['document_id','revision','raw_sha256','chunk_index','chunk_sha256','char_start','char_end','quote_sha256']as const).every(key=>a[key]===b[key]);
}
export function planningSourceBasis(source:PlanningSource|undefined,chunk:PlanningSource['chunks'][number]|undefined,anchors:unknown[]) {
  const references=anchors.map(anchor=>planningAnchor(anchor)?[anchor.document_id,anchor.revision,anchor.raw_sha256,anchor.chunk_index,anchor.chunk_sha256,anchor.char_start,anchor.char_end,anchor.quote_sha256]:anchor);
  return JSON.stringify([source?[source.document_id,source.revision,source.raw_sha256]:null,chunk?[chunk.index,chunk.sha256,chunk.char_start,chunk.char_end,chunk.content]:null,references]);
}
export async function selectedPlanningAnchor(receipt:unknown,source:PlanningSource,chunk:PlanningSource['chunks'][number],start:number,end:number):Promise<PlanningAnchor> {
  if(!integer(start)||!integer(end,1)||end<=start||end>chunk.content.length)throw new PlanningSourceError('请先选取需要引用的原文。');
  const chars=Array.from(chunk.content),charStart=Array.from(chunk.content.slice(0,start)).length,charEnd=Array.from(chunk.content.slice(0,end)).length;
  if(chars.slice(0,charStart).join('').length!==start||chars.slice(0,charEnd).join('').length!==end)throw new PlanningSourceError('选取范围截断了一个字符，请重新选取。');
  const quote=chunk.content.slice(start,end);
  let digest:ArrayBuffer;
  try {digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(quote));}
  catch(error) {throw new PlanningSourceError('来源摘录无法核对，未添加引用。',{cause:error});}
  const anchor:PlanningAnchor={document_id:source.document_id,revision:source.revision,raw_sha256:source.raw_sha256,chunk_index:chunk.index,chunk_sha256:chunk.sha256,char_start:chunk.char_start+charStart,char_end:chunk.char_start+charEnd,quote_sha256:Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('')};
  if(!planningAnchor(anchor))throw new PlanningSourceError('来源引用不完整，未添加引用。');
  try {await verifyQuotedEvidence(receipt,anchor);}
  catch(error) {throw new PlanningSourceError('原文与来源记录不一致，未添加引用。',{cause:error});}
  return anchor;
}

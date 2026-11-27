export function quotedEvidence(receipt:unknown,anchor:unknown){
  const ref=anchor as {document_id:string;revision:number;raw_sha256:string;chunk_index:number;chunk_sha256:string;char_start:number;char_end:number;quote_sha256:string};
  const source=(receipt as {documents?:{document_id:string;revision:number;raw_sha256:string;filename:string;chunks:{index:number;content:string;sha256:string;char_start:number;char_end:number}[]}[]}|undefined)?.documents?.find(value=>value.document_id===ref.document_id&&value.revision===ref.revision&&value.raw_sha256===ref.raw_sha256);
  const chunk=source?.chunks.find(value=>value.index===ref.chunk_index&&value.sha256===ref.chunk_sha256);
  if(!source||!chunk)throw new Error('当前来源快照不包含这一精确版本与片段');
  const chars=Array.from(chunk.content),start=ref.char_start-chunk.char_start,end=ref.char_end-chunk.char_start;
  if(!Number.isSafeInteger(start)||!Number.isSafeInteger(end)||start<0||end<=start||end>chars.length||ref.char_end>chunk.char_end)throw new Error('来源摘录范围无效');
  return{filename:source.filename,text:chars.slice(start,end).join(''),expected_sha256:ref.quote_sha256,chunk_content:chunk.content,chunk_sha256:ref.chunk_sha256};
}
async function hash(text:string){const raw=await globalThis.crypto.subtle.digest('SHA-256',new TextEncoder().encode(text));return Array.from(new Uint8Array(raw),byte=>byte.toString(16).padStart(2,'0')).join('');}
export async function verifyQuotedEvidence(receipt:unknown,anchor:unknown){
  const value=quotedEvidence(receipt,anchor);
  if(await hash(value.chunk_content)!==value.chunk_sha256)throw new Error('来源片段 SHA 不一致');
  if(await hash(value.text)!==value.expected_sha256)throw new Error('来源摘录 SHA 不一致');return value;
}

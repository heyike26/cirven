export type CandidateLocator={kind:'CANDIDATE_FIELD';field_pointer:string;char_start:number;char_end:number};
export type ReportLocator={kind:'REPORT_PARAGRAPH';source_id:string;source_kind:'DISCOVERY'|'PROPOSAL';artifact_id:string;docx_sha256:string;paragraph_index:number;char_start:number;char_end:number};
export type TextSelection={text:string;quote_sha256:string;offset_unit:'UNICODE_CODEPOINT';locator:CandidateLocator|ReportLocator|import('./ocr').OCRLocator};
export async function candidateTextSelection(fullText:string,start:number,end:number,pointer:string):Promise<TextSelection&{locator:CandidateLocator}>{
 if(!Number.isInteger(start)||!Number.isInteger(end)||start<0||end<=start||end>fullText.length)throw new Error('选文范围无效');
 if(!pointer.startsWith('/'))throw new Error('选文缺少文稿字段定位');
 const text=fullText.slice(start,end),char_start=Array.from(fullText.slice(0,start)).length,char_end=Array.from(fullText.slice(0,end)).length;
 if(Array.from(fullText).slice(char_start,char_end).join('')!==text)throw new Error('选文不能截断 Unicode 字符');
 const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text));
 return{text,quote_sha256:[...new Uint8Array(hash)].map(value=>value.toString(16).padStart(2,'0')).join(''),offset_unit:'UNICODE_CODEPOINT',locator:{kind:'CANDIDATE_FIELD',field_pointer:pointer,char_start,char_end}};
}
export type TextAnnotation={tool:'TEXT_SELECTION';comment:string;selection:{text_selection:TextSelection};delivery:'DRAFT'|'SEND'};

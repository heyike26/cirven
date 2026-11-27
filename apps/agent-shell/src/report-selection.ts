import {candidateTextSelection,type TextSelection} from './text-feedback.ts';
import type {RevisionRef} from './workbench-contracts';
export type ReportTextMap={source_id:string;source_kind:'DISCOVERY'|'PROPOSAL';artifact_id:string;docx_sha256:string;artifact_ref:RevisionRef&{kind:'DISCOVERY'|'PROPOSAL'};paragraphs:{index:number;text:string;sha256:string}[];offset_unit:'UNICODE_CODEPOINT';sha256:string;coverage:unknown};
/** Enumerate every exact context. Ambiguity must be selected by the user. */
export async function locateReportSelection(map:ReportTextMap,fullText:string,start:number,end:number):Promise<{selection:TextSelection;context:string}[]>{
 const quote=await candidateTextSelection(fullText,start,end,'/report-preview'),matches:{selection:TextSelection;context:string}[]=[];
 for(const paragraph of map.paragraphs){let from=0;while(from<=paragraph.text.length){const index=paragraph.text.indexOf(fullText,from);if(index<0)break;const begin=Array.from(paragraph.text.slice(0,index)).length;matches.push({selection:{...quote,locator:{kind:'REPORT_PARAGRAPH',source_id:map.source_id,source_kind:map.source_kind,artifact_id:map.artifact_id,docx_sha256:map.docx_sha256,paragraph_index:paragraph.index,char_start:begin+quote.locator.char_start,char_end:begin+quote.locator.char_end}},context:`段落 ${paragraph.index+1} · ${paragraph.text}`});from=index+Math.max(1,fullText.length);}}
 return matches;
}

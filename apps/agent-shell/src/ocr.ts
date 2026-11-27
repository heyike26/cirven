import type {BitmapRef} from './native-document';
import type {RevisionRef} from './workbench-contracts';
import {candidateTextSelection,type TextSelection} from './text-feedback.ts';

export type OCRCapabilities={available:boolean;status:'CONFIGURED'|'UNAVAILABLE';reason?:string;engine?:{name:string;version:string};network_access:'NEVER';qualification:'OBSERVATION_ONLY';component_sha256?:string;coordinate_space?:'DECODED_IMAGE_PIXELS'};
export type OCRLine={id:string;text:string;confidence:number;polygon:[number,number][];characters?:{text:string;confidence:number;polygon:[number,number][]}[]};
export type OCRObservation={observation_ref:RevisionRef;payload:{project_id:string;bitmap_ref:BitmapRef;authority:'OCR_OBSERVATION_NOT_VERIFIED_DOCUMENT_OR_PROJECT_FACT';semantic_correctness:'USER_REVIEW_REQUIRED';recognition:{schema_version:string;status:'RECOGNIZED'|'NO_TEXT';bitmap_ref:BitmapRef;bitmap:{sha256:string;width:number;height:number;source_ref:unknown};coordinate_space:'DECODED_IMAGE_PIXELS';qualification:'OBSERVATION_ONLY';network_access:'NEVER';lines:OCRLine[];text:string;sha256:string;engine:{name:string;version:string};component_sha256:string;[key:string]:unknown}}};
export type OCRLocator={kind:'OCR_LINE';observation_ref:RevisionRef;line_id:string;char_start:number;char_end:number};
export type OCRTextSelection=TextSelection&{locator:OCRLocator};

function identity(bitmap:BitmapRef){
 if(bitmap.kind==='NATIVE_CAPTURE')return [bitmap.kind,bitmap.view_ref,bitmap.frame_revision,bitmap.sha256,bitmap.binding_sha256];
 const ref=bitmap.file_source_ref;
 return [bitmap.kind,bitmap.sha256,ref.kind,ref.project_id,ref.workspace_id,ref.binding_revision,ref.binding_sha256,ref.path,ref.revision,ref.sha256];
}
export function sameBitmap(a:BitmapRef,b:BitmapRef){return JSON.stringify(identity(a))===JSON.stringify(identity(b));}
/** Only a reference is remembered. Text and boxes must be loaded from the server. */
export function ocrObservationKey(pid:string,bitmap:BitmapRef){return 'eeas-ocr:'+encodeURIComponent(pid)+':'+encodeURIComponent(JSON.stringify(identity(bitmap)));}
export function validateOCRObservation(value:OCRObservation,pid:string,bitmap:BitmapRef):OCRObservation{
 const payload=value?.payload,result=payload?.recognition,ref=value?.observation_ref;
 if(!payload||!result||!ref||payload.project_id!==pid||!payload.bitmap_ref||!result.bitmap_ref||!sameBitmap(payload.bitmap_ref,bitmap)||!sameBitmap(result.bitmap_ref,bitmap))throw new Error('识别记录不属于此项目的精确画面');
 if(ref.revision!==1||![ref.id,ref.sha256,result.sha256,result.component_sha256].every(hash=>typeof hash==='string'&&/^[a-f0-9]{64}$/.test(hash)))throw new Error('识别记录缺少完整版本凭据');
 if(payload.authority!=='OCR_OBSERVATION_NOT_VERIFIED_DOCUMENT_OR_PROJECT_FACT'||payload.semantic_correctness!=='USER_REVIEW_REQUIRED'||result.qualification!=='OBSERVATION_ONLY'||result.coordinate_space!=='DECODED_IMAGE_PIXELS'||result.network_access!=='NEVER')throw new Error('识别记录未保留画面观察的事实边界');
 const image=result.bitmap;
 if(!image||image.sha256!==bitmap.sha256||![image.width,image.height].every(size=>Number.isInteger(size)&&size>0&&size<=100000))throw new Error('文字框缺少实际解码图片的尺寸和 SHA');
 if(!Array.isArray(result.lines)||result.lines.length>1024)throw new Error('识别行列表无效');
 const ids=new Set<string>();
 for(const line of result.lines){
  if(!line||typeof line.id!=='string'||!line.id||ids.has(line.id)||typeof line.text!=='string'||!Number.isFinite(line.confidence)||line.confidence<0||line.confidence>1)throw new Error('识别文字或置信度无效');
  ids.add(line.id);
  if(!Array.isArray(line.polygon)||line.polygon.length!==4||line.polygon.some(point=>!Array.isArray(point)||point.length!==2||!point.every(Number.isFinite)||point[0]<0||point[0]>image.width||point[1]<0||point[1]>image.height))throw new Error('识别文字框超出实际图片');
 }
 if(result.text!==result.lines.map(line=>line.text).join('\n')||!['RECOGNIZED','NO_TEXT'].includes(result.status)||(result.status==='NO_TEXT')!==!result.lines.length)throw new Error('识别全文与实际行记录不一致');
 return value;
}
export function ocrPolygon(line:OCRLine,bitmap:{width:number;height:number}){return line.polygon.map(([x,y])=>`${x/bitmap.width},${y/bitmap.height}`).join(' ');}
export async function ocrTextSelection(observation:OCRObservation,lineId:string,start:number,end:number,pid:string,bitmap:BitmapRef):Promise<OCRTextSelection>{
 validateOCRObservation(observation,pid,bitmap);
 const line=observation.payload.recognition.lines.find(value=>value.id===lineId);
 if(!line)throw new Error('所选文字不在此识别记录中');
 const quote=await candidateTextSelection(line.text,start,end,'/ocr');
 return {...quote,locator:{kind:'OCR_LINE',observation_ref:observation.observation_ref,line_id:line.id,char_start:quote.locator.char_start,char_end:quote.locator.char_end}};
}

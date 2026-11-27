import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {ApiClient} from '../src/api.ts';
import {WorkbenchApi} from '../src/workbench-api.ts';
import {ocrObservationKey,ocrPolygon,ocrTextSelection,sameBitmap,validateOCRObservation} from '../src/ocr.ts';
const sha=text=>createHash('sha256').update(text).digest('hex');
const bitmap={kind:'NATIVE_CAPTURE',view_ref:'a'.repeat(32),frame_revision:3,sha256:'b'.repeat(64),binding_sha256:'c'.repeat(64)};
const observation={observation_ref:{id:'d'.repeat(64),revision:1,sha256:'e'.repeat(64)},payload:{project_id:'project',bitmap_ref:bitmap,authority:'OCR_OBSERVATION_NOT_VERIFIED_DOCUMENT_OR_PROJECT_FACT',semantic_correctness:'USER_REVIEW_REQUIRED',recognition:{status:'RECOGNIZED',bitmap_ref:bitmap,bitmap:{sha256:bitmap.sha256,width:600,height:200},qualification:'OBSERVATION_ONLY',coordinate_space:'DECODED_IMAGE_PIXELS',network_access:'NEVER',component_sha256:'f'.repeat(64),sha256:'1'.repeat(64),lines:[{id:'line-0001',text:'前😀 原始识别 后',confidence:.913,polygon:[[60,40],[420,40],[420,80],[60,80]]}],text:'前😀 原始识别 后'}}};
const response=value=>new Response(JSON.stringify(value),{status:200});

test('OCR boxes use actual decoded image dimensions, never workbench CSS size or inferred graph positions',()=>{
 assert.equal(ocrPolygon(observation.payload.recognition.lines[0],observation.payload.recognition.bitmap),'0.1,0.2 0.7,0.2 0.7,0.4 0.1,0.4');
 assert.equal(validateOCRObservation(observation,'project',bitmap),observation);
 for(const change of [value=>value.payload.recognition.lines[0].polygon[0][0]=601,value=>value.payload.recognition.lines[0].confidence=1.1,value=>value.payload.recognition.lines.push(value.payload.recognition.lines[0]),value=>value.payload.recognition.coordinate_space='CSS_PIXELS',value=>value.payload.authority='VERIFIED_FACT']){
  const value=structuredClone(observation);change(value);assert.throws(()=>validateOCRObservation(value,'project',bitmap));
 }
});
test('wrong project, capture frame, source SHA, binding or stale stored observation receipt cannot be shown as current OCR',()=>{
 assert.throws(()=>validateOCRObservation(observation,'another',bitmap),/精确画面/);
 for(const changed of [{...bitmap,frame_revision:4},{...bitmap,sha256:'2'.repeat(64)},{...bitmap,binding_sha256:'3'.repeat(64)}]){assert.equal(sameBitmap(bitmap,changed),false);assert.throws(()=>validateOCRObservation(observation,'project',changed),/精确画面/);assert.notEqual(ocrObservationKey('project',bitmap),ocrObservationKey('project',changed));}
 assert.notEqual(ocrObservationKey('project',bitmap),ocrObservationKey('another',bitmap));
 const ref={kind:'local_file',project_id:'project',workspace_id:'folder',binding_revision:1,binding_sha256:'4'.repeat(64),path:'actual.png',revision:2,sha256:'5'.repeat(64)};const a={kind:'LOCAL_FILE',file_source_ref:ref,sha256:ref.sha256},b={...a,file_source_ref:{...ref,workspace_id:'another'}};assert.equal(sameBitmap(a,b),false);
});
test('selected OCR text keeps immutable observation/line and Unicode codepoints, with exact UTF8 quote hash',async()=>{
 const selected=await ocrTextSelection(observation,'line-0001',1,3,'project',bitmap);assert.equal(selected.text,'😀');assert.equal(selected.quote_sha256,sha('😀'));assert.deepEqual(selected.locator,{kind:'OCR_LINE',observation_ref:observation.observation_ref,line_id:'line-0001',char_start:1,char_end:2});assert.equal(selected.offset_unit,'UNICODE_CODEPOINT');
 await assert.rejects(()=>ocrTextSelection(observation,'line-0001',2,3,'project',bitmap),/Unicode/);await assert.rejects(()=>ocrTextSelection(observation,'missing',0,1,'project',bitmap),/此识别记录/);await assert.rejects(()=>ocrTextSelection(observation,'line-0001',-1,3,'project',bitmap),/范围/);
});
test('no-text OCR remains empty observation; confidence is not promoted to facts',()=>{
 const empty=structuredClone(observation);empty.payload.recognition.status='NO_TEXT';empty.payload.recognition.lines=[];empty.payload.recognition.text='';assert.equal(validateOCRObservation(empty,'project',bitmap).payload.recognition.status,'NO_TEXT');empty.payload.recognition.text='伪造识别文字';assert.throws(()=>validateOCRObservation(empty,'project',bitmap),/全文/);
});
test('OCR transport sends only exact bitmap ref, reloads exact receipt and never calls a provider/model endpoint',async()=>{
 const calls=[];const client=new WorkbenchApi(new ApiClient(async(url,options)=>{if(url==='/api/bootstrap')return response({csrf_token:'actual'});calls.push({url,method:options.method,body:options.body?JSON.parse(options.body):undefined});return response(url==='/api/workbench/ocr/capabilities'?{available:true,status:'CONFIGURED',network_access:'NEVER',qualification:'OBSERVATION_ONLY'}:observation);}));
 assert.equal((await client.ocrCapabilities()).available,true);await client.recognize('project',bitmap);await client.ocrObservation('project',observation.observation_ref,bitmap);assert.deepEqual(calls.map(value=>value.method),['GET','POST','GET']);assert.deepEqual(calls[1].body,{bitmap_ref:bitmap});assert.equal(calls[2].url,'/api/projects/project/workbench/ocr/'+observation.observation_ref.id);assert.ok(calls.every(value=>value.url.includes('/workbench/ocr')));assert.equal(calls[1].body.provider_profile_ref,undefined);
 await assert.rejects(()=>client.ocrObservation('project',{...observation.observation_ref,sha256:'9'.repeat(64)},bitmap),/版本凭据/);await assert.rejects(()=>client.recognize('another',bitmap),/精确画面/);
});
test('recording selected OCR line retains bitmap and OCR ref together and never dispatches revision automatically',async()=>{
 const calls=[];const client=new WorkbenchApi(new ApiClient(async(url,options)=>{if(url==='/api/bootstrap')return response({csrf_token:'t'});calls.push({url,body:JSON.parse(options.body)});return response({native_requested:false,status:'RECORDED'});}));
 const text_selection=await ocrTextSelection(observation,'line-0001',0,3,'project',bitmap),session={project_id:'project',id:'session',revision:7,sha256:'8'.repeat(64)};const receipt=await client.recordFeedback(session,{tool:'TEXT_SELECTION',comment:'核对识别结果',selection:{text_selection,bitmap_ref:bitmap,ocr_ref:observation.observation_ref,node_ids:[],normalized_points:[]}},'only-record');assert.equal(receipt.native_requested,false);assert.equal(calls.length,1);assert.equal(calls[0].url,'/api/projects/project/workbench/feedback');assert.equal(calls[0].body.artifact_ref.revision,7);assert.deepEqual(calls[0].body.selection.bitmap_ref,bitmap);assert.deepEqual(calls[0].body.selection.ocr_ref,text_selection.locator.observation_ref);assert.equal(calls[0].body.selection.graph_id,undefined);
});

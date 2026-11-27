import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
import {nativeCapturedInput,sameNativeReference} from '../src/native-collaboration.ts';
import {authoredErrorCopy,authoredText} from '../src/authored-ui-copy.ts';
import {translateUI} from '../src/ui-language.ts';

const source=await readFile(new URL('../src/App.tsx',import.meta.url),'utf8');
const parsed=ts.createSourceFile('App.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const app=parsed.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='App');
const actual=app.body.statements.find(n=>ts.isFunctionDeclaration(n)&&n.name?.text==='captureNativeContext').getText(parsed);
const ref={id:'selected-window-frame',revision:1,sha256:'a'.repeat(64)};
const saved={context_ref:{...ref,id:'previous-frozen-input'},label:'Saved 😀',comment:'手改保留',regions:[],preview_sha256:'c'.repeat(64)};
const snapshot=()=>({operation_id:'capture-once',current:true,automatic_input_authorized:false,
 observation:{status:'READY_OBSERVATION',context_ref:ref,questions:[],window:{title:'My document 😀'},
  coverage:{metadata:true,visual:true,accessibility:true,pixel_source:'EXACT_TARGET_WINDOW',whole_page_seen:false}},
 preview:{status:'AVAILABLE',image_ref:{sha256:'b'.repeat(64)}}});
function scene(capture){
 let attachments=[structuredClone(saved)],busy=false,current=true,cid='one',pid='project';
 const calls=[],errors=[],focus=[],review=[];
 const bindings={nativeController:{capture:async(...args)=>{calls.push(args);return capture(...args);}},nativeContextBusy:()=>busy,
  nativeBridge:{getNativeInputPreview:()=>assert.fail('capture must not re-read an image')},nativeDraftAttachments:()=>attachments,
  navigation:{capture:()=>()=>current},conversationId:()=>cid,project:()=>({id:pid}),activeSession:()=>undefined,
  setNativeContextBusy:value=>{busy=value;},setError:value=>errors.push(value),nativeCapturedInput,sameNativeReference,authoredErrorCopy,
  setNativeDraftAttachments:change=>{attachments=change(attachments);},setReviewContinue:value=>review.push(value),
  setReviewRetryContext:value=>review.push(value),pageReview:()=>null,focusComposer:()=>focus.push(true)};
 const run=new Function(...Object.keys(bindings),ts.transpileModule(actual+'\nreturn captureNativeContext;',
  {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText)(...Object.values(bindings));
 return {run,calls,errors,focus,review,get inputs(){return attachments;},get busy(){return busy;},leave(){current=false;cid='two';pid='other';}};
}

test('actual Main captures once, requests only necessary modes and leaves notes in the sole message input',async()=>{
 for(const visual of [false,true]){
  const state=scene(async()=>snapshot());await state.run(visual);
  assert.equal(state.calls.length,1);assert.deepEqual(state.calls[0][0].need,{metadata:true,accessibility:!visual,visual,ocr:false});
  assert.equal(state.inputs.length,2);assert.deepEqual(state.inputs[0],saved);assert.equal(state.inputs[1].comment,'');
  assert.equal(state.inputs[1].preview_sha256,visual?'b'.repeat(64):undefined);assert.equal(state.focus.length,1);assert.equal(state.busy,false);
  assert(!state.errors.some(Boolean));
 }
});
test('actual Windows failure shape and unproven pixels cannot silently become normal screenshot attachments',async()=>{
 for(const source of ['UNKNOWN','VISIBLE_TARGET_RECTANGLE','NOT_CAPTURED',undefined]){
  const value=snapshot();value.observation.status='NEEDS_CLARIFICATION';value.observation.coverage.pixel_source=source;
  value.observation.questions=[{code:'PIXEL_SOURCE_UNKNOWN_OR_OCCLUDED',text:'Internal diagnostic must stay out of the composer.'}];
  const before=JSON.stringify(value),state=scene(async()=>value);await state.run(true);
  assert.deepEqual(state.inputs,[saved]);assert.equal(state.calls.length,1);assert.equal(state.busy,false);
  assert.equal(state.focus.length,1,'recover in the same message input');
  assert.equal(state.review.length,0,'failed capture cannot clear task continuation');
  const error=authoredText(state.errors.at(-1));assert.match(error,/原消息和附件已保留/);assert.doesNotMatch(error,/UNKNOWN|NEEDS_CLARIFICATION|Internal diagnostic/);
  const en=authoredText(state.errors.at(-1),(message,params)=>translateUI(message,'en',params));assert.match(en,/Your message and attachments are kept/);
  assert.equal(JSON.stringify(value),before);
 }
});
test('READY alone, stale scope, missing pixels or unreadable page cannot bypass input admission',()=>{
 for(const mutate of [v=>v.observation.coverage.pixel_source='UNKNOWN',v=>v.current=false,
  v=>v.preview.status='UNAVAILABLE',v=>v.observation.coverage.visual=false,
  v=>v.preview.image_ref.sha256='bad',v=>v.observation.status='UNAVAILABLE']){
  const value=snapshot();mutate(value);assert.throws(()=>nativeCapturedInput(value,true));
 }
 const page=snapshot();page.observation.coverage.accessibility=false;assert.throws(()=>nativeCapturedInput(page,false));
 const proven=snapshot();proven.observation.coverage.pixel_source='VERIFIED_TARGET_PIXELS';assert.equal(nativeCapturedInput(proven,true).preview_sha256,'b'.repeat(64));
});
test('late capture, repeated clicks and capture failure never append to a different discussion or retry the request',async()=>{
 let finish;const state=scene(()=>new Promise(resolve=>finish=resolve));const first=state.run(true);await state.run(true);
 assert.equal(state.calls.length,1);assert.equal(state.busy,true);state.leave();finish(snapshot());await first;
 assert.deepEqual(state.inputs,[saved]);assert.equal(state.focus.length,0);assert.equal(state.busy,false);
 const failing=scene(async()=>{throw Error('A real capture failed');});await failing.run(true);
 assert.deepEqual(failing.inputs,[saved]);assert.equal(failing.calls.length,1);assert.equal(failing.busy,false);
});

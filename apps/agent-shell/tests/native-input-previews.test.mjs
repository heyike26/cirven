import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import {readFileSync} from 'node:fs';
import * as native from '../src/native-collaboration.ts';
const compiled=ts.transpileModule(readFileSync(new URL('../src/native-input-previews.ts',import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const module={exports:{}};new Function('require','exports',compiled)(()=>native,module.exports);
const {NativeInputPreviews}=module.exports;
import {nativeInputAttachments,nativeMessageInput} from '../src/native-collaboration.ts';
const ref={id:'saved_source',revision:1,sha256:'a'.repeat(64)},item={context_ref:ref,label:'Saved image 😀',comment:'',regions:[],preview_sha256:'b'.repeat(64)};
const url='data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR3sAAAAASUVORK5CYII=';
const thumb=value=>({context_ref:value.contextRef,image_sha256:value.imageSha256,automatic_input_authorized:false,data_url:url});
const full=value=>({operation_id:'saved_source',current:false,automatic_input_authorized:false,observation:{status:'PARTIAL',context_ref:value.contextRef},preview:{status:'AVAILABLE',image_ref:{sha256:value.imageSha256},data_url:url}});
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('thumbnail loading shares two workers, caches at most twelve, and discards old conversation pixels',async()=>{
 let active=0,maximum=0,reads=0;const pending=[];
 const cache=new NativeInputPreviews({getNativeInputPreview:body=>new Promise(resolve=>{reads++;maximum=Math.max(maximum,++active);pending.push(()=>{active--;resolve(thumb(body));});})},()=>{});
 cache.update('one');for(let i=0;i<20;i++)cache.request({...item,context_ref:{...ref,id:'image_'+i}});
 assert.equal(reads,2);assert.equal(maximum,2);cache.update('two');pending.splice(0).forEach(resolve=>resolve());await tick();assert.equal(cache.get(item),undefined);assert.equal(reads,2,'old queued references are discarded');
 cache.request(item);pending.splice(0).forEach(resolve=>resolve());await tick();assert.equal(cache.get(item).status,'READY');cache.request(item);assert.equal(reads,3,'cached source never refetches on rendering');cache.dispose();
});
test('original previews serialize, abandon closed queued editors and reject a changed source hash',async()=>{
 const pending=[];let calls=0;const cache=new NativeInputPreviews({getNativeInputPreview:body=>{calls++;return new Promise(resolve=>pending.push(value=>resolve(value??full(body))));}},()=>{});cache.update('one');
 const first=cache.full(item);let open=true;const stale=cache.full(item,()=>open);const rejected=assert.rejects(stale,/SCOPE_CHANGED/);open=false;
 assert.equal(calls,1);pending.shift()();await first;await rejected;assert.equal(calls,1,'a closed queued editor never starts an image read');
 const mismatch=cache.full(item);const result=assert.rejects(mismatch,/SOURCE_CHANGED/);pending.shift()({...full({contextRef:ref,imageSha256:'c'.repeat(64)})});await result;cache.dispose();
});
test('typed input preserves the sole user text, exact regions and saved notes without adding write scope or pixels',()=>{
 const selected={...item,regions:[{x:.2,y:.1,width:.3,height:.4}],comment:'Legacy saved note\n😀'};
 const raw='Compare the attached image.\n{"my JSON":"literal"}';const request=nativeMessageInput('typed_input',raw,[selected],undefined);
 assert.equal(request.rawText,raw);assert.deepEqual(request.inputAttachments,nativeInputAttachments([selected]));assert.equal(request.inputAttachments[0].kind,'SCREENSHOT');assert.equal(Object.hasOwn(request,'contextRef'),false);assert.equal(Object.hasOwn(request,'write'),false);assert.doesNotMatch(JSON.stringify(request),/data:image/);
 assert.throws(()=>nativeInputAttachments([{...selected,regions:[{x:.9,y:0,width:.2,height:1}]}]));
 assert.throws(()=>nativeInputAttachments(Array.from({length:21},()=>selected)));
});

test('twenty visible references never churn the twelve-entry cache on repeated renders and removed inputs release slots',async()=>{
 let reads=0;const cache=new NativeInputPreviews({getNativeInputPreview:async body=>{reads++;return thumb(body);}},()=>{});cache.update('one');
 const items=Array.from({length:20},(_,i)=>({...item,context_ref:{...ref,id:'source_'+i}}));
 for(const current of items)cache.request(current);await tick();await tick();assert.equal(reads,12);
 for(let render=0;render<100;render++)for(const current of items)cache.request(current);await tick();assert.equal(reads,12,'rendering overflow never re-downloads or evicts retained source previews');
 cache.retain(items.slice(8));for(const current of items.slice(8))cache.request(current);await tick();await tick();assert.equal(reads,20);assert.equal(cache.get(items[0]),undefined);assert.equal(cache.get(items[19]).status,'READY');cache.dispose();
});

test('removing inputs cancels their queued thumbnail work and retry never duplicates an in-flight read',async()=>{
 const pending=[];let reads=0;const cache=new NativeInputPreviews({getNativeInputPreview:body=>new Promise(resolve=>{reads++;pending.push(()=>resolve(thumb(body)));})},()=>{});cache.update('one');
 const rows=Array.from({length:12},(_,i)=>({...item,context_ref:{...ref,id:'remove_'+i}}));for(const row of rows)cache.request(row);for(let i=0;i<50;i++)cache.request(rows[0],true);assert.equal(reads,2);
 cache.retain([]);pending.splice(0).forEach(resolve=>resolve());await tick();assert.equal(reads,2,'removed queued sources are never read');assert.equal(cache.get(rows[0]),undefined);cache.dispose();
});

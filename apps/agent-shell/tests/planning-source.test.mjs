import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {planningAnchor,planningSources,planningSourceName,planningSourceBasis,samePlanningAnchor,selectedPlanningAnchor} from '../src/planning-source.ts';
import {savedTextareaRange} from '../src/saved-textarea-range.ts';
const hash=text=>createHash('sha256').update(text).digest('hex');
const prefix='原始资料的前言。',content='\ufeff标题😀\r\n核对来源与人工备注。\r\n保留原文。';
const chunk={index:0,content,sha256:hash(content),char_start:Array.from(prefix).length,char_end:Array.from(prefix+content).length};
const source={document_id:'original-source',revision:3,raw_sha256:hash(prefix+content),filename:'用户原始资料.txt',chunks:[chunk]},receipt={documents:[source]};

test('manual quote retains the exact immutable document and Unicode codepoint range without newline normalization',async()=>{
  const start=content.indexOf('😀'),anchor=await selectedPlanningAnchor(receipt,source,chunk,start,content.length);
  assert.ok(planningAnchor(anchor));assert.equal(anchor.char_start,chunk.char_start+Array.from(content.slice(0,start)).length);
  assert.equal(anchor.char_end,chunk.char_end);assert.equal(anchor.quote_sha256,hash(content.slice(start)));
  assert.equal(planningSourceName(receipt,anchor),source.filename);
  assert.ok(samePlanningAnchor(anchor,Object.fromEntries(Object.entries(anchor).reverse())));
  assert.ok(!samePlanningAnchor(anchor,{...anchor,revision:4}));
});
test('empty or surrogate-split selection, stale metadata and changed chunk bytes cannot create a reference',async()=>{
  const start=content.indexOf('😀');
  for(const [a,b]of [[0,0],[-1,3],[0,content.length+1],[start+1,start+2],[start,start+1]])await assert.rejects(()=>selectedPlanningAnchor(receipt,source,chunk,a,b),/选取|字符/);
  await assert.rejects(()=>selectedPlanningAnchor(receipt,{...source,raw_sha256:'f'.repeat(64)},chunk,0,3),/来源记录不一致/);
  await assert.rejects(()=>selectedPlanningAnchor(receipt,source,{...chunk,content:'Z'+content.slice(1)},0,3),/来源记录不一致/);
  assert.equal(planningAnchor(null),false);assert.equal(planningAnchor({}),false);
  assert.deepEqual(planningSources({documents:[null,{...source,chunks:[null]}]}),[]);
});
test('browser-normalized multi-line selection maps back to exact BOM, CRLF and emoji source bytes',async()=>{
  const buffer=content.replace(/\r\n?/g,'\n'),range=savedTextareaRange(content,buffer,0,buffer.length);
  assert.deepEqual(range,{start:0,end:content.length});
  const anchor=await selectedPlanningAnchor(receipt,source,chunk,range.start,range.end);
  assert.equal(anchor.char_end,chunk.char_end);assert.equal(anchor.quote_sha256,hash(content));
  assert.throws(()=>savedTextareaRange(content,'unsaved '+buffer,0,3),/原文对应/);
});
test('cosmetic metadata and key order do not change selection basis; actual text or source identity changes do',()=>{
  const original=planningSourceBasis(source,chunk,[]);
  assert.equal(planningSourceBasis({...source,filename:'可读业务名.txt'},{...chunk},[]),original);
  assert.notEqual(planningSourceBasis({...source,revision:4},chunk,[]),original);
  assert.notEqual(planningSourceBasis(source,{...chunk,content:'changed'},[]),original);
});

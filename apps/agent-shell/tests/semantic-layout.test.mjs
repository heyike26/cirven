import test from 'node:test';
import assert from 'node:assert/strict';
import {boundedGraphLabel,compactSemanticPositions,readableSemanticViewport} from '../src/semantic-layout.ts';
const measure=text=>Array.from(text).length*10;
test('captions wrap CJK and explicitly ellipsise without altering original text or splitting graphemes',()=>{
 const original='审批通过且无重叠：进入借出登记；原始内容保留';const caption=boundedGraphLabel(original,80,2,measure);
 assert.equal(original,'审批通过且无重叠：进入借出登记；原始内容保留');assert.equal(caption.split('\n').length,2);assert.ok(caption.endsWith('…'));assert.ok(caption.split('\n').every(line=>measure(line)<=80));
 assert.equal(boundedGraphLabel('short',80,2,measure),'short');assert.equal(boundedGraphLabel('one\ntwo\nthree',80,2,measure),'one\ntwo…');
 const emoji='👨‍👩‍👧‍👦 family';const segmenter=new Intl.Segmenter(undefined,{granularity:'grapheme'});const parts=boundedGraphLabel(emoji,80,1,text=>Array.from(segmenter.segment(text)).length*10);assert.ok(parts.startsWith('👨‍👩‍👧‍👦'));assert.ok(!parts.includes('\u200d…'));
 assert.throws(()=>boundedGraphLabel('x',0,2,measure),RangeError);assert.equal(boundedGraphLabel('👨‍👩‍👧‍👦',20,1,value=>value==='…'?10:40),'…');
});
test('rank packing separates measured width and height while retaining rank and sibling order',()=>{
 const input=Object.freeze([{id:'a',position:{x:200,y:-600},width:194,height:79},{id:'b',position:{x:600,y:-600},width:194,height:79},{id:'c',position:{x:400,y:-300},width:194,height:79},{id:'d',position:{x:400,y:0},width:194,height:79},{id:'e',position:{x:400,y:300},width:194,height:79},{id:'f',position:{x:400,y:600},width:194,height:79}]);const before=JSON.stringify(input),packed=compactSemanticPositions(input);
 assert.equal(JSON.stringify(input),before);assert.deepEqual(Object.keys(packed),['a','b','c','d','e','f']);assert.ok(packed.a.x<packed.b.x);assert.equal(packed.a.y,packed.b.y);assert.equal(packed.c.y-packed.a.y,135);assert.equal(packed.f.y-packed.a.y,540);assert.ok(packed.b.x-packed.a.x>=296);
 const translated=input.map(node=>({...node,position:{x:node.position.x*7+1234,y:node.position.y*7-5678}}));assert.deepEqual(compactSemanticPositions(translated),packed);
 assert.deepEqual(compactSemanticPositions([]),{});assert.throws(()=>compactSemanticPositions([{id:'bad',position:{x:NaN,y:0},width:1,height:1}]),RangeError);
});
test('variable-size ranks and nodes never overlap and remain independent of old camera scale',()=>{
 const items=[{id:'a',position:{x:0,y:0},width:194,height:79},{id:'b',position:{x:100,y:0},width:230,height:120},{id:'c',position:{x:0,y:1},width:194,height:79}];const value=compactSemanticPositions(items);
 assert.ok(value.b.x-value.a.x>=(194+230)/2+32);assert.ok(value.c.y-value.a.y>=(120+79)/2+56);
});
test('fresh/locate camera keeps the existing 12px label readable; hidden bounds cannot initialize',()=>{
 const bounds={x1:-400,y1:0,w:800,h:1304},viewport=readableSemanticViewport(806,484,bounds);assert.equal(viewport.zoom,1);assert.equal(12*viewport.zoom,12);
 const located=readableSemanticViewport(500,520,bounds,{x:-200,y:1000});assert.equal(-200*located.zoom+located.pan.x,250);assert.equal(1000*located.zoom+located.pan.y,260);
 assert.equal(readableSemanticViewport(0,484,bounds),undefined);assert.equal(readableSemanticViewport(806,0,bounds),undefined);assert.equal(readableSemanticViewport(806,484,{...bounds,w:NaN}),undefined);
});

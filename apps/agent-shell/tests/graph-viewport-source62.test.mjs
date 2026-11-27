import test from 'node:test';
import assert from 'node:assert/strict';
import {graphView,graphViewport,graphCameraForViewport} from '../src/graph-view.ts';

test('a witnessed viewport preserves arbitrary manual model centre and scale in a roundtrip',()=>{
 const old=Object.freeze({zoom:1.37,pan:Object.freeze({x:-107.25,y:63.5}),viewport:Object.freeze({width:1298.5,height:640})});
 const next={width:587,height:709},camera=graphCameraForViewport(old,next);
 const point=(v,size)=>({x:(size.width/2-v.pan.x)/v.zoom,y:(size.height/2-v.pan.y)/v.zoom});
 const a=point(camera,next),b=point(old,old.viewport);assert.ok(Math.abs(a.x-b.x)<1e-9&&Math.abs(a.y-b.y)<1e-9);
 const back=graphCameraForViewport({...camera,viewport:next},old.viewport);assert.ok(Math.abs(back.pan.x-old.pan.x)<1e-9&&Math.abs(back.pan.y-old.pan.y)<1e-9);
 assert.equal(camera.zoom,old.zoom);
});
test('a legacy view has no old-size witness, so its pixel pan is never inferred or automatically fitted',()=>{
 const view={zoom:1,pan:{x:376,y:10.25},positions:{A:{x:140,y:72}},selected:['A'],mode:'flow-down',search:''};
 assert.equal(graphView(view),view);
 assert.deepEqual(graphCameraForViewport(view,{width:587,height:709}),{zoom:1,pan:view.pan});
 assert.notEqual(graphCameraForViewport(view,{width:587,height:709}).pan,view.pan);
 assert.equal(graphCameraForViewport(view,{width:0,height:709}),undefined);
});
test('viewport schema accepts bounded nonzero dimensions and rejects booleans, unknown keys and nonfinite sizes',()=>{
 for(const v of [null,[],{}, {width:1,height:true},{width:0,height:100},{width:100001,height:100},{width:NaN,height:100},{width:1,height:Infinity},{width:1,height:1,extra:0}])assert.equal(graphViewport(v),false);
 const value={zoom:1,pan:{x:0,y:0},positions:{},selected:[],mode:'flow-down',search:'',viewport:{width:684.25,height:100000}};
 assert.equal(graphView(value),value);
 assert.equal(graphView({...value,viewport:null}),undefined);
});

'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {stableOrbGeometry}=require('../orb-geometry.cjs');
const {clampOrb,arcLayout,noticeLayout}=require('../assistant-orb.cjs');

test('fixed envelope and every native region contain the same center at all display edges and negative origins',()=>{
  for(const area of [{x:0,y:0,width:1280,height:860},{x:-1536,y:-128,width:1536,height:864},{x:0,y:0,width:380,height:380},{x:0,y:0,width:56,height:56}]){
    for(let row=0;row<=8;row++)for(let column=0;column<=10;column++){
      const bubble=clampOrb({x:area.x+(area.width-56)*column/10,y:area.y+(area.height-56)*row/8},area);
      const value=stableOrbGeometry({bubble,area,arc:arcLayout(bubble,area),notice:noticeLayout(bubble,area)}),b=value.bounds;
      assert.ok(b.x>=area.x&&b.y>=area.y&&b.x+b.width<=area.x+area.width&&b.y+b.height<=area.y+area.height);
      for(const variant of Object.values(value.shape)){
        assert.deepEqual(variant.slice(0,value.shape.collapsed.length),value.shape.collapsed,'center region is invariant, never cleared');assert.ok(variant.length<512);
        for(const r of variant)assert.ok(Number.isSafeInteger(r.x)&&Number.isSafeInteger(r.y)&&r.width>0&&r.height>0&&r.x>=0&&r.y>=0&&r.x+r.width<=b.width&&r.y+r.height<=b.height);
      }
      assert.equal(b.x+value.center.x,bubble.x+28);assert.equal(b.y+value.center.y,bubble.y+28);
      assert.equal(value.contains({x:bubble.x+28,y:bubble.y+28}),true);
    }
  }
});

test('leave corridor follows actual travel paths rather than the entire enlarged client',()=>{
  const area={x:0,y:0,width:1280,height:860},bubble={x:24,y:780,width:56,height:56},arc=arcLayout(bubble,area);
  const value=stableOrbGeometry({bubble,area,arc,notice:noticeLayout(bubble,area)}),center={x:52,y:808};
  for(const item of arc.offsets)assert.equal(value.contains({x:center.x+item.dx/2,y:center.y+item.dy/2},{expanded:true,corridor:true}),true);
  const empty={x:value.bounds.x+value.bounds.width-1,y:value.bounds.y+1};
  assert.equal(value.contains(empty,{expanded:true,corridor:true}),false,'a distant transparent corner is not a menu target');
  assert.equal(value.contains({x:NaN,y:0},{expanded:true}),false);
});

test('geometry rejects regions beyond the display and does not treat an empty region as a click-through shape',()=>{
  const area={x:0,y:0,width:800,height:600},bubble={x:24,y:24,width:56,height:56};
  assert.throws(()=>stableOrbGeometry({bubble,area,arc:{bounds:{x:-1,y:0,width:100,height:100},offsets:[]}}),/exceeds/);
  const value=stableOrbGeometry({bubble,area});assert.ok(value.shape.collapsed.length>1);assert.deepEqual(value.shape.expanded,value.shape.collapsed);
  const hit=(x,y)=>value.shape.collapsed.some(r=>x>=r.x&&y>=r.y&&x<r.x+r.width&&y<r.y+r.height);
  assert.equal(hit(28,28),true);for(const point of [[1,1],[54,1],[1,54],[54,54]])assert.equal(hit(...point),false);
});

test('native menu scanlines do not turn transparent square corners into input targets',()=>{
  const area={x:0,y:0,width:1280,height:860},bubble={x:24,y:780,width:56,height:56},arc=arcLayout(bubble,area),value=stableOrbGeometry({bubble,area,arc,notice:noticeLayout(bubble,area)});
  const centers=[{x:52,y:808,r:28},...arc.offsets.map(p=>({x:52+p.dx,y:808+p.dy,r:31}))];
  const hit=(x,y)=>value.shape.expanded.some(r=>x-value.bounds.x>=r.x&&y-value.bounds.y>=r.y&&x-value.bounds.x<r.x+r.width&&y-value.bounds.y<r.y+r.height);
  for(const p of centers)assert.equal(hit(p.x,p.y),true);
  let checked=0;for(const p of centers)for(const dx of [-1,1])for(const dy of [-1,1]){
    const point={x:p.x+dx*(p.r-1),y:p.y+dy*(p.r-1)};
    if(centers.every(c=>Math.hypot(point.x-c.x,point.y-c.y)>c.r+1)){assert.equal(hit(point.x,point.y),false);checked++;}
  }
  assert.ok(checked>=4);
});

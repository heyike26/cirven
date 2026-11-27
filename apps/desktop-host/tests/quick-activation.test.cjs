'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {QuickActivation,readTimeout}=require('../quick-activation.cjs');
const state=()=>({schema:'eeas.quick-activation-state.v1',status:'OBSERVED',authority_generation:'a'.repeat(32),
  foreground_identity_sha256:'b'.repeat(64),host_scope_sha256:'c'.repeat(64),last_input_tick:0xffffffff,automatic_input_authorized:false});

test('activation only survives identical actual foreground, input and server generation within bounded reads',async()=>{
  for(const change of ['none','window','input','scope','generation','malformed','failure','slow-read','expired','clock-backwards']){
    let clock=100,reads=0;
    const guard=new QuickActivation(async()=>{
      const value=state();if(++reads===1)return value;
      if(change==='window')value.foreground_identity_sha256='c'.repeat(64);
      if(change==='input')value.last_input_tick=0; // Wrap/nonmonotonic input is a change too.
      if(change==='generation')value.authority_generation='c'.repeat(32);
      if(change==='scope')value.host_scope_sha256='d'.repeat(64);
      if(change==='malformed')value.last_input_tick=true;
      if(change==='failure')throw Error('private failure');
      if(change==='slow-read')clock+=501;
      return value;
    },{clock:()=>clock});
    const lease=await guard.begin();assert.ok(lease);
    clock=change==='expired'?10101:change==='clock-backwards'?99:500;
    assert.equal(await guard.unchanged(lease),change==='none',change);
    assert.equal(reads,['expired','clock-backwards'].includes(change)?1:2);
  }
});

test('unavailable or slow initial state grants no activation and starts no further read',async()=>{
  for(const bad of [null,{...state(),status:'UNAVAILABLE'},{...state(),automatic_input_authorized:true},{...state(),last_input_tick:-1}]){
    let reads=0;const guard=new QuickActivation(async()=>{reads++;return bad;});
    assert.equal(await guard.begin(),null);assert.equal(await guard.unchanged(null),false);assert.equal(reads,1);
  }
  let clock=0;const guard=new QuickActivation(async()=>{clock=501;return state();},{clock:()=>clock});
  assert.equal(await guard.begin(),null);
  const absent=new QuickActivation();assert.equal(await absent.begin(),null);
});

test('only trusted shortcut begin may await release; capture completion always requires a fast unchanged read',async()=>{
  for(const trigger of ['CTRL_SHIFT_SPACE','untrusted',undefined]){
    let clock=0;const options=[];
    const guard=new QuickActivation(async option=>{options.push(option);clock+=800;return state();},{clock:()=>clock});
    const lease=await guard.begin(trigger);
    assert.equal(!!lease,trigger==='CTRL_SHIFT_SPACE');
    assert.equal(readTimeout(options[0]),trigger==='CTRL_SHIFT_SPACE'?1250:450);
    if(lease){assert.equal(await guard.unchanged(lease),false);assert.deepEqual(options[1],{phase:'verify'});}
  }
  let clock=0;const options=[];
  const guard=new QuickActivation(async option=>{options.push(option);clock+=option.phase==='begin'?800:10;return state();},{clock:()=>clock});
  const lease=await guard.begin('CTRL_SHIFT_SPACE');assert.ok(await guard.unchanged(lease));
  assert.deepEqual(options,[{phase:'begin',trigger:'CTRL_SHIFT_SPACE'},{phase:'verify'}]);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {DISCOVERY_WAIT_KEY,readDiscoveryWait,saveDiscoveryWait} from '../src/discovery-wait-budget.ts';
test('new requests retain the explicit bounded preference and unconfigured requests use five minutes and explicit bounded preferences remain authoritative',()=>{
 let value=null;const storage={getItem:k=>{assert.equal(k,DISCOVERY_WAIT_KEY);return value;},setItem:(k,v)=>{assert.equal(k,DISCOVERY_WAIT_KEY);value=v;}};
 assert.equal(readDiscoveryWait(storage),300);for(const limit of [300,600,120]){assert.equal(saveDiscoveryWait(storage,limit),limit);assert.equal(readDiscoveryWait(storage),limit);}
 for(const raw of ['3000','300.0',' 300','{"timeout":600}','invalid']){value=raw;assert.equal(readDiscoveryWait(storage),300);assert.equal(value,raw);}
});
test('failed or unconfirmed persistence is surfaced without mutating the original saved choice',()=>{
 const storage={getItem:()=> '120',setItem:()=>{}};assert.throws(()=>saveDiscoveryWait(storage,300),/尚未确认保存/);assert.equal(readDiscoveryWait(storage),120);assert.throws(()=>saveDiscoveryWait({getItem:()=> '120',setItem:()=>{throw Error('quota');}},600),/尚未确认保存/);assert.equal(readDiscoveryWait({getItem:()=>{throw Error('unavailable');}}),300);
});

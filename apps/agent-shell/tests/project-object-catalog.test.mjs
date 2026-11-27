import test from 'node:test';
import assert from 'node:assert/strict';
import {ProjectObjectCatalog} from '../src/project-object-catalog.ts';
const fields=['books','harnesses','reports','proposals','sessions'];
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
function fixture(fetch){
 const data={};let selected='A';const states=[],calls=[];
 const readers=Object.fromEntries(fields.map(field=>[field,(pid,signal)=>{calls.push({field,pid,signal});return fetch(field,pid,signal);} ]));
 const catalog=new ProjectObjectCatalog(readers,pid=>pid===selected,()=>{for(const key of fields)data[key]=[];},patch=>Object.assign(data,patch),status=>states.push(status),()=>({...data}));
 return{catalog,data,states,calls,select:pid=>{selected=pid;catalog.activate(pid);}};
}
test('history selection loads all existing object kinds, and a slow previous project cannot replace them',async()=>{
 const old=Object.fromEntries(fields.map(field=>[field,deferred()]));
 const f=fixture((field,pid)=>pid==='A'?old[field].promise:Promise.resolve([{id:pid+'-'+field}]));
 f.select('A');const first=f.catalog.load('A');await Promise.resolve();
 f.select('B');assert.deepEqual(f.data.proposals,[]);await f.catalog.load('B');
 assert.equal(f.calls.length,10);assert(f.calls.slice(0,5).every(call=>call.signal.aborted));
 for(const key of fields)old[key].resolve([{id:'old-'+key}]);await first;
 for(const key of fields)assert.deepEqual(f.data[key],[{id:'B-'+key}]);
 assert.equal(f.states.at(-1).projectId,'B');assert.equal(f.states.at(-1).phase,'ready');f.catalog.stop();
});
test('a failed category preserves newer current-project results, and retry reads only that category',async()=>{
 let fail=false;const f=fixture((field,pid)=>fail&&field==='proposals'?Promise.reject(Error('503')):Promise.resolve([{id:pid+'-'+field}]));
 f.select('A');await f.catalog.load('A');f.data.proposals=[{id:'newly-saved-plan'}];fail=true;await f.catalog.load('A');
 assert.deepEqual(f.data.proposals,[{id:'newly-saved-plan'}]);assert.equal(f.states.at(-1).phase,'partial');assert.deepEqual(f.states.at(-1).failed,['proposals']);
 const count=f.calls.length;fail=false;await f.catalog.retry('A');assert.equal(f.calls.length,count+1);assert.equal(f.calls.at(-1).field,'proposals');assert.equal(f.states.at(-1).phase,'ready');
 await f.catalog.retry('A');assert.equal(f.calls.length,count+1);f.catalog.stop();
});
test('new-project read failure cannot show old objects or claim an empty successful result',async()=>{
 const f=fixture((field,pid)=>pid==='A'?Promise.resolve([{id:'A-'+field}]):Promise.reject(Error('unavailable')));
 f.select('A');await f.catalog.load('A');f.select('B');await f.catalog.load('B');
 for(const key of fields)assert.deepEqual(f.data[key],[]);
 assert.equal(f.states.at(-1).phase,'failed');assert.deepEqual(f.states.at(-1).failed,fields);
 const count=f.calls.length;await f.catalog.load('A');assert.equal(f.calls.length,count);f.catalog.stop();
});
test('a newer same-project read owns its status even if an older read fails later',async()=>{
 const old=Object.fromEntries(fields.map(field=>[field,deferred()]));let slow=true;
 const f=fixture(field=>slow?old[field].promise:Promise.resolve([{id:'latest-'+field}]));f.select('A');
 const first=f.catalog.load('A');await Promise.resolve();slow=false;await f.catalog.load('A');
 for(const key of fields)old[key].reject(Error('old failure'));await first;
 assert.equal(f.states.at(-1).phase,'ready');assert.deepEqual(f.states.at(-1).failed,[]);assert.equal(f.data.proposals[0].id,'latest-proposals');f.catalog.stop();
});
test('leaving projects and disposal abort owned reads and reject late completion',async()=>{
 const pending=Object.fromEntries(fields.map(field=>[field,deferred()]));const f=fixture(field=>pending[field].promise);f.select('A');
 const first=f.catalog.load('A');await Promise.resolve();f.select(undefined);const count=f.states.length;f.catalog.stop();
 assert(f.calls.every(call=>call.signal.aborted));for(const key of fields)pending[key].resolve([{id:'late'}]);await first;
 assert.equal(f.states.length,count);for(const key of fields)assert.deepEqual(f.data[key],[]);await f.catalog.load('A');assert.equal(f.calls.length,5);
});

test('successful reads started before a local save cannot roll back that category or drop unrelated reads',async()=>{
 const pending=Object.fromEntries(fields.map(field=>[field,deferred()]));let held=false;
 const f=fixture(field=>held?pending[field].promise:Promise.resolve([{id:'initial-'+field}]));f.select('A');await f.catalog.load('A');
 held=true;const refresh=f.catalog.load('A');await Promise.resolve();
 const local=[{id:'newly-saved-session',revision:5},{id:'initial-sessions'}];f.data.sessions=local;
 for(const key of fields)pending[key].resolve([{id:'stale-'+key}]);await refresh;
 assert.equal(f.data.sessions,local,'A completed save is newer than the old list response');
 for(const key of fields.filter(key=>key!=='sessions'))assert.deepEqual(f.data[key],[{id:'stale-'+key}]);
 assert.equal(f.states.at(-1).phase,'ready');assert.equal(f.calls.length,10);f.catalog.stop();
});

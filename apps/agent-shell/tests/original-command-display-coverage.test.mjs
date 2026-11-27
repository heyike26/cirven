import test from 'node:test';import assert from 'node:assert/strict';
import {originalCommandDisplayCoverage} from '../src/original-command-observations.ts';import {originalFileScope} from '../src/original-session-files.ts';
const H='a'.repeat(64),s={project_id:'project',session_ref:{id:'session',revision:1,sha256:H},generation:2,workspace_ref:{id:'b'.repeat(64),revision:1,binding_sha256:'c'.repeat(64)},original_session_id:'ses_root',messages:[]};
const commands=Array.from({length:5},(_,i)=>({message_id:'m'+i,part_id:'p'+i,call_id:'c'+i,record_ref:{id:'r'+i}})),catalog={scope:originalFileScope(s),original_session_id:s.original_session_id,commands};
test('display coverage contains full exact context and only actual visible command keys',()=>{
 const recent=originalCommandDisplayCoverage(catalog,s,'thread-context',false);assert.deepEqual(recent.entries,commands.slice(-3).map(({message_id,part_id,call_id})=>({message_id,part_id,call_id})));assert.deepEqual(recent.scope,originalFileScope(s));assert.equal(recent.contextKey,'thread-context');assert.equal(recent.original_session_id,'ses_root');assert.equal(Object.hasOwn(recent,'allowed_effects'),false);
 assert.equal(originalCommandDisplayCoverage(catalog,s,'thread-context',true).entries.length,5);
 for(const next of [{...s,generation:3},{...s,project_id:'other'},{...s,workspace_ref:{...s.workspace_ref,revision:2}},{...s,original_session_id:null},{...s,original_session_id:'ses_other'}])assert.equal(originalCommandDisplayCoverage(catalog,next,'thread-context',false),undefined);
 assert.equal(originalCommandDisplayCoverage(undefined,s,'thread-context',false),undefined);
});

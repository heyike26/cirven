import test from 'node:test';
import assert from 'node:assert/strict';
import {displayedCommandKeys,originalToolHasDisplayedResult} from '../src/original-command-display.ts';
import {originalFileScope} from '../src/original-session-files.ts';

const hash='a'.repeat(64),context='original-context';
const session={project_id:'project',session_ref:{id:'session',revision:1,sha256:hash},generation:2,workspace_ref:{id:'b'.repeat(64),revision:1,binding_sha256:'c'.repeat(64)},original_session_id:'ses_root',messages:[]};
const message={info:{id:'msg_one',role:'assistant',sessionID:'ses_root'},parts:[{id:'part_one',type:'tool',tool:'bash',callID:'call_one',sessionID:'ses_root',messageID:'msg_one',state:{status:'completed'}}]};
session.messages=[message];
const coverage=()=>({contextKey:context,scope:originalFileScope(session),original_session_id:'ses_root',entries:[{message_id:'msg_one',part_id:'part_one',call_id:'call_one'}]});

test('only the exact displayed terminal bash caption is replaced; pending and fallback remain',()=>{
 const keys=displayedCommandKeys(coverage(),session,context);
 assert.equal(originalToolHasDisplayedResult(message,'part_one',keys),true);
 for(const status of ['running','pending','unknown'])assert.equal(originalToolHasDisplayedResult({...message,parts:[{...message.parts[0],state:{status}}]},'part_one',keys),false);
 for(const change of [{tool:'edit'},{id:'other_part'},{callID:'other_call'},{sessionID:'ses_elsewhere'},{messageID:'other_message'}])assert.equal(originalToolHasDisplayedResult({...message,parts:[{...message.parts[0],...change}]},'part_one',keys),false);
 assert.equal(originalToolHasDisplayedResult({...message,parts:[message.parts[0],message.parts[0]]},'part_one',keys),false);
 assert.equal(originalToolHasDisplayedResult(message,'part_one',displayedCommandKeys({...coverage(),entries:[]},session,context)),false);
});

test('foreign message owner and a newly ambiguous saved call retain the fallback caption',()=>{
 const keys=displayedCommandKeys(coverage(),session,context);
 const foreign={...message,info:{...message.info,sessionID:'ses_other'},parts:[{...message.parts[0],sessionID:'ses_other'}]};
 assert.equal(originalToolHasDisplayedResult(foreign,'part_one',keys),false);
 const second={...message,info:{...message.info,id:'msg_two'},parts:[{...message.parts[0],id:'part_two',messageID:'msg_two'}]};
 assert.equal(displayedCommandKeys(coverage(),{...session,messages:[message,second]},context).size,0);
 const duplicate={...message,parts:[...message.parts,{...message.parts[0],id:'another_part'}]};
 assert.equal(originalToolHasDisplayedResult(duplicate,'part_one',keys),false);
});

test('a scope, generation, binding or native owner change never hides another discussion',()=>{
 const foreign=[];
 for(const [key,value] of [['project_id','other'],['generation',3]])foreign.push({...coverage(),scope:{...coverage().scope,[key]:value}});
 foreign.push({...coverage(),contextKey:'another discussion'},{...coverage(),original_session_id:'ses_other'});
 for(const key of ['id','revision','sha256'])foreign.push({...coverage(),scope:{...coverage().scope,session_ref:{...session.session_ref,[key]:key==='revision'?2:'different'}}});
 for(const key of ['id','revision','binding_sha256'])foreign.push({...coverage(),scope:{...coverage().scope,workspace_ref:{...session.workspace_ref,[key]:key==='revision'?2:'different'}}});
 for(const value of foreign)assert.equal(displayedCommandKeys(value,session,context).size,0);
 assert.equal(displayedCommandKeys(undefined,session,context).size,0);
 assert.equal(displayedCommandKeys(coverage(),undefined,context).size,0);
 const value=coverage();value.scope={workspace_ref:value.scope.workspace_ref,generation:value.scope.generation,session_ref:value.scope.session_ref,project_id:value.scope.project_id};
 assert.equal(displayedCommandKeys(value,session,context).size,1);
});

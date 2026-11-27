import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {ApiClient} from '../src/api.ts';
import {originalFileScope} from '../src/original-session-files.ts';
import {OriginalCommandObservationsApi,originalCommandsObservation,parseOriginalCommandObservations} from '../src/original-command-observations.ts';
const hash=s=>createHash('sha256').update(s).digest('hex'),H='a'.repeat(64);
const session=()=>({project_id:'project',session_ref:{id:'session',revision:1,sha256:H},generation:2,workspace_ref:{id:'b'.repeat(64),revision:1,binding_sha256:'c'.repeat(64)},original_session_id:'ses_root',active_operation_id:'still-running',pending_permissions:[],pending_questions:[],messages:[{info:{id:'assistant',role:'assistant',sessionID:'ses_root',parentID:'user'},parts:[{id:'part',messageID:'assistant',sessionID:'ses_root',type:'tool',tool:'bash',callID:'call',state:{status:'completed'}}]}]});
const snapshot=()=>({state:'UTF8',sha256:H,bytes:4,line_count:1,blob:'blobs/'+H+'.utf8',stat:{dev:'1',ino:'2',size:'4',mtime_ns:'3',ctime_ns:'4'}});
const row=(patch={})=>({record_ref:{id:'d'.repeat(64),revision:1,sha256:'e'.repeat(64)},operation_id:'operation',root_operation_id:'operation',native_message_id:'user',message_id:'assistant',part_id:'part',call_id:'call',source_generation:2,source_scope:originalFileScope(session()),command:'python -m unittest 中文/测试.py',workdir:'D:/项目',exit_code:0,status:'EXITED',source_status:'UNCHANGED',sources:[{path:'源码/测试.py',before:snapshot(),after:snapshot()}],output:{text:'checked 😀\n',bytes:Buffer.byteLength('checked 😀\n'),sha256:hash('checked 😀\n'),truncated:false,original_truncated:false,original_output_path:null},qualification:'ORIGINAL_COMMAND_OBSERVATION_ONLY',...patch});
const catalog=(commands=[row()],patch={})=>({schema:'eeas.original-command-observations.v1',scope:originalFileScope(session()),snapshot_ref:{id:'f'.repeat(64),revision:1,sha256:H},commands,coverage:{basis_scope:'DECLARED_FILES_ONLY',all_dependencies_captured:false,reason_codes:[]},read_only:true,model_calls:0,automatic_replay:false,...patch});

test('one terminal unique bash call key ignores idle and active-operation timestamps',()=>{
 const s=session(),key=originalCommandsObservation(s);assert(key);assert.equal(originalCommandsObservation({...s,observation_at:'later',last_event_seq:99,active_operation_id:null}),key);assert.notEqual(originalCommandsObservation({...s,generation:3}),key);
 for(const patch of [{workspace_ref:null},{messages:[]},{original_session_id:'other'}])assert.equal(originalCommandsObservation({...s,...patch}),undefined);
 for(const status of ['running','pending']){const v=session();v.messages[0].parts[0].state.status=status;assert.equal(originalCommandsObservation(v),undefined);}
 const error=session();error.messages[0].parts[0].state.status='error';assert.equal(originalCommandsObservation(error),key);
 const ambiguous=session();ambiguous.messages[0].parts.push({...ambiguous.messages[0].parts[0],id:'another'});assert.equal(originalCommandsObservation(ambiguous),undefined);
});

test('scope, exact original call ownership and restricted qualification are required',()=>{
 const s=session();assert.equal(parseOriginalCommandObservations(catalog(),s).commands.length,1);
 for(const patch of [{schema:'wrong'},{scope:{...catalog().scope,generation:3}},{read_only:false},{model_calls:1},{automatic_replay:true},{coverage:{...catalog().coverage,all_dependencies_captured:true}}])assert.throws(()=>parseOriginalCommandObservations(catalog(undefined,patch),s));
 for(const patch of [{native_message_id:'other'},{message_id:'other'},{call_id:'other'},{part_id:'other'},{operation_id:null},{source_generation:3},{qualification:'TESTS_PASSED'},{source_scope:{...catalog().scope,project_id:'another'}}])assert.throws(()=>parseOriginalCommandObservations(catalog([row(patch)]),s));
 const old=row({source_generation:1,source_scope:{...row().source_scope,generation:1}});assert.equal(parseOriginalCommandObservations(catalog([old]),s).commands.length,1);
 const ambiguous=session();ambiguous.messages[0].parts.push({...ambiguous.messages[0].parts[0],id:'duplicate'});assert.throws(()=>parseOriginalCommandObservations(catalog(),ambiguous));
});

test('unknown is never converted to zero; source metadata changes and unavailable bases stay explicit',()=>{
 const unknown=row({exit_code:null,status:'UNKNOWN_OUTCOME',output:null,source_status:'UNAVAILABLE',sources:[{path:'源码/测试.py',before:snapshot(),after:null}]});assert.equal(parseOriginalCommandObservations(catalog([unknown]),session()).commands[0].exit_code,null);
 const changed=row({source_status:'CHANGED',sources:[{path:'源码/测试.py',before:snapshot(),after:{...snapshot(),stat:{...snapshot().stat,mtime_ns:'9'}}}]});assert.equal(parseOriginalCommandObservations(catalog([changed]),session()).commands[0].source_status,'CHANGED');
 for(const patch of [{exit_code:false},{exit_code:0,status:'UNKNOWN_OUTCOME'},{exit_code:null},{output:null},{source_status:'CHANGED'},{sources:[{path:'../secret',before:snapshot(),after:snapshot()}]},{sources:[{path:'file',before:{...snapshot(),bytes:-1},after:snapshot()}]}])assert.throws(()=>parseOriginalCommandObservations(catalog([row(patch)]),session()));
 for(const patch of [{bytes:999},{original_truncated:true,truncated:false},{text:'\0'},{sha256:'wrong'}])assert.throws(()=>parseOriginalCommandObservations(catalog([row({output:{...row().output,...patch}})]),session()));
});

test('client binds five exact scope query fields, verifies output bytes and only GETs',async()=>{
 const requests=[],controller=new AbortController(),client=new OriginalCommandObservationsApi(new ApiClient(async(url,init)=>{if(url==='/api/bootstrap')return Response.json({csrf_token:'csrf'});requests.push({url,method:init.method,signal:init.signal});return Response.json(catalog());}));
 const result=await client.list(session(),controller.signal);assert.equal(result.commands[0].exit_code,0);assert.equal(requests.length,1);assert.equal(requests[0].method,'GET');assert.match(requests[0].url,/\/projects\/project\/agent-sessions\/session\/command-observations\?/);
 const params=new URL(requests[0].url,'http://fixture').searchParams;assert.deepEqual([...params.keys()].sort(),['binding_revision','binding_sha256','generation','session_sha256','workspace_id'].sort());assert.equal(params.get('session_sha256'),H);assert.equal(params.get('generation'),'2');assert.equal(requests[0].signal,controller.signal);
 const corrupt=new OriginalCommandObservationsApi(new ApiClient(async url=>Response.json(url==='/api/bootstrap'?{csrf_token:'csrf'}:catalog([row({output:{...row().output,sha256:'0'.repeat(64)}})]))));await assert.rejects(()=>corrupt.list(session()));
});

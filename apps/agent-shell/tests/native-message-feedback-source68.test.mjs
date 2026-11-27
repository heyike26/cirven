import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createRequire} from 'node:module';
import {NativeMessageClient,nativeInspectionFailure,nativeMessagePending} from '../src/native-collaboration.ts';
const req=createRequire(import.meta.url),ts=req('typescript');
function functions(path,names){
 const text=fs.readFileSync(new URL(path,import.meta.url),'utf8'),tree=ts.createSourceFile(path,text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX),found=[];
 function walk(n){if(ts.isFunctionDeclaration(n)&&names.includes(n.name?.text))found.push(n.getText(tree).replace(/^export /,''));ts.forEachChild(n,walk);}walk(tree);assert.equal(found.length,names.length);
 return ts.transpileModule(found.join('\n'),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
}
test('presentation preserves arbitrary answer/summary bytes and does not mutate receipt or diagnostic',()=>{
 const scope={};vm.createContext(scope);vm.runInContext(functions('../src/NativeMessageFeedback.tsx',['nativeFeedbackBody','nativeFeedbackNeedsInspection']),scope);
 const text="User content: Error invoking remote method 'eeas:keep': CURRENT 原始编号 123",raw='RAW_DIAGNOSTIC '+ 'long '.repeat(5000),message={operation_id:'original-A',status:'FAILED',summary:text,error:{message:raw}};
 const before=JSON.stringify(message);assert.equal(scope.nativeFeedbackBody(message),text);assert.equal(scope.nativeFeedbackBody({...message,text}),text);assert.equal(JSON.stringify(message),before);
 for(const status of ['UNKNOWN_OUTCOME','NO_RECORD','NOT_FOUND','INSPECTION_UNAVAILABLE','NOT_DISPATCHED'])assert.equal(scope.nativeFeedbackNeedsInspection({...message,status}),true);
 assert.equal(scope.nativeFeedbackNeedsInspection({...message,status:'RETURNED'}),false);assert.equal(scope.nativeFeedbackNeedsInspection({...message,status:'RETURNED'},'local save failed'),true);
 assert.equal(scope.nativeFeedbackBody({status:'FAILED',error:{message:raw},reason:'ORIGINAL_CODE'}),undefined);
});
function fixture(){
 const calls=[],errors=[],records=[],saved=[];let cid='original-conversation',pid='qa-project',rows=['original-A','original-B'].map(operation_id=>({input:'User '+operation_id,message:{operation_id,status:'UNKNOWN_OUTCOME',project_id:'qa-project',conversation_id:'original-conversation',review:{can_guide:true}}})),read;
 const context={historyGeneration:1,conversationId:()=>cid,project:()=>({id:pid}),nativeMessages:()=>rows,
  nativeClient:new NativeMessageClient({inspectNativeMessage:async id=>{calls.push(id);return read(id);}}),
  action:async(_label,run)=>{try{await run();}catch(e){errors.push(e.message);}},ordinaryControlScope:()=>null,
  nativeRecord:(input,message)=>{records.push(message);rows=rows.map(row=>row.message.operation_id===message.operation_id?{input:row.input,message}:row);},persistNativeHistory:()=>saved.push(true),
  nativeMessagePending,nativeInspectionFailure,observeNativeMessage(){},forgetNativeMessage(){},message:e=>e instanceof Error?e.message:String(e)};
 vm.createContext(context);vm.runInContext(functions('../src/App.tsx',['inspectNativeOperation']),context);
 return {context,calls,errors,records,saved,rows:()=>rows,setRead:fn=>{read=fn;},switchScope:()=>{cid='new-conversation';pid='new-project';context.historyGeneration++;},drop:()=>{rows=[];}};
}
test('actual original GET keeps default behavior and explicit row opt-in records 404 and IPC failure per exact operation',async()=>{
 const f=fixture();f.setRead(async id=>{throw Object.assign(new Error('RAW_404 '+id),{status:404,code:'NOT_FOUND'});});
 await f.context.inspectNativeOperation('original-A','User original-A');assert.deepEqual(f.calls,['original-A']);assert.deepEqual(f.errors,['RAW_404 original-A']);assert.equal(f.records.length,0);
 await f.context.inspectNativeOperation('original-A','User original-A',true);const missing=f.rows()[0].message;assert.equal(missing.operation_id,'original-A');assert.equal(missing.status,'NO_RECORD');assert.equal(missing.error.message,'RAW_404 original-A');assert.equal(missing.automatic_retry,false);assert.equal(missing.execution_outcome,'NOT_PROVEN');assert.equal(missing.project_id,'qa-project');assert.equal(missing.conversation_id,'original-conversation');assert.equal(missing.review,undefined);
 f.setRead(async()=>{throw Error("Error invoking remote method 'eeas:inspect-native-message': IPC closed");});
 await f.context.inspectNativeOperation('original-B','User original-B',true);assert.equal(f.rows()[1].message.status,'INSPECTION_UNAVAILABLE');assert.equal(f.rows()[1].message.project_id,'qa-project');assert.equal(f.rows()[1].message.conversation_id,'original-conversation');assert.equal(f.rows()[1].message.review,undefined);assert.match(f.rows()[1].message.error.message,/IPC closed/);assert.deepEqual(f.calls,['original-A','original-A','original-B']);assert.equal(f.errors.length,1);
 f.setRead(async id=>({operation_id:id,status:'RETURNED',text:'Actual answer'}));await f.context.inspectNativeOperation('original-A','User original-A',true);assert.equal(f.rows()[0].message.text,'Actual answer');assert.equal(f.rows()[1].message.status,'INSPECTION_UNAVAILABLE');
});
test('late success/error after scope change or removal cannot populate another row or report an unrelated receipt as success',async()=>{
 for(const outcome of ['success','error','removed']){
  const f=fixture();let resolve,reject;f.setRead(()=>new Promise((a,b)=>{resolve=a;reject=b;}));const pending=f.context.inspectNativeOperation('original-A','User original-A',true);
  if(outcome==='removed')f.drop();else f.switchScope();
  if(outcome==='error')reject(Error('late raw detail'));else resolve({operation_id:'original-A',status:'RETURNED',text:'late answer'});
  await pending;assert.equal(f.records.length,0);assert.equal(f.saved.length,0);assert.deepEqual(f.errors,[]);
 }
 const f=fixture();f.setRead(async()=>({operation_id:'other-operation',status:'RETURNED',text:'wrong'}));await f.context.inspectNativeOperation('original-A','User original-A',true);assert.equal(f.rows()[0].message.status,'INSPECTION_UNAVAILABLE');assert.equal(f.rows()[0].message.text.includes('wrong'),false);assert.equal(f.rows()[1].message.status,'UNKNOWN_OUTCOME');
});

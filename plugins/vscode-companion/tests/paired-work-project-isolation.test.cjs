'use strict';
// UNIT paired projections before the host's navigation render; no native claim.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),os=require('node:os'),path=require('node:path');
const {PrivateState,CompanionController}=require('../controller.cjs');
const requirements=require('./work-requirement-fixture.cjs'),cases=require('./work-case-fixture.cjs'),sources=require('./work-source-fixture.cjs');

test('a project change cannot inherit work previews or action tokens with otherwise identical source IDs',t=>{
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-work-project-isolation-'));
 t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));
 const state=new PrivateState(directory),c=new CompanionController({state});
 let rpcCalls=0;
 c.selected=structuredClone(requirements.session);
 c.client={methods:[],rpc:async()=>{rpcCalls++;throw Error('No RPC authorized');}};
 c.emit();
 c.workRequirements.view=requirements.view();
 c.workCases.view=cases.view();
 c.workCases.historyReceipt=cases.receipt();
 c.workRetests.view=cases.view();
 c.workRetests.prepared={private_review:'same original project'};
 c.workRetests.result={private_result:'same original project'};
 c.workSources.root=sources.root;
 c.workSources.rootScope=structuredClone(sources.scope);
 c.workSources.view=sources.requirements();
 const checks=[
  [()=>c.workRequirements.current(),()=>c.workRequirements.contextToken()],
  [()=>c.workCases.currentScope(),()=>c.workCases.token()],
  [()=>c.workRetests.current(),()=>c.workRetests.token()],
  [()=>c.workSources.current(),()=>c.workSources.token()],
 ];
 for(const [current,token] of checks){assert.equal(!!current(),true);assert.match(token(),/^[a-f0-9]{64}$/);}
 c.selected={...c.selected,project_id:'another_project'};
 for(const [current,token] of checks){assert.equal(!!current(),false);assert.equal(token(),null);}
 const caseView=c.workCases.snapshot(),retestView=c.workRetests.snapshot(),sourceView=c.workSources.snapshot();
 assert.equal(caseView.view,null);assert.equal(caseView.history_receipt,null);assert.deepEqual(caseView.mapping_tokens,[]);
 assert.equal(retestView.view,null);assert.equal(retestView.prepared,null);assert.equal(retestView.result,null);assert.equal(retestView.root,null);
 assert.equal(sourceView.view,null);assert.equal(sourceView.root,null);assert.equal(sourceView.catalog,null);
 assert.equal(c.workRequirements.snapshot().view,null);
 assert.equal(rpcCalls,0);assert.deepEqual(state.value.requests,[]);
});

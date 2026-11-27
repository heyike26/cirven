'use strict';
// Run the exact production IPC handler with only transport/shell boundaries fake.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {quickDigest}=require('../quick-journal.cjs'),{replyLinkTargets}=require('../quick-reply-links.bundle.cjs');
const source=fs.readFileSync(path.resolve(__dirname,'../main.cjs'),'utf8');
const start=source.indexOf("  ipcMain.handle('eeas:native-reply-link-open'");
const end=source.indexOf("  ipcMain.handle(",start+8);
function fixture(){
 let handler,release;const opened=[],requests=[],scope={generation:7,projectId:'project',conversationId:'conversation'};
 const reply={operation_id:'original',project_id:'project',conversation_id:'conversation',status:'RETURNED',text:'[Source](https://example.com/report)'};
 const context={URL,quickDigest,replyLinkTargets,quitting:false,closePreparing:false,stopped:false,quickHost:{scope},
 admitted:event=>{if(event!=='trusted')throw Error('Untrusted');},exactKeys:(body,keys)=>assert.deepEqual(Object.keys(body).sort(),[...keys].sort()),
 request:async route=>{requests.push(route);if(release)await release;return reply;},shell:{openExternal:async url=>opened.push(url)},ipcMain:{handle:(_name,fn)=>{handler=fn;}}};
 vm.runInNewContext(source.slice(start,end),context);
 const body=()=>({operationId:'original',text_sha256:quickDigest(reply.text),url:'https://example.com/report'});
 return {context,scope,reply,opened,requests,body,invoke:body=>handler('trusted',body),untrusted:body=>handler('other',body),wait:promise=>{release=promise;}};
}
test('Main explicit link requires the exact original reply body, operation and discussion before opening',async()=>{
 const f=fixture();await f.invoke(f.body());assert.deepEqual(f.opened,['https://example.com/report']);assert.deepEqual(f.requests,['/api/desktop-host/native-messages/original']);
 for(const url of ['file:///D:/secret','command:run','javascript:alert(1)','https://user:pass@example.com','https://example.com/not-in-reply'])await assert.rejects(f.invoke({...f.body(),url}));
 await assert.rejects(f.untrusted(f.body()));await assert.rejects(f.invoke({...f.body(),text_sha256:'0'.repeat(64)}));
 for(const replacement of ['No link','`https://example.com/report`','![image](https://example.com/report)','<a href="https://example.com/report">raw</a>']){f.reply.text=replacement;await assert.rejects(f.invoke(f.body()));}
 assert.equal(f.opened.length,1);
});
test('Main link refuses a stale or foreign answer and a scope switch during its read',async()=>{
 for(const changed of [{project_id:'other'},{conversation_id:'other'},{operation_id:'other'}]){const f=fixture();Object.assign(f.reply,changed);await assert.rejects(f.invoke(f.body()));assert.equal(f.opened.length,0);}
 const f=fixture();let finish;f.wait(new Promise(resolve=>finish=resolve));const opening=f.invoke(f.body());f.scope.generation++;finish();await assert.rejects(opening);assert.equal(f.opened.length,0);
});

'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {conversationHistoryPath}=require('../conversation-history.cjs');
const id='original:'+'a'.repeat(64);
test('history request stays on one exact conversation and bounded read continuation, without ambient context or mutations',()=>{
 assert.equal(conversationHistoryPath({conversationId:id}),'/api/conversations/'+encodeURIComponent(id)+'/history');
 const url=new URL('http://owned'+conversationHistoryPath({conversationId:id,limit:50,cursor:'abc_123-xyz'}));
 assert.equal(url.searchParams.get('limit'),'50');assert.equal(url.searchParams.get('cursor'),'abc_123-xyz');
 for(const body of [undefined,null,{}, {conversationId:'../../file'}, {conversationId:id,limit:0},
  {conversationId:id,limit:true},{conversationId:id,limit:101},{conversationId:id,cursor:'?url=other'},
  {conversationId:id,cursor:''},{conversationId:id,cursor:'a'.repeat(2049)},
  {conversationId:id,project_id:'borrowed'},{conversationId:id,allow_write:true}])assert.throws(()=>conversationHistoryPath(body));
});
test('actual preload forwards the typed history request through its dedicated read channel only',async()=>{
 const calls=[];let bridge;
 const ipcRenderer={invoke:async(channel,body)=>{calls.push({channel,body});return {version:1};},on(){},removeListener(){}};
 vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../preload.cjs'),'utf8'),{
  require:name=>{assert.equal(name,'electron');return {contextBridge:{exposeInMainWorld:(_,value)=>{bridge=value;}},ipcRenderer};},
 });
 const body={conversationId:id,limit:20,cursor:'owned_cursor'};
 await bridge.getConversationHistory(body);
 assert.deepEqual(calls,[{channel:'eeas:conversation-history-get',body}]);assert.equal(bridge.fetch,undefined);
 const main=fs.readFileSync(path.join(__dirname,'../main.cjs'),'utf8');
 assert.match(main,/ipcMain\.handle\('eeas:conversation-history-get',async\(event,body\)=>\{admitted\(event\);return request\(conversationHistoryPath\(body\)\);\}\)/);
});

'use strict';
const {exactKeys}=require('./ipc-policy.cjs');
const {conversationIdentity}=require('./conversation-reference.cjs');
function conversationHistoryPath(body){
 const optional=['cursor','limit'].filter(key=>Object.prototype.hasOwnProperty.call(body??{},key));
 exactKeys(body,['conversationId',...optional]);
 const identity=conversationIdentity(body.conversationId),query=new URLSearchParams();
 if(optional.includes('limit')){
  if(!Number.isSafeInteger(body.limit)||body.limit<1||body.limit>100)throw Error('A bounded history page size is required');
  query.set('limit',String(body.limit));
 }
 if(optional.includes('cursor')){
  if(typeof body.cursor!=='string'||!body.cursor.length||body.cursor.length>2048||!/^[A-Za-z0-9_-]+$/.test(body.cursor))throw Error('A bounded history continuation is required');
  query.set('cursor',body.cursor);
 }
 const suffix=query.toString();
 return '/api/conversations/'+encodeURIComponent(identity)+'/history'+(suffix?'?'+suffix:'');
}
module.exports={conversationHistoryPath};

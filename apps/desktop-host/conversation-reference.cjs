'use strict';
const {exactKeys}=require('./ipc-policy.cjs');
const identity=value=>typeof value==='string'&&/^(native|original|discovery):[a-f0-9]{64}$/.test(value);
function conversationIdentity(value){if(!identity(value))throw Error('A stable conversation identity is required');return value;}
function conversationReference(value){
 if(value==null)return null;
 exactKeys(value,['id','revision']);
 if(!identity(value.id)||!Number.isSafeInteger(value.revision)||value.revision<1)throw Error('An exact conversation organization revision is required');
 return {id:value.id,revision:value.revision};
}
module.exports={conversationIdentity,conversationReference};

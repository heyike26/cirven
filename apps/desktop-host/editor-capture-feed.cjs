'use strict';
// One authenticated backend -> original Main -> paired IDE metadata connection.
// This transport cannot create requests, publish observations or execute work.
const SHA32=/^[a-f0-9]{32}$/;
const exact=(v,keys)=>v&&typeof v==='object'&&!Array.isArray(v)&&Object.keys(v).length===keys.length&&keys.every(k=>Object.hasOwn(v,k));
function fail(){throw Object.assign(new Error('Editor capture metadata feed is invalid'),{code:'EDITOR_CAPTURE_CHANNEL_REQUIRED'});}
class DesktopEditorCaptureFeed{
 constructor({openStream,notify,isClosing=()=>false,onError=()=>{}}){
  if(typeof openStream!=='function'||typeof notify!=='function')throw new TypeError('Original Main metadata transport required');
  this.openStream=openStream;this.notify=notify;this.isClosing=isClosing;this.onError=onError;this.current=null;this.closed=false;
 }
 get active(){return !this.closed&&Boolean(this.current?.ready&&!this.current.abort.signal.aborted);}
 start(){
  if(this.closed||this.isClosing())return Promise.reject(Object.assign(new Error('Desktop is closing'),{code:'DESKTOP_UNAVAILABLE'}));
  if(this.current)return this.current.promise;
  const state={abort:new AbortController(),reader:null,ready:false,runtime:null};
  state.promise=new Promise((resolve,reject)=>{state.resolve=resolve;state.reject=reject;});this.current=state;
  state.timer=setTimeout(()=>state.abort.abort(),5000);state.timer.unref?.();
  state.task=this.run(state).catch(error=>{state.reject(error);if(!this.closed&&!this.isClosing())this.onError(error);}).finally(()=>{
   if(!state.ready)state.reject(Object.assign(new Error('Editor capture connection did not become ready'),{code:'EDITOR_CAPTURE_CHANNEL_REQUIRED'}));
   clearTimeout(state.timer);state.abort.abort();void state.reader?.cancel().catch(()=>{});if(this.current===state)this.current=null;
  });return state.promise;
 }
 async run(state){
  const response=await this.openStream(state.abort.signal);
  if(state.abort.signal.aborted){void response.body?.cancel().catch(()=>{});fail();}
  if(!response.ok||!response.headers.get('content-type')?.startsWith('application/x-ndjson')){void response.body?.cancel().catch(()=>{});fail();}
  state.reader=response.body?.getReader();if(!state.reader)fail();
  const decoder=new TextDecoder('utf-8',{fatal:true});let buffer='';
  while(!state.abort.signal.aborted){
   const chunk=await state.reader.read();if(state.abort.signal.aborted)return;
   if(chunk.value?.byteLength>256*1024)fail();
   buffer+=decoder.decode(chunk.value??new Uint8Array(),{stream:!chunk.done});let index;
   while((index=buffer.indexOf('\n'))>=0){
    const line=buffer.slice(0,index);buffer=buffer.slice(index+1);if(Buffer.byteLength(line)>16384)fail();
    let value;try{value=JSON.parse(line);}catch{fail();}
    if(!state.ready){
     if(!exact(value,['type','runtime_id'])||value.type!=='ready'||!SHA32.test(value.runtime_id??''))fail();
     state.ready=true;state.runtime=value.runtime_id;clearTimeout(state.timer);state.resolve({connected:true,read_only:true});
    }else{
     if(!exact(value,['type','runtime_id','rows'])||value.type!=='editor-capture-batch'||value.runtime_id!==state.runtime||!Array.isArray(value.rows)||value.rows.length<1||value.rows.length>16)fail();
     // Existing bridge validates every row and original actor/project grants.
     await this.notify(value.rows);
    }
   }
   if(Buffer.byteLength(buffer)>16384)fail();
   if(chunk.done)fail();
  }
 }
 async close(){this.closed=true;const state=this.current;if(!state)return;state.reject(Object.assign(new Error('Desktop capture connection closed'),{code:'DESKTOP_UNAVAILABLE'}));state.abort.abort();void state.reader?.cancel().catch(()=>{});await state.task;}
}
module.exports={DesktopEditorCaptureFeed};

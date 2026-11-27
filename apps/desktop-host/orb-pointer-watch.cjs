'use strict';

const SCHEMA='eeas.orb-pointer-watch.v1';
const validId=value=>typeof value==='string'&&/^[a-f0-9]{32}$/.test(value);

function createOrbPointerWatch({openStream,isClosing=()=>false,onDiagnostic=()=>{}}){
  if(typeof openStream!=='function')throw new Error('Owned pointer stream is required');
  return function watchOutside({onReady,onOutside,onError}){
    if(![onReady,onOutside,onError].every(fn=>typeof fn==='function'))throw new Error('Pointer watch callbacks are required');
    const abort=new AbortController();let stopped=false,reader,ready=false,identity,lease;
    const stop=()=>{if(stopped)return;stopped=true;clearTimeout(deadline);clearTimeout(lease);abort.abort();void reader?.cancel().catch(()=>{});};
    const fail=error=>{if(stopped)return;stop();onDiagnostic(error);onError(error);};
    const deadline=setTimeout(()=>fail(new Error('Pointer watch readiness timed out')),2000);deadline.unref?.();
    void (async()=>{
      if(isClosing())return stop();
      const response=await openStream(abort.signal);
      if(stopped){void response.body?.cancel().catch(()=>{});return;}
      if(!response.ok||!response.headers.get('content-type')?.startsWith('application/x-ndjson'))throw Error('Pointer watch stream unavailable');
      reader=response.body?.getReader();if(!reader)throw Error('Pointer watch body unavailable');
      const decoder=new TextDecoder('utf-8',{fatal:true});let buffer='';
      while(!stopped){
        const chunk=await reader.read();if(stopped)return;
        buffer+=decoder.decode(chunk.value??new Uint8Array(),{stream:!chunk.done});
        if(buffer.length>4096)throw Error('Pointer watch message exceeded its bound');
        for(let index;(index=buffer.indexOf('\n'))>=0;){
          const line=buffer.slice(0,index);buffer=buffer.slice(index+1);if(!line.trim())continue;
          const value=JSON.parse(line);
          if(!value||value.schema!==SCHEMA||!validId(value.watch_id)||!validId(value.host_generation)||value.sequence!==(ready?1:0))throw Error('Pointer watch identity changed');
          if(!ready){
            if(value.kind!=='READY'||value.lease_ms!==60000)throw Error('Pointer watch did not become ready');
            identity=[value.watch_id,value.host_generation];ready=true;clearTimeout(deadline);
            lease=setTimeout(()=>{if(stopped)return;stop();onOutside();},65000);lease.unref?.();onReady();
          }else{
            if(value.watch_id!==identity[0]||value.host_generation!==identity[1])throw Error('Pointer watch lifetime changed');
            if(!['OUTSIDE_POINTER_DOWN','EXPIRED','UNAVAILABLE'].includes(value.kind))throw Error('Unknown pointer watch event');
            stop();if(value.kind==='UNAVAILABLE')onError(new Error('Pointer watch unavailable'));else onOutside();return;
          }
        }
        if(chunk.done)throw Error('Pointer watch ended without a terminal event');
      }
    })().catch(fail);
    return stop;
  };
}

module.exports={createOrbPointerWatch};

// The original private SDK supplies session metadata; model text is never a
// lineage source. No tool/model call, agent loop, write grant or fallback here.
import {canonical,digest,fail,samePath} from './files.mjs';
const nativeId=value=>typeof value==='string'&&/^ses_[A-Za-z0-9_-]{1,100}$/.test(value);
export function lineageReader({scope,scopeSha,current,resolveSession}){
  let reading=0;
  return async sessionID=>{
    const start=current(),root=start.native_session_id;
    if(sessionID===root)return null;
    if(!nativeId(sessionID)||!nativeId(root)||typeof resolveSession!=='function')fail('CHILD_LINEAGE_UNAVAILABLE');
    if(reading>=8)fail('CHILD_LINEAGE_CAPACITY');
    reading++;
    const abort=new AbortController(),timeout=setTimeout(()=>abort.abort(),2000);
    let rejectTimeout;
    const expired=new Promise((_resolve,reject)=>{rejectTimeout=()=>reject(Object.assign(new Error('Child metadata deadline'),{code:'CHILD_LINEAGE_DEADLINE'}));abort.signal.addEventListener('abort',rejectTimeout,{once:true});});
    try{
      const chain=[],seen=new Set([root]);let id=sessionID;
      while(id!==root){
        if(chain.length>=12||seen.has(id)||!nativeId(id)||abort.signal.aborted)fail('CHILD_LINEAGE_INVALID');
        seen.add(id);
        const value=await Promise.race([resolveSession(id,{signal:abort.signal}),expired]);
        if(!value||typeof value!=='object'||Array.isArray(value)||value.id!==id||!nativeId(value.parentID)||
           typeof value.directory!=='string'||!samePath(value.directory,scope.workspace_root)||Buffer.byteLength(canonical(value))>65536)fail('CHILD_LINEAGE_INVALID');
        chain.push({id,parent_id:value.parentID});id=value.parentID;
      }
      const end=current();if(end.native_session_id!==root||end.scope_sha256!==scopeSha)fail('CHILD_LINEAGE_CHANGED');
      const value={schema:'eeas.change-review.child-lineage.v1',scope_sha256:scopeSha,root_native_session_id:root,
        native_session_id:sessionID,chain,directory_sha256:digest(Buffer.from(scope.workspace_root)),source:'ORIGINAL_SDK_SESSION_GET'};
      if(Buffer.byteLength(canonical(value))>8192)fail('CHILD_LINEAGE_TOO_LARGE');
      return {value,reference:{id:digest(canonical({native_session_id:sessionID})),revision:1,sha256:digest(canonical(value))}};
    }finally{clearTimeout(timeout);abort.signal.removeEventListener('abort',rejectTimeout);reading--;}
  };
}

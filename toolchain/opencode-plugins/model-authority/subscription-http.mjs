// Original Codex auth owns tokens and refresh. This wrapper sees only the final
// HTTP inference request, reserves metadata before a single forward, and never
// retries. The pinned internal title hint selects its existing HTTP path.
import {createHash,randomBytes} from 'node:crypto';
const HEADER='x-cirven-model-plan',HTTP_HINT='x-opencode-title';
const ENDPOINT='https://chatgpt.com/backend-api/codex/responses';
const sha=v=>createHash('sha256').update(v).digest('hex');
const hex=v=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const key=input=>JSON.stringify([input.sessionID,input.message?.id,input.agent]);
const SLOT=Symbol.for('cirven.original.subscription.http.meter');
export function installSubscriptionHTTP({initial,scope,ask}){
 if(!initial.subscription_http)return null;
 if(initial.subscription_http.transport!=='ORIGINAL_CODEX_HTTP_FALLBACK'||typeof initial.subscription_http.model_id!=='string'||!initial.subscription_http.model_id||globalThis[SLOT])throw Error('SUBSCRIPTION_HTTP_SCOPE_UNAVAILABLE');
 const original=globalThis.fetch,plans=new Map(),tickets=new Map(),active=new Set();let hop=0,closed=false;
 const sameScope=()=>{const value=scope();if(JSON.stringify(value.owner_ref)!==JSON.stringify(initial.owner_ref)||value.generation!==initial.generation||value.subscription_http?.model_id!==initial.subscription_http.model_id||value.subscription_http?.transport!==initial.subscription_http.transport)throw Error('SUBSCRIPTION_HTTP_OWNER_CHANGED');return value;};
 const wrapped=async(input,init)=>{
  const url=new URL(input instanceof Request?input.url:String(input));
  const headers=new Headers(init?.headers??(input instanceof Request?input.headers:undefined)),ticket=headers.get(HEADER);
  if(url.href!==ENDPOINT){if(ticket||url.hostname==='api.openai.com'&&/^\/v1\/(?:responses|chat\/completions)(?:\/|$)/.test(url.pathname))throw Error('SUBSCRIPTION_HTTP_UNEXPECTED_ROUTE');return original(input,init);}
  if(closed||!ticket||!tickets.has(ticket)||hop>=1048576||active.size>=4)throw Error('SUBSCRIPTION_HTTP_PLAN_REQUIRED');
  const plan=tickets.get(ticket),value=sameScope();
  if(plan.body.generation!==value.generation||plan.body.root_native_session_id!==value.native_session_id)throw Error('SUBSCRIPTION_HTTP_PLAN_CHANGED');
  if(init?.method!=='POST'||typeof init.body!=='string'||Buffer.byteLength(init.body)>4194304)throw Error('SUBSCRIPTION_HTTP_BODY_NOT_BOUNDED');
  const request=JSON.parse(init.body);if(request?.model!==value.subscription_http.model_id||request.stream!==true)throw Error('SUBSCRIPTION_HTTP_MODEL_CHANGED');
  // No header/auth value or source/model body is sent to the application broker.
  const protocol=plan.body.schema==='eeas.adapter-model-authority-call.v1'?'eeas.adapter-model-authority':'eeas.model-authority';
  const body={...plan.body,schema:protocol+'-http-hop.v1',hop:++hop,plan_sha256:plan.hash,request_sha256:sha(init.body),request_bytes:Buffer.byteLength(init.body)},tag=body.hop;
  active.add(tag);
  try{
   const result=await ask(value,body);
   if(result.schema!==protocol+'-http-hop-receipt.v1'||result.admitted!==true||result.step!==body.step||result.hop!==body.hop||result.generation!==body.generation||result.root_native_session_id!==body.root_native_session_id||result.native_session_id!==body.native_session_id||result.user_message_id!==body.user_message_id||result.request_sha256!==body.request_sha256||!hex(result.reservation_sha256)||!Number.isSafeInteger(result.reserved)||!Number.isSafeInteger(result.limit)||result.reserved<1||result.limit>128||result.reserved>result.limit||result.model_calls_reserved!==0)throw Error('SUBSCRIPTION_HTTP_RECEIPT_CHANGED');
   sameScope();if(closed||init.signal?.aborted)throw Error('SUBSCRIPTION_HTTP_FORWARD_CANCELLED');
   headers.delete(HEADER);headers.delete(HTTP_HINT);
   // Redirects cannot create an unmetered second forward. Payload and native
   // Authorization headers remain byte-for-byte as supplied by original auth.
   return await original(input,{...init,headers,redirect:'error'});
  }finally{active.delete(tag);}
 };
 Object.assign(wrapped,original);globalThis.fetch=wrapped;globalThis[SLOT]=wrapped;
 return {
  register(input,body,result){if(closed||!hex(result.plan_sha256)||tickets.size>=256)throw Error('SUBSCRIPTION_HTTP_PLAN_CAPACITY');const k=key(input),queue=plans.get(k)??[],ticket=randomBytes(32).toString('hex');queue.push(ticket);plans.set(k,queue);tickets.set(ticket,{body:structuredClone(body),hash:result.plan_sha256});},
  headers(input,output){sameScope();if(closed||input.model?.providerID!=='openai')throw Error('SUBSCRIPTION_HTTP_ORIGINAL_PROVIDER_REQUIRED');const k=key(input),queue=plans.get(k),ticket=queue?.shift();if(!ticket)throw Error('SUBSCRIPTION_HTTP_ORIGINAL_PLAN_REQUIRED');if(!queue.length)plans.delete(k);output.headers[HEADER]=ticket;output.headers[HTTP_HINT]='true';},
  dispose(){closed=true;plans.clear();tickets.clear();if(globalThis.fetch===wrapped)globalThis.fetch=original;if(globalThis[SLOT]===wrapped)delete globalThis[SLOT];}
 };
}

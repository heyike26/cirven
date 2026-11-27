import {AuthoredUIError,authoredCopy,authoredErrorCopy,type AuthoredUICopy} from './authored-ui-copy.ts';
import type { ProviderProfile, ReasoningEffort, SourceRef } from './contracts';
import { nativeReference, type NativeRef } from './native-collaboration.ts';

export const PROVIDER_CONNECTION_HISTORY_KEY='eeas-provider-connection-operations';
export const PROVIDER_CA_MAX_BYTES=128*1024;
export const PROVIDER_REASONING_EFFORTS=['none','minimal','low','medium','high','xhigh'] as const;
export function providerReasoningEfforts(value:unknown=undefined):ReasoningEffort[]{
  if(value===undefined)return[];
  if(!Array.isArray(value)||value.length>6||value.some(item=>typeof item!=='string'||!(PROVIDER_REASONING_EFFORTS as readonly string[]).includes(item))||new Set(value).size!==value.length)throw new Error('思考档位须为明确、不重复的支持项');
  return PROVIDER_REASONING_EFFORTS.filter(item=>value.includes(item));
}
export type ProviderHeader={name:string;value?:string;secret_value?:string};
export type ProviderConnectionBody={operation_id:string;id:string;expected_revision:number;provider_url:string;model_id:string;supports_images:boolean;reasoning_efforts?:ReasoningEffort[];auth_mode:'API_KEY'|'NONE';api_key?:string;transport:{timeout_seconds:number;ca_pem?:string;proxy?:{url:string;username?:string;password?:string};custom_headers:ProviderHeader[]}};
export type ProviderConnectionProfile=ProviderProfile&{transport?:{
  policy_ref:NativeRef;auth_mode:'API_KEY'|'NONE';timeout_seconds:number;
  ca:null|{kind:string;sha256:string;byte_length?:number|null;file_source_ref?:SourceRef|null};
  proxy:null|{url:string;auth_configured:boolean};custom_headers:{name:string;private:boolean}[];
  model_access:string;trust_environment:boolean;follow_redirects:boolean;tls_verification:string;
}};
export type ProviderHeaderDraft={id:string;name:string;mode:'PUBLIC'|'SECRET';value:string};
export type ProviderConnectionDraft={id:string;revision:number;url:string;model:string;auth:'API_KEY'|'NONE';key:string;images:boolean;reasoningEfforts:ReasoningEffort[];timeout:number;caMode:'SYSTEM'|'CUSTOM';caPem:string;proxyMode:'DIRECT'|'EXPLICIT';proxyUrl:string;proxyAuth:boolean;proxyUsername:string;proxyPassword:string;headers:ProviderHeaderDraft[];transportDecision:'REPLACE'|'RESET'|'UNDECIDED'};
export type ProviderSaveOperation={operationId:string;profileId:string;expectedRevision:number;providerUrl:string;modelId:string;authMode:'API_KEY'|'NONE';supportsImages:boolean;reasoningEfforts?:ReasoningEffort[]};
export type ProviderSaveReceipt={operation_id:string;status:'COMPLETED'|'INCOMPLETE'|'FAILED_OR_PARTIAL';request_sha256:string|null;result:ProviderConnectionProfile|null;failure?:{phase:string;code:string}|null;history_format?:string};
export type ProviderSaveView={operation:ProviderSaveOperation;status:'COMPLETED'|'INCOMPLETE'|'FAILED_OR_PARTIAL'|'NO_RECORD'|'INSPECTION_UNAVAILABLE';profile?:ProviderConnectionProfile;failure?:{phase:string;code:string}|null;message?:string;message_copy?:AuthoredUICopy};
export type ProviderSaveStorage=Pick<Storage,'getItem'|'setItem'>;
export type ProviderConnectionAdapter={save:(body:ProviderConnectionBody)=>Promise<unknown>;inspect?:(operationId:string)=>Promise<unknown>};
const validId=(value:unknown):value is string=>typeof value==='string'&&/^[A-Za-z0-9_-]{1,100}$/.test(value);
const blockedHeaders=new Set(['host','connection','content-length','contentlength','transfer-encoding','transferencoding','origin','te','trailer','upgrade','expect','proxy-connection','proxy-authorization','content-encoding','content-type']);
const sensitiveHeaders=new Set(['authorization','cookie','set-cookie','api-key','apikey','x-api-key','x-auth-token','x-access-token','x-csrf-token']);
export const providerHeaderIsSensitive=(name:string)=>sensitiveHeaders.has(name.toLowerCase())||['token','secret','password','credential','auth','key'].some(part=>name.toLowerCase().includes(part));
export const newProviderHeader=(name='',privateValue=true):ProviderHeaderDraft=>({id:crypto.randomUUID(),name,mode:privateValue?'SECRET':'PUBLIC',value:''});
export function providerTransportNeedsReview(profile?:ProviderConnectionProfile){const transport=profile?.transport;return !!transport&&(!!transport.ca||!!transport.proxy||transport.custom_headers.length>0||transport.timeout_seconds!==60);}
export function providerConnectionDraft(profile?:ProviderConnectionProfile):ProviderConnectionDraft{
  const transport=profile?.transport;return{id:profile?.profile_ref.id??'',revision:profile?.profile_ref.revision??0,url:profile?.provider_url??'',model:profile?.model_id??'',auth:profile?.auth_kind==='NONE'?'NONE':'API_KEY',key:'',images:profile?.supports_images===true,reasoningEfforts:providerReasoningEfforts(profile?.reasoning_efforts),timeout:transport?.timeout_seconds??60,caMode:transport?.ca?'CUSTOM':'SYSTEM',caPem:'',proxyMode:transport?.proxy?'EXPLICIT':'DIRECT',proxyUrl:transport?.proxy?.url??'',proxyAuth:transport?.proxy?.auth_configured===true,proxyUsername:'',proxyPassword:'',headers:transport?.custom_headers.map(header=>newProviderHeader(header.name,header.private))??[],transportDecision:providerTransportNeedsReview(profile)?'UNDECIDED':'REPLACE'};
}
export function providerCAContent(bytes:Uint8Array){
  if(!bytes.length||bytes.byteLength>PROVIDER_CA_MAX_BYTES)throw new Error('CA 证书应为 1–128 KiB 的 PEM 文件');
  let text:string;try{text=new TextDecoder('utf-8',{fatal:true}).decode(bytes);}catch{throw new Error('请选择 UTF-8 PEM 文本，二进制证书尚未适配');}
  if(/[^\u0000-\u007f]/u.test(text)||!text.includes('-----BEGIN CERTIFICATE-----')||text.replace(/-----BEGIN CERTIFICATE-----\s+[A-Za-z0-9+/=\s]+-----END CERTIFICATE-----/g,'').trim())throw new Error('请选择只含 CERTIFICATE 的 PEM；二进制证书或私钥不适用于此字段');
  return text;
}
export async function readProviderCA(file:Pick<File,'size'|'name'|'arrayBuffer'>){
  if(!file||file.size<1||file.size>PROVIDER_CA_MAX_BYTES)throw new Error('所选 CA 文件应为 1–128 KiB');
  const bytes=new Uint8Array(await file.arrayBuffer());if(bytes.byteLength!==file.size)throw new Error('所选证书文件大小已变化，请重新选择');
  return {name:file.name,pem:providerCAContent(bytes)};
}
function endpoint(value:string){
  if(!value||value.length>2000||/[\u0000-\u0020]/u.test(value)||!/^https?:\/\//.test(value))throw new Error('请输入明确的 HTTP(S) API 地址，末尾为 /v1');
  let url:URL;try{url=new URL(value);}catch{throw new Error('API 地址格式无效');}
  if(!url.hostname||url.username||url.password||url.search||url.hash||!url.pathname.endsWith('/v1'))throw new Error('API 地址须以 /v1 结尾，不能包含账号、查询参数或片段');return value;
}
function proxyAddress(value:string){
  if(!value||value.length>2048||/[^\u0021-\u007e]/u.test(value)||!/^https?:\/\//.test(value)||value.includes('\\'))throw new Error('请输入明确的 HTTP(S) 代理地址');
  let url:URL;try{url=new URL(value);}catch{throw new Error('代理地址格式无效');}
  if(!url.hostname||url.username||url.password||url.search||url.hash||!['','/'].includes(url.pathname)||url.host.includes('%'))throw new Error('代理地址不能包含账号、路径或查询参数；账号请填在私密字段');return value.replace(/\/$/,'');
}
function headerValue(value:string){if(!value||value.length>8192||value!==value.replace(/^ +| +$/g,'')||/[^\u0020-\u007e]/u.test(value))throw new Error('请求头值应为 1–8192 个可打印 ASCII 字符，不能有首尾空格');return value;}
export function providerConnectionBody(draft:ProviderConnectionDraft,key='provider-save-'+crypto.randomUUID().replaceAll('-','')):ProviderConnectionBody{
  const id=draft.id.trim(),model=draft.model.trim();if(!validId(key)||!validId(id))throw new Error('连接标识应为 1–100 个字母、数字、下划线或连字符');
  if(!Number.isSafeInteger(draft.revision)||draft.revision<0)throw new Error('原配置版本尚未核对，请重新读取');if(!/^[A-Za-z0-9_.:/-]{1,200}$/.test(model))throw new Error('请输入服务实际提供的模型标识');
  if(!['API_KEY','NONE'].includes(draft.auth)||typeof draft.images!=='boolean')throw new Error('认证方式或图片配置无效');
  if(draft.auth==='API_KEY'&&(!draft.key||draft.key.length>8192||/[\u0000-\u001f]/u.test(draft.key)))throw new Error('API Key 认证须在本次保存时明确填写有效密钥');
  if(draft.auth==='NONE'&&draft.key)throw new Error('免 API Key 模式不能保留密钥，请清空此字段');
  if(!['REPLACE','RESET','UNDECIDED'].includes(draft.transportDecision)||draft.transportDecision==='UNDECIDED')throw new Error('已有高级连接配置不会自动沿用，请明确完整重填或重置');
  if(!['SYSTEM','CUSTOM'].includes(draft.caMode)||!['DIRECT','EXPLICIT'].includes(draft.proxyMode)||typeof draft.proxyAuth!=='boolean')throw new Error('证书或代理配置方式无效');
  const transport:ProviderConnectionBody['transport']={timeout_seconds:draft.transportDecision==='RESET'?60:draft.timeout,custom_headers:[]};
  if(typeof transport.timeout_seconds!=='number'||!Number.isFinite(transport.timeout_seconds)||transport.timeout_seconds<0.1||transport.timeout_seconds>120)throw new Error('单次请求超时应为 0.1–120 秒');
  if(draft.transportDecision!=='RESET'){
    if(draft.caMode==='CUSTOM')transport.ca_pem=providerCAContent(new TextEncoder().encode(draft.caPem));
    if(draft.proxyMode==='EXPLICIT'){
      transport.proxy={url:proxyAddress(draft.proxyUrl.trim())};
      if(draft.proxyAuth){for(const value of [draft.proxyUsername,draft.proxyPassword])headerValue(value);if(draft.proxyUsername.includes(':'))throw new Error('代理账号不能包含冒号');transport.proxy.username=draft.proxyUsername;transport.proxy.password=draft.proxyPassword;}
    }
    if(draft.headers.length>32)throw new Error('最多配置 32 个自定义请求头');const names=new Set<string>();
    for(const header of draft.headers){const name=header.name.trim().toLowerCase();if(!/^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$/.test(name))throw new Error('请求头名称格式无效');if(blockedHeaders.has(name)||name.startsWith('sec'))throw new AuthoredUIError('请求头 {name} 由连接服务管理，不能自定义',{name});if(names.has(name))throw new AuthoredUIError('请求头 {name} 重复',{name});names.add(name);
      if(draft.auth==='API_KEY'&&name==='authorization')throw new Error('API Key 模式已提供 Authorization，不能重复设置');if(!['PUBLIC','SECRET'].includes(header.mode))throw new Error('请明确请求头值的存储方式');if(header.mode==='PUBLIC'&&providerHeaderIsSensitive(name))throw new AuthoredUIError('请求头 {name} 必须使用私密值',{name});const value=headerValue(header.value);transport.custom_headers.push(header.mode==='SECRET'?{name,secret_value:value}:{name,value});
    }
  }
  return{operation_id:key,id,expected_revision:draft.revision,provider_url:endpoint(draft.url.trim()),model_id:model,supports_images:draft.images,reasoning_efforts:providerReasoningEfforts(draft.reasoningEfforts),auth_mode:draft.auth,...draft.auth==='API_KEY'?{api_key:draft.key}:{},transport};
}
function operationFor(body:ProviderConnectionBody):ProviderSaveOperation{return{operationId:body.operation_id,profileId:body.id,expectedRevision:body.expected_revision,providerUrl:body.provider_url,modelId:body.model_id,authMode:body.auth_mode,supportsImages:body.supports_images,reasoningEfforts:providerReasoningEfforts(body.reasoning_efforts)};}
function validOperation(value:unknown):value is ProviderSaveOperation{const op=value as ProviderSaveOperation;if(!op||!validId(op.operationId)||!validId(op.profileId)||!Number.isSafeInteger(op.expectedRevision)||op.expectedRevision<0||typeof op.providerUrl!=='string'||typeof op.modelId!=='string'||!/^[A-Za-z0-9_.:/-]{1,200}$/.test(op.modelId)||!['API_KEY','NONE'].includes(op.authMode)||typeof op.supportsImages!=='boolean')return false;try{endpoint(op.providerUrl);providerReasoningEfforts(op.reasoningEfforts);return true;}catch{return false;}}
export function readProviderSaveHistory(storage:ProviderSaveStorage){const raw=storage.getItem(PROVIDER_CONNECTION_HISTORY_KEY);if(raw===null)return[];let value:unknown;try{value=JSON.parse(raw);}catch{throw new Error('连接保存记录无法读取；原记录保留，未重复保存');}if(!Array.isArray(value)||value.length>128||value.some(op=>!validOperation(op))||new Set(value.map(op=>op.operationId)).size!==value.length)throw new Error('连接保存记录格式需核查；未重复保存');return (value as ProviderSaveOperation[]).map(op=>({operationId:op.operationId,profileId:op.profileId,expectedRevision:op.expectedRevision,providerUrl:op.providerUrl,modelId:op.modelId,authMode:op.authMode,supportsImages:op.supportsImages,reasoningEfforts:providerReasoningEfforts(op.reasoningEfforts)}));}
function recordOperation(storage:ProviderSaveStorage,operation:ProviderSaveOperation){const history=readProviderSaveHistory(storage);if(history.some(op=>op.operationId===operation.operationId))return false;if(history.length>=128)throw new Error('本地保存操作索引已达上限；未删除历史或发送新请求');storage.setItem(PROVIDER_CONNECTION_HISTORY_KEY,JSON.stringify([...history,operation]));return true;}
export function assertSavedProvider(value:unknown,operation:ProviderSaveOperation):ProviderConnectionProfile{
  const profile=value as ProviderConnectionProfile;if(!profile||!nativeReference(profile.profile_ref)||profile.profile_ref.id!==operation.profileId||profile.profile_ref.revision!==operation.expectedRevision+1||profile.provider_url!==operation.providerUrl||profile.model_id!==operation.modelId||profile.auth_kind!==operation.authMode||!!profile.supports_images!==operation.supportsImages||profile.credential_present!==(operation.authMode==='API_KEY')||profile.model_access!=='NOT_VALIDATED')throw new Error('保存回执与本次连接标识、版本或模型配置不一致');
  const efforts=providerReasoningEfforts(profile.reasoning_efforts),expected=providerReasoningEfforts(operation.reasoningEfforts);
  if(JSON.stringify(efforts)!==JSON.stringify(expected)||profile.reasoning_qualification!==undefined&&profile.reasoning_qualification!==(efforts.length?'DECLARED_NOT_OBSERVED':'NOT_DECLARED')||efforts.length&&profile.reasoning_qualification!=='DECLARED_NOT_OBSERVED')throw new Error('保存回执的思考档位与本次声明不一致或尚未核对');return profile;
}
export class ProviderConnectionClient{
  private adapter:ProviderConnectionAdapter;private storage:ProviderSaveStorage;
  constructor(adapter:ProviderConnectionAdapter,storage:ProviderSaveStorage){this.adapter=adapter;this.storage=storage;}
  async inspect(operation:ProviderSaveOperation):Promise<ProviderSaveView>{
    if(!validOperation(operation))throw new Error('原保存操作身份不完整');if(!this.adapter.inspect)return{operation,status:'INSPECTION_UNAVAILABLE',message:'此入口尚未提供原操作回查；保留草稿，未自动重复保存。'};
    try{const receipt=await this.adapter.inspect(operation.operationId) as Omit<ProviderSaveReceipt,'status'>&{error?:{code?:string};status:string};if(receipt.status==='NO_RECORD'||receipt.error?.code==='NOT_FOUND'||receipt.error?.code==='NO_RECORD')return{operation,status:'NO_RECORD',message:'服务未找到原保存回执；不证明没有写入，未自动重新保存。'};
      if(receipt.operation_id!==operation.operationId||!['COMPLETED','INCOMPLETE','FAILED_OR_PARTIAL'].includes(receipt.status))throw new Error('原保存回执操作身份或状态不一致');if(receipt.status==='COMPLETED')return{operation,status:'COMPLETED',profile:assertSavedProvider(receipt.result,operation)};return{operation,status:receipt.status==='INCOMPLETE'?'INCOMPLETE':'FAILED_OR_PARTIAL',failure:receipt.failure,message:receipt.status==='INCOMPLETE'?'原保存尚未完成，请只读核查，未自动重发。':'原保存存在失败或部分结果，请核查当前配置与原回执，未自动重发。'};
    }catch(error){const failure=error as {status?:number;code?:string},missing=failure.status===404||['NOT_FOUND','NO_RECORD'].includes(failure.code??'');return{operation,status:missing?'NO_RECORD':'INSPECTION_UNAVAILABLE',message:missing?'服务未找到原保存回执；不证明没有写入，没有自动重复保存。':`原保存结果尚未核对；${error instanceof Error?error.message:String(error)}。没有自动重复保存。`,...missing?{}:{message_copy:authoredCopy('原保存结果尚未核对；{detail}。没有自动重复保存。',{detail:authoredErrorCopy(error)})}};}
  }
  async save(body:ProviderConnectionBody):Promise<ProviderSaveView>{
    const operation=operationFor(body);if(!validOperation(operation))throw new Error('保存操作身份不完整');const prior=readProviderSaveHistory(this.storage).find(op=>op.operationId===operation.operationId);if(prior){if(JSON.stringify(prior)!==JSON.stringify(operation))throw new Error('此操作身份已绑定另一配置，请读取原回执');const original=await this.inspect(prior);return{...original,message:'仅展示原操作回执；没有再次保存本次字段或密钥。'};}
    recordOperation(this.storage,operation);try{return{operation,status:'COMPLETED',profile:assertSavedProvider(await this.adapter.save(body),operation)};}catch(error){const result=await this.inspect(operation);return{...result,message:`${result.message??'已回查原保存回执，未重复发送。'} ${error instanceof Error?error.message:String(error)}`,message_copy:authoredCopy('{inspection} {detail}',{inspection:result.message_copy??authoredCopy(result.message??'已回查原保存回执，未重复发送。'),detail:authoredErrorCopy(error)})};}
  }
  async restore(profileId?:string){return Promise.all(readProviderSaveHistory(this.storage).filter(op=>!profileId||op.profileId===profileId).map(op=>this.inspect(op)));}
}

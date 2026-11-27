'use strict';
// Local transport only: this module never launches an agent or reads EEAS secrets.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {randomUUID,randomBytes,createHash}=require('node:crypto');
const {StringDecoder}=require('node:string_decoder');
const PROTOCOL=require('./protocol.json');
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/,UUID=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const plain=v=>v!==null&&typeof v==='object'&&!Array.isArray(v);
function fail(code,message){throw Object.assign(new Error(message),{code});}
function exact(v,keys){return plain(v)&&Object.keys(v).every(k=>keys.includes(k));}
function hash(v){return createHash('sha256').update(v).digest('hex');}
function canonical(v){return JSON.stringify(v,(_k,x)=>plain(x)?Object.fromEntries(Object.keys(x).sort().map(k=>[k,x[k]])):x);}
function safePath(filename){for(let p=filename;;p=path.dirname(p)){if(fs.existsSync(p)&&fs.lstatSync(p).isSymbolicLink())fail('UNSAFE_PATH','关联位置不能经过符号链接或目录联接。');if(path.dirname(p)===p)break;}}
function boundedFile(filename,max=16384){safePath(filename);const st=fs.lstatSync(filename);if(!st.isFile()||st.size>max)fail('INVALID_FILE','连接文件格式或大小无效。');return fs.readFileSync(filename,'utf8');}
function localWindowsPath(value){return typeof value==='string'&&/^[A-Za-z]:[\\/]/.test(value)&&!value.includes('\0')&&!value.startsWith('\\\\')&&!value.includes('://');}
function validateInstallation(root){
  if(typeof root!=='string'||!path.isAbsolute(root))fail('APP_NOT_CONFIGURED','请先关联 EEAS 安装目录。');
  root=path.resolve(root);safePath(root);if(!fs.lstatSync(root).isDirectory())fail('APP_NOT_FOUND','EEAS 安装目录不存在。');
  const start=path.join(root,'START_EEAS.cmd'),python=path.join(root,'runtime','python','pythonw.exe'),bootstrap=path.join(root,'scripts','native_package_bootstrap.py');
  boundedFile(start,65536);const script=boundedFile(bootstrap,1024*1024);safePath(python);
  const st=fs.lstatSync(python);if(!st.isFile())fail('APP_NOT_FOUND','安装包缺少 Python 启动器。');
  let marker=null;try{marker=JSON.parse(boundedFile(path.join(root,'PACKAGE_MANIFEST.json'),8*1024*1024)).ide_companion;}catch(e){if(e.code!=='ENOENT')throw e;}
  const protocolSupported=plain(marker)&&marker.protocol===PROTOCOL.protocol&&marker.background_bootstrap===true&&marker.discovery_relative_path===PROTOCOL.discovery_relative_path&&marker.transport==='MAIN_OWNED_LOOPBACK_HTTP'&&marker.external_agent_owner==='DESKTOP_EEAS_ONLY';
  return {root,python,bootstrap,protocolSupported,backgroundSupported:protocolSupported&&/["']--background["']/.test(script)};
}
function readDiscovery(root,dataDirectory=''){
  const filename=path.join(dataDirectory||path.join(root,'user-data'),'ide-companion','discovery.json');let d;
  try{d=JSON.parse(boundedFile(filename));}catch(e){if(e.code==='ENOENT')fail('APP_NOT_RUNNING','桌面 App 尚未运行，或此版本没有客户端接口。');throw e;}
  if(!exact(d,PROTOCOL.discovery_fields)||d.schema!==PROTOCOL.discovery_schema||d.protocol!==PROTOCOL.protocol||!UUID.test(d.instance_id)||!SHA.test(d.nonce)||!Number.isInteger(d.port)||d.port<1||d.port>65535||!Number.isSafeInteger(d.pid)||d.pid<1||typeof d.version!=='string'||d.version.length>80)fail('PROTOCOL_NOT_SUPPORTED','桌面 App 连接信息无效或版本不兼容。');
  return Object.freeze(d);
}
function validTicket(v){return exact(v,['client_id','client_secret'])&&UUID.test(v.client_id)&&SHA.test(v.client_secret);}
function newTicket(){return {client_id:randomUUID(),client_secret:randomBytes(32).toString('hex')};}
function validateScope(v={}){
  if(!exact(v,['project_id','session_ref','generation','conversation_id','conversation_ref'])||v.project_id!==undefined&&v.project_id!==null&&(typeof v.project_id!=='string'||!ID.test(v.project_id))||v.generation!==undefined&&(!Number.isSafeInteger(v.generation)||v.generation<0))fail('INVALID_SCOPE','会话范围无效。');
  if(v.conversation_ref!==undefined){const r=v.conversation_ref;if(!Object.hasOwn(v,'project_id')||!exact(r,['id','revision'])||typeof r.id!=='string'||!/^(native|original|discovery):[a-f0-9]{64}$/.test(r.id)||!Number.isSafeInteger(r.revision)||r.revision<0||v.session_ref!==undefined||v.generation!==undefined||v.conversation_id!==undefined)fail('INVALID_SCOPE','逻辑会话身份无效。');}
  if(v.conversation_id!==undefined&&(!ID.test(v.conversation_id)||typeof v.conversation_id!=='string'||!v.project_id||v.session_ref!==undefined||v.generation!==undefined))fail('INVALID_SCOPE','讨论范围无效。');
  if(v.session_ref!==undefined&&(!exact(v.session_ref,['id','revision','sha256'])||typeof v.session_ref.id!=='string'||!ID.test(v.session_ref.id)||v.session_ref.revision!==1||typeof v.session_ref.sha256!=='string'||!SHA.test(v.session_ref.sha256)))fail('INVALID_SCOPE','精确会话引用无效。');
  return JSON.parse(canonical(v));
}
class CaptureStreamDecoder{
  constructor({instance_id,window_id,onCapture}){this.instance=instance_id;this.window=window_id;this.onCapture=onCapture;this.decoder=new StringDecoder('utf8');this.buffer='';}
  push(bytes){this.buffer+=this.decoder.write(bytes);let at;
    for(;;){const lf=this.buffer.indexOf('\n\n'),cr=this.buffer.indexOf('\r\n\r\n');at=lf<0?cr:cr<0?lf:Math.min(lf,cr);if(at<0)break;const crlf=this.buffer.slice(at,at+4)==='\r\n\r\n',frame=this.buffer.slice(0,at);this.buffer=this.buffer.slice(at+(crlf?4:2));if(Buffer.byteLength(frame)>16384)fail('EDITOR_CAPTURE_STREAM_INVALID','状态采集通知过大。');if(!frame.trim())continue;
      const lines=frame.split(/\r?\n/);if(lines.length!==2||lines[0]!=='event: editor-capture'||!lines[1].startsWith('data: '))fail('EDITOR_CAPTURE_STREAM_INVALID','状态采集通知格式无效。');
      let v;try{v=JSON.parse(lines[1].slice(6));}catch{fail('EDITOR_CAPTURE_STREAM_INVALID','状态采集通知格式无效。');}
      if(!exact(v,['type','instance_id','window_id','observer_id','capture_ref','expires_at_ms'])||Object.keys(v).length!==6||v.type!=='editor-capture'||v.instance_id!==this.instance||v.window_id!==this.window||!SHA.test(v.observer_id??'')||!SHA.test(v.capture_ref??'')||!Number.isSafeInteger(v.expires_at_ms)||v.expires_at_ms>Date.now()+15000)fail('EDITOR_CAPTURE_STREAM_INVALID','状态采集通知身份不符。');
      if(v.expires_at_ms>=Date.now())this.onCapture(v);
    }if(Buffer.byteLength(this.buffer)>16384)fail('EDITOR_CAPTURE_STREAM_INVALID','状态采集通知未完整返回。');
  }
}
class BridgeClient{
  constructor({discovery,ticket=null,timeoutMs=12000,maxResponseBytes=PROTOCOL.limits.response_bytes}={}){this.discovery=discovery;this.ticket=ticket;this.timeoutMs=timeoutMs;this.maxResponseBytes=maxResponseBytes;this.pending=new Set();this.closed=false;}
  async request(method,route,body,requestId=null,{auth=true}={}){
    if(this.closed)fail('DISCONNECTED','连接已关闭。');
    if(!['GET','POST'].includes(method)||!/^\/v1\/(pair(?:\/status)?|rpc(?:\/[A-Za-z0-9_-]{1,100})?|status)$/.test(route))fail('INVALID_ROUTE','不允许的接口。');
    if(auth&&!validTicket(this.ticket))fail('PAIRING_REQUIRED','请连接并在桌面 App 中批准。');
    const bytes=body===undefined?null:Buffer.from(JSON.stringify(body));if(bytes&&bytes.length>PROTOCOL.limits.body_bytes)fail('REQUEST_TOO_LARGE','消息过长，请缩短或关联资料。');
    return new Promise((resolve,reject)=>{
      const headers={'accept':'application/json','connection':'close'};
      if(auth){headers.authorization='Bearer '+this.ticket.client_secret;headers['x-eeas-client-id']=this.ticket.client_id;}
      if(bytes){headers['content-type']='application/json';headers['content-length']=bytes.length;}
      const req=http.request({host:'127.0.0.1',port:this.discovery.port,path:route,method,headers,agent:false},res=>{
        const chunks=[];let size=0;
        res.on('data',chunk=>{size+=chunk.length;if(size>this.maxResponseBytes)req.destroy(Object.assign(new Error('回复过大，请到桌面 App 查看。'),{code:'RESPONSE_TOO_LARGE'}));else chunks.push(chunk);});
        res.on('error',reject);res.on('end',()=>{
          try{
            if(res.statusCode>=300&&res.statusCode<400)fail('REDIRECT_DENIED','本地接口不允许重定向。');
            if(!String(res.headers['content-type']||'').startsWith('application/json'))fail('INVALID_RESPONSE','桌面接口返回了非 JSON 回复。');
            const e=JSON.parse(Buffer.concat(chunks).toString('utf8'));
            if(!exact(e,PROTOCOL.envelope_fields)||e.protocol!==PROTOCOL.protocol||e.instance_id!==this.discovery.instance_id||!PROTOCOL.envelope_status.includes(e.status)||e.request_id!==(requestId??null))fail('RESPONSE_IDENTITY_MISMATCH','桌面实例或请求引用已变化，请刷新连接后核查原请求。');
            resolve(e);
          }catch(error){reject(error);}
        });
      });
      this.pending.add(req);req.once('close',()=>this.pending.delete(req));req.once('error',error=>reject(Object.assign(new Error('桌面连接未返回结果；请核查原请求。'),{code:error.code||'TRANSPORT_UNKNOWN'})));
      req.setTimeout(body?.method==='session.work.deliveries.export'?Math.max(this.timeoutMs,PROTOCOL.limits.native_export_client_timeout_ms):this.timeoutMs,()=>req.destroy(Object.assign(new Error('timeout'),{code:'TRANSPORT_TIMEOUT'})));
      if(bytes)req.write(bytes);req.end();
    });
  }
  observeCaptures(window,onCapture,onClosed=()=>{}){
    if(this.closed||!validTicket(this.ticket))fail('PAIRING_REQUIRED','请先连接原桌面 App。');if(!ID.test(window)||typeof onCapture!=='function')fail('INVALID_SCOPE','需要原窗口状态采集引用。');
    if(!['session.editor.capture.listen','session.editor.observer.command'].every(m=>this.methods?.includes(m)))fail('CAPABILITY_DENIED','此配对尚未批准窗口状态采集。');
    const owner=this;let opened=false,ended=false,resolve,reject,req;const ready=new Promise((yes,no)=>{resolve=yes;reject=no;});
    const close=code=>{if(ended)return;ended=true;clearTimeout(headerTimer);if(!opened)reject(Object.assign(new Error(code),{code}));try{onClosed(code);}catch{}req?.destroy();};
    const decoder=new CaptureStreamDecoder({instance_id:this.discovery.instance_id,window_id:window,onCapture});
    const headerTimer=setTimeout(()=>close('TRANSPORT_TIMEOUT'),Math.min(5000,this.timeoutMs));headerTimer.unref?.();
    req=http.request({host:'127.0.0.1',port:this.discovery.port,path:'/v1/editor-capture-events?window_id='+encodeURIComponent(window),method:'GET',agent:false,headers:{accept:'text/event-stream',authorization:'Bearer '+this.ticket.client_secret,'x-eeas-client-id':this.ticket.client_id}},res=>{
      if(res.statusCode!==200){close(res.statusCode>=300&&res.statusCode<400?'REDIRECT_DENIED':res.statusCode===401||res.statusCode===403?'PAIRING_REQUIRED':'EDITOR_CAPTURE_CHANNEL_REQUIRED');return;}
      if(!String(res.headers['content-type']||'').startsWith('text/event-stream')||res.headers['x-eeas-instance-id']!==this.discovery.instance_id){close('RESPONSE_IDENTITY_MISMATCH');return;}
      opened=true;clearTimeout(headerTimer);resolve();res.on('data',bytes=>{try{decoder.push(bytes);}catch(error){close(error.code||'EDITOR_CAPTURE_STREAM_INVALID');}});res.on('error',()=>close('DISCONNECTED'));res.on('end',()=>close('DISCONNECTED'));res.on('close',()=>close('DISCONNECTED'));
    });this.pending.add(req);req.once('close',()=>{this.pending.delete(req);close('DISCONNECTED');});req.once('error',error=>close(error.code||'TRANSPORT_UNKNOWN'));req.end();
    return {ready,get active(){return opened&&!ended&&!owner.closed;},dispose(){close('DISCONNECTED');}};
  }
  pair(label='VS Code'){if(!validTicket(this.ticket))fail('PAIRING_REQUIRED','缺少客户端票据。');return this.request('POST','/v1/pair',{protocol:PROTOCOL.protocol,...this.ticket,nonce:this.discovery.nonce,client_label:label},null,{auth:false});}
  async pairStatus(){const value=await this.request('GET','/v1/pair/status');this.projectIds=value.result?.capabilities?.project_ids;this.methods=value.status==='OK'&&value.result?.pairing_status==='PAIRED'&&Array.isArray(value.result.capabilities?.methods)?value.result.capabilities.methods.filter(m=>PROTOCOL.methods.includes(m)):[];this.supportedMethods=Array.isArray(value.result?.supported_methods)?value.result.supported_methods.filter(m=>PROTOCOL.methods.includes(m)):[];return value;}
  rpc(method,scope={},payload={},requestId=randomUUID()){
    if(!PROTOCOL.methods.includes(method)||!ID.test(requestId))fail('INVALID_METHOD','不允许的请求。');
    return this.request('POST','/v1/rpc',{protocol:PROTOCOL.protocol,request_id:requestId,method,scope:validateScope(scope),payload},requestId);
  }
  inspect(requestId){if(!ID.test(requestId))fail('INVALID_REQUEST','原请求编号无效。');return this.request('GET','/v1/rpc/'+requestId,undefined,requestId);}
  close(){this.closed=true;for(const req of this.pending)req.destroy();this.pending.clear();}
}
module.exports={PROTOCOL,CaptureStreamDecoder,BridgeClient,readDiscovery,validateInstallation,localWindowsPath,safePath,boundedFile,newTicket,validTicket,validateScope,canonical,hash};

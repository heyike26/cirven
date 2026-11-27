'use strict';
// Main-owned transport only. dispatch remains the sole existing EEAS authority;
// a client disconnect/transport deadline never cancels an external agent task.
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const {randomBytes,randomUUID,createHash,timingSafeEqual}=require('node:crypto');
const CONTRACT=require('../../plugins/vscode-companion/protocol.json');
const PROTOCOL=CONTRACT.protocol;
const METHODS=Object.freeze([...CONTRACT.methods]);
const READ_ONLY=new Set(CONTRACT.read_only_methods);
// The observer API atomically journals its metadata and immutable receipt.
// Keep its events out of the unrelated human/model mutation journal.
const API_RECEIPT_MUTATIONS=new Set(['session.editor.observer.recovery.command','session.editor.observer.command','session.editor.capture.request']);
const ID=/^[A-Za-z0-9_-]{1,100}$/,SHA=/^[a-f0-9]{64}$/;
const validId=value=>typeof value==='string'&&ID.test(value)&&!/[\x00-\x20]/.test(value);
const canonical=value=>JSON.stringify(value,(_key,item)=>item&&typeof item==='object'&&!Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(key=>[key,item[key]])):item);
const digest=value=>createHash('sha256').update(typeof value==='string'?value:canonical(value)).digest('hex');
const clone=value=>JSON.parse(canonical(value));
const object=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
function exact(value,keys){return object(value)&&Object.keys(value).every(key=>keys.includes(key));}
function fail(code,message,status=400){const error=new Error(message);Object.assign(error,{code,status,public:true});throw error;}
function validateScope(value={}){
  if(!exact(value,['project_id','session_ref','generation','conversation_id','conversation_ref']))fail('INVALID_SCOPE','会话范围无效。');
  if(value.project_id!==undefined&&value.project_id!==null&&!validId(value.project_id))fail('INVALID_SCOPE','工程引用无效。');
  if(value.conversation_ref!==undefined){const r=value.conversation_ref;if(!Object.hasOwn(value,'project_id')||!exact(r,['id','revision'])||typeof r.id!=='string'||!/^(native|original|discovery):[a-f0-9]{64}$/.test(r.id)||!Number.isSafeInteger(r.revision)||r.revision<0||value.session_ref!==undefined||value.generation!==undefined||value.conversation_id!==undefined)fail('INVALID_SCOPE','逻辑会话不能借用执行owner身份。');}
  if(value.conversation_id!==undefined&&(!validId(value.conversation_id)||!value.project_id||value.session_ref!==undefined||value.generation!==undefined))fail('INVALID_SCOPE','桌面讨论不能借用原会话身份。');
  if(value.session_ref!==undefined){const r=value.session_ref;
    if(!exact(r,['id','revision','sha256'])||!validId(r.id)||r.revision!==1||typeof r.sha256!=='string'||!SHA.test(r.sha256))fail('INVALID_SCOPE','精确会话引用无效。');}
  if(value.generation!==undefined&&(!Number.isSafeInteger(value.generation)||value.generation<0))fail('INVALID_SCOPE','会话代次无效。');
  return clone(value);
}
function privatePath(filename){
  for(let cursor=filename;;cursor=path.dirname(cursor)){
    let info;try{info=fs.lstatSync(cursor);}catch(error){if(error.code!=='ENOENT')throw error;}
    if(info?.isSymbolicLink())throw Error('IDE bridge files must not follow symbolic links');
    const parent=path.dirname(cursor);if(parent===cursor)break;
  }
}
class BridgeJournal{
  constructor(directory,{maxClients,maxRecords}){
    privatePath(directory);fs.mkdirSync(directory,{recursive:true,mode:0o700});privatePath(directory);
    this.filename=path.join(directory,'clients.json');this.maxClients=maxClients;this.maxRecords=maxRecords;
    this.value={schema:'eeas.ide-clients.v1',revision:0,clients:{},requests:{}};this.expected=null;
    const raw=this.read();if(raw!==null){const wrapped=JSON.parse(raw),v=wrapped.value;
      if(!exact(wrapped,['value','sha256'])||wrapped.sha256!==digest(v)||!exact(v,['schema','revision','clients','requests'])||
        v.schema!==this.value.schema||!Number.isSafeInteger(v.revision)||v.revision<0||!object(v.clients)||!object(v.requests)||
        Object.keys(v.clients).length>maxClients||Object.keys(v.requests).length>maxRecords)throw Error('IDE bridge journal integrity failed');
      for(const [id,c]of Object.entries(v.clients))if(!validId(id)||!exact(c,['secret_hash','status','label','methods','project_ids','at'])||
        !SHA.test(c.secret_hash)||!['PENDING','UNKNOWN','PAIRED','DENIED','REVOKED'].includes(c.status)||typeof c.label!=='string'||
        c.label.length>80||!Array.isArray(c.methods)||c.methods.some(m=>!METHODS.includes(m))||
        !(c.project_ids===null||Array.isArray(c.project_ids)&&c.project_ids.length<=128&&c.project_ids.every(validId))||!Number.isFinite(c.at))throw Error('IDE client record is invalid');
      for(const [key,r]of Object.entries(v.requests))if(typeof key!=='string'||key.length>201||!exact(r,['client_id','request_id','request_sha256','method','scope','operation_id','status','observed_status','at'])||
        !validId(r.client_id)||!validId(r.request_id)||key!==r.client_id+':'+r.request_id||!SHA.test(r.request_sha256)||!METHODS.includes(r.method)||
        !(r.operation_id===null||validId(r.operation_id))||!(r.observed_status===null||typeof r.observed_status==='string'&&/^[A-Z][A-Z0-9_]{0,63}$/.test(r.observed_status))||
        !['DISPATCHING','RETURNED','FAILED','UNKNOWN'].includes(r.status)||!Number.isFinite(r.at))throw Error('IDE request record is invalid');
      for(const r of Object.values(v.requests))validateScope(r.scope);
      this.value=v;this.expected=digest(raw);
    }
  }
  read(){
    privatePath(this.filename);if(!fs.existsSync(this.filename))return null;
    const st=fs.lstatSync(this.filename);if(!st.isFile()||st.nlink!==1||st.size>4*1024*1024)throw Error('IDE bridge journal is not a bounded independent regular file');
    return fs.readFileSync(this.filename,'utf8');
  }
  commit(change){
    const lock=this.filename+'.lock',temporary=this.filename+'.'+randomUUID()+'.next';let held=false,fd;
    try{
      privatePath(lock);fd=fs.openSync(lock,'wx',0o600);held=true;fs.closeSync(fd);fd=undefined;
      const raw=this.read();if((raw===null?null:digest(raw))!==this.expected)throw Error('IDE bridge journal CAS changed');
      const next=clone(this.value);change(next);next.revision++;
      if(Object.keys(next.clients).length>this.maxClients||Object.keys(next.requests).length>this.maxRecords)fail('CAPACITY_REACHED','连接记录容量已满，请在桌面端核查未决连接。',409);
      const bytes=canonical({value:next,sha256:digest(next)})+'\n';if(Buffer.byteLength(bytes)>4*1024*1024)throw Error('IDE bridge journal capacity reached');
      fd=fs.openSync(temporary,'wx',0o600);fs.writeFileSync(fd,bytes);fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;
      fs.renameSync(temporary,this.filename);this.value=next;this.expected=digest(bytes);
    }finally{if(fd!==undefined)fs.closeSync(fd);if(held){if(fs.existsSync(temporary))fs.unlinkSync(temporary);fs.unlinkSync(lock);}}
  }
  assertCurrent(){const raw=this.read();if((raw===null?null:digest(raw))!==this.expected)throw Error('IDE bridge journal changed outside its owner');}
}
class IdeCompanionBridge{
  constructor({dataDir,version='0.1.0',dispatch,requestPairing,reveal,onError=()=>{},requestTimeoutMs=15000,pairingTimeoutMs=120000,nativeExportTimeoutMs=180000,
    maxBodyBytes=CONTRACT.limits.body_bytes,maxClients=32,maxRecords=2048,maxConcurrent=8,maxPendingPairs=3}={}){
    if(typeof dataDir!=='string'||!path.isAbsolute(dataDir))throw Error('IDE bridge requires an explicit absolute dataDir');
    if(typeof requestPairing!=='function')throw Error('IDE bridge requires explicit user pairing approval');
    Object.assign(this,{dispatch,requestPairing,reveal,onError,version,maxBodyBytes,maxConcurrent,maxPendingPairs});
    this.requestTimeoutMs=Math.max(25,Math.min(120000,requestTimeoutMs));this.nativeExportTimeoutMs=Math.max(25,Math.min(180000,nativeExportTimeoutMs));this.pairingTimeoutMs=Math.max(25,Math.min(180000,pairingTimeoutMs));
    this.directory=path.join(dataDir,'ide-companion');this.discoveryFile=path.join(this.directory,'discovery.json');
    this.journal=new BridgeJournal(this.directory,{maxClients,maxRecords});
    this.instanceId=randomUUID();this.nonce=randomBytes(32).toString('hex');this.closed=false;this.started=false;
    this.captureStreams=new Map();this.captureNotices=new Map();this.captureVersions=new Map();this.captureWaiters=new Set();this.captureVersion=0;
    this.pairs=new Map();this.running=new Map();this.responses=new Map();this.requestHashes=new Map();this.sockets=new Set();this.timers=new Set();this.responseBytes=0;
    if(Object.values(this.journal.value.clients).some(c=>c.status==='PENDING')||Object.values(this.journal.value.requests).some(r=>r.status==='DISPATCHING'))
      this.journal.commit(v=>{for(const c of Object.values(v.clients))if(c.status==='PENDING')c.status='UNKNOWN';for(const r of Object.values(v.requests))if(r.status==='DISPATCHING')r.status='UNKNOWN';});
    this.server=http.createServer({maxHeaderSize:8192,connectionsCheckingInterval:250},(req,res)=>void this.handle(req,res));this.server.maxConnections=Math.max(4,maxConcurrent+maxPendingPairs+26);
    this.server.maxHeadersCount=32;this.server.maxRequestsPerSocket=1;
    this.server.headersTimeout=5000;this.server.requestTimeout=5000;this.server.keepAliveTimeout=1000;
    this.server.on('connection',socket=>{this.sockets.add(socket);socket.on('close',()=>this.sockets.delete(socket));});
    this.server.on('clientError',(_error,socket)=>{socket.end('HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n');});
    this.server.on('error',error=>this.report(error));
  }
  report(error){try{Promise.resolve(this.onError({code:error?.code||'BRIDGE_IO_FAILED'})).catch(()=>{});}catch{}}
  timer(callback,ms){const t=setTimeout(()=>{this.timers.delete(t);callback();},ms);t.unref?.();this.timers.add(t);return t;}
  untimer(t){clearTimeout(t);this.timers.delete(t);}
  envelope(request_id,status,fields={}){return {protocol:PROTOCOL,instance_id:this.instanceId,request_id:request_id??null,status,...fields};}
  response(res,status,value){if(res.destroyed||res.writableEnded)return;let body=canonical(value);
    if(Buffer.byteLength(body)>2*1024*1024){status=502;body=canonical(this.envelope(value.request_id,'ERROR',{error:{code:'RESULT_TOO_LARGE',message:'回复过大，请在桌面端或按原操作分页查看。'}}));}
    res.writeHead(status,{'content-type':'application/json; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff','connection':'close'});res.end(body);
  }
  start(){if(this.startPromise)return this.startPromise;this.startPromise=this.startServer();return this.startPromise;}
  async startServer(){
    if(this.closed)throw Error('IDE bridge is closed');if(this.started)return this.discovery();
    await new Promise((resolve,reject)=>{this.server.once('error',reject);this.server.listen(0,'127.0.0.1',()=>{this.server.removeListener('error',reject);resolve();});});
    this.started=true;if(this.closed){await new Promise(resolve=>this.server.close(resolve));throw Error('IDE bridge closed during startup');}
    const discovery=this.discovery(),temporary=this.discoveryFile+'.'+randomUUID()+'.next';
    try{privatePath(this.discoveryFile);fs.writeFileSync(temporary,canonical(discovery)+'\n',{flag:'wx',mode:0o600});fs.renameSync(temporary,this.discoveryFile);}
    catch(error){if(fs.existsSync(temporary))fs.unlinkSync(temporary);await this.close();throw error;}
    return discovery;
  }
  discovery(){return {schema:'eeas.ide-discovery.v1',protocol:PROTOCOL,instance_id:this.instanceId,nonce:this.nonce,
    port:this.server.address()?.port??null,pid:process.pid,version:this.version};}
  async readBody(req){
    if(req.headers['content-type']?.split(';')[0].trim()!=='application/json')fail('CONTENT_TYPE_REQUIRED','需要JSON请求。',415);
    const declared=Number(req.headers['content-length']);if(Number.isFinite(declared)&&declared>this.maxBodyBytes)fail('BODY_TOO_LARGE','请求内容超出限制。',413);
    const chunks=[];let size=0;for await(const chunk of req){size+=chunk.length;if(size>this.maxBodyBytes)fail('BODY_TOO_LARGE','请求内容超出限制。',413);chunks.push(chunk);}
    try{return JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{fail('INVALID_JSON','JSON请求无效。');}
  }
  authenticate(req,{allowPending=false,allowMissing=false}={}){
    this.journal.assertCurrent();
    const id=req.headers['x-eeas-client-id'],match=/^Bearer ([a-f0-9]{64})$/.exec(req.headers.authorization??'');
    if(!validId(id)||!match)fail('PAIRING_REQUIRED','请先连接并在桌面端确认。',401);
    const client=this.journal.value.clients[id];if(!client){if(allowMissing)return {id,client:null};fail('PAIRING_NOT_FOUND','未找到该连接，请显式重新连接。',401);}
    const hashed=digest('eeas-ide-ticket\0'+id+'\0'+match[1]);
    if(!timingSafeEqual(Buffer.from(hashed,'hex'),Buffer.from(client.secret_hash,'hex')))fail('CLIENT_SECRET_MISMATCH','此客户端票据不匹配，请在桌面端撤销后重新连接。',403);
    if(client.status==='REVOKED')fail('PAIRING_REVOKED','此连接已在桌面端撤销。',403);
    if(!allowPending&&client.status!=='PAIRED')fail('PAIRING_REQUIRED','桌面端尚未批准此连接。',403);
    return {id,client};
  }
  pairingResult(id){const client=this.journal.value.clients[id];return {client_id:id,pairing_status:client?.status??'NOT_FOUND',
    supported_methods:[...METHODS],
    capabilities:client?.status==='PAIRED'?{methods:client.methods,project_ids:client.project_ids,shared_business_authority:true,
      capability_semantics:'GRANTED_HOST_API_RECHECK_REQUIRED',shared_session_read:client.methods.some(m=>['sessions.list','session.get','session.operation','session.events'].includes(m)),
      task_diff_read:client.methods.includes('session.diff')&&client.methods.includes('session.diff.file'),
      child_diff_read:client.methods.includes('session.child.diff')&&client.methods.includes('session.child.diff.file'),
      native_discussion_read:client.methods.includes('native.conversations.list')&&client.methods.includes('native.conversation.get'),
      native_discussion_send:client.methods.includes('native.message'),
      raw_guidance:client.methods.includes('session.guidance'),desktop_input:false,provider_secrets:false}:null};}
  async pair(body){
    this.journal.assertCurrent();
    if(!exact(body,['protocol','client_id','client_secret','nonce','client_label'])||body.protocol!==PROTOCOL||!validId(body.client_id)||typeof body.client_secret!=='string'||body.client_secret.length!==64||!SHA.test(body.client_secret)||
      body.nonce!==this.nonce||typeof body.client_label!=='string'||!body.client_label.trim()||body.client_label.length>80||/[\x00-\x1f]/.test(body.client_label))fail('INVALID_PAIRING','连接请求或实例挑战无效。');
    const old=this.journal.value.clients[body.client_id],hashed=digest('eeas-ide-ticket\0'+body.client_id+'\0'+body.client_secret);
    if(old){if(old.secret_hash!==hashed)fail('CLIENT_SECRET_MISMATCH','客户端标识已绑定另一票据；需要桌面端显式撤销。',409);
      return this.pairingResult(body.client_id);}
    if(this.pairs.size>=this.maxPendingPairs)fail('PAIRING_BUSY','已有连接等待确认，请稍后核查原请求。',429);
    this.journal.commit(v=>{v.clients[body.client_id]={secret_hash:hashed,status:'PENDING',label:body.client_label,
      methods:[],project_ids:null,at:Date.now()};});
    const controller=new AbortController(),entry={controller,timer:null};this.pairs.set(body.client_id,entry);
    const finish=(status,approval)=>{
      if(this.closed||this.pairs.get(body.client_id)!==entry)return;this.pairs.delete(body.client_id);this.untimer(entry.timer);
      try{this.journal.commit(v=>{const c=v.clients[body.client_id];if(c.status!=='PENDING')return;c.status=status;
        if(status==='PAIRED'){c.methods=approval.methods===undefined?[...METHODS]:approval.methods;c.project_ids=approval.project_ids??null;}});}
      catch(error){this.report(error);}
    };
    entry.timer=this.timer(()=>{controller.abort();finish('UNKNOWN');},this.pairingTimeoutMs);
    Promise.resolve().then(()=>this.requestPairing({client_id:body.client_id,client_label:body.client_label,instance_id:this.instanceId,signal:controller.signal})).then(approval=>{
      if(approval?.approved!==true){finish('DENIED');return;}
      if((approval.methods!==undefined&&(!Array.isArray(approval.methods)||approval.methods.some(m=>!METHODS.includes(m))||new Set(approval.methods).size!==approval.methods.length))||
        !(approval.project_ids===undefined||approval.project_ids===null||Array.isArray(approval.project_ids)&&approval.project_ids.length<=128&&approval.project_ids.every(validId))){finish('DENIED');return;}
      finish('PAIRED',approval);
    },()=>finish('UNKNOWN'));
    return this.pairingResult(body.client_id);
  }
  history(id,requestId){const key=id+':'+requestId;if(this.responses.has(key))return this.responses.get(key);
    if(this.running.has(key))return this.envelope(requestId,'PENDING');
    const row=this.journal.value.requests[key];if(!row)return null;
    return this.envelope(requestId,'UNKNOWN',{result:{request_status:row.status,response_available:false,method:row.method,scope:row.scope,
      operation_id:row.operation_id,observed_status:row.observed_status,recovery:'INSPECT_ORIGINAL_OPERATION'}});
  }
  remember(key,value){
    const bytes=Buffer.byteLength(canonical(value));
    if(bytes>2*1024*1024)value=this.envelope(value.request_id,'ERROR',{error:{code:'RESULT_TOO_LARGE',message:'回复过大，请按原操作分页查看；不要重复执行。'}});
    if(this.responses.has(key))this.responseBytes-=Buffer.byteLength(canonical(this.responses.get(key)));
    this.responses.set(key,value);this.responseBytes+=Buffer.byteLength(canonical(value));
    while(this.responses.size>256||this.responseBytes>8*1024*1024){const id=[...this.responses.keys()].find(k=>!this.running.has(k));if(id===undefined)break;
      this.responseBytes-=Buffer.byteLength(canonical(this.responses.get(id)));this.responses.delete(id);this.requestHashes.delete(id);}
    return value;
  }
  captureStream(id,client,window,res){
    if(!client.methods.includes('session.editor.capture.listen')||!client.methods.includes('session.editor.observer.command'))fail('CAPABILITY_DENIED','此连接未获准接收编辑器状态采集请求。',403);
    const key=id+':'+window;if(this.captureStreams.has(key))fail('EDITOR_CAPTURE_CHANNEL_BUSY','原窗口已有状态采集连接。',409);
    if(this.captureStreams.size>=16)fail('EDITOR_CAPTURE_CAPACITY','状态采集连接已满，原任务保留。',429);
    res.writeHead(200,{'content-type':'text/event-stream; charset=utf-8','cache-control':'no-store','x-content-type-options':'nosniff','x-eeas-instance-id':this.instanceId,'connection':'keep-alive'});res.flushHeaders();
    const entry={client_id:id,window,res,blocked:false};this.captureStreams.set(key,entry);
    for(const notice of this.captureNotices.values())if(notice.key===key)notice.delivered=false;
    res.on('close',()=>{if(this.captureStreams.get(key)===entry)this.captureStreams.delete(key);});
    res.on('drain',()=>{entry.blocked=false;this.flushCaptures(key);});this.flushCaptures(key);
  }
  flushCaptures(key){const stream=this.captureStreams.get(key);if(!stream||stream.blocked||stream.res.destroyed||this.closed)return;
    const client=this.journal.value.clients[stream.client_id];if(client?.status!=='PAIRED'){stream.res.end();return;}
    for(const [id,notice]of this.captureNotices){
      if(notice.expires_at_ms<Date.now()){this.captureNotices.delete(id);continue;}
      if(notice.key!==key||notice.delivered)continue;
      if(!client.methods.includes('session.editor.capture.listen')||!client.methods.includes('session.editor.observer.command')||client.project_ids!==null&&!client.project_ids.includes(notice.project_id))continue;
      const value={type:'editor-capture',instance_id:this.instanceId,window_id:stream.window,observer_id:notice.observer_id,capture_ref:notice.capture_ref,expires_at_ms:notice.expires_at_ms};
      const bytes='event: editor-capture\ndata: '+canonical(value)+'\n\n';notice.delivered=true;
      if(!stream.res.write(bytes)){stream.blocked=true;break;}
    }
  }
  notifyEditorCaptures(rows){
    if(this.closed)return {queued:0};if(!Array.isArray(rows)||rows.length>16)fail('EDITOR_CAPTURE_INVALID','原窗口采集目标不完整。');
    const current=Date.now();for(const [key,value]of this.captureNotices)if(value.expires_at_ms<current)this.captureNotices.delete(key);
    let queued=0;const changed=new Set();
    for(const row of rows){if(!exact(row,['observer_id','editor_client_id','editor_window_id','project_id','capture_ref','expires_at_ms'])||!SHA.test(row.observer_id??'')||!SHA.test(row.capture_ref??'')||!validId(row.editor_client_id)||!validId(row.editor_window_id)||!validId(row.project_id)||!Number.isSafeInteger(row.expires_at_ms)||row.expires_at_ms>current+15000)fail('EDITOR_CAPTURE_INVALID','原窗口采集范围不完整。');
      if(row.expires_at_ms<current)continue;const client=this.journal.value.clients[row.editor_client_id];
      if(client?.status!=='PAIRED'||!client.methods.includes('session.editor.capture.listen')||!client.methods.includes('session.editor.observer.command')||client.project_ids!==null&&!client.project_ids.includes(row.project_id))continue;
      const key=row.editor_client_id+':'+row.editor_window_id,id=key+':'+row.capture_ref+':'+row.observer_id;
      if(!this.captureNotices.has(id)){if(this.captureNotices.size>=128)fail('EDITOR_CAPTURE_CAPACITY','窗口采集通知容量已满，原任务保留。',429);this.captureNotices.set(id,{...row,key,delivered:false});}
      changed.add(key);queued++;
    }for(const key of changed)this.flushCaptures(key);return {queued};
  }
  captureCursor(reference){return this.captureVersions.get(reference)?.version||0;}
  recordEditorCapture(value){if(this.closed||value?.capture_ref!=null&&!SHA.test(value.capture_ref)||!SHA.test(value?.observer_id??'')||!Number.isSafeInteger(value.revision))return;
    const references=new Set();if(value.capture_ref)references.add(value.capture_ref);const windows=new Set();for(const notice of this.captureNotices.values())if(notice.observer_id===value.observer_id&&notice.expires_at_ms>=Date.now()){references.add(notice.capture_ref);notice.delivered=false;windows.add(notice.key);}
    for(const key of windows)this.flushCaptures(key);for(const reference of references)this.signalEditorCapture(reference,value);
  }
  signalEditorCapture(reference,value){
    const current=Date.now();for(const [key,v]of this.captureVersions)if(v.expires<current)this.captureVersions.delete(key);
    let entry=this.captureVersions.get(reference);if(!entry){if(this.captureVersions.size>=128)return;entry={version:0,expires:current+15000,seen:new Set()};this.captureVersions.set(reference,entry);}
    const key=value.observer_id+':'+value.revision;if(entry.seen.has(key))return;if(entry.seen.size>=32)return;entry.seen.add(key);entry.version=++this.captureVersion;
    for(const waiter of [...this.captureWaiters])if(waiter.reference===reference)waiter.finish(true);
  }
  waitEditorCapture(reference,{after=0,ms=8000,signal}={}){
    if(this.closed||signal?.aborted)return Promise.resolve(false);if(this.captureCursor(reference)>after)return Promise.resolve(true);
    if(this.captureWaiters.size>=8)fail('EDITOR_CAPTURE_CAPACITY','窗口状态核对等待已满，原任务保留。',429);
    return new Promise(resolve=>{const waiter={reference,finish:null};let timer;
      const finish=changed=>{if(!this.captureWaiters.delete(waiter))return;this.untimer(timer);signal?.removeEventListener('abort',abort);resolve(changed);};
      const abort=()=>finish(false);waiter.finish=finish;this.captureWaiters.add(waiter);timer=this.timer(()=>finish(false),Math.max(1,Math.min(8000,ms)));signal?.addEventListener('abort',abort,{once:true});if(signal?.aborted)finish(false);
    });
  }
  async rpc(id,client,body){
    if(!exact(body,['protocol','request_id','method','scope','payload'])||body.protocol!==PROTOCOL||!validId(body.request_id)||typeof body.method!=='string'||!object(body.payload??{}))fail('INVALID_RPC','请求字段无效。');
    if(!METHODS.includes(body.method))fail('NOT_SUPPORTED','当前桌面版本不支持此操作。',404);
    if(body.method==='session.editor.capture.listen')fail('STREAM_TRANSPORT_REQUIRED','采集通知使用原配对流连接。');
    if(!client.methods.includes(body.method))fail('CAPABILITY_DENIED','此连接未获准执行该操作。',403);
    const scope=validateScope(body.scope),payload=body.payload??{},key=id+':'+body.request_id,hash=digest({...body,scope,payload});
    if(body.method.startsWith('conversation.')){const directory=['conversation.list','conversation.create','conversation.creation.operation'].includes(body.method);if(scope.session_ref!==undefined||scope.generation!==undefined||scope.conversation_id!==undefined||!Object.hasOwn(scope,'project_id')||(directory?scope.conversation_ref!==undefined:scope.conversation_ref===undefined))fail('INVALID_SCOPE','请选择准确的逻辑会话。');}
    else if(scope.conversation_ref!==undefined)fail('INVALID_SCOPE','此方法不能借用逻辑会话身份。');
    if(body.method.startsWith('native.')){
      if(!scope.project_id||scope.session_ref!==undefined||scope.generation!==undefined||
          (body.method==='native.conversations.list'?scope.conversation_id!==undefined:!scope.conversation_id))fail('INVALID_SCOPE','请选择准确的桌面讨论。');
    }else if(scope.conversation_id!==undefined)fail('INVALID_SCOPE','原会话方法不能借用桌面讨论身份。');
    if(client.project_ids!==null&&((scope.project_id&&!client.project_ids.includes(scope.project_id))||!scope.project_id&&!['projects.list','status','lifecycle.reveal'].includes(body.method)))fail('SCOPE_DENIED','此连接未获准访问该工程。',403);
    const previous=this.running.get(key)||this.journal.value.requests[key];if(previous){if(previous.request_sha256!==hash)fail('IDEMPOTENCY_CONFLICT','原请求标识已绑定不同内容。',409);
      return this.history(id,body.request_id)??this.envelope(body.request_id,'PENDING');}
    if(this.responses.has(key)){if(this.requestHashes.get(key)!==hash)fail('IDEMPOTENCY_CONFLICT','原请求标识已绑定不同内容。',409);return this.responses.get(key);}
    const captureWait=body.method==='session.editor.capture.wait',waiting=[...this.running.values()].filter(v=>v.method==='session.editor.capture.wait').length;
    if(captureWait?waiting>=8:this.running.size-waiting>=this.maxConcurrent)fail('BRIDGE_BUSY','已有请求处理中，请核查原请求。',429);
    if(body.method==='lifecycle.reveal'&&typeof this.reveal!=='function'||body.method!=='lifecycle.reveal'&&typeof this.dispatch!=='function')fail('NOT_SUPPORTED','当前桌面版本未接入此操作。',404);
    const row={client_id:id,request_id:body.request_id,request_sha256:hash,method:body.method,scope,
      operation_id:validId(payload.operation_id)?payload.operation_id:['session.work.deliveries.export','session.work.maintenance.change'].includes(body.method)&&validId(payload.request?.operation_id)?payload.request.operation_id:['session.work.deliveries.export.operation','session.work.deliveries.export.cancel','session.work.deliveries.export.reconcile'].includes(body.method)&&validId(payload.identity?.operation_id)?payload.identity.operation_id:['conversation.configure','native.context.recovery.command','conversation.native.recovery.command'].includes(body.method)&&validId(payload.request?.operation_id)?payload.request.operation_id:null,status:'DISPATCHING',observed_status:null,at:Date.now()};
    const durable=!READ_ONLY.has(body.method)&&!API_RECEIPT_MUTATIONS.has(body.method);if(durable)this.journal.commit(v=>{v.requests[key]=row;});
    const controller=new AbortController(),entry={...row,controller,timer:null};this.running.set(key,entry);this.requestHashes.set(key,hash);
    let resolve;const observed=new Promise(done=>{resolve=done;});
    const publish=(status,fields,recordStatus)=>{
      if(this.closed||this.running.get(key)!==entry)return;
      let value;
      try{value=clone(this.envelope(body.request_id,status,fields));}
      catch{value=this.envelope(body.request_id,'ERROR',{error:{code:'RESPONSE_UNAVAILABLE',message:'回复暂不可读取，请核查原操作；不要重复执行。'}});}
      if(recordStatus){this.running.delete(key);this.untimer(entry.timer);
        if(durable)try{this.journal.commit(v=>{v.requests[key].status=recordStatus;
          v.requests[key].observed_status=typeof fields.result?.status==='string'&&/^[A-Z][A-Z0-9_]{0,63}$/.test(fields.result.status)?fields.result.status:null;});}
          catch(error){value.local_persistence_status='FAILED';this.report(error);}}
      if(this.journal.value.clients[id]?.status==='REVOKED')value=this.envelope(body.request_id,'ERROR',{error:{code:'PAIRING_REVOKED',message:'连接已撤销，原任务未被取消。'}});
      resolve(this.remember(key,value));
    };
    entry.timer=this.timer(()=>{controller.abort();if(durable)try{this.journal.commit(v=>{v.requests[key].status='UNKNOWN';});}catch(error){this.report(error);}
      publish('UNKNOWN',{result:{request_status:'UNKNOWN',response_available:false,method:body.method,scope,operation_id:row.operation_id,recovery:'INSPECT_ORIGINAL_OPERATION'}});},body.method==='session.work.deliveries.export'?this.nativeExportTimeoutMs:this.requestTimeoutMs);
    Promise.resolve().then(()=>{
      if(this.closed||this.journal.value.clients[id]?.status!=='PAIRED')fail('PAIRING_REVOKED','连接已撤销，原任务未被取消。',403);
      return body.method==='lifecycle.reveal'?this.reveal({client_id:id,signal:controller.signal}):
        this.dispatch(body.method,{scope,payload},{client_id:id,request_id:body.request_id,signal:controller.signal,
          grants:{methods:[...client.methods],project_ids:client.project_ids===null?null:[...client.project_ids]},currentGrants:()=>{this.journal.assertCurrent();const live=this.journal.value.clients[id];if(this.closed||live?.status!=='PAIRED')fail('PAIRING_REVOKED','连接已撤销，原任务未被取消。',403);if(!live.methods.includes(body.method))fail('CAPABILITY_DENIED','此连接未获准执行该操作。',403);return {methods:[...live.methods],project_ids:live.project_ids===null?null:[...live.project_ids]};}});
    }).then(result=>{
      if(body.method==='projects.list'&&client.project_ids!==null&&Array.isArray(result?.projects))result={...result,projects:result.projects.filter(p=>client.project_ids.includes(p.id))};
      publish('OK',{result:result??null},'RETURNED');
    },error=>{const known=error?.public===true&&/^[A-Z][A-Z0-9_]{0,63}$/.test(error.code??'');
      const uncertain=body.method==='session.work.maintenance.change'&&error?.code==='WORK_MAINTENANCE_UNKNOWN'||body.method.startsWith('native.')&&['OPERATION_OUTCOME_UNKNOWN','OPERATION_INPUT_MISMATCH'].includes(error?.code)||body.method.startsWith('session.work.deliveries.export')&&error?.code==='NATIVE_EXPORT_UNKNOWN';
      publish('ERROR',{error:{code:known?error.code:'DISPATCH_UNCONFIRMED',message:known&&typeof error.message==='string'?error.message.slice(0,300):'请求未能确认，请核查原操作；不要重复发送。'}},known&&!uncertain?'FAILED':'UNKNOWN');}).catch(()=>publish('ERROR',{error:{code:'RESPONSE_UNAVAILABLE',message:'回复暂不可读取，请核查原操作；不要重复执行。'}},'UNKNOWN'));
    return observed;
  }
  async handle(req,res){let requestId=null;
    try{
      if(this.closed)fail('BRIDGE_CLOSED','桌面连接正在关闭。',503);
      if(req.headers.host!=='127.0.0.1:'+this.server.address().port||req.headers.origin!==undefined||req.headers['sec-fetch-site']!==undefined)fail('ORIGIN_DENIED','仅允许已配对的本机扩展连接。',403);
      const capture=req.method==='GET'?/^\/v1\/editor-capture-events\?window_id=([A-Za-z0-9_-]{1,100})$/.exec(req.url):null;
      if(req.url.includes('#')||req.url.includes('?')&&!capture)fail('PATH_DENIED','连接路径无效。',404);
      if(req.method==='POST'&&req.url==='/v1/pair'){const result=await this.pair(await this.readBody(req));this.response(res,result.pairing_status==='PENDING'?202:200,this.envelope(null,result.pairing_status==='PENDING'?'PENDING':'OK',{result}));return;}
      if(req.method==='GET'&&req.url==='/v1/pair/status'){const {id}=this.authenticate(req,{allowPending:true,allowMissing:true});this.response(res,200,this.envelope(null,'OK',{result:this.pairingResult(id)}));return;}
      const auth=this.authenticate(req);
      if(req.method==='GET'&&capture){this.captureStream(auth.id,auth.client,capture[1],res);return;}
      const match=/^\/v1\/rpc\/([A-Za-z0-9_-]{1,100})$/.exec(req.url);
      if(req.method==='GET'&&match){requestId=match[1];const value=this.history(auth.id,requestId)||this.running.has(auth.id+':'+requestId)&&this.envelope(requestId,'PENDING');
        if(!value)fail('REQUEST_NOT_FOUND','尚未记录此请求；请核查原操作，不要自动重发。',404);this.response(res,200,value);return;}
      if(req.method==='POST'&&req.url==='/v1/rpc'){const body=await this.readBody(req);requestId=validId(body?.request_id)?body.request_id:null;const disconnect=()=>{if(['session.editor.capture.wait','session.work.deliveries.export'].includes(body.method))this.running.get(auth.id+':'+requestId)?.controller.abort();};res.once('close',disconnect);try{this.response(res,200,await this.rpc(auth.id,auth.client,body));}finally{res.removeListener('close',disconnect);}return;}
      fail('NOT_SUPPORTED','此连接路径或方法不受支持。',404);
    }catch(error){const known=error?.public===true;this.response(res,known?error.status??400:503,this.envelope(requestId,'ERROR',{error:{code:known?error.code:'BRIDGE_UNAVAILABLE',message:known?error.message:'本机连接记录暂不可用，原任务未取消。'}}));if(!known)this.report(error);}
  }
  revoke(clientId){
    if(this.closed)throw Error('IDE bridge is closed');if(!validId(clientId)||!this.journal.value.clients[clientId])return false;
    this.journal.commit(v=>{v.clients[clientId].status='REVOKED';});const pair=this.pairs.get(clientId);
    if(pair){pair.controller.abort();this.untimer(pair.timer);this.pairs.delete(clientId);}
    for(const stream of this.captureStreams.values())if(stream.client_id===clientId)stream.res.end();
    for(const [key,value]of this.captureNotices)if(value.editor_client_id===clientId)this.captureNotices.delete(key);return true;
  }
  async close(){
    if(this.closePromise)return this.closePromise;this.closed=true;
    for(const entry of [...this.pairs.values(),...this.running.values()])entry.controller.abort();
    for(const timer of this.timers)clearTimeout(timer);this.timers.clear();this.pairs.clear();
    try{if(Object.values(this.journal.value.clients).some(c=>c.status==='PENDING')||Object.values(this.journal.value.requests).some(r=>r.status==='DISPATCHING'))this.journal.commit(v=>{
      for(const c of Object.values(v.clients))if(c.status==='PENDING')c.status='UNKNOWN';for(const r of Object.values(v.requests))if(r.status==='DISPATCHING')r.status='UNKNOWN';});}catch(error){this.report(error);}
    for(const stream of this.captureStreams.values())stream.res.end();this.captureStreams.clear();this.captureNotices.clear();
    for(const waiter of [...this.captureWaiters])waiter.finish(false);this.captureVersions.clear();
    this.running.clear();
    try{privatePath(this.discoveryFile);if(fs.existsSync(this.discoveryFile)&&JSON.parse(fs.readFileSync(this.discoveryFile)).instance_id===this.instanceId)fs.unlinkSync(this.discoveryFile);}catch(error){this.report(error);}
    this.closePromise=new Promise(resolve=>{if(!this.started)resolve({status:'CLOSED',external_tasks_cancelled:false});
      else this.server.close(()=>resolve({status:'CLOSED',external_tasks_cancelled:false}));
      for(const socket of this.sockets)socket.destroy();});return this.closePromise;
  }
}
module.exports={IdeCompanionBridge,PROTOCOL,METHODS,validateScope};

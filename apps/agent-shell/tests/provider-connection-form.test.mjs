import * as authored from '../src/authored-ui-copy.ts';
import * as language from './fixture-ui-language.mjs';
import assert from 'node:assert/strict';
import { readFile, writeFile, mkdtemp, rm } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { spawn } from 'node:child_process';
import readline from 'node:readline';
import test from 'node:test';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import { renderToString } from 'solid-js/web';
import * as solid from '../node_modules/solid-js/dist/solid.js';
import { ApiClient, ApiError } from '../src/api.ts';
import * as connection from '../src/provider-connection.ts';
import { ProviderConnectionClient, PROVIDER_CONNECTION_HISTORY_KEY, PROVIDER_CA_MAX_BYTES, PROVIDER_REASONING_EFFORTS, newProviderHeader, providerCAContent, providerConnectionBody, providerConnectionDraft, providerHeaderIsSensitive, providerTransportNeedsReview, providerReasoningEfforts, readProviderCA, readProviderSaveHistory } from '../src/provider-connection.ts';

const app=resolve(dirname(fileURLToPath(import.meta.url)),'..'),root=resolve(app,'../..');
const storage=()=>{const values=new Map();return{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),values};};
const draft=()=>({...providerConnectionDraft(),id:'intranet',url:'http://127.0.0.1:1/v1',model:'company-model',auth:'NONE'});
const profile=(body,extra={})=>({profile_ref:{id:body.id,revision:body.expected_revision+1,sha256:'a'.repeat(64)},provider_url:body.provider_url,model_id:body.model_id,auth_kind:body.auth_mode,credential_present:body.auth_mode==='API_KEY',supports_images:body.supports_images,model_access:'NOT_VALIDATED',reasoning_efforts:body.reasoning_efforts??[],reasoning_qualification:body.reasoning_efforts?.length?'DECLARED_NOT_OBSERVED':'NOT_DECLARED',...extra});

test('reasoning declarations are opt-in, strict, canonical and included in immutable local history and unknown receipt comparisons',async()=>{
 assert.deepEqual(providerConnectionDraft().reasoningEfforts,[]);assert.deepEqual(providerReasoningEfforts(),[]);
 const d={...draft(),reasoningEfforts:['high','low']},body=providerConnectionBody(d,'reasoning-unknown');assert.deepEqual(body.reasoning_efforts,['low','high']);
 for(const invalid of [null,'low',true,{},['max'],['LOW'],['low','low'],[1],[...PROVIDER_REASONING_EFFORTS,'low']])assert.throws(()=>providerConnectionBody({...draft(),reasoningEfforts:invalid}));
 const local=storage();let posts=0;const actual=profile(body),client=new ProviderConnectionClient({save:async()=>{posts++;throw Error('lost response');},inspect:async()=>({operation_id:body.operation_id,status:'COMPLETED',result:actual})},local);
 assert.equal((await client.save(body)).status,'COMPLETED');assert.equal(posts,1);assert.deepEqual(readProviderSaveHistory(local)[0].reasoningEfforts,['low','high']);
 await assert.rejects(client.save({...body,reasoning_efforts:['medium']}),/另一配置/);assert.equal(posts,1);
 for(const wrong of [{reasoning_efforts:['medium']},{reasoning_efforts:undefined,reasoning_qualification:undefined},{reasoning_efforts:['low','high'],reasoning_qualification:'OBSERVED'},{reasoning_efforts:['low','low']}]){
  const reader=new ProviderConnectionClient({save:()=>assert.fail('GET only'),inspect:async()=>({operation_id:body.operation_id,status:'COMPLETED',result:{...actual,...wrong}})},local);
  assert.equal((await reader.restore())[0].status,'INSPECTION_UNAVAILABLE');
 }
 const old=storage(),legacyBody=providerConnectionBody(draft(),'legacy-history'),legacy={operationId:legacyBody.operation_id,profileId:legacyBody.id,expectedRevision:0,providerUrl:legacyBody.provider_url,modelId:legacyBody.model_id,authMode:legacyBody.auth_mode,supportsImages:false};old.setItem(PROVIDER_CONNECTION_HISTORY_KEY,JSON.stringify([legacy]));
 assert.deepEqual(readProviderSaveHistory(old)[0].reasoningEfforts,[]);
 const oldResult=profile(legacyBody);delete oldResult.reasoning_efforts;delete oldResult.reasoning_qualification;
 assert.equal((await new ProviderConnectionClient({save:()=>assert.fail('legacy GET only'),inspect:async()=>({operation_id:legacyBody.operation_id,status:'COMPLETED',result:oldResult})},old).restore())[0].status,'COMPLETED');
});

test('explicit NONE omits api_key; API_KEY needs new credentials, and a complete reset removes previous advanced material',()=>{
 const value=draft(),none=providerConnectionBody(value,'none');assert.equal(none.auth_mode,'NONE');assert.ok(!Object.hasOwn(none,'api_key'));assert.deepEqual(none.transport,{timeout_seconds:60,custom_headers:[]});assert.throws(()=>providerConnectionBody({...value,key:'must-not-retain'}),/不能保留/);assert.throws(()=>providerConnectionBody({...value,auth:'API_KEY'}),/本次保存/);const keyed=providerConnectionBody({...value,auth:'API_KEY',key:'fixture-explicit-key'});assert.equal(keyed.api_key,'fixture-explicit-key');
 assert.throws(()=>providerConnectionBody({...value,transportDecision:'UNDECIDED'}),/不会自动沿用/);const reset=providerConnectionBody({...value,transportDecision:'RESET',caMode:'CUSTOM',caPem:'invalid ignored after explicit reset',proxyMode:'EXPLICIT',proxyUrl:'invalid',headers:[newProviderHeader('X-Auth-Token')]});assert.deepEqual(reset.transport,{timeout_seconds:60,custom_headers:[]});
});

test('timeout, endpoint and proxy controls reject ambiguous, credential-bearing and boolean inputs before saving',()=>{
 for(const timeout of [0,0.09,120.01,NaN,Infinity,true,'60'])assert.throws(()=>providerConnectionBody({...draft(),timeout}));for(const timeout of [0.1,60,120])assert.equal(providerConnectionBody({...draft(),timeout}).transport.timeout_seconds,timeout);
 for(const url of ['https://user:password@example.test/v1','https://example.test/v1?key=x','https://example.test/v2','file:///v1'])assert.throws(()=>providerConnectionBody({...draft(),url}));for(const proxyUrl of ['http://user:password@proxy.test:8080','http://proxy.test/path','http://proxy.test/?key=x','socks5://proxy.test','http://代理.example'])assert.throws(()=>providerConnectionBody({...draft(),proxyMode:'EXPLICIT',proxyUrl}));
 assert.throws(()=>providerConnectionBody({...draft(),proxyMode:'EXPLICIT',proxyUrl:'http://proxy.test:8080',proxyAuth:true,proxyUsername:'user',proxyPassword:''}));const body=providerConnectionBody({...draft(),proxyMode:'EXPLICIT',proxyUrl:'http://proxy.test:8080/',proxyAuth:true,proxyUsername:'fixture-user',proxyPassword:'fixture-password'});assert.deepEqual(body.transport.proxy,{url:'http://proxy.test:8080',username:'fixture-user',password:'fixture-password'});
});

test('request headers distinguish private values, forbid transport-owned names and detect authorization collisions and duplicates',()=>{
 for(const name of ['Authorization','Cookie','X-Token','X-Api-Key','X-Private-Credential']){assert.equal(providerHeaderIsSensitive(name),true);assert.throws(()=>providerConnectionBody({...draft(),headers:[{...newProviderHeader(name,false),value:'must-be-private'}]}),/私密值/);}
 for(const name of ['Host','Connection','Origin','ContentLength','Sec-Fetch-Site','Security-Context','Content-Type'])assert.throws(()=>providerConnectionBody({...draft(),headers:[{...newProviderHeader(name),value:'fixture'}]}),/不能自定义/);
 assert.throws(()=>providerConnectionBody({...draft(),headers:[{...newProviderHeader('X-Organization',false),value:'one'},{...newProviderHeader('x-organization',false),value:'two'}]}),/重复/);assert.throws(()=>providerConnectionBody({...draft(),auth:'API_KEY',key:'fixture',headers:[{...newProviderHeader('Authorization'),value:'Bearer fixture'}]}),/Authorization/);
 const body=providerConnectionBody({...draft(),headers:[{...newProviderHeader('X-Organization',false),value:'fixture-public'},{...newProviderHeader('X-Auth-Token'),value:'fixture-private'}]});assert.deepEqual(body.transport.custom_headers,[{name:'x-organization',value:'fixture-public'},{name:'x-auth-token',secret_value:'fixture-private'}]);
});

test('chosen certificate bytes are bounded and read locally; unsupported binary/private-key content is rejected without a path or upload primitive',async()=>{
 const pem='-----BEGIN CERTIFICATE-----\nQUFBQQ==\n-----END CERTIFICATE-----\n',bytes=new TextEncoder().encode(pem);let reads=0;const actual=await readProviderCA({size:bytes.length,name:'本机证书.pem',arrayBuffer:async()=>{reads++;return bytes.buffer;}});assert.deepEqual(actual,{name:'本机证书.pem',pem});assert.equal(reads,1);await assert.rejects(readProviderCA({size:PROVIDER_CA_MAX_BYTES+1,name:'large.pem',arrayBuffer:()=>assert.fail('oversized bytes must not be read')}));assert.throws(()=>providerCAContent(new Uint8Array([0xff,0xfe])));assert.throws(()=>providerCAContent(new TextEncoder().encode('-----BEGIN '+ 'PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----')));assert.throws(()=>providerCAContent(new Uint8Array(PROVIDER_CA_MAX_BYTES+1)));const padded=new TextEncoder().encode(pem+' '.repeat(PROVIDER_CA_MAX_BYTES-bytes.length));assert.equal(providerCAContent(padded).length,PROVIDER_CA_MAX_BYTES);
});

test('existing redacted transport prefill keeps public metadata, never refills secret values, and requires an explicit replacement decision',()=>{
 const body=providerConnectionBody(draft()),saved=profile(body,{transport:{timeout_seconds:120,ca:{kind:'PEM_BYTES',sha256:'b'.repeat(64)},proxy:{url:'http://proxy.test:8080',auth_configured:true},custom_headers:[{name:'x-organization',private:false},{name:'x-auth-token',private:true}]}}),value=providerConnectionDraft(saved);assert.equal(providerTransportNeedsReview(saved),true);assert.equal(value.url,body.provider_url);assert.equal(value.timeout,120);assert.equal(value.proxyUrl,'http://proxy.test:8080');assert.equal(value.transportDecision,'UNDECIDED');for(const field of ['key','caPem','proxyUsername','proxyPassword'])assert.equal(value[field],'');assert.ok(value.headers.every(header=>header.value===''));assert.equal(value.headers[1].mode,'SECRET');assert.throws(()=>providerConnectionBody(value),/不会自动沿用/);assert.throws(()=>providerConnectionBody({...value,transportDecision:'REPLACE'}));
});

test('metadata persists before single dispatch; timeout, missing records and restart recover only by original operation GET',async()=>{
 const local=storage(),body=providerConnectionBody({...draft(),auth:'API_KEY',key:'fixture-never-persist-key'},'lost-save'),calls=[],adapter={save:async value=>{calls.push('POST');assert.equal(readProviderSaveHistory(local)[0].operationId,value.operation_id);throw new Error('connection lost');},inspect:async id=>{calls.push('GET:'+id);throw new ApiError(404,'NO_RECORD','no recorded outcome');}},client=new ProviderConnectionClient(adapter,local),unknown=await client.save(body);assert.equal(unknown.status,'NO_RECORD');assert.match(unknown.message,/没有自动重复保存/);await client.save({...body,api_key:'another-private-value'});await new ProviderConnectionClient(adapter,local).restore();assert.equal(calls.filter(call=>call==='POST').length,1);assert.ok(calls.filter(call=>call.startsWith('GET:')).every(call=>call==='GET:lost-save'));const serialized=local.getItem(PROVIDER_CONNECTION_HISTORY_KEY);for(const value of ['fixture-never-persist-key','another-private-value','api_key','ca_pem','proxyPassword','secret_value'])assert.ok(!serialized.includes(value));
 const blocked={getItem:()=>null,setItem:()=>{throw new Error('storage blocked');}};await assert.rejects(new ProviderConnectionClient(adapter,blocked).save({...body,operation_id:'blocked'}),/storage blocked/);assert.equal(calls.filter(call=>call==='POST').length,1);
});

test('a returned foreign version or incomplete outcome never becomes saved; delayed successful acknowledgement is resolved without a second POST',async()=>{
 const local=storage(),body=providerConnectionBody(draft(),'recover-success');let saves=0;const expected=profile(body),client=new ProviderConnectionClient({save:async()=>{saves++;throw new Error('acknowledgement lost');},inspect:async id=>({operation_id:id,status:'COMPLETED',result:expected,request_sha256:'f'.repeat(64)})},local);const actual=await client.save(body);assert.equal(actual.status,'COMPLETED');assert.deepEqual(actual.profile,expected);assert.equal(saves,1);
 for(const receipt of [{operation_id:'other',status:'COMPLETED',result:expected},{operation_id:body.operation_id,status:'COMPLETED',result:{...expected,profile_ref:{...expected.profile_ref,revision:20}}},{operation_id:body.operation_id,status:'COMPLETED',result:{...expected,model_access:'VALIDATED'}}])assert.equal((await new ProviderConnectionClient({save:()=>assert.fail('not replayed'),inspect:async()=>receipt},local).restore())[0].status,'INSPECTION_UNAVAILABLE');
 const operation=readProviderSaveHistory(local)[0];for(const status of ['INCOMPLETE','FAILED_OR_PARTIAL']){const view=await new ProviderConnectionClient({save:()=>assert.fail('not replayed'),inspect:async()=>({operation_id:body.operation_id,status,result:null,failure:{phase:'PROFILE_COMMIT',code:'FIXTURE'}})},local).inspect(operation);assert.equal(view.status,status);assert.ok(!view.profile);}
});

// Actual app routes, SQLite profile store, immutable journal and original CLI
// archive/hash installation. No gateway listener, request, model or GUI is used.
const apiScript=String.raw`
import json,os,sys,tempfile
from pathlib import Path
from datetime import datetime,timedelta,timezone
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from apps.api.config import Settings
from apps.api.main import create_app
private=rsa.generate_private_key(public_exponent=65537,key_size=2048)
name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'EEAS frontend certificate format fixture')])
now=datetime.now(timezone.utc)
cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(private,hashes.SHA256())
pem=cert.public_bytes(serialization.Encoding.PEM).decode('ascii')
with tempfile.TemporaryDirectory(prefix='eeas-provider-form-api-') as folder:
 settings=Settings(Path(folder));client=None
 def open_client():
  value=TestClient(create_app(settings),base_url=settings.origin)
  token=value.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'}).json()['csrf_token']
  value.headers.update({'Origin':settings.origin,'X-EEAS-CSRF':token})
  return value
 client=open_client()
 try:
  for line in sys.stdin:
   try:
    cmd=json.loads(line)
    if cmd.get('fixture')=='certificate':result={'status':200,'body':{'pem':pem}}
    elif cmd.get('fixture')=='restart':client.close();client=open_client();result={'status':200,'body':{'restarted':True}}
    else:
     response=client.request(cmd.get('method','GET'),cmd['path'],json=cmd.get('body'),headers=cmd.get('headers') or {})
     result={'status':response.status_code,'body':response.json()}
    print(json.dumps(result,ensure_ascii=False),flush=True)
   except Exception as error:print(json.dumps({'fixture_error':type(error).__name__}),flush=True)
 finally:client.close()
`;
function fixture(t){const env=Object.fromEntries(Object.entries(process.env).filter(([name])=>!name.startsWith('EEAS_')));env.PYTHONDONTWRITEBYTECODE='1';const child=spawn(resolve(root,'.venv/bin/python'),['-u','-c',apiScript],{cwd:root,env,stdio:['pipe','pipe','pipe']}),waiting=[],calls=[];let stderr='';child.stderr.on('data',value=>stderr=(stderr+value).slice(-6000));const lines=readline.createInterface({input:child.stdout});lines.on('line',line=>{const next=waiting.shift();if(next)try{next.resolve(JSON.parse(line));}catch(error){next.reject(error);}});child.on('error',error=>waiting.splice(0).forEach(item=>item.reject(error)));child.on('exit',code=>waiting.splice(0).forEach(item=>item.reject(new Error(`provider fixture exit ${code}: ${stderr}`))));t.after(async()=>{child.stdin.end();await new Promise(resolve=>child.exitCode!==null?resolve():child.once('exit',resolve));lines.close();});const raw=body=>new Promise((resolve,reject)=>{waiting.push({resolve,reject});child.stdin.write(JSON.stringify(body)+'\n');});const api=new ApiClient(async(path,options)=>{const method=options?.method??'GET';calls.push({path,method});const value=await raw({method,path,headers:options?.headers??{},body:options?.body?JSON.parse(options.body):undefined});assert.ok(!value.fixture_error,JSON.stringify(value));return new Response(JSON.stringify(value.body),{status:value.status});});const adapter={save:body=>api.request('/api/planning/providers','POST',body,undefined,false),inspect:id=>api.request('/api/planning/providers/operations/'+encodeURIComponent(id))};return{raw,api,calls,adapter};}

test('actual API saves private CA/proxy/header material locally, returns redacted NOT_VALIDATED profile, and reads the same immutable receipt after restart',async t=>{
 const f=fixture(t),certificate=await f.raw({fixture:'certificate'});assert.equal(certificate.status,200);const value={...draft(),reasoningEfforts:['medium','high'],caMode:'CUSTOM',caPem:certificate.body.pem,timeout:120,proxyMode:'EXPLICIT',proxyUrl:'http://127.0.0.1:9',proxyAuth:true,proxyUsername:'fixture-private-user',proxyPassword:'fixture-private-password',headers:[{...newProviderHeader('X-Organization',false),value:'fixture-public-org'},{...newProviderHeader('X-Auth-Token'),value:'fixture-private-header'}]},body=providerConnectionBody(value,'actual-api-save'),local=storage(),client=new ProviderConnectionClient(f.adapter,local),actual=await client.save(body);assert.equal(actual.status,'COMPLETED',JSON.stringify(actual));assert.equal(actual.profile.model_access,'NOT_VALIDATED');assert.equal(actual.profile.auth_kind,'NONE');assert.equal(actual.profile.credential_present,false);assert.deepEqual(actual.profile.reasoning_efforts,['medium','high']);assert.equal(actual.profile.reasoning_qualification,'DECLARED_NOT_OBSERVED');assert.equal(actual.profile.transport.timeout_seconds,120);assert.equal(actual.profile.transport.proxy.auth_configured,true);assert.equal(actual.profile.transport.ca.kind,'PEM_BYTES');assert.deepEqual(actual.profile.transport.custom_headers,[{name:'x-auth-token',private:true},{name:'x-organization',private:false}]);
 const serialized=JSON.stringify(actual.profile)+local.getItem(PROVIDER_CONNECTION_HISTORY_KEY);for(const forbidden of ['fixture-private-user','fixture-private-password','fixture-private-header','fixture-public-org','BEGIN CERTIFICATE'])assert.ok(!serialized.includes(forbidden));assert.equal(f.calls.filter(call=>call.method==='POST').length,1);await f.raw({fixture:'restart'});const restored=await new ProviderConnectionClient(f.adapter,local).restore();assert.deepEqual(restored[0].profile.profile_ref,actual.profile.profile_ref);assert.equal(f.calls.filter(call=>call.method==='POST').length,1);
 const edit={...providerConnectionDraft(actual.profile),transportDecision:'RESET',auth:'API_KEY',key:'fixture-current-key'},changed=await client.save(providerConnectionBody(edit,'actual-api-new-version'));assert.equal(changed.status,'COMPLETED');assert.equal(changed.profile.profile_ref.revision,2);assert.equal(changed.profile.credential_present,true);assert.equal(changed.profile.transport.ca,null);assert.equal(changed.profile.transport.proxy,null);assert.deepEqual(changed.profile.transport.custom_headers,[]);assert.equal(f.calls.filter(call=>call.path.includes('chat/completions')).length,0);
 const missing=await f.raw({path:'/api/planning/providers/operations/nonexistent-fixture'});assert.equal(missing.status,404);
});

test('actual Solid form starts compact, supports NONE without key, exposes explicit advanced replacement and never claims a save validates the model',async t=>{
 const directory=await mkdtemp(resolve(process.env.TMPDIR??resolve(app,'tests'),'.provider-ui-'));t.after(()=>rm(directory,{recursive:true,force:true}));const source=await readFile(resolve(app,'src/ProviderConnectionForm.tsx'),'utf8'),transpiled=ts.transpileModule(source,{reportDiagnostics:true,compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}});assert.deepEqual(transpiled.diagnostics,[]);const js=transpiled.outputText.replace(/import ['"]\.\/provider-layout\.css['"];?/g,'').replace(/from ['"]\.\/authored-ui-copy['"]/g,`from '${new URL('../src/authored-ui-copy.ts',import.meta.url).href}'`).replace(/from ['"]\.\/ui-language['"]/g,`from '${new URL('./fixture-ui-language.mjs',import.meta.url).href}'`).replace(/from ['"]\.\/provider-connection['"]/g,`from '${pathToFileURL(resolve(app,'src/provider-connection.ts')).href}'`),code=babel.transformSync(js,{filename:'provider-form.jsx',presets:[[solidPreset,{generate:'ssr',hydratable:false}]],configFile:false,babelrc:false}).code.replace(/from (['"])(solid-js(?:\/web)?)\1/g,(_match,_quote,specifier)=>`from '${import.meta.resolve(specifier)}'`),file=resolve(directory,'provider-form.mjs');await writeFile(file,code);const {default:Form,ProviderConnectionReceipt}=await import(pathToFileURL(file).href),body=providerConnectionBody(draft(),'ssr'),saved=profile(body),props={onSave:()=>assert.fail('SSR must not save'),onInspect:()=>assert.fail('SSR must not inspect'),storage:storage()};const empty=renderToString(()=>Form(props));assert.match(empty,/认证方式/);assert.ok(!/<details[^>]*\bopen(?:\s|[=>])/.test(empty));assert.match(empty,/系统 CA 加附加 PEM 证书/);assert.match(empty,/添加请求头/);assert.ok(!empty.includes('JSON'));
 const none=renderToString(()=>Form({...props,initialProfile:saved}));assert.ok(!none.includes('aria-label="本次 API 密钥"'));assert.match(none,/免 API Key/);assert.match(none,/不会自动沿用|不回填/);const existing=profile(body,{transport:{timeout_seconds:60,ca:{kind:'PEM_BYTES',sha256:'b'.repeat(64)},proxy:{url:'http://proxy.test:8080',auth_configured:true},custom_headers:[{name:'x-auth-token',private:true}]}}),edit=renderToString(()=>Form({...props,initialProfile:existing}));assert.match(edit,/不会静默清空/);assert.match(edit,/完整重填本次高级配置/);assert.match(edit,/选择本机 PEM 证书/);assert.match(edit,/本次代理账号（私密）/);assert.ok(!edit.includes('fixture-private-password'));
 const view={operation:{operationId:body.operation_id,profileId:body.id},status:'COMPLETED',profile:saved},receipt=renderToString(()=>ProviderConnectionReceipt({view,busy:false,onInspect:()=>{}}));assert.match(receipt,/已保存，尚未验证模型连接/);assert.match(receipt,/没有发送测试请求/);assert.match(receipt,/回查原保存操作/);
});

// Actual Solid signals/For/Show and compiled handlers, with only the DOM host in
// memory. Saves and recovery below still use the real app/SQLite API fixture.
const universalSource=(await readFile(resolve(app,'node_modules/solid-js/universal/dist/universal.js'),'utf8'))
 .replace("from 'solid-js'",`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/solid.js')).href}'`);
const {createRenderer}=await import('data:text/javascript;base64,'+Buffer.from(universalSource).toString('base64'));
const componentSource=await readFile(resolve(app,'src/ProviderConnectionForm.tsx'),'utf8');
function formHarness(t,props){
 const host={createElement:type=>({type,props:{},children:[],parent:null}),createTextNode:value=>({type:'#text',value,children:[],parent:null}),
  isTextNode:node=>node.type==='#text',replaceText:(node,value)=>{node.value=value;},
  insertNode(parent,node,anchor){if(node.parent){const previous=node.parent.children;previous.splice(previous.indexOf(node),1);}node.parent=parent;const index=anchor?parent.children.indexOf(anchor):-1;parent.children.splice(index<0?parent.children.length:index,0,node);},
  removeNode(parent,node){const index=parent.children.indexOf(node);if(index>=0)parent.children.splice(index,1);node.parent=null;},
  setProperty:(node,name,value)=>{node.props[name]=value;},getParentNode:node=>node.parent,getFirstChild:node=>node.children[0],getNextSibling:node=>node.parent?.children[node.parent.children.indexOf(node)+1]};
 const renderer=createRenderer(host),js=ts.transpileModule(componentSource,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
 let code=babel.transformSync(js,{filename:'ProviderConnectionForm.jsx',presets:[[solidPreset,{generate:'universal',moduleName:'fixture-renderer'}]],configFile:false,babelrc:false}).code;
 const parsed=ts.createSourceFile('form.mjs',code,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS),bindings={};
 for(const node of [...parsed.statements].reverse())if(ts.isImportDeclaration(node)){
  const imported=node.moduleSpecifier.text==='solid-js'?solid:node.moduleSpecifier.text==='fixture-renderer'?renderer:node.moduleSpecifier.text==='./ui-language'?language:node.moduleSpecifier.text==='./authored-ui-copy'?authored:connection;
  if(node.importClause?.name)bindings[node.importClause.name.text]=imported.default;
  if(node.importClause?.namedBindings&&ts.isNamedImports(node.importClause.namedBindings))for(const item of node.importClause.namedBindings.elements)bindings[item.name.text]=imported[item.propertyName?.text??item.name.text];
  code=code.slice(0,node.getStart(parsed))+code.slice(node.end);
 }
 code=code.replace('export default function ProviderConnectionForm','function ProviderConnectionForm').replace('export function ProviderConnectionReceipt','function ProviderConnectionReceipt');
 const Form=new Function(...Object.keys(bindings),code+'\nreturn ProviderConnectionForm;')(...Object.values(bindings));
 const container=host.createElement('root'),dispose=renderer.render(()=>Form(props),container);t.after(dispose);
 const nodes=()=>{const result=[];const walk=node=>{result.push(node);for(const child of node.children)walk(child);};walk(container);return result;};
 return {nodes,find:(type,label)=>nodes().find(node=>node.type===type&&(!label||node.props['aria-label']===label)),text:()=>nodes().filter(node=>node.type==='#text').map(node=>node.value).join(' ')};
}
async function until(predicate,timeout=25000){const deadline=Date.now()+timeout;while(!predicate()){if(Date.now()>deadline)throw Error('Actual provider form/API did not settle');await new Promise(resolve=>setTimeout(resolve,10));}}

test('actual Solid checkbox choices save through real API, survive a lost response and reopen without enabling other models',async t=>{
 const f=fixture(t),local=storage(),saved=[],bodies=[];
 // Start timing the form operation after the independent API and its SQLite
 // store have actually booted, as they must be before a user can open it.
 const ready=await f.raw({method:'GET',path:'/api/planning/providers'});
 assert.equal(ready.status,200);
 const view=formHarness(t,{storage:local,onSave:async body=>{bodies.push(body);await f.adapter.save(body);throw Error('real response lost after commit');},onInspect:f.adapter.inspect,onSaved:value=>saved.push(value)});
 await new Promise(resolve=>setImmediate(resolve));
 const input=(label,value)=>view.find('input',label).props.onInput({currentTarget:{value}});
 input('连接标识','explicit-reasoning');input('API 地址','http://127.0.0.1:1/v1');input('模型标识','independent-model');
 view.find('select','认证方式').props.onChange({currentTarget:{value:'NONE'}});
 assert.equal(view.find('details').props.open,false);view.find('details').props.onToggle({currentTarget:{open:true}});
 for(const value of PROVIDER_REASONING_EFFORTS)assert.equal(view.find('input','思考档位 '+value).props.checked,false);
 for(const value of ['low','high'])view.find('input','思考档位 '+value).props.onChange({currentTarget:{checked:true}});
 assert.equal(view.find('input','思考档位 medium').props.checked,false);
 view.find('form').props.onSubmit({preventDefault(){}});
 await until(()=>saved.length===1);
 assert.equal(bodies.length,1);assert.deepEqual(bodies[0].reasoning_efforts,['low','high']);assert.deepEqual(saved[0].reasoning_efforts,['low','high']);
 assert.equal(saved[0].reasoning_qualification,'DECLARED_NOT_OBSERVED');assert.equal(f.calls.filter(call=>call.method==='POST').length,1);
 assert.match(view.text(),/思考档位已声明，尚未观察实际调用/);
 await f.raw({fixture:'restart'});
 const reopened=formHarness(t,{initialProfile:saved[0],storage:local,onSave:()=>assert.fail('no second POST'),onInspect:f.adapter.inspect});
 await new Promise(resolve=>setImmediate(resolve));
 assert.equal(reopened.find('input','思考档位 low').props.checked,true);assert.equal(reopened.find('input','思考档位 high').props.checked,true);
 assert.equal(reopened.find('input','思考档位 minimal').props.checked,false);assert.equal(reopened.find('input','思考档位 xhigh').props.checked,false);
 assert.deepEqual(providerConnectionDraft(profile(providerConnectionBody(draft(),'different'))).reasoningEfforts,[]);
 assert.equal(f.calls.filter(call=>call.path.includes('chat/completions')).length,0);
});

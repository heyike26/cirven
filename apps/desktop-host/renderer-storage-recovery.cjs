'use strict';
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto');
const MAX_BYTES=16*1024*1024,MAX_SCAN=64*1024*1024;
const sha=value=>crypto.createHash('sha256').update(value).digest('hex');
const validOrigin=value=>typeof value==='string'&&/^http:\/\/127\.0\.0\.1:[1-9][0-9]{3,4}$/.test(value)&&Number(value.split(':').at(-1))>=1024&&Number(value.split(':').at(-1))<=65535;
function requireValue(ok){if(!ok)throw Error('RESTORED_RENDERER_STORAGE_UNAVAILABLE');}
function ordinary(file,limit){
  const stat=fs.lstatSync(file);requireValue(stat.isFile()&&!stat.isSymbolicLink()&&stat.nlink===1&&stat.size<=limit);return stat;
}
function noLinks(file){for(let current=path.resolve(file);;current=path.dirname(current)){if(fs.existsSync(current))requireValue(!fs.lstatSync(current).isSymbolicLink());if(path.dirname(current)===current)break;}}
function readJSON(file,limit=65536){noLinks(file);ordinary(file,limit);return JSON.parse(fs.readFileSync(file,'utf8'));}
function candidates(dataDir,provenance){
  if(provenance.renderer_source_origin!=null){requireValue(validOrigin(provenance.renderer_source_origin));return [provenance.renderer_source_origin];}
  // Legacy archives excluded the service-port record. These are candidates,
  // never trusted origins: Chromium must find owned state at exactly one of
  // them. No LevelDB values, ordering or compressed records are interpreted.
  const directory=path.join(dataDir,'session','Local Storage','leveldb');
  if(!fs.existsSync(directory))return [];
  noLinks(directory);requireValue(fs.lstatSync(directory).isDirectory());
  const files=fs.readdirSync(directory).filter(name=>/^\d+\.(ldb|log)$/.test(name));requireValue(files.length<=64);
  const result=new Set();let bytes=0;
  for(const name of files){const file=path.join(directory,name),stat=ordinary(file,MAX_SCAN);bytes+=stat.size;requireValue(bytes<=MAX_SCAN);
    const text=fs.readFileSync(file).toString('latin1');
    for(const match of text.matchAll(/http:\/\/127\.0\.0\.1:[0-9]{4,5}(?![0-9])/g))if(validOrigin(match[0]))result.add(match[0]);
    requireValue(result.size<=16);
  }
  requireValue(files.length===0||result.size>0);return [...result].sort();
}
const EXPERIENCE_PREFIX='cirven-task-experience-v1:';
const OWNED_PREFIXES=['eeas-',EXPERIENCE_PREFIX,'cirven-stage-view-v1:','cirven-stage-input-v1:'];
const OWNED_KEYS=['cirven-workflow-reading-v1'];
const ownedKey=key=>typeof key==='string'&&(OWNED_KEYS.includes(key)||OWNED_PREFIXES.some(prefix=>key.startsWith(prefix)));
const valueBound=(key,value)=>key.startsWith(EXPERIENCE_PREFIX)?Buffer.byteLength(value)<=8*1024*1024:value.length<=2*1024*1024;
function checkedRows(rows){
  requireValue(Array.isArray(rows)&&rows.length<=3000);let bytes=0;const keys=new Set();
  for(const row of rows){requireValue(Array.isArray(row)&&row.length===2&&ownedKey(row[0])&&row[0].length<=8192&&typeof row[1]==='string'&&valueBound(row[0],row[1])&&!keys.has(row[0]));keys.add(row[0]);bytes+=Buffer.byteLength(row[0]+row[1]);requireValue(bytes<=MAX_BYTES);}
  return rows;
}
// Unknown-operation journals and control drafts retain their original bytes.
// Existing renderer readers reset review consent and recheck live owner/grant
// identities; migration must not erase the journals that prevent replay.
function projectRows(rows){return checkedRows(rows).map(row=>[...row]);}
function importExpression(rows,origin){
  checkedRows(rows);requireValue(validOrigin(origin));
  return `(()=>{if(location.origin!==${JSON.stringify(origin)})throw Error('RESTORE_ORIGIN_CHANGED');const rows=${JSON.stringify(rows)};for(const [key,value]of rows){const old=localStorage.getItem(key);if(old!==null&&old!==value)throw Error('RESTORE_DRAFT_CONFLICT');}for(const [key,value]of rows){if(localStorage.getItem(key)===null)localStorage.setItem(key,value);}for(const [key,value]of rows){if(localStorage.getItem(key)!==value)throw Error('RESTORE_DRAFT_UNCONFIRMED');}return rows.length;})()`;
}
const exportExpression=`(()=>{
  const prefixes=${JSON.stringify(OWNED_PREFIXES)},exactKeys=${JSON.stringify(OWNED_KEYS)};
  const rows=[];let bytes=0;
  if(localStorage.length>3000)throw Error('RESTORE_STORAGE_LIMIT');
  for(let i=0;i<localStorage.length;i++){
    const key=localStorage.key(i);
    if(key===null||!exactKeys.includes(key)&&!prefixes.some(prefix=>key.startsWith(prefix)))continue;
    const value=localStorage.getItem(key),size=new TextEncoder().encode(value).length;
    bytes+=new TextEncoder().encode(key).length+size;
    if(bytes>${MAX_BYTES}||key.length>8192||(key.startsWith(${JSON.stringify(EXPERIENCE_PREFIX)})?size>8388608:value.length>2097152))
      throw Error('RESTORE_STORAGE_LIMIT');
    rows.push([key,value]);
  }
  return rows;
})()`;
function selectSource(found,currentOrigin){
  const old=found.filter(item=>item.origin!==currentOrigin&&item.rows.length>0);
  requireValue(old.length<=1);if(old.length)return old[0];
  return found.find(item=>item.origin===currentOrigin&&item.rows.length>0);
}
async function recoverRendererStorage({dataDir,origin,BrowserWindow,ses,signal}){
  const proofFile=path.join(dataDir,'restore-provenance.json');if(!fs.existsSync(proofFile))return {status:'NOT_A_RESTORED_INSTALLATION'};
  const proof=readJSON(proofFile);requireValue(proof.schema_version==='eeas.restore-provenance.v1'&&path.resolve(proof.target_data_dir)===path.resolve(dataDir)&&/^[a-f0-9]{64}$/.test(proof.snapshot_sha256)&&/^[a-f0-9]{32}$/.test(proof.snapshot_id)&&validOrigin(origin));
  const journal=path.join(dataDir,'renderer-storage-restorations'),receiptFile=path.join(journal,proof.snapshot_id+'.json');noLinks(journal);
  if(fs.existsSync(receiptFile)){const receipt=readJSON(receiptFile,MAX_BYTES*2);requireValue(receipt.schema==='cirven.renderer-storage-restoration.v1'&&receipt.snapshot_sha256===proof.snapshot_sha256&&receipt.target_data_dir===path.resolve(dataDir)&&receipt.target_origin===origin&&receipt.status==='COMPLETE'&&Array.isArray(receipt.keys)&&receipt.keys.length<=3000);return {status:'ALREADY_RESTORED',keys:receipt.keys.length};}
  const origins=candidates(dataDir,proof);let window,cancelled=false;const schemes=[];
  const check=()=>{requireValue(!cancelled&&!signal?.aborted);};
  const stop=()=>{cancelled=true;if(window&&!window.isDestroyed())window.destroy();};
  const timer=setTimeout(stop,15000);signal?.addEventListener('abort',stop,{once:true});
  const route='/__cirven_restore_'+crypto.randomUUID(),allowed=new Set([...origins,origin].map(value=>value+route));
  try{
    check();
    // Interception belongs only to this restored App's Chromium session and
    // precedes the normal renderer/Quick surfaces. Never fetch an old server,
    // even if the original installation is currently listening on that port.
    await ses.protocol.handle('http',request=>{requireValue(request.method==='GET'&&allowed.has(request.url));return new Response('<!doctype html><meta charset="utf-8"><body></body>',{headers:{'Content-Type':'text/html; charset=utf-8','Content-Security-Policy':"default-src 'none'; connect-src 'none'; frame-src 'none'; worker-src 'none'; form-action 'none'; base-uri 'none'"}});});schemes.push('http');
    await ses.protocol.handle('https',()=>new Response('',{status:403}));schemes.push('https');check();
    window=new BrowserWindow({show:false,webPreferences:{session:ses,nodeIntegration:false,contextIsolation:true,sandbox:true,webviewTag:false,devTools:false}});
    window.webContents.setWindowOpenHandler(()=>({action:'deny'}));window.webContents.on('will-navigate',(event,url)=>{if(!allowed.has(url))event.preventDefault();});
    const found=[];
    for(const sourceOrigin of origins){check();await window.loadURL(sourceOrigin+route);check();const rows=checkedRows(await window.webContents.executeJavaScript(exportExpression));check();found.push({origin:sourceOrigin,rows});}
    const selected=selectSource(found,origin);requireValue(selected||origins.length===0);
    const rows=selected?projectRows(selected.rows):[];check();await window.loadURL(origin+route);check();
    await window.webContents.executeJavaScript(importExpression(rows,origin));check();ses.flushStorageData();
    // Publish only after readback; partial prior writes may be completed only
    // if still identical. A conflicting destination is never overwritten.
    noLinks(journal);fs.mkdirSync(journal,{recursive:true,mode:0o700});
    const receipt={schema:'cirven.renderer-storage-restoration.v1',status:'COMPLETE',snapshot_sha256:proof.snapshot_sha256,target_data_dir:path.resolve(dataDir),source_origin:selected?.origin??null,target_origin:origin,keys:rows.map(([key,value])=>({key,sha256:sha(value)})),model_calls:0,source_storage_changed:false};
    fs.writeFileSync(receiptFile,JSON.stringify(receipt)+'\n',{flag:'wx',mode:0o600});return {status:'RESTORED',keys:rows.length};
  }finally{
    clearTimeout(timer);signal?.removeEventListener('abort',stop);if(window&&!window.isDestroyed())window.destroy();
    for(const scheme of schemes.reverse())ses.protocol.unhandle(scheme);
  }
}
module.exports={recoverRendererStorage,candidates,checkedRows,projectRows,importExpression,selectSource,validOrigin};

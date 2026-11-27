import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';

export const MAX_SIDE_BYTES=256*1024;
export const fail=code=>{throw Object.assign(new Error(code),{code});};
export const digest=value=>createHash('sha256').update(value).digest('hex');
export function canonical(value){
  if(Array.isArray(value))return '['+value.map(canonical).join(',')+']';
  if(value!==null&&typeof value==='object')return '{'+Object.keys(value).sort().map(k=>JSON.stringify(k)+':'+canonical(value[k])).join(',')+'}';
  if(value===undefined||typeof value==='number'&&!Number.isFinite(value))fail('INVALID_JSON');
  return JSON.stringify(value);
}
export const samePath=(a,b)=>process.platform==='win32'?path.resolve(a).toLowerCase()===path.resolve(b).toLowerCase():path.resolve(a)===path.resolve(b);
export function inside(root,filename){const r=path.relative(root,filename);return r===''||!r.startsWith('..'+path.sep)&&r!=='..'&&!path.isAbsolute(r);}
export function identity(s){return {dev:String(s.dev),ino:String(s.ino)};}
export function stamp(s){return {...identity(s),size:String(s.size),mtime_ns:String(s.mtimeNs),ctime_ns:String(s.ctimeNs)};}
const equal=(a,b)=>canonical(a)===canonical(b);
export function absolute(filename){
  if(typeof filename!=='string'||!filename||filename.length>32768||filename.includes('\0')||!path.isAbsolute(filename)||filename.startsWith('\\\\'))fail('INVALID_ABSOLUTE_PATH');
  return path.resolve(filename);
}
export function ordinaryChain(filename,{file=false,missing=false}={}){
  const resolved=absolute(filename),parsed=path.parse(resolved);let cursor=parsed.root;const chain=[];
  for(const component of resolved.slice(parsed.root.length).split(path.sep).filter(Boolean)){
    cursor=path.join(cursor,component);let s;
    try{s=fs.lstatSync(cursor,{bigint:true});}catch(e){if(e.code==='ENOENT'&&missing)return {chain,missing:cursor};throw e;}
    if(s.isSymbolicLink()||!samePath(fs.realpathSync(cursor),cursor))fail('LINK_OR_ALIAS');
    const last=samePath(cursor,resolved);
    if(last&&file){if(!s.isFile()||s.nlink!==1n)fail('NOT_ORDINARY_FILE');}
    else if(!s.isDirectory())fail('NOT_ORDINARY_DIRECTORY');
    chain.push({path:cursor,...identity(s)});
  }
  return {chain,missing:null};
}
export function checkChain(chain){
  for(const row of chain){
    const s=fs.lstatSync(row.path,{bigint:true});
    if(!s.isDirectory()||s.isSymbolicLink()||!equal(identity(s),{dev:row.dev,ino:row.ino})||!samePath(fs.realpathSync(row.path),row.path))fail('ANCESTOR_CHANGED');
  }
}
export function readBounded(filename,max=MAX_SIDE_BYTES){
  ordinaryChain(filename,{file:true});const before=fs.lstatSync(filename,{bigint:true});
  if(before.size>BigInt(max))fail('TOO_LARGE');
  const fd=fs.openSync(filename,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW??0));
  try{
    const opened=fs.fstatSync(fd,{bigint:true});
    if(!opened.isFile()||opened.nlink!==1n||!equal(stamp(before),stamp(opened)))fail('READ_CHANGED');
    const buffer=Buffer.alloc(max+1);let length=0;
    while(length<buffer.length){const n=fs.readSync(fd,buffer,length,buffer.length-length,null);if(!n)break;length+=n;}
    if(length>max)fail('TOO_LARGE');
    const after=fs.fstatSync(fd,{bigint:true}),named=fs.lstatSync(filename,{bigint:true});
    if(!equal(stamp(opened),stamp(after))||!equal(stamp(after),stamp(named))||named.isSymbolicLink()||named.nlink!==1n)fail('READ_CHANGED');
    return {buffer:buffer.subarray(0,length),stat:stamp(after)};
  }finally{fs.closeSync(fd);}
}
export function utf8(buffer){
  let text;try{text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(buffer);}catch{fail('NOT_UTF8');}
  if(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(text))fail('BINARY_CONTENT');
  return text;
}
export const lines=text=>{const t=text.startsWith('\ufeff')?text.slice(1):text;return t===''?0:t.split('\n').length-(t.endsWith('\n')?1:0);};
export function relative(root,filename){
  absolute(filename);if(!inside(root,filename)||samePath(root,filename))fail('OUTSIDE_WORKSPACE');
  const rel=path.relative(root,filename).split(path.sep).join('/');validateRelative(rel);return rel;
}
export function validateRelative(rel){
  if(typeof rel!=='string'||!rel||rel.length>2048||rel.includes('\\')||rel.includes('\0')||rel.includes(':')||rel.startsWith('/')||rel.split('/').some(x=>!x||x==='.'||x==='..'))fail('INVALID_RELATIVE_PATH');
  return rel;
}
export function permitted(rel,grants){return grants==='*'||grants.some(g=>g==='*'||(g.endsWith('/')?rel.startsWith(g):rel===g));}
export function snapshot(root,rel){
  const filename=path.join(root,...rel.split('/')),parent=ordinaryChain(path.dirname(filename),{missing:true});
  if(parent.missing){checkChain(parent.chain);return {value:{state:'ABSENT',sha256:null,bytes:0,line_count:0,blob:null,stat:null},buffer:null,parents:parent.chain};}
  let exists=true;try{fs.lstatSync(filename);}catch(e){if(e.code==='ENOENT')exists=false;else throw e;}
  if(!exists){checkChain(parent.chain);return {value:{state:'ABSENT',sha256:null,bytes:0,line_count:0,blob:null,stat:null},buffer:null,parents:parent.chain};}
  const {buffer,stat}=readBounded(filename),text=utf8(buffer);checkChain(parent.chain);const hash=digest(buffer);
  return {value:{state:'UTF8',sha256:hash,bytes:buffer.length,line_count:lines(text),blob:'blobs/'+hash+'.utf8',stat},buffer,parents:parent.chain};
}
export function mkdirOwned(parent,name){
  ordinaryChain(parent);const out=path.join(parent,name);
  try{fs.mkdirSync(out,{mode:0o700});}catch(e){if(e.code!=='EEXIST')throw e;}
  ordinaryChain(out);return out;
}
export function writeOnce(filename,buffer){
  const parents=ordinaryChain(path.dirname(filename)).chain;
  const fd=fs.openSync(filename,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL|(fs.constants.O_NOFOLLOW??0),0o600);
  try{fs.writeFileSync(fd,buffer);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  checkChain(parents);ordinaryChain(filename,{file:true});
}
export function putBlob(root,snap){
  if(!snap.buffer)return;const target=path.join(root,snap.value.blob);
  try{writeOnce(target,snap.buffer);}catch(e){if(e.code!=='EEXIST')throw e;const actual=readBounded(target);if(digest(actual.buffer)!==snap.value.sha256)fail('BLOB_CONFLICT');}
}
export function readJSON(filename,max=256*1024){
  const {buffer}=readBounded(filename,max);let value;try{value=JSON.parse(utf8(buffer));}catch{fail('INVALID_JSON');}return value;
}

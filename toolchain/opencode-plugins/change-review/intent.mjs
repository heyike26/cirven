// Exact subset proof only. Fuzzy edits or formatter changes remain PARTIAL.
import path from 'node:path';
import {fail,MAX_SIDE_BYTES,utf8,relative,validateRelative} from './files.mjs';
const plain=s=>s.startsWith('\ufeff')?s.slice(1):s;
const normalize=s=>s.replaceAll('\r\n','\n');
const withBom=(text,bom)=>Buffer.from((bom?'\ufeff':'')+plain(text),'utf8');
// ctx.directory is already bound to the scope root. Resolve only a literal
// tool argument here; protocol members and files.relative remain strict.
function toolTarget(root,filename){
  if(typeof filename!=='string'||!filename||filename.length>4096||/[\u0000-\u001f\u007f*?\[\]]/u.test(filename))fail('INVALID_RELATIVE_PATH');
  const windows=process.platform==='win32',isAbsolute=path.isAbsolute(filename);
  let local=windows?filename.replaceAll('\\','/'):filename;
  if(isAbsolute){
    if(windows?!/^[A-Za-z]:\//.test(local):!local.startsWith('/')||local.startsWith('//'))fail('INVALID_ABSOLUTE_PATH');
    local=local.slice(windows?3:1);
  }
  if(!isAbsolute&&local.startsWith('./'))local=local.slice(2);
  if(local.split('/').some(p=>!p||p==='.'||p==='..'||windows&&(p!==p.replace(/[ .]+$/u,'')||/[:<>"|]/u.test(p)||/^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$/iu.test(p))))fail('INVALID_RELATIVE_PATH');
  if(!isAbsolute)validateRelative(local);return relative(root,isAbsolute?filename:path.join(root,...local.split('/')));
}
export function toolIntent(tool,args,root){
  if(!args||typeof args!=='object'||Array.isArray(args))fail('INVALID_TOOL_ARGS');
  if(tool==='write'||tool==='edit'){
    if(typeof args.filePath!=='string')fail('INVALID_TOOL_ARGS');const rel=toolTarget(root,args.filePath);
    const keys=tool==='write'?['filePath','content']:['filePath','oldString','newString','replaceAll'];
    if(Object.keys(args).some(k=>!keys.includes(k)))fail('INVALID_TOOL_ARGS');
    for(const k of tool==='write'?['content']:['oldString','newString']){
      if(typeof args[k]!=='string'||Buffer.byteLength(args[k])>MAX_SIDE_BYTES)fail('TOO_LARGE');utf8(Buffer.from(args[k]));
    }
    if(tool==='edit'&&args.replaceAll!==undefined&&typeof args.replaceAll!=='boolean')fail('INVALID_TOOL_ARGS');
    return {paths:[rel],operations:[{type:tool,path:rel}],args};
  }
  if(tool!=='apply_patch')fail('UNSUPPORTED_TOOL');
  if(Object.keys(args).some(k=>k!=='patchText')||typeof args.patchText!=='string'||Buffer.byteLength(args.patchText)>MAX_SIDE_BYTES)fail('INVALID_PATCH');
  const ls=args.patchText.replaceAll('\r\n','\n').split('\n');if(ls.at(-1)==='')ls.pop();
  if(ls.shift()!=='*** Begin Patch'||ls.pop()!=='*** End Patch')fail('UNSUPPORTED_PATCH_SYNTAX');
  const operations=[];let op;
  for(const line of ls){
    const m=/^\*\*\* (Add|Update|Delete) File: (.+)$/.exec(line);
    if(m){op={type:m[1].toLowerCase(),path:toolTarget(root,m[2]),body:[]};operations.push(op);continue;}
    if(!op)fail('UNSUPPORTED_PATCH_SYNTAX');
    if(line.startsWith('*** Move to: ')){
      if(op.type!=='update'||op.body.length||op.move)fail('UNSUPPORTED_PATCH_SYNTAX');
      op.move=toolTarget(root,line.slice(13));continue;
    }
    if(line.startsWith('*** ')&&line!=='*** End of File')fail('UNSUPPORTED_PATCH_SYNTAX');op.body.push(line);
  }
  if(!operations.length||operations.length>20)fail('PATCH_CAPACITY');const paths=[];
  for(const item of operations){
    if(item.type==='add'&&item.body.some(l=>!l.startsWith('+'))||item.type==='delete'&&item.body.length)fail('UNSUPPORTED_PATCH_SYNTAX');
    for(const p of [item.path,...(item.move?[item.move]:[])]){if(paths.some(x=>process.platform==='win32'?x.toLowerCase()===p.toLowerCase():x===p))fail('DUPLICATE_PATCH_PATH');paths.push(p);}
  }
  return {paths,operations,args};
}
function editExpected(args,before){
  const raw=before?.state==='UTF8'?before.text:'',source=plain(raw);
  if(args.oldString===''){if(before?.state!=='ABSENT')fail('INTENT_NOT_EXACT');return withBom(args.newString,args.newString.startsWith('\ufeff'));}
  if(before?.state!=='UTF8'||args.oldString===args.newString)fail('INTENT_NOT_EXACT');
  const ending=source.includes('\r\n')?'\r\n':'\n';
  const old=normalize(args.oldString).replaceAll('\n',ending),next=normalize(args.newString).replaceAll('\n',ending),n=source.split(old).length-1;
  if(!n||!args.replaceAll&&n!==1)fail('INTENT_NOT_EXACT');
  const result=args.replaceAll?source.split(old).join(next):source.replace(old,()=>next);
  return withBom(result,raw.startsWith('\ufeff')||result.startsWith('\ufeff'));
}
function patchExpected(op,before){
  if(op.type==='delete')return null;
  if(op.type==='add'){
    if(before.state!=='ABSENT')fail('ADD_TARGET_EXISTED');
    let text=op.body.map(l=>l.slice(1)).join('\n');if(text&&!text.endsWith('\n'))text+='\n';return Buffer.from(text);
  }
  if(before.state!=='UTF8')fail('INTENT_NOT_EXACT');
  const raw=before.text;let ls=plain(raw).split('\n');if(ls.at(-1)==='')ls.pop();
  let cursor=0,old=[],next=[],inChunk=false;
  const apply=()=>{
    if(!inChunk)return;if(!old.length)fail('UNSUPPORTED_PATCH_CONTEXT');const matches=[];
    for(let i=cursor;i<=ls.length-old.length;i++)if(old.every((x,k)=>ls[i+k]===x))matches.push(i);
    if(matches.length!==1)fail('INTENT_NOT_EXACT');const at=matches[0];ls.splice(at,old.length,...next);cursor=at+next.length;old=[];next=[];
  };
  for(const l of op.body){
    if(l==='@@'||l.startsWith('@@ ')){apply();inChunk=true;continue;}
    if(l==='*** End of File')continue;
    if(!inChunk||![' ','-','+'].includes(l[0]))fail('UNSUPPORTED_PATCH_CONTEXT');
    if(l[0]!=='+')old.push(l.slice(1));if(l[0]!=='-')next.push(l.slice(1));
  }
  apply();if(!inChunk)fail('UNSUPPORTED_PATCH_CONTEXT');return withBom(ls.join('\n')+'\n',raw.startsWith('\ufeff'));
}
export function expectedFor(intent,before){
  const out=new Map();
  for(const op of intent.operations){
    const snap=before.get(op.path),b={...snap.value,text:snap.buffer?utf8(snap.buffer):''};let expected;
    if(op.type==='write')expected=withBom(intent.args.content,b.text.startsWith('\ufeff')||intent.args.content.startsWith('\ufeff'));
    else if(op.type==='edit')expected=editExpected(intent.args,b);else expected=patchExpected(op,b);
    if(op.move){if(before.get(op.move).value.state!=='ABSENT')fail('MOVE_TARGET_EXISTED');out.set(op.path,null);out.set(op.move,expected);}
    else out.set(op.path,expected);
  }
  return out;
}
// Fixed trimDiff may remove shared indentation. Every hunk is still checked
// against BOTH full snapshots and all unchanged gaps must match exactly.
export function verifyPatch(before,after,patch){
  if(typeof patch!=='string'||Buffer.byteLength(patch)>MAX_SIDE_BYTES)return false;
  const split=text=>{const a=normalize(plain(text)).split('\n');if(a.at(-1)==='')a.pop();return text===''?[]:a;};
  const a=split(before),b=split(after),ls=normalize(patch).split('\n');let i=0,found=0,lastA=0,lastB=0,indent=null;
  const matches=(actual,value)=>{
    if(actual===value)return true;if(typeof actual!=='string'||!actual.endsWith(value))return false;
    const n=actual.length-value.length;if(!/^\s*$/.test(actual.slice(0,n)))return false;
    if(indent===null)indent=n;return n===indent||value===''&&n<=indent;
  };
  while(i<ls.length){
    const h=/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(ls[i++]);if(!h)continue;
    found++;const ac=h[2]===undefined?1:Number(h[2]),bc=h[4]===undefined?1:Number(h[4]);
    let x=Number(h[1])-(ac?1:0),y=Number(h[3])-(bc?1:0),na=0,nb=0;
    if(x<lastA||y<lastB||x>a.length||y>b.length||a.slice(lastA,x).join('\n')!==b.slice(lastB,y).join('\n'))return false;
    while(i<ls.length&&!ls[i].startsWith('@@ ')){
      const l=ls[i];if(l==='\\ No newline at end of file'){i++;continue;}
      if(na===ac&&nb===bc)break;
      if(!l||![' ','+','-'].includes(l[0]))return false;i++;
      if(l[0]!=='+'){if(!matches(a[x++],l.slice(1)))return false;na++;}
      if(l[0]!=='-'){if(!matches(b[y++],l.slice(1)))return false;nb++;}
    }
    if(na!==ac||nb!==bc)return false;lastA=x;lastB=y;
  }
  return found>0&&a.slice(lastA).join('\n')===b.slice(lastB).join('\n');
}

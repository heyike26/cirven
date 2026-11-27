'use strict';
// Native file editors only. This module never writes/saves/reloads a disk file.
const fs=require('node:fs'),path=require('node:path');
const {hash,safePath,localWindowsPath}=require('./client.cjs');
const MAX=524288;
function fail(code){throw Object.assign(new Error(code),{code});}
const stripBom=text=>text.startsWith('\ufeff')?text.slice(1):text;
function localUri(vscode,value){const uri=vscode.Uri.parse(value);if(uri.scheme!=='file'||uri.authority||uri.query||uri.fragment||!path.isAbsolute(uri.fsPath)||process.platform==='win32'&&!localWindowsPath(uri.fsPath))fail('LOCAL_EDITOR_REQUIRED');return uri;}
function parentIdentity(uri,grant){const p=grant.file_location?.parent_ref,name=grant.file_location?.name;
 if(!p||typeof name!=='string'||path.basename(uri.fsPath)!==name||!/^\d{1,32}$/.test(p.device??'')||!/^\d{1,32}$/.test(p.inode??''))fail('EDITOR_PARENT_CHANGED');
 const info=fs.statSync(path.dirname(uri.fsPath),{bigint:true});if(!info.isDirectory()||info.dev.toString()!==p.device||info.ino.toString()!==p.inode)fail('EDITOR_PARENT_CHANGED');return info;}
function readDisk(uri,grant){safePath(uri.fsPath);const parent=parentIdentity(uri,grant),fd=fs.openSync(uri.fsPath,fs.constants.O_RDONLY|(fs.constants.O_NOFOLLOW||0));
 try{const before=fs.fstatSync(fd,{bigint:true});if(!before.isFile()||before.nlink!==1n||before.size>BigInt(MAX))fail('EDITOR_SOURCE_UNAVAILABLE');
  const bytes=Buffer.alloc(MAX+1);let count=0;while(count<bytes.length){const n=fs.readSync(fd,bytes,count,bytes.length-count,null);if(!n)break;count+=n;}if(count>MAX)fail('DIFF_TOO_LARGE');
  const after=fs.fstatSync(fd,{bigint:true}),leaf=fs.lstatSync(uri.fsPath,{bigint:true});safePath(uri.fsPath);const last=parentIdentity(uri,grant);
  if(after.nlink!==1n||leaf.nlink!==1n||['dev','ino','size','mtimeNs','ctimeNs'].some(k=>before[k]!==after[k])||leaf.isSymbolicLink()||leaf.dev!==before.dev||leaf.ino!==before.ino||last.dev!==parent.dev||last.ino!==parent.ino)fail('SOURCE_CHANGED');
  const raw=bytes.subarray(0,count),text=raw.toString('utf8');if(text.includes('\0')||!Buffer.from(text,'utf8').equals(raw))fail('EDITOR_ENCODING_UNSUPPORTED');return {sha256:hash(raw),byte_length:count,text,file_identity:{device:before.dev.toString(),inode:before.ino.toString()}};
 }finally{fs.closeSync(fd);}}
function basis(document){if(!document||document.isClosed||!Number.isSafeInteger(document.version)||document.version<1)fail('EDITOR_DOCUMENT_CHANGED');const text=document.getText();if(Buffer.byteLength(text)>MAX||text.includes('\0'))fail('DIFF_TOO_LARGE');return {document_version:document.version,content_sha256:hash(text),is_dirty:document.isDirty===true};}
function uriKey(uri){return process.platform==='win32'?uri.fsPath.toLowerCase():uri.fsPath;}
class NativeEditor{
 constructor(vscode){this.vscode=vscode;}
 uri(value){return localUri(this.vscode,value);}
 documents(uri){return (this.vscode.workspace.textDocuments||[]).filter(d=>d.uri.scheme==='file'&&uriKey(d.uri)===uriKey(uri)&&!d.isClosed);}
 workspaceHeaders(rootValue){
  const root=this.uri(rootValue),all=this.vscode.workspace.textDocuments;
  if(!Array.isArray(all))fail('EDITOR_UNAVAILABLE');if(all.length>512)fail('DOCUMENT_LIMIT');
  const rows=[],seen=new Set(),rootKey=uriKey(root);
  for(const document of all){if(document.isClosed||!['file','untitled'].includes(document.uri?.scheme))continue;
   const raw=document.uri;if(raw.authority||raw.query||raw.fragment||!path.isAbsolute(raw.fsPath))continue;
   const name=uriKey(raw),rel=path.relative(rootKey,name);if(!rel||rel==='..'||rel.startsWith('..'+path.sep)||path.isAbsolute(rel))continue;
   const relative=path.relative(root.fsPath,raw.fsPath).split(path.sep).join('/');
   if(relative.length>1024||relative.split('/').some(p=>!p||p==='.'||p==='..'||['.git','.eeas-runtime','.eeas-sdk-state'].includes(p.toLowerCase())||p.toLowerCase().startsWith('.eeas-write-'))||/[\\:\x00-\x1f\x7f]/.test(relative))fail('EDITOR_UNAVAILABLE');
   if(seen.has(name))fail('EDITOR_UNAVAILABLE');seen.add(name);rows.push({document,path:relative,dirty:document.isDirty===true||raw.scheme==='untitled',version:document.version});
   if(rows.length>64)fail('DOCUMENT_LIMIT');
  }rows.sort((a,b)=>a.path<b.path?-1:a.path>b.path?1:0);return {root,rows};}
 workspaceStamp(rootValue){try{const {rows}=this.workspaceHeaders(rootValue);return JSON.stringify(rows.map(row=>[row.path,row.dirty,row.dirty?null:row.version]));}catch(error){return 'UNAVAILABLE:'+String(error.code||'EDITOR_UNAVAILABLE');}}
 observeWorkspace(prepared,permission=null){
  const base={captured_at_epoch_ms:Date.now(),availability:'UNAVAILABLE',root_identity:null,documents:[],documents_complete:false,unavailable_reason:'EDITOR_UNAVAILABLE',permission_ref:null};let captured;
  try{captured=this.workspaceHeaders(prepared.root_uri);for(const row of captured.rows){const b=basis(row.document);base.documents.push({path:row.path,...b,is_dirty:row.dirty});}
   base.documents_complete=true;if(Buffer.byteLength(JSON.stringify(base))>49152)fail('DOCUMENT_LIMIT');
  }catch(error){return {...base,documents:[],documents_complete:false,unavailable_reason:error.code==='DOCUMENT_LIMIT'||error.code==='DIFF_TOO_LARGE'?'DOCUMENT_LIMIT':'EDITOR_UNAVAILABLE'};}
  const exact=()=>{safePath(captured.root.fsPath);const st=fs.lstatSync(captured.root.fsPath,{bigint:true});if(!st.isDirectory()||st.isSymbolicLink()||st.dev.toString()!==prepared.root_identity.device||st.ino.toString()!==prepared.root_identity.inode)fail('EDITOR_PARENT_CHANGED');};
  try{exact();const stamp=this.workspaceStamp(prepared.root_uri);if(stamp!==JSON.stringify(captured.rows.map(row=>[row.path,row.dirty,row.dirty?null:row.version])))fail('EDITOR_DOCUMENT_CHANGED');exact();
   return {...base,availability:'OBSERVED',root_identity:{...prepared.root_identity},unavailable_reason:null,permission_ref:permission};
  }catch{return {...base,availability:'UNAVAILABLE',root_identity:null,unavailable_reason:'NAMESPACE_CHANGED',permission_ref:null};}}
 subscribeWorkspace(listener){const subscriptions=[];
  for(const name of ['onDidOpenTextDocument','onDidChangeTextDocument','onDidSaveTextDocument','onDidCloseTextDocument']){
   const event=this.vscode.workspace[name];if(typeof event!=='function')continue;subscriptions.push(event.call(this.vscode.workspace,value=>listener(value.document||value)));
  }return {complete:subscriptions.length===4,dispose(){for(const entry of subscriptions)entry.dispose();}};}
 observe(entry){if(!entry.editor_uri||!entry.lease?.grant)fail('EDITOR_SOURCE_UNAVAILABLE');const uri=this.uri(entry.editor_uri),disk=readDisk(uri,entry.lease.grant),docs=this.documents(uri);
  if(docs.length!==1)fail('EDITOR_DOCUMENT_REQUIRED');return {uri,disk,document:docs[0],basis:basis(docs[0])};}
 observeFile(entry,prepared,action){
  if(!entry.lease?.grant||!entry.editor_uri||!['COMMIT','UNDO','CONFIRM','MANUAL_RESOLVE'].includes(action))fail('EDITOR_CHANGE_MISMATCH');
  const uri=this.uri(prepared.editor_uri),original=this.uri(entry.editor_uri),s=prepared.snapshot,g=entry.lease.grant,manual=action==='MANUAL_RESOLVE';
  if(uriKey(uri)!==uriKey(original))fail('EDITOR_CHANGE_MISMATCH');
  const docs=this.documents(uri);if(docs.length>16)fail('EDITOR_DOCUMENT_CHANGED');
  const observations=docs.map(basis);if(observations.some(d=>d.is_dirty))fail('DIRTY_EDITOR');
  // A replaced/missing original root permits only explicit handoff release.
  // Never inspect its replacement namespace to infer an earlier file effect.
  if(s.availability==='UNAVAILABLE'){if(!manual)fail('EDITOR_SOURCE_UNAVAILABLE');return {documents:observations};}
  if(s.availability!=='OBSERVED'||!s.location||!s.saved)fail('EDITOR_CHANGE_MISMATCH');
  if(!manual&&(s.location.path!==g.path||s.location.name!==g.file_location?.name||s.location.parent_ref.device!==g.file_location?.parent_ref?.device||s.location.parent_ref.inode!==g.file_location?.parent_ref?.inode))fail('EDITOR_PARENT_CHANGED');
  const readGrant={file_location:s.location};
  if(s.saved.exists){const disk=readDisk(uri,readGrant);
   if(disk.sha256!==s.saved.sha256||disk.byte_length!==s.saved.byte_length||hash(stripBom(disk.text))!==s.editor_text_sha256||s.file_identity&&(disk.file_identity.device!==s.file_identity.device||disk.file_identity.inode!==s.file_identity.inode))fail('SOURCE_CHANGED');
   if(observations.some(d=>d.content_sha256!==s.editor_text_sha256))fail('EDITOR_DOCUMENT_CHANGED');
  }else{safePath(uri.fsPath);parentIdentity(uri,readGrant);let missing=false;
   try{fs.lstatSync(uri.fsPath);}catch(error){if(error.code==='ENOENT')missing=true;else throw error;}
   if(!missing)fail('SOURCE_CHANGED');safePath(uri.fsPath);parentIdentity(uri,readGrant);
   if(!manual&&observations.length)fail('EDITOR_ABSENT_DOCUMENT_OPEN');
  }
  return {documents:observations};}
 async prepare(entry,sourceText,targetText){const grant=entry.lease.grant;if(grant.mode!=='TEXT_REPLACE'||grant.source_has_bom!==grant.target_has_bom)fail('EDITOR_ENCODING_UNSUPPORTED');
  if(hash(sourceText)!==grant.saved_basis.sha256||hash(targetText)!==grant.target_sha256||hash(stripBom(sourceText))!==grant.source_editor_sha256||hash(stripBom(targetText))!==grant.target_editor_sha256)fail('EDITOR_CHANGE_MISMATCH');
  const target=stripBom(targetText),withoutCrlf=target.replace(/\r\n/g,'');if(/\r/.test(withoutCrlf)||target.includes('\r\n')&&withoutCrlf.includes('\n'))fail('EDITOR_ENCODING_UNSUPPORTED');
  const uri=this.uri(entry.editor_uri),existing=this.documents(uri);if(existing.some(d=>d.isDirty))fail('DIRTY_EDITOR');readDisk(uri,grant);
  const document=await this.vscode.workspace.openTextDocument(uri),observed=this.observe(entry);
  if(observed.document!==document||observed.basis.is_dirty||observed.disk.sha256!==grant.saved_basis.sha256||observed.basis.content_sha256!==grant.source_editor_sha256||document.getText()!==stripBom(sourceText))fail('SOURCE_CHANGED');
  const version=document.version;await this.vscode.window.showTextDocument(document,{preview:false,preserveFocus:false});
  const checked=this.observe(entry);if(checked.document!==document||document.version!==version||checked.basis.is_dirty||checked.disk.sha256!==grant.saved_basis.sha256)fail('SOURCE_CHANGED');
  return {document,uri,version,basis:checked.basis,target,targetEol:target.includes('\r\n')?this.vscode.EndOfLine.CRLF:this.vscode.EndOfLine.LF};}
 apply(entry,prepared,beforeInvoke){const g=entry.lease.grant,checked=this.observe(entry),doc=prepared.document;
  if(checked.document!==doc||doc.version!==prepared.version||checked.basis.is_dirty||checked.basis.content_sha256!==g.source_editor_sha256||checked.disk.sha256!==g.saved_basis.sha256)fail('SOURCE_CHANGED');
  const edit=new this.vscode.WorkspaceEdit(),range=new this.vscode.Range(doc.positionAt(0),doc.positionAt(doc.getText().length)),edits=[this.vscode.TextEdit.replace(range,prepared.target)];
  if(doc.eol!==prepared.targetEol)edits.push(this.vscode.TextEdit.setEndOfLine(prepared.targetEol));edit.set(doc.uri,edits);
  // The journal and the final version check run synchronously before the host
  // captures its versioned WorkspaceEdit DTO; no intervening await is allowed.
  beforeInvoke();if(doc.version!==prepared.version||doc.isDirty||doc.isClosed)fail('EDITOR_DOCUMENT_CHANGED');
  return this.vscode.workspace.applyEdit(edit,{isRefactoring:false});}
 async open(entry,current){const uri=this.uri(entry.editor_uri);readDisk(uri,entry.lease.grant);const document=await this.vscode.workspace.openTextDocument(uri);if(!current())return;await this.vscode.window.showTextDocument(document,{preview:false,preserveFocus:false});}
}
module.exports={NativeEditor,readDisk,basis,stripBom,localUri,MAX};

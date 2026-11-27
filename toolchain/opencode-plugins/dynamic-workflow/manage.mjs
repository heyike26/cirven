// Offline, explicit JSON-config activation. Importing this module has no effects.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {randomUUID} from 'node:crypto';
import {canonical,digest,ID} from './protocol.mjs';

async function local(filename){
  if(!path.isAbsolute(filename)||path.normalize(filename)!==filename)throw new Error('ABSOLUTE_LOCAL_PATH_REQUIRED');
  for(let item=filename;;item=path.dirname(item)){
    try{if((await fs.lstat(item)).isSymbolicLink())throw new Error('SYMLINK_REJECTED');}catch(error){if(error.code!=='ENOENT')throw error;}
    if(path.dirname(item)===item)break;
  }
}
export async function verifyBundle(directory,manifestSha256){
  await local(directory);const raw=await fs.readFile(path.join(directory,'manifest.json'),'utf8');
  if(!/^[a-f0-9]{64}$/.test(manifestSha256)||digest(raw)!==manifestSha256)throw new Error('MANIFEST_DRIFT');
  const manifest=JSON.parse(raw);if(manifest.id!==ID||manifest.runtime.version!=='1.19.0')throw new Error('BUNDLE_VERSION_REJECTED');
  const checked=new Set([directory]);
  for(const row of manifest.files){
    if(!/^[A-Za-z0-9_./-]+$/.test(row.path)||row.path.startsWith('/')||row.path.split('/').some(part=>!part||part==='.'||part==='..'))throw new Error('BUNDLE_MEMBER_REJECTED');
    let filename=directory;
    for(const part of row.path.split('/')){
      filename=path.join(filename,part);
      if(!checked.has(filename)){if((await fs.lstat(filename)).isSymbolicLink())throw new Error('SYMLINK_REJECTED');checked.add(filename);}
    }
    const raw=await fs.readFile(filename);
    if(raw.length!==row.bytes||digest(raw.toString('utf8'))!==row.sha256)throw new Error('BUNDLE_SOURCE_DRIFT');
  }
  return manifest;
}
export async function configure({configPath,expectedConfigSha256,bundleRoot,manifestSha256,pluginConfigPath,pluginConfigSha256,disable=false,replaceEntry=null}){
  await verifyBundle(bundleRoot,manifestSha256);await local(configPath);
  if(!configPath.endsWith('.json'))throw new Error('JSONC_EDIT_UNSUPPORTED');
  const entry=pathToFileURL(path.join(bundleRoot,'index.mjs')).href,lockPath=configPath+'.eeas-workflow.lock';
  const lock=await fs.open(lockPath,'wx',0o600);let temporary;
  try{
    const raw=await fs.readFile(configPath,'utf8');if(digest(raw)!==expectedConfigSha256)throw new Error('CONFIG_CAS_CONFLICT');
    const config=JSON.parse(raw);if(!config||typeof config!=='object'||Array.isArray(config)||config.plugin!==undefined&&!Array.isArray(config.plugin))throw new Error('CONFIG_SHAPE_INVALID');
    const list=config.plugin??[],matches=item=>(typeof item==='string'?item:item?.[0])===entry;
    if(disable)config.plugin=list.filter(item=>!matches(item));
    else{
      await local(pluginConfigPath);const pluginRaw=await fs.readFile(pluginConfigPath,'utf8');
      if(digest(pluginRaw)!==pluginConfigSha256)throw new Error('PRIVATE_CONFIG_DRIFT');
      const desired=[entry,{configPath:pluginConfigPath,configSha256:pluginConfigSha256}];
      const existing=list.find(matches);if(existing&&canonical(existing)!==canonical(desired))throw new Error('EXISTING_ACTIVATION_CONFLICT');
      if(replaceEntry!==null&&(!replaceEntry.startsWith('file://')||replaceEntry===entry))throw new Error('REPLACEMENT_ENTRY_INVALID');
      config.plugin=list.filter(item=>!matches(item)&&(typeof item==='string'?item:item?.[0])!==replaceEntry);config.plugin.push(desired);
    }
    const next=canonical(config)+'\n';if(canonical(JSON.parse(raw))===canonical(config))return {changed:false,sha256:digest(raw),entry,restart_required:true};
    temporary=configPath+'.'+randomUUID()+'.tmp';await fs.writeFile(temporary,next,{flag:'wx',mode:0o600});await fs.rename(temporary,configPath);
    return {changed:true,sha256:digest(next),entry,restart_required:true};
  }finally{if(temporary)try{await fs.unlink(temporary);}catch{}await lock.close();await fs.unlink(lockPath);}
}
if(process.argv[1]&&path.resolve(process.argv[1])===fileURLToPath(import.meta.url)){
  const [command,requestPath]=process.argv.slice(2);
  if(command!=='configure'||!requestPath){process.stderr.write('Usage: node manage.mjs configure /absolute/request.json\n');process.exitCode=2;}
  else try{await local(requestPath);const result=await configure(JSON.parse(await fs.readFile(requestPath,'utf8')));process.stdout.write(canonical(result)+'\n');}
  catch{process.stderr.write('Workflow activation failed; original configuration was not adopted. Check exact hashes and JSON configuration.\n');process.exitCode=1;}
}

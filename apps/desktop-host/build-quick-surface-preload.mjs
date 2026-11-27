import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash} from 'node:crypto';

const here=path.dirname(fileURLToPath(import.meta.url)),root=path.resolve(here,'../..');
const output=process.argv[2];
if(!output||!path.isAbsolute(output))throw new Error('Pass an explicit absolute build output directory');
const require=createRequire(path.join(root,'apps/agent-shell/package.json'));
const {build,version}=require('esbuild');
const common={absWorkingDir:root,nodePaths:[path.join(root,'apps/agent-shell/node_modules')],bundle:true,minify:true,charset:'utf8',legalComments:'inline',metafile:true,write:false};
const result=await build({absWorkingDir:root,entryPoints:['apps/desktop-host/quick-surface-preload.cjs'],
  ...common,outfile:'quick-surface-preload.bundle.cjs',platform:'browser',format:'iife',target:'es2022',external:['electron']});
const links=await build({...common,entryPoints:['apps/desktop-host/quick-reply-links.mjs'],
  outfile:'quick-reply-links.bundle.cjs',platform:'node',format:'cjs',target:'node22',external:[]});
const sha=bytes=>createHash('sha256').update(bytes).digest('hex');
const record=relative=>{const bytes=fs.readFileSync(path.resolve(root,relative));return {bytes:bytes.length,sha256:sha(bytes)};};
const resolvedRecord=file=>({path:fs.realpathSync(file),...record(file)});
const dependencies=Object.fromEntries(['marked','dompurify'].map(name=>{
  const packageFile=path.join(root,'apps/agent-shell/node_modules',name,'package.json');
  const entries=Object.keys(result.metafile.inputs).filter(relative=>relative.includes('/node_modules/'+name+'/')).map(relative=>({graph_key:relative,...resolvedRecord(path.resolve(root,relative))}));
  return [name,{version:JSON.parse(fs.readFileSync(packageFile,'utf8')).version,package_json:resolvedRecord(packageFile),entries}];
}));
const builderFiles=['esbuild/package.json','esbuild','@esbuild/'+process.platform+'-'+process.arch+'/bin/esbuild'].map(name=>resolvedRecord(require.resolve(name)));
const inputs=Object.fromEntries([...new Set([...Object.keys(result.metafile.inputs),...Object.keys(links.metafile.inputs),
  ...builderFiles.map(item=>path.relative(root,item.path)),
  'apps/desktop-host/build-quick-surface-preload.mjs','apps/agent-shell/package.json',
  'apps/agent-shell/node_modules/marked/package.json','apps/agent-shell/node_modules/dompurify/package.json',
  'apps/agent-shell/public/licenses/dependencies/marked/LICENSE','apps/agent-shell/public/licenses/dependencies/dompurify/LICENSE'])].sort().map(relative=>[relative,record(relative)]));
const externals=result.metafile.outputs['quick-surface-preload.bundle.cjs'].imports;
if(externals.some(item=>!item.external||item.path!=='electron'))throw new Error('Unexpected runtime dependency');
if(links.metafile.outputs['quick-reply-links.bundle.cjs'].imports.length)throw new Error('Reply link checker must be standalone');
const bytes=result.outputFiles[0].contents;
const linkBytes=links.outputFiles[0].contents;
fs.mkdirSync(output,{recursive:true});
fs.writeFileSync(path.join(output,'quick-surface-preload.bundle.cjs'),bytes);
fs.writeFileSync(path.join(output,'quick-reply-links.bundle.cjs'),linkBytes);
fs.writeFileSync(path.join(output,'quick-surface-preload.build.json'),JSON.stringify({schema:'cirven.quick-preload-build.v1',
  builder:{name:'esbuild',version,files:builderFiles},runtime_externals:['electron'],inputs,dependencies,
  graphs:{preload:result.metafile,reply_links:links.metafile},
  host_output:{path:'apps/desktop-host/quick-reply-links.bundle.cjs',bytes:linkBytes.length,sha256:sha(linkBytes)},
  output:{path:'apps/desktop-host/quick-surface-preload.bundle.cjs',bytes:bytes.length,sha256:sha(bytes)}},null,2)+'\n');
console.log(JSON.stringify({output,bytes:bytes.length,sha256:sha(bytes),inputs:Object.keys(inputs).length}));

// Development tests only. Keep original module URLs and module identity while
// using the already installed compiler; no bundles, mocks or runtime downloads.
// Node API: https://nodejs.org/download/release/v22.15.0/docs/api/module.html
import {registerHooks,createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';

const require=createRequire(import.meta.url);
const {transformSync,version}=require('esbuild');
const app=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const root=path.resolve(app,'../..');
const withinRoot=file=>{const relative=path.relative(root,file);return relative!== '..'&&!relative.startsWith('..'+path.sep)&&!path.isAbsolute(relative);};
const trace=process.env.CIRVEN_TEST_MODULE_TRACE;
const hash=bytes=>createHash('sha256').update(bytes).digest('hex');
if(trace&&!path.isAbsolute(trace))throw Error('CIRVEN_TEST_MODULE_TRACE must be an absolute private test path');
function record(value){if(trace)fs.appendFileSync(trace,JSON.stringify(value)+'\n',{mode:0o600});}
record({kind:'TEST_COMPILER',pid:process.pid,node:process.version,compiler:'esbuild',version,
  package_path:fs.realpathSync(require.resolve('esbuild/package.json')),
  package_sha256:hash(fs.readFileSync(require.resolve('esbuild/package.json')))});

registerHooks({
  resolve(specifier,context,nextResolve){
    try{return nextResolve(specifier,context);}
    catch(error){
      if(error.code!=='ERR_MODULE_NOT_FOUND'||!specifier.startsWith('.')||!context.parentURL?.startsWith('file:'))throw error;
      const url=new URL(specifier,context.parentURL);
      if(url.search||url.hash)throw error;
      const file=fileURLToPath(url);
      if(!withinRoot(file)||path.extname(file))throw error;
      for(const candidate of [file+'.ts',file+'.tsx',path.join(file,'index.ts')]){
        if(fs.existsSync(candidate)&&fs.statSync(candidate).isFile())return nextResolve(pathToFileURL(candidate).href,context);
      }
      throw error;
    }
  },
  load(url,context,nextLoad){
    if(!url.startsWith('file:'))return nextLoad(url,context);
    const file=fileURLToPath(url);
    if(!file.endsWith('.ts')||!withinRoot(file)||file.includes(path.sep+'node_modules'+path.sep))return nextLoad(url,context);
    const raw=fs.readFileSync(file);
    const result=transformSync(raw.toString('utf8'),{loader:'ts',format:'esm',target:'node22',sourcefile:file,sourcemap:'inline'});
    record({kind:'TEST_TYPESCRIPT_MODULE',pid:process.pid,path:file,sha256:hash(raw),bytes:raw.length});
    return {format:'module',source:result.code,shortCircuit:true};
  }
});

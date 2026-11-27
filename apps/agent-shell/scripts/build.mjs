import { readFileSync, existsSync, mkdirSync, symlinkSync, copyFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { applyUpstreamPatches } from './apply-upstream-patches.mjs';
const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');const upstream=resolve(app,'../../staging-native-ui/upstream');
const manifest=JSON.parse(readFileSync(resolve(upstream,'../source-manifest.json'),'utf8'));
for(const file of manifest.files){const bytes=readFileSync(resolve(upstream,file.path));if(bytes.length!==file.bytes||createHash('sha256').update(bytes).digest('hex')!==file.sha256)throw new Error(`固定上游源变化: ${file.path}`);}
applyUpstreamPatches();
const modules=resolve(app,'node_modules');
const link=(target,location)=>{if(!existsSync(location)){mkdirSync(dirname(location),{recursive:true});const windows=process.platform==='win32';symlinkSync(windows?target:relative(dirname(location),target),location,windows?'junction':'dir');}};
link(modules,resolve(upstream,'node_modules'));
for(const name of ['app','ui','session-ui','schema','core'])link(resolve(upstream,'packages',name),resolve(modules,'@opencode-ai',name));
const assets=resolve(app,'public/native-assets');mkdirSync(assets,{recursive:true});
for(const name of ['Inter.ttf','JetBrainsMonoNerdFontMono-Regular.woff2'])copyFileSync(resolve(upstream,'packages/ui/src/assets/fonts',name),resolve(assets,name));
const patchNotices=resolve(app,'public/licenses/upstream-patches');mkdirSync(patchNotices,{recursive:true});
for(const name of ['pierre-trees-1.0.0-beta.4.patch','pierre-trees-manifest.json'])copyFileSync(resolve(app,'patches',name),resolve(patchNotices,name));
const result=spawnSync(process.execPath,[resolve(modules,'vite/bin/vite.js'),'build'],{cwd:app,stdio:'inherit',env:process.env});process.exit(result.status??1);

import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';
import {build} from 'esbuild';
import {authoredDictionary,createProductUIOverlay} from '../scripts/product-ui-overlay.mjs';

const app=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const upstream=resolve(app,'../../staging-native-ui/upstream');
const brand=JSON.parse(readFileSync(resolve(app,'../../packages/branding/product.json'),'utf8'));
const manifest=JSON.parse(readFileSync(resolve(app,'patches/product-ui-copy.json'),'utf8'));
const upstreamManifest=resolve(upstream,'../source-manifest.json');
const sha=value=>createHash('sha256').update(value).digest('hex');
const placeholders=value=>[...value.matchAll(/\{\{[^{}]+\}\}/g)].map(match=>match[0]).sort();
const keyValues=(code,name)=>Object.fromEntries([...authoredDictionary(code,name).entries].map(([key,item])=>[key,item.value]));
const source=target=>readFileSync(resolve(upstream,target.path),'utf8');

test('overlay reads three actual pinned dictionaries, retains exact keys/placeholders and leaves upstream/license bytes unchanged',()=>{
  const overlay=createProductUIOverlay(),beforeManifest=readFileSync(upstreamManifest);
  const license=readFileSync(resolve(upstream,'LICENSE'));
  const pinned=JSON.parse(beforeManifest).files;
  assert.equal(manifest.targets.reduce((sum,target)=>sum+Object.keys(target.messages).length,0),100);
  for(const target of manifest.targets){
    const code=source(target),before=keyValues(code,target.export_name);
    assert.equal(sha(code),target.source_sha256);
    assert.equal(pinned.find(file=>file.path===target.path).sha256,target.source_sha256);
    const result=overlay.transform(code,resolve(upstream,target.path)),after=keyValues(result,target.export_name);
    assert.deepEqual(Object.keys(after),Object.keys(before));
    for(const [key,value]of Object.entries(before)){
      assert.deepEqual(placeholders(after[key]),placeholders(value),key);
      if(!Object.hasOwn(target.messages,key))assert.equal(after[key],value,key);
    }
    assert.doesNotMatch(Object.values(after).filter(value=>!/(opencode\.json|opencode\.ai\/zen)/.test(value)).join('\n'),/OpenCode|\bopencode\b/);
    assert.equal(readFileSync(resolve(upstream,target.path),'utf8'),code);
    assert.equal(overlay.transform(code,resolve(upstream,target.path)+'?t=123'),result);
  }
  assert.deepEqual(readFileSync(upstreamManifest),beforeManifest);
  assert.deepEqual(readFileSync(resolve(upstream,'LICENSE')),license);
  assert.equal(sha(license),pinned.find(file=>file.path==='LICENSE').sha256);
  assert.deepEqual(readFileSync(resolve(app,'public/licenses/OpenCode-MIT.txt')),license);
  assert.match(license.toString(),/Copyright \(c\) 2025 opencode/);
});

async function compiledDictionary(locale,overlay){
  const result=await build({entryPoints:[resolve(upstream,`packages/app/src/i18n/${locale}.ts`)],
    bundle:true,write:false,format:'cjs',platform:'node',target:'es2022',logLevel:'silent',
    plugins:overlay?[{name:'actual-product-copy',setup(builder){builder.onLoad({filter:/\/i18n\/(?:en|zh|desktop-native)\.ts$/},args=>{
      const code=readFileSync(args.path,'utf8');return {contents:overlay.transform(code,args.path)??code,loader:'ts'};
    });}}]:[]});
  assert.equal(result.errors.length,0);
  const context={module:{exports:{}}};vm.runInNewContext(result.outputFiles[0].text,context);
  return {...context.module.exports.dict};
}

test('actual TS catalogs compile and execute with inherited English desktop copy and complete zh/en key sets intact',async()=>{
  const overlay=createProductUIOverlay();
  for(const locale of ['en','zh']){
    const before=await compiledDictionary(locale),after=await compiledDictionary(locale,overlay);
    assert.deepEqual(Object.keys(after).sort(),Object.keys(before).sort());
    assert.equal(after['app.name.desktop'],brand.short_name);
    assert.equal(after['desktop.menu.app'],brand.short_name);
    assert.match(after['desktop.menu.ariaLabel'],/Cirven/);
    assert.equal(after['provider.connect.opencodeZen.visit.link'],'opencode.ai/zen');
    assert.equal(after['dialog.plugins.empty'],before['dialog.plugins.empty']);
    assert.equal(after['error.chain.checkConfig'],before['error.chain.checkConfig']);
    assert.deepEqual(placeholders(after['provider.connect.apiKey.description']),['{{provider}}','{{provider}}']);
    assert.match(after['provider.connect.apiKey.description'],/Cirven/);
    assert.doesNotMatch(after['provider.connect.opencodeZen.line1'],/Cirven/);
    assert.doesNotMatch(after['sidebar.gettingStarted.line1'],/free|免费/i);
    const edited=new Set(manifest.targets.filter(target=>target.locale===(locale==='zh'?'zh-CN':'en')).flatMap(target=>Object.keys(target.messages)));
    for(const [key,value]of Object.entries(before))if(!edited.has(key))assert.equal(after[key],value,key);
  }
});

test('source drift and missing/changed exact literal fail closed before an overlay can hide incompatible upstream content',()=>{
  const target=manifest.targets[0],code=source(target),file=resolve(upstream,target.path);
  assert.throws(()=>createProductUIOverlay().transform(code+'\n',file),/source changed/);
  for(const altered of ['missing','value']){
    const copy=structuredClone(manifest);
    if(altered==='missing')copy.targets[0].messages['missing.product.key']={expected:'OpenCode',replacement:'@SHORT_NAME@'};
    else copy.targets[0].messages['app.name.desktop'].expected='unexpected upstream value';
    assert.throws(()=>createProductUIOverlay({manifest:copy}).transform(code,file),/literal changed/);
  }
});

test('the manifest cannot change URL/configuration identifiers or placeholders, expand scope, or silently weaken its schema',()=>{
  for(const change of [
    copy=>{copy.targets[0].messages['provider.connect.apiKey.description'].replacement='Connect provider';},
    copy=>{copy.targets[0].messages['provider.connect.opencodeZen.visit.link']={expected:'opencode.ai/zen',replacement:'cirven.example'};},
    copy=>{copy.targets[0].messages['dialog.plugins.empty']={expected:'Plugins configured in opencode.json',replacement:'Plugins configured in cirven.json'};},
    copy=>{copy.targets[0].path='../outside.ts';},
    copy=>{copy.targets[0].locale='fr';},
    copy=>{copy.targets[0].export_name='provider';},
    copy=>{copy.schema_version=2;},
  ]){
    const copy=structuredClone(manifest);change(copy);assert.throws(()=>createProductUIOverlay({manifest:copy}));
  }
});

test('unrelated modules, file-type glyphs, provider IDs, licensing and user content never enter the copy overlay',()=>{
  const overlay=createProductUIOverlay(),user='const user = "OpenCode is in the user document; opencode.json must remain.";';
  for(const file of ['packages/app/src/components/user-content.tsx','packages/ui/src/components/provider-icon.tsx',
      'packages/ui/src/components/file-icon.tsx','LICENSE','packages/app/src/i18n/fr.ts']){
    assert.equal(overlay.transform(user,resolve(upstream,file)),null);
  }
  assert.equal(overlay.transform(user,resolve(app,'src/i18n/en.ts')),null);
  assert.throws(()=>authoredDictionary('export const dict = {"a":"one","a":"two"}','dict'),/unique/);
  assert.throws(()=>authoredDictionary('export const dict = {"a":getUserContent()}','dict'),/literal/);
});

test('application Vite integration retains canonical logo alias, overlays before Solid and binds implementation to UI source digest',async()=>{
  const {default:config}=await import('../vite.config.mjs');
  const plugin=config.plugins.find(value=>value.name==='eeas-fixed-upstream-source');
  assert.equal(plugin.enforce,'pre');
  assert.equal(plugin.resolveId('@opencode-ai/ui/logo'),resolve(app,'src/BrandLogo.tsx'));
  const target=manifest.targets[0],changed=plugin.transform(source(target),resolve(upstream,target.path));
  assert.equal(keyValues(changed,'dict')['app.name.desktop'],brand.short_name);
  assert.equal(plugin.transform('url(/assets/Inter.ttf)',resolve(upstream,'packages/app/src/index.css')),'url(/agent-shell/native-assets/Inter.ttf)');
  assert.match(readFileSync(resolve(app,'vite.config.mjs'),'utf8'),/digest\.update\(productUICopy\.implementation_sha256\)/);
});

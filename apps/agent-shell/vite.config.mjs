import { defineConfig } from 'vite';
import solid from 'vite-plugin-solid';
import tailwind from '@tailwindcss/vite';
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve, dirname, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createProductUIOverlay } from './scripts/product-ui-overlay.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const brandingDirectory = resolve(here, '../../packages/branding');
const productBrand = JSON.parse(readFileSync(resolve(brandingDirectory, 'product.json'), 'utf8'));
const iconBytes = readFileSync(resolve(brandingDirectory, 'icon.svg'));
const htmlText = value => String(value).replace(/[&<>"']/g, character => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' })[character]);
const brandMetadata = { name:'eeas-product-metadata', transformIndexHtml:{order:'pre',handler(html){
  const marker='<title>__PRODUCT_DISPLAY_NAME__</title>';
  if(html.split(marker).length!==2)throw new Error('Product title marker must occur exactly once');
  return html.replace(marker,`<title>${htmlText(productBrand.display_name)}</title>`).replace('</head>',`<link rel="icon" type="image/svg+xml" href="data:image/svg+xml;base64,${iconBytes.toString('base64')}"/></head>`);
}}};
const upstream = resolve(here, '../../staging-native-ui/upstream');
const productUICopy = createProductUIOverlay({upstreamRoot:upstream});
const names = ['app', 'ui', 'session-ui', 'schema', 'core'];
const packages = names.map(name => ({ directory: resolve(upstream, 'packages', name), manifest: JSON.parse(readFileSync(resolve(upstream, 'packages', name, 'package.json'), 'utf8')) }));
const dependencies = Object.keys(JSON.parse(readFileSync(resolve(here, 'package.json'), 'utf8')).dependencies).filter(name=>!['motion-dom','motion-utils'].includes(name));
const digest=createHash('sha256');function hashSource(directory){for(const name of readdirSync(directory,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){const path=resolve(directory,name.name);if(name.isDirectory())hashSource(path);else{digest.update(path.slice(here.length));digest.update(readFileSync(path));}}}hashSource(resolve(here,'src'));hashSource(resolve(here,'patches'));digest.update(readFileSync(resolve(here,'package-lock.json')));for(const name of ['product.json','icon.svg']){digest.update('packages/branding/'+name);digest.update(readFileSync(resolve(brandingDirectory,name)));}
function hashSharedSource(directory,base=directory){for(const entry of readdirSync(directory,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){const file=resolve(directory,entry.name);if(entry.isDirectory())hashSharedSource(file,base);else{digest.update('packages/i18n/'+relative(base,file).replaceAll('\\','/'));digest.update(readFileSync(file));}}}
hashSharedSource(resolve(here,'../../packages/i18n'));
digest.update('scripts/product-ui-overlay.mjs');digest.update(productUICopy.implementation_sha256);
const uiSourceHash=digest.digest('hex');
function originalSources() {
  return { name: 'eeas-fixed-upstream-source', enforce: 'pre', resolveId(id) {
    if(id==='@opencode-ai/ui/logo')return resolve(here,'src/BrandLogo.tsx');
    if(id==='@opencode-ai/ui/provider-icon')return resolve(here,'src/ProductProviderIcon.tsx');
    for (const item of packages) {
      const name = item.manifest.name;
      if (id !== name && !id.startsWith(name + '/')) continue;
      const sub = id === name ? '.' : './' + id.slice(name.length + 1);
      for (const [key, destination] of Object.entries(item.manifest.exports ?? {}).sort((a,b) => b[0].length - a[0].length)) {
        if (typeof destination !== 'string') continue;
        const wildcard = key.indexOf('*');
        let middle;
        if (wildcard < 0) { if (key !== sub) continue; middle = ''; }
        else { const prefix = key.slice(0, wildcard), suffix = key.slice(wildcard + 1); if (!sub.startsWith(prefix) || !sub.endsWith(suffix)) continue; middle = sub.slice(prefix.length, suffix ? -suffix.length : undefined); }
        const path = resolve(item.directory, destination.replace('*', middle));
        if (existsSync(path)) return path;
      }
      throw new Error('固定上游 source export 不存在: ' + id);
    }
  },transform(code,id){const branded=productUICopy.transform(code,id);if(branded!==null)return branded;if(id.endsWith('/packages/app/src/index.css'))return code.replaceAll('/assets/JetBrainsMonoNerdFontMono-Regular.woff2','/agent-shell/native-assets/JetBrainsMonoNerdFontMono-Regular.woff2').replaceAll('/assets/Inter.ttf','/agent-shell/native-assets/Inter.ttf');}};
}
export default defineConfig({ base: '/agent-shell/', plugins: [brandMetadata, originalSources(), solid(), tailwind()], resolve: { dedupe: dependencies, alias: { '@': resolve(upstream, 'packages/app/src') } }, define: { '__EEAS_UI_SOURCE_SHA__':JSON.stringify(uiSourceHash),'import.meta.env.VITE_OPENCODE_CHANNEL': JSON.stringify('dev') }, worker: { format: 'es' }, build: { target: 'es2022', sourcemap: process.env.EEAS_UI_DEBUG_MAPS==='1' }, server: { host: '127.0.0.1', strictPort: true } });

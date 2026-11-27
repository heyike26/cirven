/** Application build overlay. Fixed upstream files are read, never rewritten. */
import {readFileSync,realpathSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {dirname, relative, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import ts from 'typescript';

const here=dirname(fileURLToPath(import.meta.url));
const root=resolve(here,'../../..');
const allowedTargets=new Map([
  ['packages/app/src/i18n/en.ts',{export_name:'dict',locale:'en'}],
  ['packages/app/src/i18n/zh.ts',{export_name:'dict',locale:'zh-CN'}],
  ['packages/app/src/i18n/desktop-native.ts',{export_name:'DESKTOP_NATIVE_ENGLISH',locale:'en'}],
]);
const sha=value=>createHash('sha256').update(value).digest('hex');
const placeholders=value=>[...value.matchAll(/\{\{[^{}]+\}\}/g)].map(match=>match[0]).sort();
const technicalReferences=value=>[...value.matchAll(/https?:\/\/[^\s"'<>]+|opencode\.ai(?:\/[^\s"'<>]*)?|opencode\.json/g)].map(match=>match[0]).sort();
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);

export function authoredDictionary(code,exportName,file='product-ui.ts'){
  const source=ts.createSourceFile(file,code,ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);
  if(source.parseDiagnostics.length)throw new Error('Product copy source must be valid TypeScript');
  const candidates=[];
  for(const statement of source.statements){
    if(!ts.isVariableStatement(statement))continue;
    if(!statement.modifiers?.some(modifier=>modifier.kind===ts.SyntaxKind.ExportKeyword))continue;
    for(const declaration of statement.declarationList.declarations){
      if(!ts.isIdentifier(declaration.name)||declaration.name.text!==exportName)continue;
      let value=declaration.initializer;
      while(value&&(ts.isAsExpression(value)||ts.isSatisfiesExpression(value)||ts.isParenthesizedExpression(value)))value=value.expression;
      if(!value||!ts.isObjectLiteralExpression(value))throw new Error('Product copy export must be an object literal');
      candidates.push(value);
    }
  }
  if(candidates.length!==1)throw new Error('Product copy export must occur exactly once');
  const entries=new Map();
  for(const property of candidates[0].properties){
    if(ts.isSpreadAssignment(property))continue;
    if(!ts.isPropertyAssignment(property)||!ts.isStringLiteral(property.name)||!ts.isStringLiteral(property.initializer))throw new Error('Product copy properties must remain literal authored strings');
    if(entries.has(property.name.text))throw new Error('Product copy keys must be unique');
    entries.set(property.name.text,{value:property.initializer.text,start:property.initializer.getStart(source),end:property.initializer.end});
  }
  return {source,entries};
}

export function createProductUIOverlay({upstreamRoot=resolve(root,'staging-native-ui/upstream'),
    manifest=JSON.parse(readFileSync(resolve(here,'../patches/product-ui-copy.json'),'utf8')),
    brand=JSON.parse(readFileSync(resolve(root,'packages/branding/product.json'),'utf8'))}={}){
  if(manifest.schema_version!==1||manifest.scope!=='AUTHORED_APP_COPY_ONLY_ZH_EN'||
      manifest.upstream_commit!=='733b2c627e5aec39cb33cc22373bdce33053ef2c'||!Array.isArray(manifest.targets)||manifest.targets.length!==3)throw new Error('Product copy overlay manifest is unsupported');
  if(!brand||!['display_name','short_name'].every(key=>typeof brand[key]==='string'&&brand[key].length>0))throw new Error('Canonical product names are required');
  const targets=new Map();
  for(const target of manifest.targets){
    const allowed=allowedTargets.get(target.path);
    if(!allowed||targets.has(target.path)||target.export_name!==allowed.export_name||target.locale!==allowed.locale||
        !/^[a-f0-9]{64}$/.test(target.source_sha256)||!target.messages||typeof target.messages!=='object'||Array.isArray(target.messages))throw new Error('Product copy target is outside the exact authored dictionary allowlist');
    const messages=new Map();
    for(const [key,item]of Object.entries(target.messages)){
      if(!item||Object.keys(item).sort().join(',')!=='expected,replacement'||typeof item.expected!=='string'||typeof item.replacement!=='string')throw new Error('Product copy replacement must carry its exact expected literal');
      const value=item.replacement.replaceAll('@DISPLAY_NAME@',brand.display_name).replaceAll('@SHORT_NAME@',brand.short_name);
      if(/@[A-Z_]+@/.test(value)||!same(placeholders(item.expected),placeholders(value))||
          !same(technicalReferences(item.expected),technicalReferences(value)))throw new Error('Product copy cannot change placeholders, technical filenames or URLs');
      messages.set(key,{expected:item.expected,value});
    }
    if(!messages.size)throw new Error('Product copy target needs explicit replacements');
    targets.set(target.path,{...target,messages});
  }
  // Vite/esbuild canonicalize dependency symlinks; both IDs must refer to
  // this one explicitly selected root, rather than suffix-matching any file.
  const upstreamRoots=[...new Set([resolve(upstreamRoot),realpathSync(upstreamRoot)])];
  return Object.freeze({
    implementation_sha256:sha(readFileSync(fileURLToPath(import.meta.url))),
    transform(code,id){
      const file=resolve(id.split('?')[0]);
      const selected=upstreamRoots.map(upstream=>relative(upstream,file).replaceAll('\\','/')).find(path=>targets.has(path));
      const target=targets.get(selected);
      if(!target)return null;
      if(sha(code)!==target.source_sha256)throw new Error(`Fixed product copy source changed: ${selected}`);
      const before=authoredDictionary(code,target.export_name,selected),edits=[];
      for(const [key,item]of target.messages){
        const property=before.entries.get(key);
        if(!property||property.value!==item.expected)throw new Error(`Fixed product copy literal changed: ${selected} / ${key}`);
        edits.push({start:property.start,end:property.end,value:JSON.stringify(item.value)});
      }
      let result=code;
      for(const edit of edits.sort((a,b)=>b.start-a.start))result=result.slice(0,edit.start)+edit.value+result.slice(edit.end);
      const after=authoredDictionary(result,target.export_name,selected);
      if(!same([...before.entries.keys()],[...after.entries.keys()]))throw new Error('Product copy key integrity changed');
      for(const [key,item]of before.entries){
        if(after.entries.get(key).value!==(target.messages.get(key)?.value??item.value))throw new Error('Product copy changed an unrelated value');
      }
      return result;
    },
  });
}

'use strict';
const locales=require('./locales.json');
const namespaces=new Set(['common','host','vscode','renderer','renderer-agent','main','bootstrap','package-copy']);
const cache=new Map();
function resolveLocale(preference='system',systemLocale='en'){
  if(!locales.preference_options.includes(preference))throw new Error('Unsupported UI language preference');
  return preference==='system'?(/^zh(?:-|_|$)/i.test(String(systemLocale))?'zh-CN':'en'):preference;
}
function localeInfo(preference='system',systemLocale='en'){return {preference,resolved_locale:resolveLocale(preference,systemLocale)};}
function catalog(namespace){
  if(!namespaces.has(namespace))throw new Error('Unknown UI language namespace');
  if(!cache.has(namespace)){
    const value=require('./'+namespace+'.json');
    if(value.schema_version!==1||!value.messages||Array.isArray(value.messages))throw new Error('Invalid UI language catalog');
    for(const pair of Object.values(value.messages)){
      if(!pair||!locales.supported.every(locale=>typeof pair[locale]==='string'))throw new Error('Incomplete bilingual UI message');
      Object.freeze(pair);
    }
    Object.freeze(value.messages);cache.set(namespace,Object.freeze(value));
  }
  return cache.get(namespace);
}
function t(namespace,key,locale,params={}){
  if(!locales.supported.includes(locale))throw new Error('A resolved UI locale is required');
  const text=catalog(namespace).messages[key]?.[locale]??String(key);
  return text.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g,(match,name)=>
    Object.hasOwn(params,name)&&['string','number'].includes(typeof params[name])?String(params[name]):match);
}
function browserCatalog(namespace){return catalog(namespace).messages;}
function htmlCatalog(namespace){return JSON.stringify(browserCatalog(namespace)).replace(/[&<>"']/g,
  c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
module.exports={SUPPORTED_LOCALES:Object.freeze([...locales.supported]),PREFERENCES:Object.freeze([...locales.preference_options]),resolveLocale,localeInfo,t,catalog,browserCatalog,htmlCatalog};

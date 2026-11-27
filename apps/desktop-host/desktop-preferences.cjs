'use strict';
const fs=require('node:fs');
const {randomBytes}=require('node:crypto');
const UI_LANGUAGES=Object.freeze(['system','zh-CN','en']);
const SCHEMA='eeas.desktop-preferences.v1';
function readPreferences(filename){
  try{
    const raw=fs.readFileSync(filename,'utf8');
    if(Buffer.byteLength(raw)>4096)return {schema:SCHEMA};
    const value=JSON.parse(raw);
    return value&&typeof value==='object'&&!Array.isArray(value)&&value.schema===SCHEMA?value:{schema:SCHEMA};
  }catch{return {schema:SCHEMA};}
}
function writePreference(filename,key,value){
  if(!((key==='ui_language'&&UI_LANGUAGES.includes(value))||(['assistant_orb_enabled','companion_pinned'].includes(key)&&typeof value==='boolean')))
    throw new TypeError('A supported desktop preference is required');
  // Both setters are synchronous in the one Main owner. Merge before writing,
  // so a language change cannot reset Orb or a future persisted preference.
  const next={...readPreferences(filename),[key]:value};
  const bytes=JSON.stringify(next)+'\n';
  if(Buffer.byteLength(bytes)>4096)throw new Error('Desktop preferences exceed the local size limit');
  const temporary=filename+'.'+randomBytes(12).toString('hex')+'.next';
  try{
    fs.writeFileSync(temporary,bytes,{encoding:'utf8',flag:'wx',mode:0o600});
    fs.renameSync(temporary,filename);
  }catch(error){
    try{fs.unlinkSync(temporary);}catch{}
    throw error;
  }
  return next;
}
module.exports={UI_LANGUAGES,readPreferences,writePreference};

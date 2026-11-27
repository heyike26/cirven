'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),os=require('node:os');
const {readPreferences,writePreference}=require('../desktop-preferences.cjs');
function fixture(t){const directory=fs.mkdtempSync(path.join(os.tmpdir(),'cirven-language-'));t.after(()=>fs.rmSync(directory,{recursive:true,force:true}));return path.join(directory,'desktop-preferences.json');}
test('language and Orb share a durable preference without overwriting each other or future values',t=>{
 const filename=fixture(t);
 fs.writeFileSync(filename,JSON.stringify({schema:'eeas.desktop-preferences.v1',assistant_orb_enabled:false,future_option:{keep:'user'}}));
 writePreference(filename,'ui_language','en');
 let saved=JSON.parse(fs.readFileSync(filename));
 assert.equal(saved.assistant_orb_enabled,false);assert.equal(saved.ui_language,'en');assert.deepEqual(saved.future_option,{keep:'user'});
 writePreference(filename,'assistant_orb_enabled',true);
 saved=readPreferences(filename);assert.equal(saved.ui_language,'en');assert.equal(saved.assistant_orb_enabled,true);
 writePreference(filename,'ui_language','zh-CN');
 assert.equal(JSON.parse(fs.readFileSync(filename)).ui_language,'zh-CN');
 assert.deepEqual(fs.readdirSync(path.dirname(filename)),['desktop-preferences.json']);
});
test('invalid language and unrecognized writes leave the actual preference bytes intact',t=>{
 const filename=fixture(t);writePreference(filename,'ui_language','system');const before=fs.readFileSync(filename);
 for(const value of [null,'fr','zh-cn',{},true,'en\n'])assert.throws(()=>writePreference(filename,'ui_language',value),TypeError);
 assert.throws(()=>writePreference(filename,'provider_secret','not a preference'),TypeError);
 assert.deepEqual(fs.readFileSync(filename),before);
});
test('failed atomic replacement preserves the old preferences and removes its own temporary file',t=>{
 const filename=fixture(t);writePreference(filename,'ui_language','en');const before=fs.readFileSync(filename);
 t.mock.method(fs,'renameSync',()=>{const error=new Error('original fixture write failure');error.code='EACCES';throw error;});
 assert.throws(()=>writePreference(filename,'ui_language','zh-CN'),/original fixture/);
 assert.deepEqual(fs.readFileSync(filename),before);assert.deepEqual(fs.readdirSync(path.dirname(filename)),['desktop-preferences.json']);
});
test('malformed or oversized legacy data defaults safely without creating or changing a file',t=>{
 const filename=fixture(t);assert.equal(readPreferences(filename).schema,'eeas.desktop-preferences.v1');assert.equal(fs.existsSync(filename),false);
 for(const raw of ['[1]',JSON.stringify({schema:'other',ui_language:'en'}),'not-json','x'.repeat(4097)]){
  fs.writeFileSync(filename,raw);assert.deepEqual(readPreferences(filename),{schema:'eeas.desktop-preferences.v1'});assert.equal(fs.readFileSync(filename,'utf8'),raw);
 }
});

test('chat pin is an exact durable boolean and merges locale, orb and future preferences',t=>{
 const filename=fixture(t);writePreference(filename,'ui_language','en');writePreference(filename,'assistant_orb_enabled',false);
 writePreference(filename,'companion_pinned',true);assert.equal(readPreferences(filename).companion_pinned,true);
 writePreference(filename,'ui_language','zh-CN');assert.equal(readPreferences(filename).companion_pinned,true);
 writePreference(filename,'companion_pinned',false);const before=fs.readFileSync(filename);
 for(const value of [null,'false',0,1,{},[]])assert.throws(()=>writePreference(filename,'companion_pinned',value),TypeError);
 assert.deepEqual(fs.readFileSync(filename),before);assert.equal(readPreferences(filename).assistant_orb_enabled,false);
});

'use strict';
const test=require('node:test'),assert=require('node:assert/strict');
const {resolveLocale,t,catalog,htmlCatalog}=require('../../../packages/i18n/index.cjs');
test('shared locale contract resolves system language without accepting arbitrary paths or locales',()=>{
  for(const value of ['zh-CN','zh-TW','ZH_hk','zh'])assert.equal(resolveLocale('system',value),'zh-CN');
  assert.equal(resolveLocale('system','fr-FR'),'en');assert.equal(resolveLocale('en','zh-CN'),'en');
  assert.throws(()=>resolveLocale('../../private'),'preference');assert.throws(()=>catalog('../../private'));
  assert.throws(()=>t('host','发送','system'));assert.equal(t('host','发送','en'),'Send');
  assert.equal(t('common','window.main','en',{product:'用户 <正文> $&'}),'用户 <正文> $&');
  assert.equal(t('common','window.main','en',{product:{secret:'not used'}}),'{product}');
});
test('owned catalogs provide both locales with matching placeholders and safe inert HTML transport',()=>{
  for(const ns of ['common','host','vscode'])for(const [key,pair]of Object.entries(catalog(ns).messages)){
    assert.equal(typeof pair['zh-CN'],'string',key);assert.equal(typeof pair.en,'string',key);
    const vars=value=>[...value.matchAll(/\{([A-Za-z][A-Za-z0-9_]*)\}/g)].map(m=>m[1]).sort();
    assert.deepEqual(vars(pair.en),vars(pair['zh-CN']),key);
  }
  assert.doesNotMatch(htmlCatalog('host'),/[<>"']/);assert.match(htmlCatalog('host'),/&quot;/);
});

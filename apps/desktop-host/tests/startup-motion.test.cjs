'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {startupMotion,STARTUP_MOTION_DURATION_MS}=require('../startup-motion.cjs');
const threePart='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><g data-motion-part="outer-C" fill="#123456"><path d="M4 4H18V9H9V23H18V28H4Z"/></g><g fill="#FEDCBA" data-motion-part="inner-fold"><path d="M14 9 21 20 27 9H31L23 25H19L10 9Z"/></g><g data-motion-part="lower-sweep" fill="#1A9988"><path d="M14 24H26V28H14Z"/></g></svg>';
const originalSVG=markup=>markup.slice(markup.indexOf('<svg'),markup.lastIndexOf('</svg>')+6).replace(/<g data-eeas-motion-part="[^"]+">([\s\S]*?)<\/g><\/g>/g,'$1</g>');
test('startup presentation reuses injected local SVG bytes and palette without any runtime asset access',()=>{
  const value=startupMotion({svg:threePart,brand:'Cirven Agent'});
  assert.equal(originalSVG(value.markup),threePart);assert.match(value.markup,/data-structure="three-part"/);assert.match(value.markup,/data-opening="true"/);
  assert.equal(STARTUP_MOTION_DURATION_MS,680);assert.doesNotMatch(value.css,/#[\da-f]{3,8}\b|\bfill\s*:|\bstroke\s*:/i);
  const module={exports:{}};vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../startup-motion.cjs'),'utf8'),{module,Buffer,require:()=>assert.fail('pure presentation cannot import a runtime dependency'),setTimeout:()=>assert.fail('no startup timers'),setInterval:()=>assert.fail('no polling'),fetch:()=>assert.fail('no network')});
  assert.equal(module.exports.startupMotion({svg:threePart,brand:'Cirven Agent'}).markup,value.markup);
});
test('CONNECTING/stable state opts out and unknown or duplicate groups use the intact whole-mark fallback',()=>{
  assert.match(startupMotion({svg:threePart,brand:'Cirven',opening:false}).markup,/data-opening="false"/);
  for(const svg of [threePart.replace('lower-sweep','other'),threePart.replace('lower-sweep','outer-C'),'<svg viewBox="0 0 24 24"><path d="M2 2H20V20H2Z"/></svg>']){
    const view=startupMotion({svg,brand:'Cirven'});assert.match(view.markup,/data-structure="whole-mark"/);assert.ok(view.markup.includes(svg));
  }
});
test('the current canonical vector can be supplied unchanged without selecting or writing a replacement',()=>{
  const filename=path.resolve(__dirname,'../../../packages/branding/icon.svg'),svg=fs.readFileSync(filename,'utf8').trim(),before=fs.readFileSync(filename);
  const result=startupMotion({svg,brand:'Cirven Agent'});assert.equal(originalSVG(result.markup),svg);assert.deepEqual(fs.readFileSync(filename),before);
});
test('motion wrappers preserve original nested geometry and never animate normalization transforms',()=>{
  const source=threePart.replace('<path d="M4','<g transform="translate(1000 2000) scale(.028)"><path d="M4').replace('Z"/></g>','Z"/></g></g>');
  const view=startupMotion({svg:source,brand:'Cirven'});
  assert.match(view.markup,/<g data-eeas-motion-part="outer-C"><g data-motion-part="outer-C" fill="#123456"><g transform="translate\(1000 2000\) scale\(\.028\)">/);
  assert.equal((view.markup.match(/data-eeas-motion-part=/g)||[]).length,3);
  assert.doesNotMatch(view.css,/\]\s*g\[data-motion-part/);
  assert.ok(view.markup.includes('<path d="M4 4H18V9H9V23H18V28H4Z"/></g></g></g>'));
});
test('brand accessibility labels are escaped and cannot create controls or steal focus',()=>{
  const result=startupMotion({svg:threePart,brand:'A <B> & "C" \'D\''});
  assert.match(result.markup,/aria-label="A &lt;B&gt; &amp; &quot;C&quot; &#39;D&#39;"/);assert.match(result.markup,/role="img"/);assert.doesNotMatch(result.markup,/tabindex|autofocus|<button|<input|<script/i);
});
test('invalid input or an active/external SVG is refused before it can enter a startup document',()=>{
  for(const svg of ['',null,'<svg/>','<svg></svg><svg></svg>','<svg><script>alert(1)</script></svg>','<svg onload="alert(1)"></svg>','<svg><image href="file:///private"/></svg>','<svg><foreignObject/></svg>','<svg><animate attributeName="opacity" repeatCount="indefinite"/></svg>','<svg><path fill="url(https://example.invalid)"/></svg>','<svg><style>@import "x";</style></svg>','<svg>'+(' '.repeat(32768))+'</svg>'])assert.throws(()=>startupMotion({svg,brand:'Cirven'}),/LOCAL_SVG_REQUIRED/);
  for(const options of [{brand:''},{brand:'\0'},{brand:'a'.repeat(161)},{brand:'Cirven',opening:'false'},{brand:42}])assert.throws(()=>startupMotion({svg:threePart,...options}),/INPUT_INVALID/);
  const gradient='<svg viewBox="0 0 32 32"><defs><linearGradient id="local"><stop offset="0" stop-color="#146"/></linearGradient></defs><path fill="url(#local)" d="M1 1H20V20Z"/></svg>';
  assert.ok(startupMotion({svg:gradient,brand:'Cirven'}).markup.includes(gradient));
});
test('motion stays bounded and scoped; system preference removes every animation without changing SVG paint',()=>{
  const {css}=startupMotion({svg:threePart,brand:'Cirven'});
  assert.doesNotMatch(css,/infinite|rotate\(|filter:|will-change|animation-delay|\b(?:body|html|:root)\b/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);assert.match(css,/animation:none;transition:none/);
  assert.equal((css.match(/@keyframes eeasStartupMotion/g)||[]).length,5);
  assert.doesNotMatch(css,/\b(?:width|height|left|top|margin|padding)\s*:[^;}]*\b(?:ms|s)\b/);
});
module.exports={threePart};

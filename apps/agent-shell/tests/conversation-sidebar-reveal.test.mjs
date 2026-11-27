import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import ts from 'typescript';
const text=await readFile(new URL('../src/conversation-sidebar-reveal.ts',import.meta.url),'utf8');
const js=ts.transpileModule(text,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText;
const {createSidebarSelectionReveal,revealSidebarSelection}=await import('data:text/javascript;base64,'+Buffer.from(js).toString('base64'));
function setup(){
  let active='old',next=0;const frames=new Map(),revealed=[];
  const control=createSidebarSelectionReveal({activeId:()=>active,reveal:value=>revealed.push(value),schedule:fn=>{frames.set(++next,fn);return next;},cancelFrame:id=>frames.delete(id)});
  return {control,revealed,setActive:value=>{active=value;},flush(){for(const [id,fn]of [...frames]){frames.delete(id);fn();}}};
}
test('only a successful explicit selection whose active identity changed owns a one-shot reveal',()=>{
  const f=setup(),ticket=f.control.begin('qa25','source72');f.control.complete(ticket);f.flush();assert.equal(f.revealed.length,0);
  f.setActive('qa25');f.control.complete(ticket);f.flush();assert.deepEqual(f.revealed,[ticket]);
  f.control.complete(ticket);f.flush();assert.equal(f.revealed.length,1);
  assert.equal(f.control.begin('qa25','source72'),undefined);
});
test('a later selection or a rejected old selection cannot scroll the newer conversation',()=>{
  const f=setup(),old=f.control.begin('qa25','source72'),current=f.control.begin('other','project-b');
  f.setActive('other');f.control.complete(current);f.control.cancel(old);f.control.complete(old);f.flush();assert.deepEqual(f.revealed,[current]);
});
test('user scrolling, failure and unmount cancel pending or already scheduled correction',()=>{
  for(const stage of ['pending','scheduled','dispose']){
    const f=setup(),ticket=f.control.begin('qa25','source72');f.setActive('qa25');
    if(stage!=='pending')f.control.complete(ticket);
    if(stage==='dispose')f.control.dispose();else f.control.cancel();
    f.control.complete(ticket);f.flush();assert.equal(f.revealed.length,0,stage);
  }
});
test('an active identity change after scheduling, and metadata-only refreshes, never follow the old selection',()=>{
  const f=setup(),ticket=f.control.begin('qa25','source72');f.setActive('qa25');f.control.complete(ticket);f.setActive('other');f.flush();assert.equal(f.revealed.length,0);
  f.setActive('qa25');f.flush();assert.equal(f.revealed.length,0);
});
test('a scheduler or DOM failure after successful selection remains auxiliary and is consumed',()=>{
  let active='old',callback,reveals=0;
  const scheduling=createSidebarSelectionReveal({activeId:()=>active,reveal:()=>reveals++,schedule:()=>{throw Error('scheduler unavailable');}});
  const first=scheduling.begin('qa25','source72');active='qa25';assert.doesNotThrow(()=>scheduling.complete(first));assert.equal(active,'qa25');
  const rendering=createSidebarSelectionReveal({activeId:()=>active,reveal:()=>{reveals++;throw Error('row unmounted during layout');},schedule:fn=>{callback=fn;return 1;},cancelFrame:()=>{}});
  active='old';const second=rendering.begin('qa25','source72');active='qa25';rendering.complete(second);assert.doesNotThrow(()=>callback());rendering.complete(second);assert.equal(reveals,1);
});
test('cancellation failure cannot block a new selection or leave an unmounted callback owned',()=>{
  let active='old',callback,reveals=0;
  const f=createSidebarSelectionReveal({activeId:()=>active,reveal:()=>reveals++,schedule:fn=>{callback=fn;return 1;},cancelFrame:()=>{throw Error('cancel failed');}});
  const first=f.begin('qa25','source72');active='qa25';f.complete(first);
  assert.doesNotThrow(()=>f.begin('other','project-b'));callback();assert.equal(reveals,0);
  const next=f.begin('other','project-b');active='other';f.complete(next);assert.doesNotThrow(()=>f.dispose());callback();assert.equal(reveals,0);
});
function geometry({top=-100,bottom=-60,project='source72',hidden=false,connected=true}={}){
  const rect={top,bottom,height:bottom-top},folder={dataset:{projectKey:project}},row={dataset:{conversationId:'qa25'},getClientRects:()=>[rect],getBoundingClientRect:()=>rect,closest:selector=>selector==='[data-project-key]'?folder:hidden?{}:null};
  return {row,list:{isConnected:connected,clientTop:0,clientHeight:300,scrollTop:600,parentElement:null,getClientRects:()=>[{}],getBoundingClientRect:()=>({top:100,bottom:400}),querySelectorAll:()=>[row]}};
}
test('current-project reorder reveals only the exact row in its own scroll container with minimum delta',()=>{
  const f=geometry();revealSidebarSelection(f.list,'qa25','source72');assert.equal(f.list.scrollTop,392);
  const inside=geometry({top:150,bottom:190});revealSidebarSelection(inside.list,'qa25','source72');assert.equal(inside.list.scrollTop,600);
  const below=geometry({top:400,bottom:440});revealSidebarSelection(below.list,'qa25','source72');assert.equal(below.list.scrollTop,648);
});
test('hidden, removed, wrong-project or duplicate rows cannot move the list or use outer scrolling/focus',()=>{
  for(const opts of [{hidden:true},{connected:false},{project:'other'}]){
    const f=geometry(opts);revealSidebarSelection(f.list,'qa25','source72');assert.equal(f.list.scrollTop,600);
  }
  const f=geometry();f.list.querySelectorAll=()=>[f.row,f.row];revealSidebarSelection(f.list,'qa25','source72');assert.equal(f.list.scrollTop,600);
  assert.doesNotMatch(text,/\.focus\s*\(|scrollIntoView\s*\(/);
});

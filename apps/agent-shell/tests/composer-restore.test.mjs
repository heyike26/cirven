import test from 'node:test';
import assert from 'node:assert/strict';
import {createRoot,createSignal,createEffect,batch} from 'solid-js/dist/solid.js';
import {ComposerDraftPersistence,draftKey,readComposerDraft,hydrateDraftRefs} from '../src/composer-draft.ts';
const ref={id:'actual-record',revision:1,sha256:'a'.repeat(64)};
function storage(){const values=new Map();return{getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,value),values};}

test('actual Solid restore effects cannot replace saved session attachments with the previous new-work empty draft',()=>{
 const local=storage(),raw='原有草稿 😀\r\n完整意见 ';local.setItem(draftKey('project','session'),JSON.stringify({text:raw,feedback_refs:[ref]}));const persistence=new ComposerDraftPersistence(local);
 const ui=createRoot(dispose=>{
  const [session,setSession]=createSignal(),[text,setText]=createSignal(''),[refs,setRefs]=createSignal([]),[ready,setReady]=createSignal(false);
  createEffect(()=>{text();refs();if(ready())persistence.save('project',session(),text(),refs());});
  return{dispose,setSession,setText,setRefs,setReady};
 });
 const {setSession,setText,setRefs,setReady}=ui;
 try{const initial=persistence.begin('project');persistence.initialize(initial);setReady(true);
  // This is the real failure sequence: session changes before the restore read.
  setSession('session');assert.deepEqual(readComposerDraft(local,'project','session'),{text:raw,feedback_refs:[ref]});
  const restore=persistence.begin('project','session');batch(()=>{setReady(false);setText(restore.draft.text);setRefs([]);persistence.initialize(restore);setReady(true);});
  assert.deepEqual(readComposerDraft(local,'project','session'),{text:raw,feedback_refs:[ref]});
  setText(raw+'补充');assert.equal(readComposerDraft(local,'project','session').feedback_refs.length,1);
  const actual=[{feedback_ref:ref,payload:{comment:'批注已保存'}}];const hydrated=hydrateDraftRefs(restore.draft.feedback_refs,actual);persistence.hydrated(restore);setRefs(hydrated.map(value=>value.feedback_ref));assert.deepEqual(readComposerDraft(local,'project','session'),{text:raw+'补充',feedback_refs:[ref]});
 }finally{ui.dispose();}
});
test('late hydration for another project/session or older generation cannot arm or delete the current draft',()=>{
 const local=storage(),store=new ComposerDraftPersistence(local),first=store.begin('p','a',{text:'A',feedback_refs:[ref]});store.initialize(first);const second=store.begin('p','b',{text:'B',feedback_refs:[]});store.initialize(second);assert.equal(store.current(first),false);assert.equal(store.hydrated(first),false);assert.equal(store.save('p','a','wrong',[]),false);store.save('p','b','B',[]);assert.equal(readComposerDraft(local,'p','b').text,'B');
 const repeated=store.begin('p','b',{text:'B新版',feedback_refs:[ref]});store.initialize(repeated);assert.equal(store.hydrated(second),false);assert.equal(store.save('another','b','wrong',[]),false);store.pause();assert.equal(store.hydrated(repeated),false);assert.equal(store.save('p','b','wrong',[]),false);
});
test('temporary feedback fetch failure preserves opaque refs and user edits, then only exact authoritative refs survive hydration',()=>{
 const local=storage(),store=new ComposerDraftPersistence(local),lease=store.begin('p','s',{text:'已保存',feedback_refs:[ref]});store.initialize(lease);assert.equal(store.hasPending('p','s'),true);store.save('p','s','用户继续编辑\r\n',[]);assert.deepEqual(readComposerDraft(local,'p','s'),{text:'用户继续编辑\r\n',feedback_refs:[ref]});
 assert.deepEqual(hydrateDraftRefs(lease.draft.feedback_refs,[{feedback_ref:{...ref,sha256:'b'.repeat(64)}}]),[]);store.hydrated(lease);store.save('p','s','用户继续编辑\r\n',[]);assert.equal(store.hasPending('p','s'),false);assert.deepEqual(readComposerDraft(local,'p','s').feedback_refs,[]);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {FileTree} from '@pierre/trees';
import {ExplorerModel} from '../src/explorer-model.ts';
const row=(path,type='file')=>({path,name:path.split('/').at(-1),type,readable:true,change:'UNCHANGED',deleted:false,comparison_basis:'FIRST_DIRECTORY_OBSERVATION_BYTES',sha256:null,verification:type==='directory'?'DIRECTORY_ENTRY_ONLY':'CONTENT_SHA256',restored:false});
const settle=()=>new Promise(resolve=>setImmediate(resolve));
test('actual upstream model retains user-expanded directory across asynchronous child arrival; repeated loading notifications do not reset paths',async()=>{
 let rows=[row('nested','directory'),row('root.txt')],reset=0;const tree=new FileTree({paths:[],initialExpansion:'closed',flattenEmptyDirectories:false,search:false});const model=new ExplorerModel(tree,()=>rows,()=>{});const off=tree.subscribe(()=>model.observe());tree.onMutation('reset',()=>reset++);
 try{model.schedule();await settle();tree.getItem('nested/').expand();model.schedule();model.schedule();await settle();assert.equal(reset,1);assert.equal(tree.getItem('nested/').isExpanded(),true);rows=[...rows,row('nested/中文 子项.txt')];model.schedule();await settle();assert.equal(reset,2);assert.equal(tree.getItem('nested/').isExpanded(),true);assert.equal(tree.getItem('nested/中文 子项.txt').getPath(),'nested/中文 子项.txt');}finally{model.stop();off();tree.cleanUp();}
});
test('collapse while a request is pending wins over late child data; cleanup discards already queued updates',async()=>{
 let rows=[row('nested','directory')];const tree=new FileTree({paths:[],initialExpansion:'closed',flattenEmptyDirectories:false});const model=new ExplorerModel(tree,()=>rows,()=>{});const off=tree.subscribe(()=>model.observe());
 try{model.schedule();await settle();tree.getItem('nested/').expand();tree.getItem('nested/').collapse();rows=[...rows,row('nested/late.txt')];model.schedule();await settle();assert.equal(tree.getItem('nested/').isExpanded(),false);rows=[row('removed.txt')];model.schedule();model.stop();await settle();assert.equal(tree.getItem('removed.txt'),null);}finally{model.stop();off();tree.cleanUp();}
});
test('same active full-path query refreshes matches for newly loaded Unicode children without closing their ancestor',async()=>{
 let rows=[row('nested','directory'),row('other.txt')];const tree=new FileTree({paths:[],initialExpansion:'closed',flattenEmptyDirectories:false,search:false});const model=new ExplorerModel(tree,()=>rows,()=>{});const off=tree.subscribe(()=>model.observe());
 try{model.schedule();await settle();tree.setSearch('nested/中文 子项.txt');assert.deepEqual(tree.getSearchMatchingPaths(),[]);rows=[...rows,row('nested/中文 子项.txt')];model.schedule();await settle();assert.deepEqual(tree.getSearchMatchingPaths(),['nested/中文 子项.txt']);assert.equal(tree.getItem('nested/').isExpanded(),true);assert.equal(tree.getSearchValue(),'nested/中文 子项.txt');}finally{model.stop();off();tree.cleanUp();}
});

'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../quick-surface-preload.cjs'),'utf8');
const code=source.slice(source.indexOf('function supervisorDisplayMessages('),source.indexOf('\nfunction renderSupervisor('));
function fixture(){
  const nodes=new Map(),node=()=>({style:{},children:[],hidden:false,append(...values){this.children.push(...values);this.firstElementChild=this.children[0];this.lastElementChild=this.children.at(-1);},addEventListener(name,fn){this[name]=fn;},before(value){nodes.set('#'+value.id,value);}});
  nodes.set('#supervisor-history',node());nodes.set('.activity-content',Object.assign(node(),{scrollTop:120}));nodes.set('#supervisor-tasks',{open:true});
  const context={document:{querySelector:key=>nodes.get(key),createElement:node},Date,state:{supervisor:{selection_revision:0}},supervisorSelectionRevision:-1,supervisorSelectionView:null,supervisorFollow:false,supervisorText:key=>key,renderSupervisor:()=>{}};
  vm.createContext(context);vm.runInContext(code+'\nthis.display=supervisorDisplayMessages',context);
  const selected={operation_id:'old',request_sha256:'a'.repeat(64),question:'Original question',answer_text:'Original answer',status:'RETURNED'};
  const snapshot={operations:[{operation_id:'old',request_sha256:selected.request_sha256,status:'RETURNED'}],messages:[{id:'new_user',role:'user',text:'Newer unrelated question',operation_id:'new'},{id:'new_assistant',role:'assistant',text:'Newer unrelated answer',operation_id:'new'}]};
  function select(){context.state.supervisor.selection={...selected};context.state.supervisor.selection_revision++;return context.display(snapshot);}
  return{context,nodes,selected,snapshot,select};
}
test('old operation outside the recent message window displays its exact controlled question and answer',()=>{
  const f=fixture(),view=f.select();assert.deepEqual(Array.from(view.messages,message=>message.text),['Original question','Original answer']);
  assert.equal(f.nodes.get('#supervisor-tasks').open,false);assert.equal(f.context.supervisorFollow,true);
  f.snapshot.messages.push({id:'later_user',role:'user',text:'Follow-up question',operation_id:'follow'});
  assert.deepEqual(Array.from(f.context.display(f.snapshot).messages,message=>message.text),['Original question','Original answer','Follow-up question']);
  assert.equal(f.context.display(f.snapshot).retained.length,5);
});
test('recent discussion dismisses locally; broadcasts stay dismissed and explicit reselection reopens',()=>{
  const f=fixture();f.select();const button=f.nodes.get('#supervisor-selection').lastElementChild;button.click();
  assert.equal(f.context.supervisorSelectionView,null);assert.deepEqual(Array.from(f.context.display(f.snapshot).messages,message=>message.text),['Newer unrelated question','Newer unrelated answer']);
  assert.equal(f.nodes.get('.activity-content').scrollTop,120);assert.equal(f.context.supervisorFollow,false);
  f.context.state.supervisor.selection.answer_text='Updated old answer';assert.equal(f.context.display(f.snapshot).messages.length,2);assert.equal(f.nodes.get('#supervisor-selection').hidden,true);
  f.select();assert.equal(f.nodes.get('#supervisor-selection').hidden,false);
});
test('only exact operation messages supersede selected text; terminal display updates outside history stay readable',()=>{
  const f=fixture();f.selected.status='UNKNOWN';f.selected.answer_text='';f.snapshot.operations[0].status='UNKNOWN';let view=f.select();assert.equal(view.messages[1].status,'UNKNOWN');assert.equal(view.messages[0].status,undefined);
  f.context.state.supervisor.selection.answer_text='Recovered outside recent history';f.context.state.supervisor.selection.status='RETURNED';f.snapshot.operations[0].status='RETURNED';view=f.context.display(f.snapshot);assert.equal(view.messages[1].text,'Recovered outside recent history');
  f.snapshot.messages.push({id:'exact_assistant',role:'assistant',operation_id:'old',text:'Exact newer receipt',status:'FAILED'});view=f.context.display(f.snapshot);assert.equal(view.messages[1].text,'Exact newer receipt');assert.equal(view.messages[1].status,'FAILED');assert.equal(view.messages.some(row=>row.text==='Newer unrelated answer'),false);
});
test('changed operation digest removes selected display instead of associating another task',()=>{
  const f=fixture();f.select();f.snapshot.operations[0].request_sha256='b'.repeat(64);const view=f.context.display(f.snapshot);assert.equal(f.context.supervisorSelectionView,null);assert.equal(f.nodes.get('#supervisor-selection').hidden,true);assert.equal(view.messages[0].id,'new_user');
});
test('recent-window backfill from older operations or older local answers is not treated as a new follow-up',()=>{
  const f=fixture();f.snapshot.operations.push({operation_id:'hidden_old',request_sha256:'b'.repeat(64),status:'RETURNED'});f.select();
  f.snapshot.messages.push({id:'backfill',role:'assistant',text:'Unrelated old history',operation_id:'hidden_old'},{id:'local_old',role:'assistant',text:'Older local status',created_at:'2020-01-01T00:00:00.000Z'});
  assert.deepEqual(Array.from(f.context.display(f.snapshot).messages,message=>message.text),['Original question','Original answer']);
  f.snapshot.messages.push({id:'local_new',role:'assistant',text:'New local status',created_at:new Date(Date.now()+1).toISOString()});
  assert.equal(f.context.display(f.snapshot).messages.at(-1).text,'New local status');
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {DiscussionDraftLease,DiscussionDraftMigrationConflict,discussionDraftKey,migrateConversationDraft,readDraftCopies} from '../src/discussion-drafts.ts';
const store=()=>{const data=new Map();return{data,getItem:key=>data.get(key)??null,setItem:(key,value)=>data.set(key,value)}};
const scope=(id='first',projectId=null)=>({kind:'native',projectId,id});
test('independent histories retain their own draft across project and unbound navigation',()=>{
 const storage=store(),a=new DiscussionDraftLease(storage,scope()),b=new DiscussionDraftLease(storage,scope('second','project'));
 a.save({text:'人工新增文字',attachments:[]});b.save({text:'another draft',attachments:[]});
 assert.equal(new DiscussionDraftLease(storage,scope()).read().text,'人工新增文字');
 assert.equal(new DiscussionDraftLease(storage,scope('second','project')).read().text,'another draft');
 assert.notEqual(discussionDraftKey(scope('same')),discussionDraftKey(scope('same','project')));
});
test('concurrent edits conflict without overwriting either current input or stored input',()=>{
 const storage=store(),a=new DiscussionDraftLease(storage,scope()),b=new DiscussionDraftLease(storage,scope());
 a.save({text:'first writer',attachments:[]});assert.throws(()=>b.save({text:'second writer',attachments:[]}),/CONFLICT/);
 assert.equal(new DiscussionDraftLease(storage,scope()).read().text,'first writer');
 assert.equal(b.read().text,'');
});
test('corrupt attachment and quota or unknown write do not become successful empty draft',()=>{
 const storage=store();storage.setItem(discussionDraftKey(scope()),JSON.stringify({text:'retained',attachments:[{comment:'missing basis'}]}));
 assert.throws(()=>new DiscussionDraftLease(storage,scope()),/UNAVAILABLE/);
 const quota={getItem:()=>null,setItem:()=>{throw new Error('quota')}};
 assert.throws(()=>new DiscussionDraftLease(quota,scope()).save({text:'keep me',attachments:[]}),/quota/);
 const lost={getItem:()=>null,setItem:()=>{}};assert.throws(()=>new DiscussionDraftLease(lost,scope()).save({text:'keep me',attachments:[]}),/UNCONFIRMED/);
});

const canonicalId='native:'+'a'.repeat(64);
test('project move and source changes retain one draft while old source values remain as backups',()=>{
 const storage=store(),a=scope('old','project-a'),b=scope('new','project-b');
 new DiscussionDraftLease(storage,a).save({text:'My unsent draft',attachments:[]});
 const oldRaw=storage.getItem(discussionDraftKey(a)),draft=migrateConversationDraft(storage,canonicalId,[a,b]);
 assert.equal(draft.read().text,'My unsent draft');
 draft.save({text:'Later edit after moving',attachments:[]});
 assert.equal(new DiscussionDraftLease(storage,a).read().text,'Later edit after moving');
 assert.equal(new DiscussionDraftLease(storage,b).read().text,'Later edit after moving');
 assert.equal(storage.getItem(discussionDraftKey(a)),oldRaw);
 draft.save({text:'',attachments:[]});
 assert.equal(migrateConversationDraft(storage,canonicalId,[a,b]).read().text,'');
});
test('a writer opened before migration cannot overwrite the linked canonical draft',()=>{
 const storage=store(),a=scope(),old=new DiscussionDraftLease(storage,a);
 migrateConversationDraft(storage,canonicalId,[a]);
 assert.throws(()=>old.save({text:'Late old-window input',attachments:[]}),/CONFLICT/);
 assert.equal(new DiscussionDraftLease(storage,{kind:'conversation',projectId:null,id:canonicalId}).read().text,'');
});
test('different drafts are retained as a conflict rather than combined or assigned to another project',()=>{
 const storage=store(),a=scope('a'),b=scope('b');
 new DiscussionDraftLease(storage,a).save({text:'First independently edited input',attachments:[]});
 new DiscussionDraftLease(storage,b).save({text:'Second independently edited input',attachments:[]});
 const before=[...storage.data];assert.throws(()=>migrateConversationDraft(storage,canonicalId,[a,b]),/CONFLICT/);
 assert.deepEqual([...storage.data],before);
});
function recoveredConflict(storage,sources){
 try{migrateConversationDraft(storage,canonicalId,sources);}catch(error){
  assert.ok(error instanceof DiscussionDraftMigrationConflict);
  return error;
 }
 assert.fail('Expected a migration conflict');
}
const savedScope={kind:'conversation',projectId:null,id:canonicalId};
const draft=text=>({text,attachments:[]});
test('can continue either version after a cold restart and recovers both original drafts',()=>{
 for(const choice of ['local','saved']){
  const storage=store(),original={kind:'original',projectId:'project-a',id:'old_owner'};
  new DiscussionDraftLease(storage,savedScope).save(draft('Current conversation input'));
  new DiscussionDraftLease(storage,original).save(draft('Existing original worker input'));
  const raw=storage.getItem(discussionDraftKey(original)),conflict=recoveredConflict(storage,[original]);
  assert.equal(conflict.saved.text,'Current conversation input');
  assert.equal(conflict.local.text,'Existing original worker input');
  conflict.resolveDraft(choice);
  const accepted=migrateConversationDraft(storage,canonicalId,[original]);
  assert.deepEqual(accepted.read(),conflict[choice]);
  assert.deepEqual(new DiscussionDraftLease(storage,original).read(),accepted.read());
  assert.equal(storage.getItem(discussionDraftKey(original)),raw);
  const [pair]=readDraftCopies(storage,accepted);
  assert.deepEqual([pair.local,pair.saved],[conflict.local,conflict.saved]);
 }
});
test('an edited source after the dialog opens invalidates the pending choice before either draft is written',()=>{
 const storage=store(),source=scope('older');
 new DiscussionDraftLease(storage,savedScope).save(draft('Already saved here'));
 new DiscussionDraftLease(storage,source).save(draft('An earlier source'));
 const conflict=recoveredConflict(storage,[source]);
 new DiscussionDraftLease(storage,source).save(draft('Newer edit from the other window'));
 const before=[...storage.data];
 assert.throws(()=>conflict.resolveDraft('saved'),/CONFLICT/);
 assert.deepEqual([...storage.data],before);
});
test('additional incompatible sources require their own choice and keep the replaced alternatives',()=>{
 const storage=store(),sources=['first','second','third'].map(id=>scope(id));
 for(const s of sources)new DiscussionDraftLease(storage,s).save(draft(s.id));
 const first=recoveredConflict(storage,sources);
 assert.deepEqual([first.saved.text,first.local.text],['first','second']);
 first.resolveDraft('local');
 const second=recoveredConflict(storage,sources);
 assert.deepEqual([second.saved.text,second.local.text],['second','third']);
 second.resolveDraft('saved');
 const accepted=migrateConversationDraft(storage,canonicalId,sources);
 assert.equal(accepted.read().text,'second');
 assert.deepEqual(readDraftCopies(storage,accepted).map(p=>[p.saved.text,p.local.text]),[['first','second'],['second','third']]);
 assert.deepEqual(sources.map(s=>JSON.parse(storage.getItem(discussionDraftKey(s))).text),['first','second','third']);
});
test('unknown migration link write retains both canonical copy and old text for explicit recovery',()=>{
 const storage=store(),a=scope();new DiscussionDraftLease(storage,a).save({text:'Keep this',attachments:[]});
 const lost={getItem:storage.getItem,setItem:(key,value)=>{if(!key.startsWith('eeas-discussion-draft-link-'))storage.setItem(key,value);}};
 assert.throws(()=>migrateConversationDraft(lost,canonicalId,[a]),/UNCONFIRMED/);
 assert.equal(new DiscussionDraftLease(storage,a).read().text,'Keep this');
 assert.equal(new DiscussionDraftLease(storage,{kind:'conversation',projectId:null,id:canonicalId}).read().text,'Keep this');
 assert.equal(migrateConversationDraft(storage,canonicalId,[a]).read().text,'Keep this');
});
test('live default input migrates only with an exact registered source key and preserves attachments',()=>{
 const storage=store(),a=scope('exact-default'),value={text:'Current default chat',attachments:[{context_ref:{id:'frame',revision:1,sha256:'a'.repeat(64)},label:'Saved frame',comment:'My note',regions:[]}]};
 assert.throws(()=>migrateConversationDraft(storage,canonicalId,[a],{scope:scope('wrong-default'),value}),/SCOPE_REQUIRED/);
 const draft=migrateConversationDraft(storage,canonicalId,[a],{scope:a,value});assert.deepEqual(draft.read(),value);
 assert.deepEqual(new DiscussionDraftLease(storage,a).read(),value);
});

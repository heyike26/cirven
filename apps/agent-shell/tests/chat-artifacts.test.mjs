import test from 'node:test';
import assert from 'node:assert/strict';
import { chatDeliveryFiles, openChatDeliveryFile } from '../src/chat-artifacts.ts';

const file = (name, index) => {
  const path = `delivery/immutable-version/${name}`, sha256 = index.toString(16).padStart(64, '0');
  return { path, sha256, file_source_ref: { kind: 'local_file', project_id: 'p', workspace_id: 'w', binding_revision: 2, binding_sha256: 'a'.repeat(64), path, revision: 1, sha256 } };
};
const files = ['任务书.docx', '流程.svg', '流程.png', '证据图.png', 'taskgraph.json', ...Array.from({ length: 17 }, (_, i) => `harness-${i}.md`)].map(file);
const receipt = { operation_id: 'actual-export', status: 'EXPORTED_CONFIRMED_PLAN_DERIVATIVES', project_files: files, harness: { files: [files[0], files.at(-1)] } };
const artifact = { artifactId: receipt.operation_id, type: 'planning-delivery', scope: { projectId: 'p' }, data: receipt };

test('22 delivery files deduplicate exact sources; compact actions prioritize Word and one visual format per diagram without dropping the remaining files', () => {
  const view = chatDeliveryFiles(receipt);
  assert.equal(view.all.length, 22);
  assert.deepEqual(view.primary.map(item => item.filename), ['任务书.docx', '流程.png', '证据图.png']);
  assert.equal(view.remaining.length, 19);
  assert.equal(new Set([...view.primary, ...view.remaining].map(item => item.source.path)).size, 22);
  assert.ok(view.all.every(item => !item.filename.includes('/')));
  const unknown = file('no-source.md', 100); delete unknown.file_source_ref;
  const changed = { ...receipt, project_files: [...files, unknown] };
  assert.equal(chatDeliveryFiles(changed).missingSources, 1);
  assert.equal(chatDeliveryFiles(changed).all.length, 22);
  assert.equal(chatDeliveryFiles({ status: 'FAILED_OR_PARTIAL', automatic_model_replay: false }).all.length, 0);
});

test('chat click dispatches the existing exact-source action; foreign or altered references never become a path primitive', async () => {
  const calls = [], context = { dispatch: async (...args) => { calls.push(args); return { status: 'OPEN_REQUESTED' }; } };
  const selected = chatDeliveryFiles(receipt).primary[0];
  await openChatDeliveryFile(context, artifact, receipt, selected);
  assert.equal(calls[0][0], 'delivery.file.open');
  assert.equal(calls[0][1], artifact);
  assert.deepEqual(calls[0][2], { receipt, file: files[0] });
  await assert.rejects(() => openChatDeliveryFile(context, { ...artifact, scope: { projectId: 'other' } }, receipt, selected), /实际交付来源/);
  await assert.rejects(() => openChatDeliveryFile(context, artifact, receipt, { ...selected, source: { ...selected.source, sha256: 'f'.repeat(64) } }), /实际交付来源/);
  await assert.rejects(() => openChatDeliveryFile(context, { ...artifact, artifactId: 'another-operation' }, receipt, selected), /绑定这条交付回答/);
  assert.equal(calls.length, 1);
  assert.ok(!Object.hasOwn(calls[0][2], 'native_path'));
});

test('a real harness INDEX and one representative visual stay primary; friendly Word name needs the exact report hash',async()=>{
 const index=file('INDEX.md',90),fake=file('elsewhere/INDEX.md',91);const value={...receipt,report:{artifact:{id:'report',filename:'本地资料审阅任务书.docx',sha256:files[0].sha256}},harness:{files:[index]},project_files:[...files,index,fake]};
 const view=chatDeliveryFiles(value);assert.deepEqual(view.primary.map(v=>v.filename),['任务书.docx','流程.png','INDEX.md']);
 assert.equal(view.primary[0].label,'本地资料审阅任务书.docx');assert.equal(view.primary[2].kind,'HARNESS');assert.equal(view.all.length,24);
 assert.equal(new Set([...view.primary,...view.remaining].map(v=>v.source.path)).size,24);
 const calls=[];await openChatDeliveryFile({dispatch:async(...args)=>calls.push(args)},artifact,value,view.primary[2]);assert.equal(calls[0][0],'delivery.file.open');assert.equal(calls[0][2].file,index);
 const changed={...value,report:{artifact:{...value.report.artifact,sha256:'f'.repeat(64)}}};assert.equal(chatDeliveryFiles(changed).primary[0].label,undefined);
 const noRealIndex={...value,harness:{files:[]}};assert.equal(chatDeliveryFiles(noRealIndex).all.find(v=>v.filename==='INDEX.md').kind,'FILE');
});

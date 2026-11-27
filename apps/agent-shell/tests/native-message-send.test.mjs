import test from 'node:test';
import assert from 'node:assert/strict';
import { NativeCollaborationController, confirmedCollaborationFullscreen, NativeMessageClient, NativeRequestIndexError, nativeMessageInput,
  dispatchNativeMessage, readNativeHistory, saveNativeHistory, restoreNativeHistory } from '../src/native-collaboration.ts';

const ref = { id: 'frozen-page', revision: 4, sha256: 'a'.repeat(64) };
const snapshot = revision => ({ operation_id: 'observed', current: true, observation: { status: 'PARTIAL', context_ref: { ...ref, revision } } });
const storage = () => { const data = new Map(); return { getItem: key => data.get(key) ?? null, setItem: (key, value) => data.set(key, value) }; };

test('actual dispatch requests fresh Main context for ordinary asks; only explicit valid annotations freeze a reference', async () => {
  const calls = [], contexts = [], local = storage();
  let publish;
  const bridge = {
    version: 2, getCollaborationState: async () => ({ mode: 'companion', context: snapshot(4) }),
    onNativeContext: listener => { publish = listener; return () => {}; },
    submitNativeMessage: async body => { calls.push(body); if (!body.contextRef) publish(snapshot(5)); return { operation_id: body.operationId, status: 'PROCESSING' }; },
  };
  const controller = new NativeCollaborationController(bridge, () => {}, value => contexts.push(value));
  await controller.start();
  const client = new NativeMessageClient(bridge);
  await dispatchNativeMessage(client, local, nativeMessageInput('ordinary', ' 原问题😀\r\n ', [], snapshot(4)));
  assert.equal(Object.hasOwn(calls[0], 'contextRef'), false);
  assert.equal(calls[0].rawText, ' 原问题😀\r\n ');
  assert.deepEqual(contexts.map(value => value.observation.context_ref.revision), [4, 5]);
  const annotation = { context_ref: { ...ref, revision: 5 }, label: '用户明确冻结', comment: '旧帧批注\r\n', regions: [] };
  await dispatchNativeMessage(client, local, nativeMessageInput('explicit', '', [annotation], snapshot(5)));
  assert.deepEqual(calls[1].contextRef, annotation.context_ref);
  assert.equal(calls[1].rawText,'请查看所附内容。');
  assert.equal(calls[1].inputAttachments[0].user_note,annotation.comment);
  for (const current of [snapshot(6), undefined, { ...snapshot(5), current: false }]) {
    const frozen=nativeMessageInput('frozen-source', '', [annotation], current);
    assert.deepEqual(frozen.inputAttachments[0].context_ref,annotation.context_ref);
    assert.equal(Object.hasOwn(frozen,'contextRef'),false,'a historical attachment is source data, never current native authority');
  }
  assert.equal(calls.length, 2, 'invalid frozen feedback must not reach IPC');
  controller.stop();
});

test('request index precedes IPC and restart inspects missing/unavailable operations without dropping questions or replaying sends', async () => {
  const local = storage(), input = ' 完整问题😀\r\n保留尾空格 ';
  saveNativeHistory(local, [{ operation_id: 'older', input: '先前问题' }]);
  let sends = 0;
  const client = new NativeMessageClient({
    submitNativeMessage: async body => {
      sends++;
      assert.deepEqual(readNativeHistory(local).at(-1), { input, operation_id: body.operationId });
      throw new Error('IPC disconnected after server might have accepted');
    },
    inspectNativeMessage: async operationId => operationId === 'unknown-send'
      ? { operation_id: operationId, status: 'NO_RECORD', error: { code: 'NOT_FOUND', message: 'typed original 404' }, execution_outcome: 'NOT_PROVEN', automatic_retry: false }
      : Promise.reject(new Error('Controller unavailable')),
  });
  await assert.rejects(() => dispatchNativeMessage(client, local, nativeMessageInput('unknown-send', input, [], snapshot(4))), /IPC disconnected/);
  const restored = await restoreNativeHistory(client, readNativeHistory(local));
  assert.deepEqual(restored.map(item => item.input), ['先前问题', input]);
  assert.deepEqual(restored.map(item => item.message.status), ['INSPECTION_UNAVAILABLE', 'NO_RECORD']);
  assert.match(restored[1].message.text, /没有记录不证明未执行/);
  assert.ok(restored.every(item => item.message.text.includes('不能自动重发')));
  assert.equal(sends, 1);
  assert.equal(readNativeHistory(local).length, 2);
});

test('storage failure stops the actual dispatch; late history persistence retains existing pending operation indexes', async () => {
  let sends = 0;
  const client = new NativeMessageClient({ submitNativeMessage: async body => { sends++; return { operation_id: body.operationId, status: 'PROCESSING' }; } });
  const broken = { getItem: () => null, setItem: () => { throw new Error('Storage full'); } };
  await assert.rejects(() => dispatchNativeMessage(client, broken, nativeMessageInput('not-sent', '保留草稿', [], snapshot(4))), error => error instanceof NativeRequestIndexError && /尚未发送/.test(error.message));
  assert.equal(sends, 0);
  const local = storage();
  saveNativeHistory(local, [{ operation_id: 'pending-original', input: '未确认的原问题' }]);
  await dispatchNativeMessage(client, local, nativeMessageInput('new-message', '新问题', [], snapshot(4)));
  saveNativeHistory(local, [{ operation_id: 'new-message', input: '新问题' }]);
  assert.deepEqual(readNativeHistory(local).map(item => item.operation_id), ['pending-original', 'new-message']);
  const wrong = new NativeMessageClient({ inspectNativeMessage: async () => ({ operation_id: 'another-op', status: 'RETURNED' }) });
  const restored = await restoreNativeHistory(wrong, readNativeHistory(local));
  assert.ok(restored.every(item => item.message.status === 'INSPECTION_UNAVAILABLE'));
  assert.deepEqual(restored.map(item => item.message.operation_id), ['pending-original', 'new-message']);
});

test('App fullscreen command waits for the actual host confirmation and never uses desktop geometry or input', async () => {
  const calls = [];
  let finish;
  const bridge = { setCollaborationFullscreen: enabled => { calls.push(enabled); return new Promise(resolve => { finish = resolve; }); } };
  let completed = false;
  const enter = confirmedCollaborationFullscreen(bridge, true).then(state => { completed = true; return state; });
  await Promise.resolve();
  assert.equal(completed, false);
  assert.deepEqual(calls, [true]);
  finish({ mode: 'workbench', fullscreen: true });
  assert.equal((await enter).fullscreen, true);
  const leave = confirmedCollaborationFullscreen(bridge, false);
  assert.deepEqual(calls, [true, false]);
  finish({ mode: 'workbench', fullscreen: false });
  assert.equal((await leave).fullscreen, false);
  await assert.rejects(() => confirmedCollaborationFullscreen({}, true), /尚未接入/);
  await assert.rejects(() => confirmedCollaborationFullscreen({ setCollaborationFullscreen: async () => ({ mode: 'workbench', fullscreen: false }) }, true), /尚未确认/);
});

test('binary readonly questions dispatch exact pinned SourceRef without a stale frame or write grant; mixed scopes and oversized raw drafts stay local', async () => {
  const source={kind:'local_file',project_id:'p',workspace_id:'actual-bound',binding_revision:2,binding_sha256:'b'.repeat(64),path:'produced.docx',revision:1,sha256:'c'.repeat(64)};
  const calls=[],client=new NativeMessageClient({submitNativeMessage:async body=>{calls.push(body);return{operation_id:body.operationId,status:'NOT_CONFIGURED',file_source_ref:body.fileSourceRef,file_review_status:'CURRENT'};}});
  const body=nativeMessageInput('binary-read','这段解释😀\r\n',[],snapshot(4),undefined,source);
  await dispatchNativeMessage(client,storage(),body);
  assert.deepEqual(calls[0].fileSourceRef,source);
  assert.equal(Object.hasOwn(calls[0],'contextRef'),false,'Main must observe a fresh page independently of the exact pinned file');
  assert.equal(Object.hasOwn(calls[0],'write'),false);
  const annotation={context_ref:ref,label:'another native object',comment:'不要借给Word',regions:[]};
  const mixed=nativeMessageInput('mixed','解释',[annotation],snapshot(4),undefined,source);
  assert.deepEqual(mixed.fileSourceRef,source);assert.equal(mixed.rawText,'解释');assert.deepEqual(mixed.inputAttachments[0].context_ref,ref);
  assert.throws(()=>nativeMessageInput('too-long','😀'.repeat(16001),[],snapshot(4),undefined,source),/超过16000/);
  assert.equal(nativeMessageInput('unicode-bound','😀'.repeat(16000),[],snapshot(4),undefined,source).rawText.length,32000);
  assert.equal(calls.length,1);
  assert.equal(source.path,'produced.docx');
});

test('OS fullscreen exit state wins a slower initial host read and the state subscription is released on disposal', async () => {
  let finishInitial, publishState;
  const states = [], releases = [];
  const bridge = {
    getCollaborationState: () => new Promise(resolve => { finishInitial = resolve; }),
    onNativeContext: () => () => releases.push('context'),
    onCollaborationState: listener => { publishState = listener; return () => releases.push('state'); },
  };
  const controller = new NativeCollaborationController(bridge, state => states.push(state), () => {});
  const started = controller.start();
  publishState({ mode: 'workbench', fullscreen: false });
  finishInitial({ mode: 'workbench', fullscreen: true });
  await started;
  assert.deepEqual(states.map(state => state.fullscreen), [false]);
  publishState({ mode: 'companion', fullscreen: false });
  assert.equal(states.at(-1).mode, 'companion');
  controller.stop();
  publishState({ mode: 'workbench', fullscreen: true });
  assert.equal(states.length, 2);
  assert.deepEqual(releases, ['context', 'state']);
});
test('companion pin control waits for the actual owned-window state and a newer workbench event wins a late pin response',async()=>{
  const calls=[],states=[];let publish,finish;
  const controller=new NativeCollaborationController({getCollaborationState:async()=>({mode:'companion',pinned:false}),onNativeContext:()=>()=>{},onCollaborationState:listener=>{publish=listener;return()=>{};},setCompanionPinned:enabled=>{calls.push(enabled);return new Promise(resolve=>{finish=resolve;});}},value=>states.push(value),()=>{});
  await controller.start();const pin=controller.pin(true);assert.equal(states.at(-1).pinned,false);assert.deepEqual(calls,[true]);finish({mode:'companion',pinned:true});await pin;assert.equal(states.at(-1).pinned,true);
  const unpin=controller.pin(false);publish({mode:'workbench',pinned:false});finish({mode:'companion',pinned:false});await unpin;assert.equal(states.at(-1).mode,'workbench');assert.deepEqual(calls,[true,false]);controller.stop();
  const absent=new NativeCollaborationController({},()=>{},()=>{});await assert.rejects(()=>absent.pin(true),/尚未提供/);
});
test('only the explicit quick-question event requests composer focus; ordinary context publication never steals focus and cleanup releases it',async()=>{
  let publishContext,publishQuick,focuses=0;const contexts=[],released=[];
  const controller=new NativeCollaborationController({getCollaborationState:async()=>({mode:'companion'}),onNativeContext:listener=>{publishContext=listener;return()=>released.push('context');},onQuickQuestion:listener=>{publishQuick=listener;return()=>released.push('quick');}},()=>{},value=>contexts.push(value),()=>focuses++);
  await controller.start();publishContext(snapshot(5));assert.equal(focuses,0);assert.equal(contexts.length,1);publishQuick();assert.equal(focuses,1);publishContext(snapshot(6));assert.equal(focuses,1);controller.stop();publishQuick();assert.equal(focuses,1);assert.deepEqual(released,['context','quick']);
});

test('canonical route and revision are persisted before dispatch even when the message has no image or tool selection',async()=>{
 const local=storage(),conversationRef={id:'native:'+'c'.repeat(64),revision:2};let calls=0;
 const body=nativeMessageInput('canonical_turn','My current draft',[],undefined,undefined,undefined,'target_alias',undefined,null,conversationRef);
 assert.deepEqual(body.conversationRef,conversationRef);assert.equal(body.contextRef,undefined);
 const client=new NativeMessageClient({submitNativeMessage:async request=>{calls++;const saved=readNativeHistory(local,'target_alias');assert.deepEqual(saved[0].request.conversationRef,conversationRef);throw Error('lost acknowledgement');}});
 await assert.rejects(dispatchNativeMessage(client,local,body),/lost acknowledgement/);assert.equal(calls,1);assert.deepEqual(readNativeHistory(local,'target_alias')[0].request.conversationRef,conversationRef);
 assert.throws(()=>nativeMessageInput('invalid_route','Draft',[],undefined,undefined,undefined,'target_alias',undefined,null,{...conversationRef,revision:true}));
});

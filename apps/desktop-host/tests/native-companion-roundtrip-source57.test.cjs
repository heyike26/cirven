'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { NativeCompanion } = require('../native-companion.cjs');
const turn = () => new Promise(resolve => setImmediate(resolve));

// The only external boundary is an owned Electron window with controlled
// native placement. The production presentation queue and ACK run unchanged.
function scene(options = {}) {
  const area = { x: 0, y: 0, width: 1920, height: 1040, ...options.area };
  const state = { bounds: { x: 700, y: 90, width: 1180, height: 880 },
    minimum: [780, 480], opacity: 0.8, minimized: true, maximized: false,
    fullscreen: false, destroyed: false, pinned: false, ...options.state };
  const controls = { ...options.controls }, requests = [], calls = [], stages = [], own = new EventEmitter();
  let normalReads = 0;
  const live = call => { assert.equal(state.destroyed, false, 'Call after native destruction: ' + call); calls.push(call); };
  Object.assign(own, {
    isDestroyed: () => state.destroyed, isMinimized: () => state.minimized,
    isMaximized: () => !state.minimized && state.maximized, isFullScreen: () => !state.minimized && state.fullscreen,
    getNormalBounds() { live('normal'); return { ...state.bounds }; },
    getBounds() {
      live('read');
      if (state.minimized || state.maximized || state.fullscreen) return { ...area };
      const value = { ...state.bounds };
      return requests.length && controls.read ? controls.read(value, ++normalReads) : value;
    },
    getMinimumSize: () => [...state.minimum],
    setMinimumSize(width, height) { live('minimum'); state.minimum = [width, height]; },
    setBounds(value) {
      live('apply'); requests.push({ ...value });
      if (!controls.ignore) state.bounds = controls.map ? controls.map({ ...value }, requests.length) : { ...value };
      controls.afterApply?.(requests.length);
    },
    restore() { live('restore'); state.minimized = false; state.maximized = true; own.emit('restore'); },
    unmaximize() { live('unmaximize'); state.maximized = false; own.emit('unmaximize'); },
    setFullScreen(value) { live('fullscreen'); state.fullscreen = value; own.emit('leave-full-screen'); },
    minimize() { live('minimize'); state.minimized = true; },
    getOpacity() { live('opacity.read'); return state.opacity; },
    setOpacity(value) { live('opacity.write'); state.opacity = value; },
    isAlwaysOnTop: () => state.pinned, setAlwaysOnTop(value) { live('pin'); state.pinned = value; },
    show() { live('show'); }, focus() { live('focus'); },
    hide() { assert.fail('No new hide strategy'); }, setShape() { assert.fail('No desktop region changes'); },
  });
  const screen = { getAllDisplays: () => [{ workArea: area }], getCursorScreenPoint: () => ({ x: 1900, y: 900 }),
    getDisplayNearestPoint: () => ({ workArea: area }) };
  const broker = new NativeCompanion({ window: own, screen, platform: 'win32', initialMode: 'workbench',
    ...(options.diagnostics === false ? {} : { onGeometryDiagnostic: value => stages.push(structuredClone(value)) }),
    request: async route => ({ status: route.endsWith('/close') ? 'CLOSED' : 'ACTIVE', configured: false }) });
  const oldCache = options.cache;
  if (oldCache) broker.companionBounds = { ...oldCache };
  function clean() {
    for (const event of ['restore', 'unmaximize', 'leave-full-screen', 'closed']) assert.equal(own.listenerCount(event), 0, event);
  }
  function failed() {
    assert.equal(state.opacity, 0.8); assert.equal(state.minimized, true); assert.equal(broker.mode, 'workbench');
    assert.deepEqual(broker.companionBounds, oldCache); assert.equal(state.pinned, false);
    assert.equal(stages.some(stage => stage.stage === 'target-acknowledged'), false); clean();
  }
  return { area, state, controls, requests, calls, stages, own, broker, clean, failed };
}
const target = { x: 700, y: 90, width: 520, height: 760 };
const offset = (width, height, x = 0) => value => ({ ...value, x: value.x + x, width: value.width + width, height: value.height + height });

for (const [label, width, height, corrected] of [
  ['actual Source55 +2/+2', 2, 2, { ...target, width: 518, height: 758 }],
  ['opposite size directions', -2, 2, { ...target, width: 522, height: 758 }],
  ['height undershoot', 2, -2, { ...target, width: 518, height: 762 }],
  ['only height differs', 0, 2, { ...target, height: 758 }],
  ['only width differs', 2, 0, { ...target, width: 518 }],
]) test('Source57 ' + label + ' uses one measured request and ACKs the original exact target', async () => {
  const s = scene({ controls: { map: offset(width, height) } });
  await s.broker.setMode('companion');
  assert.deepEqual(s.requests, [target, corrected]); assert.deepEqual(s.state.bounds, target);
  assert.deepEqual(s.broker.companionBounds, target); assert.equal(s.broker.mode, 'companion'); assert.equal(s.state.opacity, 0.8);
  const applied = s.stages.filter(stage => stage.stage === 'target-request');
  assert.equal(applied.length, 2); assert.deepEqual(applied.map(stage => stage.target), [target, target]);
  assert.deepEqual(applied.map(stage => stage.requested), [target, corrected]);
  assert.equal(s.stages.filter(stage => stage.stage === 'target-compensation').length, 1);
  assert.deepEqual(s.stages.find(stage => stage.stage === 'target-compensation').roundtrip_delta, { width, height });
  const finalReads = s.stages.filter(stage => ['target-readback', 'target-settled-readback'].includes(stage.stage) && stage.attempt === 1);
  assert.equal(finalReads.length, 2); assert.deepEqual(finalReads.map(stage => stage.actual), [target, target]);
  assert.deepEqual(s.stages.find(stage => stage.stage === 'target-acknowledged').acknowledged, target); s.clean();
});

test('Source57 identity roundtrip needs one request and the existing one-DIP ACK is unchanged', async () => {
  for (const map of [value => value, offset(1, -1)]) {
    const s = scene({ controls: { map } }); await s.broker.setMode('companion');
    assert.deepEqual(s.requests, [target]); assert.deepEqual(s.broker.companionBounds, s.state.bounds);
    assert.equal(s.stages.some(stage => stage.stage === 'target-compensation'), false); s.clean();
  }
});

test('Source57 large and position deltas retain the bounded same-target attempt without compensation', async () => {
  for (const controls of [{ ignore: true }, { map: offset(3, 3) }, { map: offset(2, 2, 2) }, { map: offset(1.5, 0) }]) {
    const s = scene({ controls }); await assert.rejects(s.broker.setMode('companion'), { code: 'OWN_COMPANION_TARGET_NOT_ACKNOWLEDGED' });
    assert.deepEqual(s.requests, [target, target]); assert.equal(s.stages.some(stage => stage.stage === 'target-compensation'), false); s.failed();
  }
});

test('Source57 an ignored setter with a small apparent offset still cannot obtain ACK', async () => {
  const s = scene({ state: { bounds: { ...target, width: 522, height: 762 } }, controls: { ignore: true } });
  await assert.rejects(s.broker.setMode('companion'), { code: 'OWN_COMPANION_TARGET_NOT_ACKNOWLEDGED' });
  assert.deepEqual(s.requests, [target, { ...target, width: 518, height: 758 }]); s.failed();
});

test('Source57 unstable native readings never infer a correction or pass the two-read ACK', async () => {
  const s = scene({ diagnostics: false, controls: { read: (value, n) => ({ ...value, width: value.width + (n % 2 ? 2 : 3) }) } });
  await assert.rejects(s.broker.setMode('companion'), { code: 'OWN_COMPANION_TARGET_NOT_ACKNOWLEDGED' });
  assert.deepEqual(s.requests, [target, target]); s.failed();
});

test('Source57 correction may not fall below minimum or silently clamp to a different target', async () => {
  const s = scene({ area: { width: 390 }, controls: { map: offset(2, 0) } });
  await assert.rejects(s.broker.setMode('companion'), { code: 'OWN_COMPANION_TARGET_NOT_ACKNOWLEDGED' });
  assert.equal(s.requests.length, 2); assert.equal(s.requests.every(value => value.width === 390), true);
  assert.equal(s.stages.some(stage => stage.stage === 'target-compensation'), false); s.failed();
});

test('Source57 a compensated request outside the work area is not written', async () => {
  const s = scene({ controls: { map: offset(-2, 0) } });
  await assert.rejects(s.broker.setMode('companion', { anchor: { x: 1800, y: 900 } }), { code: 'OWN_COMPANION_TARGET_NOT_ACKNOWLEDGED' });
  assert.deepEqual(s.requests, [{ x: 1400, y: 128, width: 520, height: 760 }, { x: 1400, y: 128, width: 520, height: 760 }]);
  assert.equal(s.stages.some(stage => stage.stage === 'target-compensation'), false); s.failed();
});

test('Source57 work-area replacement fences the correction before another native apply', async () => {
  const s = scene({ diagnostics: false, controls: { map: offset(2, 2) } });
  s.controls.read = (value, n) => { if (n === 2) s.area.width = 1000; return value; };
  await assert.rejects(s.broker.setMode('companion'), { code: 'OWN_COMPANION_TARGET_NOT_ACKNOWLEDGED' });
  assert.deepEqual(s.requests, [target]); s.failed();
});

test('Source57 a nonlinear second roundtrip fails with no third request and keeps the last cache', async () => {
  const cache = { x: 1000, y: 100, width: 640, height: 800 };
  const s = scene({ cache, controls: { map: (value, attempt) => offset(attempt === 1 ? 2 : 4, 2)(value) } });
  await assert.rejects(s.broker.setMode('companion'), { code: 'OWN_COMPANION_TARGET_NOT_ACKNOWLEDGED' });
  assert.deepEqual(s.requests, [cache, { ...cache, width: 638, height: 798 }]); s.failed();
  const calls = [...s.calls]; await turn(); await turn(); assert.deepEqual(s.calls, calls);
});

test('Source57 user-adjusted companion cache remains the target rather than resetting to 520x760', async () => {
  const cache = { x: 1000, y: 100, width: 640, height: 800 }, s = scene({ cache, controls: { map: offset(2, -2) } });
  await s.broker.setMode('companion');
  assert.deepEqual(s.requests, [cache, { ...cache, width: 638, height: 802 }]);
  assert.deepEqual(s.state.bounds, cache); assert.deepEqual(s.broker.companionBounds, cache); s.clean();
});

test('Source57 a stale presentation between stable reads and correction cannot write late bounds', async () => {
  const s = scene({ diagnostics: false, controls: { map: offset(2, 2) } });
  s.controls.read = (value, n) => { if (n === 2) s.broker.presentationEpoch++; return value; };
  await assert.rejects(s.broker.setMode('companion'), { code: 'PRESENTATION_CHANGED' });
  assert.deepEqual(s.requests, [target]); s.failed();
  const calls = [...s.calls]; await turn(); await turn(); assert.deepEqual(s.calls, calls);
});

test('Source57 closing or native destruction before correction has no late native calls', async () => {
  for (const destroy of [false, true]) {
    const s = scene({ diagnostics: false, controls: { map: offset(2, 2) } }); let closing;
    s.controls.read = (value, n) => {
      if (n === 2) {
        if (destroy) { s.state.destroyed = true; s.own.emit('closed'); } else closing = s.broker.close();
      }
      return value;
    };
    await assert.rejects(s.broker.setMode('companion'), /closed/); await closing;
    assert.deepEqual(s.requests, [target]); assert.equal(s.broker.companionBounds, undefined); assert.equal(s.broker.mode, 'workbench');
    if (!destroy) assert.equal(s.state.opacity, 0.8);
    const calls = [...s.calls]; await turn(); await turn(); assert.deepEqual(s.calls, calls); s.clean();
  }
});

test('Source57 stable-read compensation stays inside the original opacity deadline', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const s = scene({ diagnostics: false, controls: { map: offset(2, 2) } });
  s.controls.read = (value, n) => { if (n === 2) t.mock.timers.tick(2000); return value; };
  await assert.rejects(s.broker.setMode('companion'), /尚未退出放大或最小化/);
  assert.deepEqual(s.requests, [target]); s.failed();
  const calls = [...s.calls]; t.mock.timers.tick(10000); await turn(); assert.deepEqual(s.calls, calls);
});

test('Source57 the original second setter error survives without cache commit or a third request', async () => {
  const failure = new Error('Native corrective setter failed'), s = scene({ controls: { map: offset(2, 2), afterApply: n => { if (n === 2) throw failure; } } });
  await assert.rejects(s.broker.setMode('companion'), error => error === failure);
  assert.deepEqual(s.requests, [target, { ...target, width: 518, height: 758 }]); s.failed();
});

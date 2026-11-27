'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { NativeCompanion, revealOwnWindow } = require('../native-companion.cjs');

// Controlled Electron lifecycle only: late native placement is modeled
// explicitly. No Electron, native window, network, capture, CLI or model runs.
const turn = () => new Promise(resolve => setImmediate(resolve));
function gate() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
function scene(options = {}) {
  const area = { x: 0, y: 0, width: 1920, height: 1040 };
  const state = { minimized: true, maximized: false, fullscreen: false, destroyed: false,
    normal: { x: 700, y: 90, width: 1180, height: 880 }, minimum: [780, 480], opacity: 0.8, pinned: false, ...options.state };
  const calls = [], records = [], restoreStarted = gate(), boundsStarted = gate();
  const config = { ...options.config }, own = new EventEmitter();
  const live = name => { assert.equal(state.destroyed, false, 'destroyed-window call: ' + name); calls.push(name); };
  const completeRestore = () => { state.minimized = false; state.maximized = true; calls.push('restore.complete'); own.emit('restore'); };
  Object.assign(own, {
    isDestroyed: () => state.destroyed,
    isMinimized: () => state.minimized, isMaximized: () => !state.minimized && state.maximized,
    isFullScreen: () => !state.minimized && state.fullscreen,
    getBounds() { live('bounds.read'); return state.minimized ? { x: -32000, y: -32000, width: 160, height: 28 } :
      state.maximized || state.fullscreen ? { ...area } : { ...state.normal }; },
    getNormalBounds() { live('normal.read'); return { ...state.normal }; },
    getMinimumSize: () => [...state.minimum],
    setMinimumSize(w, h) { live('minimum'); state.minimum = [w, h]; },
    setBounds(value) {
      live('bounds.write'); records.push({ ...value }); boundsStarted.resolve();
      if (config.boundsError) throw config.boundsError;
      if (!config.ignoreBounds) state.normal = { ...value, ...(config.adjustment ?? {}) };
      config.afterBounds?.(value, records.length);
    },
    restore() { live('restore'); restoreStarted.resolve();
      if (config.earlyRestore) state.minimized = false; else completeRestore(); },
    minimize() { live('minimize'); state.minimized = true; },
    unmaximize() { live('unmaximize'); state.maximized = false; own.emit('unmaximize'); config.afterUnmaximize?.(); },
    setFullScreen(value) { live('fullscreen'); state.fullscreen = value; own.emit('leave-full-screen'); },
    getOpacity() { live('opacity.read'); return state.opacity; },
    setOpacity(value) { live('opacity:' + value); state.opacity = value; },
    isAlwaysOnTop: () => state.pinned, setAlwaysOnTop(value) { live('pin:' + value); state.pinned = value; },
    show() { live('show'); config.onShow?.(); }, focus() { live('focus'); },
    hide() { assert.fail('no new hide strategy'); }, setShape() { assert.fail('no desktop region'); },
  });
  const screen = { getAllDisplays: () => [{ workArea: area }], getCursorScreenPoint: () => ({ x: 1900, y: 900 }),
    getDisplayNearestPoint: () => ({ workArea: area }) };
  const broker = new NativeCompanion({ window: own, screen, platform: 'win32',
    initialMode: options.initialMode ?? 'workbench', pinnedPreference: options.pinnedPreference ?? false,
    onGeometryDiagnostic: options.onGeometryDiagnostic,
    request: async (route, body) => {
      calls.push('request:' + route.split('/').at(-1));
      assert.ok(!route.includes('/workspace/'));
      if (body?.presentation) await config.onPresentation?.();
      return route.endsWith('/status') ? { configured: false } : { status: route.endsWith('/close') ? 'CLOSED' : 'ACTIVE' };
    } });
  function clean() { for (const event of ['restore', 'unmaximize', 'leave-full-screen', 'closed']) assert.equal(own.listenerCount(event), 0, event); }
  return { own, broker, state, config, calls, records, screen, restoreStarted, boundsStarted, completeRestore, clean };
}

test('Source55 an early flag needs the actual event; late placement in the native stack precedes the frozen target', async () => {
  const stages = [], s = scene({ config: { earlyRestore: true }, onGeometryDiagnostic: value => stages.push(value) });
  s.config.afterUnmaximize = () => {
    setImmediate(() => { s.state.normal = { x: 100, y: 90, width: 1180, height: 880 }; s.calls.push('late-placement'); });
  };
  const pending = s.broker.setMode('companion', { anchor: { x: 1800, y: 900 } });
  await s.restoreStarted.promise; await turn();
  assert.deepEqual(s.records, []); assert.equal(s.calls.includes('unmaximize'), false);
  assert.equal(s.state.opacity, 0); assert.equal(s.broker.mode, 'workbench');
  s.completeRestore(); await pending;
  const target = { x: 1400, y: 128, width: 520, height: 760 };
  assert.deepEqual(s.state.normal, target); assert.deepEqual(s.broker.companionBounds, target);
  assert.ok(s.calls.indexOf('late-placement') < s.calls.indexOf('bounds.write'));
  const ack = stages.findIndex(value => value.stage === 'target-acknowledged');
  assert.ok(ack > stages.findIndex(value => value.stage === 'target-settled-readback'));
  assert.equal(s.state.opacity, 0.8); assert.equal(s.calls.includes('show'), false); s.clean();
});

test('Source55 a late overwrite permits exactly one correction of the same frozen target', async () => {
  const s = scene();
  s.config.afterBounds = (_, count) => { if (count === 1) setImmediate(() => {
    s.state.normal = { x: 740, y: 90, width: 1180, height: 880 }; s.calls.push('late-placement');
  }); };
  await s.broker.setMode('companion');
  assert.equal(s.records.length, 2); assert.deepEqual(s.records[0], s.records[1]);
  assert.deepEqual(s.state.normal, s.records[0]); assert.deepEqual(s.broker.companionBounds, s.records[0]); s.clean();
});

test('Source55 ignored or persistently displaced bounds fail without committing cache, mode or pin', async () => {
  for (const config of [{ ignoreBounds: true }, { adjustment: { width: 523 } }]) {
    const s = scene({ config, pinnedPreference: true });
    const oldCache = { x: 1150, y: 100, width: 600, height: 800 }; s.broker.companionBounds = { ...oldCache };
    await assert.rejects(s.broker.setMode('companion'), error => error.code === 'OWN_COMPANION_TARGET_NOT_ACKNOWLEDGED');
    assert.equal(s.records.length, 2); assert.deepEqual(s.records[0], s.records[1]);
    assert.deepEqual(s.broker.companionBounds, oldCache); assert.equal(s.broker.mode, 'workbench');
    assert.equal(s.state.pinned, false); assert.equal(s.state.opacity, 0.8); assert.equal(s.state.minimized, true);
    const after = [...s.calls]; await turn(); await turn(); assert.deepEqual(s.calls, after); s.clean();
  }
});

test('Source55 actual DIP readback accepts a one-DIP tolerance and stores the acknowledged rectangle', async () => {
  const s = scene({ config: { adjustment: { width: 521, height: 759 } } });
  await s.broker.setMode('companion');
  assert.equal(s.records.length, 1); assert.equal(s.state.normal.width, 521); assert.equal(s.state.normal.height, 759);
  assert.deepEqual(s.broker.companionBounds, s.state.normal); s.clean();
});

test('Source55 cache, anchor and fallback targets are copied before asynchronous presentation admission', async () => {
  for (const kind of ['CACHE', 'ANCHOR', 'FALLBACK']) {
    const anchor = { x: 1800, y: 900 }, cached = { x: 1100, y: 110, width: 640, height: 800 };
    const entered = gate(), released = gate(), s = scene({ config: { onPresentation: async () => { entered.resolve(); await released.promise; } } });
    if (kind === 'CACHE') s.broker.companionBounds = { ...cached };
    const pending = s.broker.setMode('companion', kind === 'ANCHOR' ? { anchor } : {});
    await entered.promise;
    anchor.x = 0; anchor.y = 0;
    if (s.broker.companionBounds) s.broker.companionBounds.width = 999;
    s.state.normal = { x: 1, y: 1, width: 999, height: 999 }; released.resolve(); await pending;
    const expected = kind === 'CACHE' ? cached : kind === 'ANCHOR' ? { x: 1400, y: 128, width: 520, height: 760 } :
      { x: 700, y: 90, width: 520, height: 760 };
    assert.deepEqual(s.records[0], expected); assert.deepEqual(s.broker.companionBounds, expected); s.clean();
  }
});

test('Source55 Windows bounds/events authority is mandatory and never silently falls back to flag-only success', async () => {
  for (const method of ['getBounds', 'setBounds', 'isMinimized', 'isMaximized', 'isFullScreen', 'on', 'removeListener']) {
    const s = scene(); s.own[method] = undefined;
    await assert.rejects(s.broker.setMode('companion'), /bounds authority/);
    assert.deepEqual(s.records, []); assert.equal(s.state.opacity, 0.8); assert.equal(s.calls.includes('restore'), false);
    assert.equal(s.calls.includes('request:presentation'), false); s.clean();
  }
});

test('Source55 bounds setter errors preserve the original failure and old cache through opacity cleanup', async () => {
  const failure = new Error('original setBounds failure'), s = scene({ config: { boundsError: failure } });
  const cached = { x: 1000, y: 100, width: 620, height: 800 }; s.broker.companionBounds = { ...cached };
  await assert.rejects(s.broker.setMode('companion'), error => error === failure);
  assert.equal(s.records.length, 1); assert.deepEqual(s.broker.companionBounds, cached);
  assert.equal(s.state.opacity, 0.8); assert.equal(s.state.minimized, true); s.clean();
});

test('Source55 newer presentation cancels a queued ACK checkpoint before any stale correction', async () => {
  const s = scene({ config: { ignoreBounds: true } }); let next;
  s.config.afterBounds = () => { delete s.config.afterBounds; next = s.broker.setMode('workbench'); };
  await assert.rejects(s.broker.setMode('companion'), error => error.code === 'PRESENTATION_CHANGED');
  await next; assert.equal(s.broker.mode, 'workbench'); assert.equal(s.state.opacity, 0.8);
  assert.equal(s.records.filter(value => value.width === 520).length, 1); assert.equal(s.broker.companionBounds, undefined);
  const after = [...s.calls]; await turn(); await turn(); assert.deepEqual(s.calls, after); s.clean();
});

test('Source55 closing or destruction between target request and ACK prevents late native calls', async () => {
  for (const destroyed of [false, true]) {
    const s = scene(); let closing;
    s.config.afterBounds = () => { delete s.config.afterBounds; queueMicrotask(() => {
      if (destroyed) { s.state.destroyed = true; s.own.emit('closed'); } else closing = s.broker.close();
    }); };
    await assert.rejects(s.broker.setMode('companion'), /closed/); if (closing) await closing;
    assert.equal(s.records.length, 1); assert.equal(s.broker.companionBounds, undefined); assert.equal(s.broker.mode, 'workbench');
    if (!destroyed) assert.equal(s.state.opacity, 0.8);
    const after = [...s.calls]; await turn(); await turn(); assert.deepEqual(s.calls, after); s.clean();
  }
});

test('Source55 ACK checkpoints remain inside the original two-second opacity deadline', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const s = scene();
  s.config.afterBounds = () => queueMicrotask(() => t.mock.timers.tick(2000));
  await assert.rejects(s.broker.setMode('companion'), /尚未退出放大或最小化/);
  assert.equal(s.records.length, 1); assert.equal(s.state.opacity, 0.8); assert.equal(s.state.minimized, true);
  assert.equal(s.broker.mode, 'workbench'); assert.equal(s.broker.companionBounds, undefined);
  const after = [...s.calls]; await turn(); t.mock.timers.tick(10000); await turn(); assert.deepEqual(s.calls, after); s.clean();
});

test('Source55 default diagnostics are absent; opt-in finite records carry geometry only and cannot change outcomes', async () => {
  const old = process.env.EEAS_DIAGNOSTIC_NATIVE_GEOMETRY; delete process.env.EEAS_DIAGNOSTIC_NATIVE_GEOMETRY;
  try {
    const s = scene(); assert.equal(s.broker.geometryDiagnostic, undefined); await s.broker.setMode('companion'); s.clean();
    const records = [], d = scene({ onGeometryDiagnostic: value => { records.push(structuredClone(value)); throw new Error('diagnostic sink unavailable'); } });
    await d.broker.setMode('companion');
    assert.ok(records.length > 0 && records.length < 40); assert.ok(records.some(value => value.stage === 'target-acknowledged'));
    for (const value of records) {
      assert.equal(value.scope, 'NATIVE_COMPANION_GEOMETRY'); assert.equal(value.epoch, 1);
      assert.equal(/hwnd|owner_pid|raw_text|account|password|token/i.test(JSON.stringify(value)), false);
    }
    assert.equal(d.state.normal.width, 520); d.clean();
    process.env.EEAS_DIAGNOSTIC_NATIVE_GEOMETRY = '1'; const enabled = scene();
    assert.equal(typeof enabled.broker.geometryDiagnostic, 'function'); enabled.clean();
  } finally { if (old === undefined) delete process.env.EEAS_DIAGNOSTIC_NATIVE_GEOMETRY; else process.env.EEAS_DIAGNOSTIC_NATIVE_GEOMETRY = old; }
});

test('Source55 target ACK precedes generic reveal; later generic show placement is outside this bounded guarantee', async () => {
  const s = scene(); await s.broker.setMode('companion'); assert.equal(s.state.normal.width, 520);
  s.config.onShow = () => { s.state.normal = { x: 0, y: 0, width: 1180, height: 880 }; };
  revealOwnWindow(s.own, s.screen);
  assert.equal(s.state.normal.width, 1180, 'the slice does not silently rewrite generic show semantics');
  assert.equal(s.broker.companionBounds.width, 520, 'only the acknowledged target was cached'); s.clean();
});

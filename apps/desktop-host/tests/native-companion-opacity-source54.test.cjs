'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { NativeCompanion, revealOwnWindow } = require('../native-companion.cjs');

// Controlled BrowserWindow lifecycle, including Windows restore-to-maximized
// semantics. These tests do not qualify DWM pixels or execute Electron/native UI.
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
function scene(options = {}) {
  const original = options.opacity ?? 0.72;
  const area = { x: 0, y: 0, width: 1920, height: 1040 };
  const normal = { x: 840, y: 90, width: 650, height: 800 };
  const state = { minimized: true, maximized: false, fullscreen: false, opacity: original,
    normal: { ...normal }, minimum: [780, 480], pinned: false, destroyed: false, ...options.state };
  const calls = [], restoreStarted = deferred(), unmaximizeStarted = deferred(), getStarted = deferred();
  const config = { restore: 'SYNC', unmaximize: 'SYNC', restoreOpacityFailures: 0, ...options.config };
  const own = new EventEmitter();
  const live = name => { assert.equal(state.destroyed, false, 'destroyed-window call: ' + name); calls.push(name); };
  const restoreComplete = () => { state.minimized = false; state.maximized = true; calls.push('native.restore.complete'); own.emit('restore'); };
  const unmaximizeComplete = () => { state.maximized = false; calls.push('native.unmaximize.complete'); own.emit('unmaximize'); };
  Object.assign(own, {
    isDestroyed: () => state.destroyed,
    isMinimized: () => state.minimized,
    isMaximized: () => !state.minimized && state.maximized,
    isFullScreen: () => !state.minimized && state.fullscreen,
    getNormalBounds: () => ({ ...state.normal }),
    getBounds: () => state.minimized ? { x: -32000, y: -32000, width: 160, height: 28 } :
      state.maximized || state.fullscreen ? { ...area } : { ...state.normal },
    getMinimumSize: () => [...state.minimum],
    setMinimumSize(w, h) { live('minimum'); state.minimum = [w, h]; },
    setBounds(value) { live('bounds'); if (config.boundsError) throw config.boundsError; state.normal = { ...value }; },
    getOpacity() { live('opacity.read'); if (config.readError) throw config.readError; return state.opacity; },
    setOpacity(value) {
      live('opacity:' + value);
      if (value === 0) {
        if (!config.ignoreArm) state.opacity = config.armReadback ?? value;
        if (config.armError) throw config.armError;
      } else {
        if (config.restoreOpacityFailures > 0) { config.restoreOpacityFailures--; throw new Error('opacity restore failure'); }
        if (!config.ignoreOpacityRestore) state.opacity = value;
        config.onOpacityRestore?.();
      }
    },
    restore() { live('restore'); restoreStarted.resolve(); if (config.restoreError) throw config.restoreError;
      if (config.restore === 'EARLY_FLAG') state.minimized = false;
      if (config.restore === 'SYNC') restoreComplete(); },
    minimize() { live('minimize'); state.minimized = true; },
    unmaximize() { live('unmaximize'); unmaximizeStarted.resolve(); if (config.unmaximizeError) throw config.unmaximizeError;
      if (config.unmaximize === 'SYNC') unmaximizeComplete(); },
    setFullScreen(value) { live('fullscreen:' + value); state.fullscreen = value; own.emit('leave-full-screen'); },
    isAlwaysOnTop: () => state.pinned,
    setAlwaysOnTop(value) { live('pin:' + value); state.pinned = value; },
    show() { live('show'); }, focus() { live('focus'); },
    hide() { assert.fail('no persistent hidden state'); },
    setShape() { assert.fail('no desktop geometry'); },
  });
  const screen = { getAllDisplays: () => [{ workArea: area }], getCursorScreenPoint: () => ({ x: 1800, y: 900 }),
    getDisplayNearestPoint: () => ({ workArea: area }) };
  const broker = new NativeCompanion({ window: own, screen, platform: options.platform ?? 'win32',
    initialMode: options.initialMode ?? 'workbench', pinnedPreference: options.pinnedPreference ?? false,
    request: async (route, body) => {
      calls.push('request:' + route.split('/').at(-1));
      if (body?.presentation) { config.onPresentation?.(); if (config.presentationGate) await config.presentationGate.promise; }
      if (route.endsWith('/get')) { getStarted.resolve(); if (config.getGate) await config.getGate.promise; }
      if (route.endsWith('/close') && config.closeError) throw config.closeError;
      return route.endsWith('/status') ? { configured: false } : { status: route.endsWith('/close') ? 'CLOSED' : 'ACTIVE' };
    } });
  function clean() { for (const name of ['restore', 'unmaximize', 'leave-full-screen', 'closed']) assert.equal(own.listenerCount(name), 0, name); }
  return { own, state, config, calls, broker, screen, normal, original, restoreComplete, unmaximizeComplete,
    restoreStarted, unmaximizeStarted, getStarted, clean };
}
const opacityWrites = s => s.calls.filter(value => value.startsWith('opacity:'));
const turns = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

test('Source54 opacity protects only the native minimized restore, before bounds and before network status', async () => {
  const s = scene({ pinnedPreference: true });
  await s.broker.setMode('companion');
  assert.equal(s.state.opacity, s.original); assert.equal(s.state.minimized, false); assert.equal(s.state.maximized, false);
  assert.equal(s.state.normal.width, 520); assert.equal(s.state.normal.height, 760); assert.equal(s.state.pinned, true);
  const at = key => s.calls.indexOf(key);
  assert.ok(at('request:presentation') < at('opacity:0'));
  assert.ok(at('opacity:0') < at('restore')); assert.ok(at('restore') < at('unmaximize'));
  assert.ok(at('bounds') < at('opacity:' + s.original));
  assert.ok(at('opacity:' + s.original) < at('pin:true'));
  assert.ok(at('opacity:' + s.original) < at('request:get'));
  assert.deepEqual(opacityWrites(s), ['opacity:0', 'opacity:' + s.original]);
  assert.equal(s.calls.includes('show'), false); assert.equal(s.calls.includes('focus'), false); s.clean();
});

test('Source54 pending presentation admission and pending GET never hold the window transparent', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const presentationGate = deferred(), getGate = deferred(), s = scene({ config: { presentationGate, getGate } });
  const pending = s.broker.setMode('companion'); await turns();
  assert.deepEqual(opacityWrites(s), []); assert.equal(s.state.minimized, true);
  t.mock.timers.tick(10000); presentationGate.resolve(); await s.getStarted.promise;
  assert.equal(s.state.opacity, s.original); assert.equal(s.state.normal.width, 520);
  const after = [...s.calls]; t.mock.timers.tick(10000); await turns(); assert.deepEqual(s.calls, after);
  getGate.resolve(); await pending; s.clean();
});

test('Source54 normal, non-Windows, workbench, and generic reveal paths do not change opacity', async () => {
  for (const input of [{ state: { minimized: false, maximized: true } }, { platform: 'linux' }, { platform: 'darwin' }]) {
    const s = scene(input); await s.broker.setMode('companion'); assert.deepEqual(opacityWrites(s), []); s.clean();
  }
  const s = scene(); await s.broker.setMode('workbench'); revealOwnWindow(s.own, s.screen);
  assert.deepEqual(opacityWrites(s), []); assert.equal(s.state.maximized, true); s.clean();
});

test('Source54 companion user rectangle and exact opacity are restored without changing pin preference', async () => {
  const s = scene({ initialMode: 'companion', opacity: 0.4, pinnedPreference: true, state: { pinned: true } });
  await s.broker.setMode('companion', { anchor: { x: 10, y: 10 } });
  assert.deepEqual(s.state.normal, s.normal); assert.equal(s.state.opacity, 0.4); assert.equal(s.state.pinned, true);
  assert.equal(s.calls.some(value => value.startsWith('pin:')), false); s.clean();
});

test('Source54 invalid or unavailable original opacity fails before changing native geometry', async () => {
  for (const original of [0, -0.1, 1.1, NaN]) {
    const s = scene({ opacity: original }); await assert.rejects(s.broker.setMode('companion'), /opacity/);
    assert.deepEqual(opacityWrites(s), []); assert.equal(s.calls.includes('restore'), false); s.clean();
  }
  const s = scene({ config: { readError: new Error('opacity unavailable') } });
  await assert.rejects(s.broker.setMode('companion'), /opacity unavailable/);
  assert.equal(s.calls.includes('restore'), false); s.clean();
});

test('Source54 setter throw-after-change and readback mismatch recover before any restore', async () => {
  for (const config of [{ armError: new Error('arm changed then failed') }, { armReadback: 0.1 }, { ignoreArm: true }]) {
    const s = scene({ config }); await assert.rejects(s.broker.setMode('companion'), /arm changed|not applied/);
    assert.equal(s.state.opacity, s.original); assert.equal(s.state.minimized, true);
    assert.equal(s.calls.includes('restore'), false); assert.equal(s.calls.includes('bounds'), false); s.clean();
  }
});

test('Source54 native failures retain original errors and minimize our Main before restoring opacity', async () => {
  for (const key of ['restoreError', 'unmaximizeError', 'boundsError']) {
    const failure = new Error(key), s = scene({ pinnedPreference: true, config: { [key]: failure } });
    await assert.rejects(s.broker.setMode('companion'), error => error === failure);
    assert.equal(s.state.opacity, s.original); assert.equal(s.state.minimized, true); assert.equal(s.broker.mode, 'workbench');
    assert.equal(s.state.pinned, false); assert.deepEqual(s.state.normal, s.normal);
    assert.ok(s.calls.indexOf('minimize') < s.calls.lastIndexOf('opacity:' + s.original)); s.clean();
  }
});

test('Source54 asynchronous restore retains opacity protection through premature flags/events', async () => {
  const s = scene({ config: { restore: 'EARLY_FLAG', unmaximize: 'DEFERRED' } });
  const pending = s.broker.setMode('companion'); await s.restoreStarted.promise; await turns();
  assert.equal(s.state.opacity, 0); assert.equal(s.calls.includes('bounds'), false); assert.equal(s.calls.includes('unmaximize'), false);
  s.restoreComplete(); await s.unmaximizeStarted.promise; s.own.emit('unmaximize'); await turns();
  assert.equal(s.state.opacity, 0); assert.equal(s.calls.includes('bounds'), false);
  s.unmaximizeComplete(); await pending; assert.equal(s.state.opacity, s.original); s.clean();
});

test('Source54 restore and unmaximize share one two-second budget and late events cannot write bounds', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const s = scene({ config: { restore: 'DEFERRED', unmaximize: 'DEFERRED' } });
  const pending = s.broker.setMode('companion');
  const rejected = assert.rejects(pending, /尚未退出放大或最小化/);
  await s.restoreStarted.promise; t.mock.timers.tick(1400); s.restoreComplete(); await s.unmaximizeStarted.promise;
  t.mock.timers.tick(599); assert.equal(s.state.opacity, 0);
  t.mock.timers.tick(1); await rejected; assert.equal(s.state.opacity, s.original); assert.equal(s.state.minimized, true);
  assert.equal(s.calls.includes('bounds'), false); s.clean();
  const after = [...s.calls]; s.unmaximizeComplete(); s.own.emit('restore'); await turns();
  assert.deepEqual(s.calls, [...after, 'native.unmaximize.complete']); t.mock.timers.tick(10000); s.clean();
});

test('Source54 newer workbench intent cleans opacity immediately and fences the old geometry', async () => {
  const s = scene({ config: { restore: 'DEFERRED' } });
  const pending = s.broker.setMode('companion'), rejected = assert.rejects(pending, error => error.code === 'PRESENTATION_CHANGED');
  await s.restoreStarted.promise; const next = s.broker.setMode('workbench');
  assert.equal(s.state.opacity, s.original); await rejected; await next;
  assert.equal(s.broker.mode, 'workbench'); assert.equal(s.calls.includes('unmaximize'), false);
  assert.notEqual(s.state.normal.width, 520); const count = s.calls.length;
  s.restoreComplete(); await turns(); assert.deepEqual(s.calls.slice(count), ['native.restore.complete']); s.clean();
});

test('Source54 closing cleans before the close API and destroyed windows receive no opacity or geometry calls', async () => {
  const s = scene({ config: { restore: 'DEFERRED' } });
  const pending = s.broker.setMode('companion'), rejected = assert.rejects(pending, /closed/);
  await s.restoreStarted.promise; await s.broker.close(); await rejected;
  assert.equal(s.state.opacity, s.original); assert.ok(s.calls.indexOf('opacity:' + s.original) < s.calls.indexOf('request:close')); s.clean();
  const d = scene({ config: { restore: 'DEFERRED' } });
  const dying = d.broker.setMode('companion'), dead = assert.rejects(dying, /closed/);
  await d.restoreStarted.promise; const count = d.calls.length; d.state.destroyed = true; d.own.emit('closed'); await dead;
  assert.deepEqual(d.calls.slice(count), []); assert.equal(revealOwnWindow(d.own, d.screen), false); d.clean();
});

test('Source54 one failed opacity restoration is retried with the exact original value', async () => {
  const s = scene({ config: { restoreOpacityFailures: 1 } }); await s.broker.setMode('companion');
  assert.deepEqual(opacityWrites(s), ['opacity:0', 'opacity:' + s.original, 'opacity:' + s.original]);
  assert.equal(s.state.opacity, s.original); s.clean();
});

test('Source54 failed restoration stays pending; explicit own reveal recovers before any show or focus', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const s = scene({ config: { restoreOpacityFailures: 4 } });
  await assert.rejects(s.broker.setMode('companion'), /opacity recovery is pending/);
  assert.equal(s.state.opacity, 0); assert.equal(s.state.normal.width, 520); assert.equal(s.broker.mode, 'workbench');
  assert.equal(opacityWrites(s).length, 3); const count = s.calls.length;
  t.mock.timers.tick(10000); await turns(); assert.equal(s.calls.length, count, 'no polling or unbounded retries');
  assert.throws(() => revealOwnWindow(s.own, s.screen), /opacity recovery is pending/);
  assert.equal(s.calls.includes('show'), false); assert.equal(s.calls.includes('focus'), false);
  const other = scene({ state: { minimized: false } }); revealOwnWindow(other.own, other.screen);
  assert.deepEqual(opacityWrites(other), [], 'pending cleanup is exact-window only');
  const before = s.calls.length; assert.equal(revealOwnWindow(s.own, s.screen), true);
  const recovered = s.calls.slice(before); assert.ok(recovered.indexOf('opacity:' + s.original) < recovered.indexOf('show'));
  assert.equal(s.state.opacity, s.original); const writes = opacityWrites(s).length;
  revealOwnWindow(s.own, s.screen); assert.equal(opacityWrites(s).length, writes, 'cleanup is idempotent'); s.clean(); other.clean();
});

test('Source54 original native error survives a simultaneous opacity-recovery failure', async () => {
  const original = new Error('original unmaximize failure');
  const s = scene({ config: { unmaximizeError: original, restoreOpacityFailures: 2 } });
  await assert.rejects(s.broker.setMode('companion'), error => error === original && error.opacityRecoveryPending === true);
  assert.equal(s.state.minimized, true); assert.deepEqual(s.state.normal, s.normal);
  revealOwnWindow(s.own, s.screen); assert.equal(s.state.opacity, s.original); s.clean();
});

test('Source54 explicit recovery during a pending transition cancels the old bounds and timers', async t => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const s = scene({ config: { restore: 'DEFERRED' } });
  const pending = s.broker.setMode('companion'), rejected = assert.rejects(pending, error => error.code === 'PRESENTATION_CHANGED');
  await s.restoreStarted.promise; revealOwnWindow(s.own, s.screen); const afterReveal = [...s.calls]; await rejected;
  assert.equal(s.state.opacity, s.original); assert.deepEqual(s.calls, afterReveal, 'cancelled companion writes no later geometry');
  assert.notEqual(s.state.normal.width, 520);
  const count = s.calls.length; t.mock.timers.tick(10000); await turns(); assert.equal(s.calls.length, count); s.clean();
});

test('Source54 an intent changed by the restore setter cannot commit stale mode or pin', async () => {
  const s = scene({ pinnedPreference: true }); let next;
  s.config.onOpacityRestore = () => { delete s.config.onOpacityRestore; next = s.broker.setMode('workbench'); };
  await assert.rejects(s.broker.setMode('companion'), error => error.code === 'PRESENTATION_CHANGED');
  await next; assert.equal(s.state.opacity, s.original); assert.equal(s.broker.mode, 'workbench');
  assert.equal(s.calls.includes('pin:true'), false); s.clean();
});

'use strict';
const {quickFileLinkRef,quickNativeReview}=require('./quick-journal.cjs');
const {inputAttachments}=require('./native-input-attachments.cjs');
const {conversationReference}=require('./conversation-reference.cjs');
const { randomUUID } = require('node:crypto');
const { performance } = require('node:perf_hooks');
const { exactKeys } = require('./ipc-policy.cjs');

// Only this module's temporary opacity changes are recoverable here. Never
// infer ownership from an invisible window or overwrite a user's opacity.
const ownedOpacity = new WeakMap();
const restoreTimeout = () => new Error('自身窗口尚未退出放大或最小化状态，请重新打开轻量对话。');
function presentationChanged() {
  const error = new Error('Collaboration presentation changed; the previous document-open action is stale');
  error.code = 'PRESENTATION_CHANGED';
  return error;
}

function armOwnedOpacity(window, assertCurrent) {
  const original = window.getOpacity();
  if (!Number.isFinite(original) || original <= 0 || original > 1) throw new Error('Actual visible own-window opacity is unavailable');
  const deadline = performance.now() + 2000, aborted = new Set();
  let timer, error, stopped = false, closed = false, attempted = false, normalized = false, nativeStarted = false;
  const guard = {
    active: true,
    assertCurrent() {
      if (error) throw error;
      assertCurrent();
      if (performance.now() >= deadline) { guard.abort(restoreTimeout()); throw error; }
    },
    onAbort(listener) { aborted.add(listener); if (error) listener(error); return () => aborted.delete(listener); },
    nativeStarted() { nativeStarted = true; },
    normalized() { normalized = true; },
    cleanup(retry = false) {
      if (stopped) return true;
      guard.active = false;
      clearTimeout(timer); window.removeListener?.('closed', onClosed);
      if (closed || window.isDestroyed?.()) { stopped = true; ownedOpacity.delete(window); return true; }
      if (attempted && !retry) return false;
      attempted = true;
      // A failed restore must not deliberately reveal a still-maximized Main.
      // Reassert only our window's minimized state before restoring opacity.
      if (error && nativeStarted && !normalized) {
        try { window.minimize(); } catch { /* Opacity cleanup must still run. */ }
      }
      for (let attempt = 0; attempt < 2; attempt++) {
        try {
          if (window.isDestroyed?.()) { stopped = true; ownedOpacity.delete(window); return true; }
          window.setOpacity(original);
          if (closed || window.isDestroyed?.()) { stopped = true; ownedOpacity.delete(window); return true; }
          if (window.getOpacity() === original) { stopped = true; ownedOpacity.delete(window); return true; }
        } catch { /* One bounded retry; later explicit reveal can recover. */ }
      }
      return false;
    },
    abort(reason) {
      if (!error) error = reason;
      for (const listener of [...aborted]) listener(error);
      return guard.cleanup();
    },
  };
  const onClosed = () => { closed = true; guard.abort(new Error('Native collaboration is closed')); };
  ownedOpacity.set(window, guard); // A setter can throw after changing native state.
  window.on?.('closed', onClosed);
  timer = setTimeout(() => guard.abort(restoreTimeout()), 2000);
  try {
    guard.assertCurrent();
    window.setOpacity(0);
    guard.assertCurrent();
    if (window.getOpacity() !== 0) throw new Error('Own-window opacity protection was not applied');
    guard.assertCurrent();
  } catch (cause) { if (!guard.abort(cause)) cause.opacityRecoveryPending = true; throw cause; }
  return guard;
}

function recoverOwnedOpacity(window) {
  const pending = ownedOpacity.get(window);
  if (!pending) return;
  const recovered = pending.active ? pending.abort(presentationChanged()) : pending.cleanup(true);
  if (!recovered) throw new Error('Own-window opacity recovery is pending; please reveal this window again');
}

function validRectangle(value) {
  return value && ['x', 'y', 'width', 'height'].every(key => Number.isFinite(value[key])) && value.width > 0 && value.height > 0;
}

/** Electron bounds and workArea are both DIP. Only the supplied owned window moves. */
function clampOwnWindowBounds(bounds, workArea) {
  if (!validRectangle(bounds) || !validRectangle(workArea)) return undefined;
  const width = Math.max(1, Math.min(Math.round(bounds.width), Math.floor(workArea.width)));
  const height = Math.max(1, Math.min(Math.round(bounds.height), Math.floor(workArea.height)));
  const left = Math.ceil(workArea.x), top = Math.ceil(workArea.y);
  return { x: Math.min(Math.max(Math.round(bounds.x), left), Math.floor(workArea.x + workArea.width - width)),
    y: Math.min(Math.max(Math.round(bounds.y), top), Math.floor(workArea.y + workArea.height - height)), width, height };
}

function ownWindowWorkArea(window, screen) {
  if (!screen) return undefined;
  const bounds = window.getBounds?.();
  const displays = screen.getAllDisplays?.() ?? [];
  let best, overlap = 0;
  if (validRectangle(bounds)) for (const display of displays) {
    const area = display.workArea;
    if (!validRectangle(area)) continue;
    const covered = Math.max(0, Math.min(bounds.x + bounds.width, area.x + area.width) - Math.max(bounds.x, area.x)) *
      Math.max(0, Math.min(bounds.y + bounds.height, area.y + area.height) - Math.max(bounds.y, area.y));
    if (covered > overlap) { overlap = covered; best = area; }
  }
  if (best) return best;
  // A removed monitor must not keep the app on an unreachable old rectangle.
  const cursor = screen.getCursorScreenPoint?.();
  const area = cursor && screen.getDisplayNearestPoint?.(cursor)?.workArea;
  if (validRectangle(area)) return area;
  const nearest = validRectangle(bounds) ? screen.getDisplayMatching?.(bounds)?.workArea : undefined;
  return validRectangle(nearest) ? nearest : displays.find(display => validRectangle(display.workArea))?.workArea;
}

/** Shared explicit app recovery. Never captures or injects input into any window. */
function revealOwnWindow(window, screen, { focus = true, show = true, size, minimum, bounds: requestedBounds } = {}) {
  if (!window || window.isDestroyed?.()) return false;
  if (show || focus) recoverOwnedOpacity(window);
  if (show && window.isMinimized?.()) window.restore();
  const preferred = validRectangle(requestedBounds) ? requestedBounds : undefined;
  const area = ownWindowWorkArea(preferred ? { getBounds: () => preferred } : window, screen);
  const currentMinimum = window.getMinimumSize?.();
  const wantedMinimum = minimum ?? currentMinimum;
  if (wantedMinimum) {
    const width = area ? Math.min(wantedMinimum[0], Math.floor(area.width)) : wantedMinimum[0];
    const height = area ? Math.min(wantedMinimum[1], Math.floor(area.height)) : wantedMinimum[1];
    if (!currentMinimum || currentMinimum[0] !== width || currentMinimum[1] !== height) window.setMinimumSize(width, height);
  }
  if (!window.isFullScreen?.() && !window.isMaximized?.()) {
    const before = window.getBounds?.();
    const desired = preferred ?? (validRectangle(before) ? { ...before, ...(size ? { width: size[0], height: size[1] } : {}) } : undefined);
    const next = desired && clampOwnWindowBounds(desired, area);
    if (next) {
      if (!validRectangle(before) || Object.keys(next).some(key => next[key] !== before[key])) window.setBounds(next, false);
    } else if (size && !screen) window.setSize(size[0], size[1], false);
  }
  if (show) window.show();
  if (focus) window.focus();
  return true;
}

// Promise continuations can run inside Electron's WM_SIZE notification. Leave
// that native message stack before using the resulting flags or placement.
async function afterNativeTurn(window, assertCurrent, guard) {
  assertCurrent();
  await new Promise((resolve, reject) => {
    let immediate, stopAbort, settled = false;
    const finish = error => {
      if (settled) return;
      settled = true; clearImmediate(immediate); stopAbort?.();
      window.removeListener?.('closed', closed);
      error ? reject(error) : resolve();
    };
    const closed = () => finish(new Error('Native collaboration is closed'));
    window.on?.('closed', closed);
    if (guard) stopAbort = guard.onAbort(finish);
    if (settled) { stopAbort?.(); return; }
    immediate = setImmediate(() => { try { assertCurrent(); finish(); } catch (error) { finish(error); } });
  });
  assertCurrent();
}

async function nativeTransition(window, isActive, change, event, assertCurrent, guard, trace) {
  assertCurrent();
  const active = window[isActive]?.() === true;
  trace?.('native-check', { event, active });
  if (!active) return;
  await new Promise((resolve, reject) => {
    let timer, stopAbort, settled = false;
    const clean = () => {
      clearTimeout(timer);
      stopAbort?.();
      window.removeListener?.(event, changed);
      window.removeListener?.('closed', closed);
    };
    const finish = error => {
      if (settled) return;
      settled = true; clean();
      error ? reject(error) : resolve();
    };
    const changed = () => {
      try {
        assertCurrent();
        const inactive = window[isActive]?.() !== true;
        trace?.('native-event', { event, inactive });
        if (inactive) finish();
      } catch (error) { finish(error); }
    };
    const closed = () => finish(new Error('Native collaboration is closed'));
    window.on?.(event, changed);
    window.on?.('closed', closed);
    if (guard) stopAbort = guard.onAbort(finish);
    else timer = setTimeout(() => finish(restoreTimeout()), 2000);
    if (settled) { stopAbort?.(); return; }
    try {
      assertCurrent();
      trace?.('native-request', { event });
      assertCurrent();
      change();
      // Real BrowserWindow is an EventEmitter. A cleared early flag is not its
      // completion event. Minimal non-event host doubles model sync changes.
      if (typeof window.on !== 'function') changed();
    } catch (error) { finish(error); }
  });
  await afterNativeTurn(window, assertCurrent, guard);
  if (window[isActive]?.() === true) throw restoreTimeout();
  trace?.('native-settled', { event });
}

async function leaveExpandedPresentation(window, assertCurrent = () => {}, guard, trace) {
  // A minimized Windows window can report isMaximized() false while retaining
  // a maximized restore state. Restore first, then inspect and leave that state
  // before applying the companion rectangle in the same presentation queue.
  for (const [isActive, change, event] of [
    ['isMinimized', () => window.restore(), 'restore'],
    ['isFullScreen', () => window.setFullScreen(false), 'leave-full-screen'],
    ['isMaximized', () => window.unmaximize(), 'unmaximize'],
  ]) {
    await nativeTransition(window, isActive, change, event, assertCurrent, guard, trace);
  }
}

function frozenCompanionLayout(window, screen, preferred) {
  if (['getBounds', 'setBounds', 'isMinimized', 'isMaximized', 'isFullScreen', 'on', 'removeListener']
    .some(name => typeof window[name] !== 'function')) {
    throw new Error('Actual own-window bounds authority is unavailable');
  }
  const observedNormal = window.getNormalBounds?.();
  const normal = preferred ?? (validRectangle(observedNormal) ? observedNormal : window.getBounds());
  if (!validRectangle(normal)) throw new Error('Actual own-window normal bounds are unavailable');
  const desired = preferred ?? { ...normal, width: 520, height: 760 };
  const area = ownWindowWorkArea({ getBounds: () => desired }, screen);
  const target = screen ? clampOwnWindowBounds(desired, area) :
    Object.fromEntries(['x', 'y', 'width', 'height'].map(key => [key, Math.round(desired[key])]));
  if (!validRectangle(target)) throw new Error('An available companion display is required');
  return { target: Object.freeze(target), workArea: area && Object.freeze({ ...area }),
    currentWorkArea: () => ownWindowWorkArea({ getBounds: () => target }, screen), minimum: Object.freeze([
    area ? Math.min(390, Math.floor(area.width)) : 390,
    area ? Math.min(480, Math.floor(area.height)) : 480,
  ]) };
}

function targetMatches(actual, target) {
  return validRectangle(actual) && ['x', 'y', 'width', 'height'].every(key => Math.abs(actual[key] - target[key]) <= 1);
}

function sameBounds(actual, expected) {
  return validRectangle(actual) && validRectangle(expected) &&
    ['x', 'y', 'width', 'height'].every(key => actual[key] === expected[key]);
}

function compensatedTargetRequest(target, requested, actual, minimum, workArea) {
  if (!validRectangle(actual) || !validRectangle(workArea) ||
      ['x', 'y'].some(key => Math.abs(actual[key] - target[key]) > 1)) return;
  const delta = { width: actual.width - requested.width, height: actual.height - requested.height };
  if (Object.values(delta).some(value => !Number.isSafeInteger(value) || Math.abs(value) > 2)) return;
  const corrected = { ...target, width: target.width - delta.width, height: target.height - delta.height };
  if (!Object.values(corrected).every(Number.isSafeInteger) || corrected.width < minimum[0] ||
      corrected.height < minimum[1] || !sameBounds(corrected, clampOwnWindowBounds(corrected, workArea))) return;
  return corrected;
}

function targetNotAcknowledged() {
  const error = new Error('自身窗口尺寸尚未确认，请重新打开轻量对话。');
  error.code = 'OWN_COMPANION_TARGET_NOT_ACKNOWLEDGED';
  return error;
}

async function acknowledgeCompanionTarget(window, layout, assertCurrent, guard, trace) {
  const { target, minimum, workArea } = layout;
  const normal = () => window.isMinimized?.() !== true && window.isMaximized?.() !== true && window.isFullScreen?.() !== true;
  const currentArea = () => !workArea || sameBounds(workArea, layout.currentWorkArea());
  assertCurrent();
  if (!normal() || !currentArea()) throw targetNotAcknowledged();
  const currentMinimum = window.getMinimumSize?.();
  if (!currentMinimum || minimum.some((value, i) => value !== currentMinimum[i])) window.setMinimumSize(...minimum);
  let requested = target;
  for (let attempt = 0; attempt < 2; attempt++) {
    assertCurrent();
    if (!normal() || !currentArea()) throw targetNotAcknowledged();
    const before = window.getBounds();
    const applied = !targetMatches(before, target);
    trace?.('target-request', { attempt, target, requested, before, applied });
    assertCurrent();
    if (applied) window.setBounds({ ...requested }, false);
    await afterNativeTurn(window, assertCurrent, guard);
    const actual = window.getBounds();
    trace?.('target-readback', { attempt, target, requested, actual });
    await afterNativeTurn(window, assertCurrent, guard);
    const settled = window.getBounds();
    trace?.('target-settled-readback', { attempt, target, requested, actual: settled });
    assertCurrent();
    if (!normal() || !currentArea()) throw targetNotAcknowledged();
    if (sameBounds(actual, settled) && targetMatches(settled, target)) return { ...settled };
    if (attempt === 0 && applied && sameBounds(actual, settled)) {
      const corrected = compensatedTargetRequest(target, requested, settled, minimum, workArea);
      if (corrected) {
        trace?.('target-compensation', { target, requested: corrected,
          roundtrip_delta: { width: settled.width - requested.width, height: settled.height - requested.height } });
        requested = corrected;
      }
    }
  }
  throw targetNotAcknowledged();
}

function reference(value) {
  exactKeys(value, ['id', 'revision', 'sha256']);
  if (!/^[A-Za-z0-9_-]{1,100}$/.test(value.id) || !Number.isSafeInteger(value.revision) ||
      value.revision < 1 || !/^[a-f0-9]{64}$/.test(value.sha256)) throw new Error('Invalid exact context reference');
  return { ...value };
}

function characters(text) {
  if(typeof text!=='string') throw new Error('Actual Unicode text is required');
  const value=Array.from(text);
  if(value.some(char=>char.length===1&&char.charCodeAt(0)>=0xD800&&char.charCodeAt(0)<=0xDFFF)) {
    throw new Error('Message text contains an invalid Unicode surrogate');
  }
  return value;
}

function observationInput(raw = {}) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw) ||
      Object.keys(raw).some(key => !['intent', 'need', 'taskRef'].includes(key))) {
    throw new Error('Unsupported current-page observation fields');
  }
  const intent = raw.intent ?? '读取当前页面以准备本条问答';
  if (typeof intent !== 'string' || !intent.trim() || characters(intent).length > 8192) throw new Error('A bounded observation intent is required');
  const need = raw.need ?? { metadata: true, accessibility: true, visual: true, ocr: false };
  exactKeys(need, ['metadata', 'accessibility', 'visual', 'ocr']);
  if (Object.values(need).some(value => typeof value !== 'boolean') || !Object.values(need).some(Boolean)) {
    throw new Error('Declared observation modes are required');
  }
  return { operation_id: randomUUID(), intent, need: { ...need },
    task_ref: raw.taskRef == null ? null : reference(raw.taskRef) };
}

// The companion changes only its own normal window. No desktop/AppBar geometry,
// foreign-window attachment, resolution, DPI, input injection or model loop.
class NativeCompanion {
  constructor({ window, screen, ownedWindows = {}, request, imageReader, onContext, onGeometryDiagnostic, initialMode = 'workbench', pinnedPreference = false, onPinnedChange, isClosing = () => false, platform = process.platform, ownerPid = process.pid }) {
    if (!['companion','workbench'].includes(initialMode) || typeof pinnedPreference !== 'boolean') throw new Error('Explicit window preferences required');
    this.window = window; this.request = request; this.imageReader = imageReader;
    this.screen = screen;
    this.ownedWindows = ownedWindows;
    this.platform = platform; this.ownerPid = ownerPid; this.mode = initialMode;
    this.context = null; this.closed = false; this.tail = Promise.resolve();
    this.onContext = typeof onContext === 'function' ? onContext : undefined;
    this.messageContexts = new Map();
    this.pinnedPreference = pinnedPreference;
    this.onPinnedChange = onPinnedChange;
    this.isClosing = isClosing;
    this.comparisonActive = false;
    this.companionBounds = undefined;
    this.presentationEpoch = 0;
    this.geometryDiagnostic = typeof onGeometryDiagnostic === 'function' ? onGeometryDiagnostic :
      process.env.EEAS_DIAGNOSTIC_NATIVE_GEOMETRY === '1' ? value => process.stderr.write(JSON.stringify(value) + '\n') : undefined;
  }
  serial(fn) {
    const result = this.tail.then(fn); this.tail = result.catch(() => undefined); return result;
  }
  assertOpen() {
    if (this.closed || this.isClosing() || this.window.isDestroyed?.()) throw new Error('Native collaboration is closed');
  }
  getPresentationEpoch() { return this.presentationEpoch; }
  assertPresentation(epoch) {
    this.assertOpen();
    if (epoch !== this.presentationEpoch) {
      throw presentationChanged();
    }
  }
  observedCompanionBounds() {
    if (this.mode !== 'companion') return;
    const expanded = this.window.isMinimized?.() === true || this.window.isFullScreen?.() === true || this.window.isMaximized?.() === true;
    const bounds = expanded ? this.window.getNormalBounds?.() : this.window.getBounds?.();
    if (validRectangle(bounds)) return { ...bounds };
  }
  rememberCompanionBounds() {
    const bounds = this.observedCompanionBounds();
    if (bounds) this.companionBounds = bounds;
  }
  traceGeometry(stage, epoch, details = {}) {
    if (!this.geometryDiagnostic) return;
    try {
      const window = this.window, alive = !window.isDestroyed?.();
      this.geometryDiagnostic({ scope: 'NATIVE_COMPANION_GEOMETRY', stage, epoch,
        ...(alive ? { actual: window.getBounds?.(), minimized: window.isMinimized?.() === true,
          maximized: window.isMaximized?.() === true, fullscreen: window.isFullScreen?.() === true } : { destroyed: true }), ...details });
    } catch { /* Diagnostic output must not change a presentation outcome. */ }
  }
  identity(owned = this.window) {
    const handle = owned.getNativeWindowHandle();
    if (!Buffer.isBuffer(handle) || ![4, 8].includes(handle.length)) throw new Error('Actual owned native window is unavailable');
    const hwnd = handle.length === 8 ? handle.readBigUInt64LE() : BigInt(handle.readUInt32LE());
    if (hwnd <= 0n) throw new Error('Actual owned native window is unavailable');
    return { hwnd_decimal: hwnd.toString(), owner_pid: this.ownerPid };
  }
  async initialize() {
    if (this.platform !== 'win32') return this.get();
    await this.request('/api/desktop-host/collaboration/bind', {
      main_window: this.identity(), owned_windows: Object.fromEntries(Object.entries(this.ownedWindows)
        .map(([role, owned]) => [role, this.identity(owned)])), presentation: this.mode === 'companion' ? 'NATIVE_COMPANION' : 'PROJECT_WORKBENCH',
    });
    return this.get();
  }
  async get() {
    if (this.closed) return { mode: this.mode, status: this.closeStatus ?? 'CLOSE_UNKNOWN', capabilities: { current_context: false, native_input: false } };
    const authority = await this.request('/api/desktop-host/collaboration/get');
    const contextStatus = await this.request('/api/desktop-host/context/status');
    if (this.closed) return this.get();
    return { ...authority, mode: this.mode, platform: this.platform,
      fullscreen: this.window.isFullScreen?.() === true,
      pinned: this.window.isAlwaysOnTop?.() === true,
      pinned_preference: this.pinnedPreference,
      comparison_active: false,
      presentation_epoch: this.presentationEpoch,
      context_capabilities: authority.context, context_status: contextStatus,
      capabilities: { current_context: authority.status === 'ACTIVE' && contextStatus.configured === true,
        native_input: false }, context: this.context,
      desktop_layout: 'UNCHANGED', automatic_input_authorized: false };
  }
  capture(raw) {
    return this.serial(async () => {
      if (this.closed) throw new Error('Native collaboration is closed');
      const body = observationInput(raw), state = await this.get();
      body.expected_workspace_session_ref = state.session_ref ?? null;
      const snapshot = await this.request('/api/desktop-host/context/observe', body);
      if (snapshot.operation_id !== body.operation_id || snapshot.automatic_input_authorized !== false) {
        throw new Error('Current-page observation receipt differs from the exact request');
      }
      if (snapshot.preview?.status === 'AVAILABLE' && this.imageReader) {
        const image = snapshot.preview.image_ref;
        const route = image?.download_url;
        if (typeof route !== 'string' || !/^\/api\/desktop-host\/context\/images\/[A-Za-z0-9_-]{1,100}\?revision=[1-9][0-9]*&sha256=[a-f0-9]{64}$/.test(route)) {
          throw new Error('Untrusted current-page image route');
        }
        // Original registered bytes are fetched privately by Main. No host
        // credentials, arbitrary URL or filesystem primitive reaches the view.
        snapshot.preview.data_url = await this.imageReader(route, image);
        delete snapshot.preview.image_ref.download_url;
      }
      this.context = snapshot;
      return snapshot;
    });
  }
  async applyMode(mode, epoch, anchor, checkCurrent) {
    // Internal queue member: calling public setMode from another serial member
    // would wait on itself. All own-window mutation stays in this same queue.
    this.assertOpen();
    const assertCurrent = () => { this.assertPresentation(epoch); checkCurrent?.(); };
    assertCurrent();
    const strictCompanion = this.platform === 'win32' && mode === 'companion';
    // Observe without committing: failure must retain the last acknowledged
    // companion cache, never a late native placement or an iconic rectangle.
    const observed = this.observedCompanionBounds();
    if (!strictCompanion && observed) this.companionBounds = observed;
    const preferred = observed ?? (this.companionBounds && { ...this.companionBounds }) ??
      (strictCompanion && anchor && this.screen ? this.anchoredBounds(anchor) : undefined);
    const layout = strictCompanion ? frozenCompanionLayout(this.window, this.screen, preferred) : undefined;
    const trace = (stage, details) => this.traceGeometry(stage, epoch, details);
    const value = await this.request('/api/desktop-host/collaboration/presentation', {
      presentation: mode === 'companion' ? 'NATIVE_COMPANION' : 'PROJECT_WORKBENCH',
    });
    assertCurrent();
    if (value.status !== 'ACTIVE') throw new Error('Native collaboration is not active');
    recoverOwnedOpacity(this.window);
    if (layout) trace('target-frozen', { target: layout.target, minimum: layout.minimum });
    const bounds = { show: false, focus: false,
      minimum: [mode === 'companion' ? 390 : 780, 480],
      ...(mode === 'companion' && this.companionBounds ? { bounds: this.companionBounds } :
        mode === 'companion' && anchor && this.screen ? {bounds: this.anchoredBounds(anchor)} :
        { size: mode === 'companion' ? [520, 760] : [1180, 880] }) };
    const guard = this.platform === 'win32' && mode === 'companion' && this.window.isMinimized?.() === true
      ? armOwnedOpacity(this.window, assertCurrent) : undefined;
    const current = () => guard ? guard.assertCurrent() : assertCurrent();
    const commitPresentation = () => {
      this.mode = mode;
      if (mode === 'workbench') this.comparisonActive = false;
      const pinned = mode === 'companion' && this.pinnedPreference;
      if ((this.window.isAlwaysOnTop?.() === true) !== pinned) this.window.setAlwaysOnTop(pinned);
    };
    let failure, acknowledged;
    try {
      if (mode === 'companion') {
        guard?.nativeStarted();
        await leaveExpandedPresentation(this.window, current, guard, layout ? trace : undefined);
      }
      current();
      if (layout) {
        acknowledged = await acknowledgeCompanionTarget(this.window, layout, current, guard, trace);
      } else {
        if (!guard) commitPresentation();
        revealOwnWindow(this.window, this.screen, bounds);
      }
      current();
      guard?.normalized();
      if (layout) trace('target-acknowledged', { target: layout.target, acknowledged });
    } catch (error) {
      failure = error;
      guard?.abort(error);
      if (layout) trace('target-failed', { code: error.code ?? 'NATIVE_PRESENTATION_FAILED' });
      throw error;
    } finally {
      if (guard && !guard.cleanup()) {
        if (failure) failure.opacityRecoveryPending = true;
        else throw new Error('Own-window opacity recovery is pending; please reveal this window again');
      }
    }
    assertCurrent();
    if (guard || layout) commitPresentation();
    if (layout) this.companionBounds = acknowledged;
    else if (mode === 'companion') this.rememberCompanionBounds();
    return this.get();
  }
  anchoredBounds(anchor) {
    if (!anchor || !Number.isFinite(anchor.x) || !Number.isFinite(anchor.y)) throw new Error('An owned bubble anchor is required');
    const area = this.screen.getDisplayNearestPoint(anchor)?.workArea;
    if (!validRectangle(area)) throw new Error('An available bubble display is required');
    return clampOwnWindowBounds({x:anchor.x+16,y:anchor.y-760-12,width:520,height:760},area);
  }
  async setMode(mode, {anchor} = {}) {
    if (!['companion', 'workbench'].includes(mode)) throw new Error('Unknown collaboration presentation');
    this.assertOpen();
    const epoch = ++this.presentationEpoch;
    ownedOpacity.get(this.window)?.abort(presentationChanged());
    return this.serial(async () => {
      this.assertPresentation(epoch);const state=await this.applyMode(mode,epoch,anchor);
      this.assertPresentation(epoch);return state;
    });
  }
  async submitMessage(raw) {
    exactKeys(raw, ['operationId', 'rawText', ...['contextRef', 'providerProfileRef', 'fileSourceRef', 'fileLinkRef', 'conversationId', 'projectId', 'nativeReview', 'inputAttachments', 'conversationRef', 'contextSelectionId'].filter(key => Object.hasOwn(raw ?? {}, key))]);
    if (this.closed) throw new Error('Native collaboration is closed');
    if (typeof raw.rawText !== 'string' || !raw.rawText.trim() || characters(raw.rawText).length > 16000) {
      throw new Error('A bounded actual message is required');
    }
    // An explicit reference belongs to a frozen annotation. Plain questions
    // read the actual recent external window afresh, even while EEAS has focus.
    // Capture failures leave the question unsent instead of reusing old pixels.
    if (typeof raw.operationId !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(raw.operationId)) throw new Error('Exact message operation required');
    if(raw.contextSelectionId!==undefined&&(typeof raw.contextSelectionId!=='string'||!/^[a-f0-9]{64}$/.test(raw.contextSelectionId)))throw new Error('Exact reviewed context selection required');
    const review=quickNativeReview(raw.nativeReview);
    const attachments=inputAttachments(raw.inputAttachments);
    const conversationRef=conversationReference(raw.conversationRef);
    if(review?.version===1&&(raw.fileSourceRef||raw.fileLinkRef))throw new Error('当前页检查不能混用保存稿输入');
    const explicitRef = raw.contextRef == null ? null : reference(raw.contextRef);
    const fileLink=raw.fileLinkRef==null?null:quickFileLinkRef(raw.fileLinkRef);
    if(fileLink&&(raw.fileSourceRef||explicitRef&&review?.version!==2&&!attachments.length))throw new Error('Choose the saved file or frozen page annotation explicitly');
    if(attachments.length&&explicitRef&&['id','revision','sha256'].some(key=>explicitRef[key]!==attachments[0].context_ref[key]))throw new Error('The primary context must match the first selected input');
    if(raw.projectId!==undefined&&(typeof raw.projectId!=='string'||! /^[A-Za-z0-9_-]{1,100}$/.test(raw.projectId)))throw new Error('Exact question project required');
    if(fileLink&&raw.projectId!==fileLink.project_id)throw new Error('Saved document belongs to another question project');
    if(raw.conversationId!==undefined&&(typeof raw.conversationId!=='string'||! /^[A-Za-z0-9_-]{1,100}$/.test(raw.conversationId)))throw new Error('A bounded conversation identity is required');
    const signature = JSON.stringify({text:raw.rawText,context:explicitRef,provider:raw.providerProfileRef??null,file:raw.fileSourceRef??null,link:fileLink,project:raw.projectId??null,conversation:raw.conversationId??null,...(review?{nativeReview:review}:{}),...(attachments.length?{inputAttachments:attachments}:{}),...(conversationRef?{conversationRef}:{}),...(raw.contextSelectionId?{contextSelectionId:raw.contextSelectionId}:{})});
    let owned = this.messageContexts.get(raw.operationId);
    if (owned && owned.signature !== signature) throw new Error('Message operation input differs');
    if (!owned) {
      owned = {signature, snapshot:attachments.length||conversationRef?Promise.resolve(explicitRef):fileLink&&review?.version!==2?Promise.resolve(null):explicitRef !== null ? Promise.resolve(explicitRef) : this.capture({intent:characters(raw.rawText).slice(0,8192).join('')}).then(snapshot=>{
        this.onContext?.(snapshot);
        return snapshot.observation?.context_ref ?? null;
      })};
      this.messageContexts.set(raw.operationId,owned);
      // Backend keeps the durable operation ledger. This bounded cache only
      // prevents a retry in the same host from changing its original frame.
      if(this.messageContexts.size>256) this.messageContexts.delete(this.messageContexts.keys().next().value);
    }
    let contextRef;
    try { contextRef = await owned.snapshot; }
    catch(error) {
      this.messageContexts.delete(raw.operationId);
      if(review?.version===2)return {operation_id:raw.operationId,status:'NOT_DISPATCHED',dispatch_state:'NOT_DISPATCHED',execution_started:false,error:{code:'CURRENT_PAGE_UNAVAILABLE',message:'未取得当前页，请重新读取'}};
      throw error;
    }
    if (this.closed) throw new Error('Native collaboration is closed');
    if(review?.version===2&&!contextRef)return {operation_id:raw.operationId,status:'NOT_DISPATCHED',dispatch_state:'NOT_DISPATCHED',execution_started:false,error:{code:'CURRENT_PAGE_UNAVAILABLE',message:'未取得当前页，请重新读取'}};
    return this.request('/api/desktop-host/native-messages', {
      operation_id: raw.operationId, raw_text: raw.rawText,...(review?{native_review:review}:{}),
      ...(attachments.length?{input_attachments:attachments}:{}),
      ...(conversationRef?{conversation_ref:conversationRef}:{}),
      ...(raw.contextSelectionId?{context_selection_id:raw.contextSelectionId}:{}),
      context_ref: contextRef, provider_profile_ref: raw.providerProfileRef ?? null,
      file_source_ref: raw.fileSourceRef ?? null,
      ...(fileLink?{file_link_ref:fileLink}:{}),
      ...(raw.projectId?{project_id:raw.projectId}:{}),
      ...(raw.conversationId?{conversation_id:raw.conversationId}:{}),
    });
  }
  async setFullscreen(enabled) {
    if(typeof enabled !== 'boolean') throw new Error('An explicit own-window fullscreen state is required');
    this.assertOpen();
    const epoch = ++this.presentationEpoch;
    ownedOpacity.get(this.window)?.abort(presentationChanged());
    return this.serial(async () => {
      this.assertPresentation(epoch);
      const state=await this.get();
      this.assertPresentation(epoch);
      if(state.status!=='ACTIVE') throw new Error('Native collaboration is not active');
      this.rememberCompanionBounds();
      this.window.setFullScreen(enabled);
      // Electron reports the actual window state, never a CSS/desktop-size claim.
      const actual=await this.get();this.assertPresentation(epoch);return actual;
    });
  }
  async setCompanionPinned(enabled) {
    if (typeof enabled !== 'boolean') throw new Error('An explicit companion pin state is required');
    if (this.closed) throw new Error('Only the active companion window can be pinned');
    const epoch = ++this.presentationEpoch;
    ownedOpacity.get(this.window)?.abort(presentationChanged());
    return this.serial(async () => {
      this.assertPresentation(epoch);
      const state = await this.get();
      this.assertPresentation(epoch);
      if (state.status !== 'ACTIVE') throw new Error('Native collaboration is not active');
      // Persist only a direct user choice. A hidden Quick view and the owned
      // Main share this preference, even when Main is showing the workbench.
      if (this.onPinnedChange) this.onPinnedChange(enabled);
      this.pinnedPreference = enabled;
      this.comparisonActive = false;
      const pinned = this.mode === 'companion' && enabled;
      if ((this.window.isAlwaysOnTop?.() === true) !== pinned) this.window.setAlwaysOnTop(pinned);
      const actual=await this.get();this.assertPresentation(epoch);return actual;
    });
  }
  async prepareDocumentOpen(options = {}) {
    exactKeys(options, ['expectedEpoch', 'current'].filter(key => Object.hasOwn(options ?? {}, key)));
    const epoch = Object.hasOwn(options, 'expectedEpoch') ? options.expectedEpoch : this.presentationEpoch;
    if (!Number.isSafeInteger(epoch) || epoch < 0) throw new Error('An exact presentation epoch is required');
    if (options.current !== undefined && typeof options.current !== 'function') throw new Error('An exact document-open guard is required');
    this.assertOpen();
    // Called only after a confirmed ShellOpen. A newer user choice still wins;
    // the automatic handoff never changes the persistent pin preference.
    const visible = () => this.window.isVisible?.() !== false && this.window.isMinimized?.() !== true;
    const current = () => {
      this.assertPresentation(epoch); options.current?.();
      if (!visible()) throw presentationChanged();
    };
    return this.serial(async () => {
      this.assertPresentation(epoch);
      const state = await this.get();
      this.assertPresentation(epoch);
      if (state.status !== 'ACTIVE') throw new Error('Native collaboration is not active');
      if (!visible()) return state;
      current();
      let actual = state;
      if (this.mode !== 'companion' || this.window.isFullScreen?.() === true || this.window.isMaximized?.() === true)
        actual = await this.applyMode('companion', epoch, undefined, current);
      else this.rememberCompanionBounds();
      current();
      // Inactive show alone can leave this window behind the opened viewer.
      // A synchronous topmost roundtrip raises an unpinned window even when
      // HWND_TOP does not. Always clear our temporary state, including when a
      // synchronous window event invalidates this handoff; queued user choices
      // still run afterwards. Never persist this temporary pin or take focus.
      this.window.showInactive?.();
      current();
      if (this.window.isAlwaysOnTop?.() !== true) {
        try {
          this.window.setAlwaysOnTop(true);
          if (this.window.isAlwaysOnTop() !== true) throw new Error('Temporary window pin was not applied');
          current();
        } finally {
          if (!this.window.isDestroyed?.()) {
            this.window.setAlwaysOnTop(false);
            if (this.window.isAlwaysOnTop() !== false) throw new Error('Temporary window pin was not restored');
          }
        }
      } else this.window.moveTop();
      current();
      return actual;
    });
  }
  async close() {
    this.closed = true;
    ownedOpacity.get(this.window)?.abort(new Error('Native collaboration is closed'));
    this.closeStatus = 'CLOSE_UNKNOWN';
    const receipt = await this.request('/api/desktop-host/collaboration/close', {});
    if (receipt?.status === 'CLOSED') this.closeStatus = 'CLOSED';
    return receipt;
  }
}
module.exports = { NativeCompanion, observationInput, reference, clampOwnWindowBounds, revealOwnWindow, leaveExpandedPresentation };

'use strict';
// Generated from desktop source authority; do not edit independently.
// SHA256 apps/agent-shell/src/original-work-maintenance.ts d82af858d52312e6896e1e7e235c82c3f92ea475eb9fced645b5addb18ffa710
// SHA256 apps/agent-shell/src/work-maintenance-editor.ts 3670098173a4daa29746ae00e582f1168783a87839c9ef77b6cc0652b82a1684
// SHA256 apps/agent-shell/src/original-work-requirements.ts 069eb91b1e7d58985943cea5f5a20a1f07f923ca84b1f9e3dd717c0ffba6bc85
// SHA256 apps/agent-shell/src/original-work-test-cases.ts 673435c5e7472b1ef9c301165b573596f189abb18b82e054292b99eb6856d1b6
const { webcrypto: crypto } = require('node:crypto');
const { canonical, hash, workRequirementScope, OriginalWorkMaintenanceApi, validateMaintenancePrepare, validateMaintenanceRequest, validateMaintenanceReview, validateMaintenanceOperation } = require('./work-maintenance-wire.cjs');
const copy = (v) => structuredClone(v), same = (a, b) => canonical(a) === canonical(b), bytes = (v) => new TextEncoder().encode(JSON.stringify(v)).length;
const sha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v), id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v), text = (v, max) => typeof v === 'string' && Array.from(v).length <= max && !/[\0\uD800-\uDFFF]/u.test(v);
const exact = (v, keys) => !!v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === keys.length && Object.keys(v).every(k => keys.includes(k));
function check(v, code = 'WORK_MAINTENANCE_LOCAL_INVALID') { if (!v)
    throw Error(code); }
const emptyMaintenanceDraft = () => ({ query: null, review_sha256: null, label: '', retention_note: '' });
const maintenanceUnresolved = (a) => a.phase !== 'RECORDED' && a.retained_note === null;
function maintenanceOwner(s, root) { const { generation, ...scope } = workRequirementScope(s, root); return { project_id: s.project_id, ...scope }; }
const maintenanceLocalKey = (s, root) => 'cirven-work-maintenance-v1:' + canonical(maintenanceOwner(s, root));
function owned(q, o) { return same(q.session_ref, o.session_ref) && same(q.workspace_ref, o.workspace_ref) && q.root_operation_id === o.root_operation_id; }
function queryOf(r) { const { operation_id, review_sha256, review_confirmed, ...q } = r; return q; }
function draft(d, o) { check(exact(d, ['query', 'review_sha256', 'label', 'retention_note']) && text(d.retention_note, 4000) && text(d.label, 100) && (d.review_sha256 === null || sha(d.review_sha256))); if (d.query) {
    validateMaintenancePrepare(d.query, true);
    check(owned(d.query, o));
}
else
    check(d.review_sha256 === null); }
function validateMaintenanceLocal(raw, o) {
    if (raw === null)
        return { version: 1, owner: copy(o), revision: 0, draft: emptyMaintenanceDraft(), copies: [], attempts: [] };
    check(new TextEncoder().encode(raw).length <= 1048576, 'WORK_MAINTENANCE_LOCAL_CAPACITY');
    const v = JSON.parse(raw);
    check(exact(v, ['version', 'owner', 'revision', 'draft', 'copies', 'attempts']) && v.version === 1 && same(v.owner, o) && Number.isSafeInteger(v.revision) && v.revision >= 0 && Array.isArray(v.copies) && v.copies.length <= 4 && Array.isArray(v.attempts) && v.attempts.length <= 32);
    draft(v.draft, o);
    for (const c of v.copies) {
        check(exact(c, ['id', 'draft']) && id(c.id));
        draft(c.draft, o);
    }
    check(new Set(v.copies.map(c => c.id)).size === v.copies.length);
    for (const a of v.attempts) {
        check(exact(a, ['request', 'checksum', 'phase', 'initial_unknown', 'intent_known', 'last_lookup', 'result', 'retained_note']) && sha(a.checksum) && ['PENDING', 'UNKNOWN', 'RECORDED'].includes(a.phase) && typeof a.initial_unknown === 'boolean' && typeof a.intent_known === 'boolean' && ['NOT_CHECKED', 'NOT_FOUND', 'FOUND'].includes(a.last_lookup) && (a.retained_note === null || text(a.retained_note, 4000) && a.retained_note.trim().length > 0));
        validateMaintenanceRequest(a.request);
        check(owned(a.request, o));
        if (a.result)
            check(a.phase === 'RECORDED' && a.intent_known && a.result.receipt.operation_id === a.request.operation_id && a.result.receipt.request_sha256 === a.checksum);
        else
            check(a.phase !== 'RECORDED' && !a.intent_known);
    }
    check(new Set(v.attempts.map(a => a.checksum)).size === v.attempts.length && new Set(v.attempts.map(a => a.request.operation_id)).size === v.attempts.length);
    check(bytes(v) + v.attempts.filter(a => a.phase !== 'RECORDED').length * 266240 <= 1048576, 'WORK_MAINTENANCE_LOCAL_CAPACITY');
    return v;
}
function resultIdentity(result) { const { operation_id, request_sha256, ...scope } = result.scope; return { scope, root_sha256: result.original_request.root_sha256, operation_id, request_sha256 }; }
async function validateAttempts(rows, pid) { for (const a of rows) {
    check(await hash(a.request) === a.checksum);
    if (a.result)
        await validateMaintenanceOperation(a.result, pid, resultIdentity(a.result), { request: a.request, allow_writes: true });
} }
const dirty = new Set(), queued = new Set();
async function flushWorkMaintenanceDrafts() { for (;;) {
    const all = [...new Set([...dirty, ...queued])];
    if (!all.length)
        return;
    await Promise.all(all.map(j => j.flush()));
} }
class MaintenanceJournal {
    storage;
    locks;
    newId;
    owner;
    key;
    local;
    raw;
    changed = false;
    waiting = 0;
    tail = Promise.resolve();
    listeners = new Set();
    constructor(s, root, storage, locks = globalThis.navigator?.locks ?? null, newId = () => crypto.randomUUID()) {
        this.storage = storage;
        this.locks = locks;
        this.newId = newId;
        this.owner = maintenanceOwner(s, root);
        this.key = maintenanceLocalKey(s, root);
        this.raw = storage.getItem(this.key);
        this.local = validateMaintenanceLocal(this.raw, this.owner);
        if (this.local.attempts.some(a => a.phase === 'PENDING')) {
            this.local.attempts = this.local.attempts.map(a => a.phase === 'PENDING' ? { ...a, phase: 'UNKNOWN', initial_unknown: true } : a);
            this.local.revision++;
            this.changed = true;
            dirty.add(this);
        }
    }
    value() { return copy(this.local); }
    unsaved() { return this.changed; }
    running() { return this.waiting > 0; }
    observed() { return this.listeners.size > 0; }
    subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
    emit() { for (const f of this.listeners)
        f(); }
    async validate() { validateMaintenanceLocal(JSON.stringify(this.local), this.owner); await validateAttempts(this.local.attempts, this.owner.project_id); return this; }
    current() { check(!this.changed, 'WORK_MAINTENANCE_LOCAL_UNSAVED'); check(this.storage.getItem(this.key) === this.raw, 'WORK_MAINTENANCE_DRAFT_CONFLICT'); return this.value(); }
    write(v, basis = this.raw) { this.local = copy(v); this.changed = true; dirty.add(this); this.emit(); const raw = JSON.stringify(v); validateMaintenanceLocal(raw, this.owner); check(this.storage.getItem(this.key) === basis, 'WORK_MAINTENANCE_DRAFT_CONFLICT'); this.storage.setItem(this.key, raw); check(this.storage.getItem(this.key) === raw, 'WORK_MAINTENANCE_DRAFT_CONFLICT'); this.raw = raw; this.changed = false; dirty.delete(this); this.emit(); return this.value(); }
    serial(run) { this.waiting++; queued.add(this); const p = this.tail.catch(() => { }).then(async () => { check(this.locks, 'WORK_MAINTENANCE_LOCK_UNAVAILABLE'); const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 5000); try {
        return await this.locks.request(this.key, { mode: 'exclusive', signal: abort.signal }, async () => { clearTimeout(timer); return run(); });
    }
    finally {
        clearTimeout(timer);
    } }).finally(() => { this.waiting--; if (!this.waiting)
        queued.delete(this); this.emit(); }); this.tail = p; return p; }
    edit(d) { draft(d, this.owner); this.local.draft = copy(d); this.local.revision++; this.changed = true; dirty.add(this); this.emit(); }
    flush() { return this.serial(async () => this.changed ? this.write(this.local) : this.value()); }
    save(a, clearReviewed = false) { const v = this.value(), i = v.attempts.findIndex(b => b.checksum === a.checksum); if (i < 0) {
        check(v.attempts.length < 32 && !v.attempts.some(b => b.request.operation_id === a.request.operation_id), 'WORK_MAINTENANCE_HISTORY_CAPACITY');
        v.attempts.push(copy(a));
    }
    else {
        const old = v.attempts[i];
        check(same(old.request, a.request) && (!old.initial_unknown || a.initial_unknown) && (!old.intent_known || a.intent_known) && (old.retained_note === null || old.retained_note === a.retained_note));
        if (old.phase === 'RECORDED')
            check(a.phase === 'RECORDED' && same(old.result?.receipt, a.result?.receipt), 'WORK_MAINTENANCE_ORIGINAL_RESULT_CHANGED');
        v.attempts[i] = copy(a);
    } if (clearReviewed && same(v.draft.query, queryOf(a.request)))
        v.draft.review_sha256 = null; v.revision++; return this.write(v); }
    async observe(a, result) { await validateMaintenanceOperation(result, this.owner.project_id, resultIdentity(result), { request: a.request, allow_writes: true }); const old = this.value().attempts.find(b => b.checksum === a.checksum); check(old); return this.save({ ...old, phase: 'RECORDED', intent_known: true, last_lookup: 'FOUND', result: copy(result) }, true); }
    selectDraft(next) { return this.serial(async () => { draft(next, this.owner); const v = this.current(); if (v.draft.query && !same(v.draft, next) && !v.copies.some(c => same(c.draft, v.draft))) {
        check(v.copies.length < 4, 'WORK_MAINTENANCE_COPY_CAPACITY');
        v.copies.push({ id: this.newId(), draft: copy(v.draft) });
    } v.draft = copy(next); v.revision++; return this.write(v); }); }
    async copyAction(id, remove = false) { return this.serial(async () => { const v = this.current(), row = v.copies.find(c => c.id === id); check(row); if (remove)
        v.copies = v.copies.filter(c => c.id !== id);
    else {
        const previous = v.draft;
        v.draft = row.draft;
        row.draft = previous;
    } v.revision++; return this.write(v); }); }
    async resolve(choice) {
        return this.serial(async () => {
            const raw = this.storage.getItem(this.key), saved = validateMaintenanceLocal(raw, this.owner), local = this.value();
            await validateAttempts([...saved.attempts, ...local.attempts], this.owner.project_id);
            const selected = choice === 'LOCAL' ? local.draft : saved.draft, v = copy(saved);
            v.draft = copy(selected);
            const keep = (d, id) => { if (same(d, selected) || v.copies.some(c => same(c.draft, d)))
                return; check(v.copies.length < 4, 'WORK_MAINTENANCE_COPY_CAPACITY'); v.copies.push({ id: id && !v.copies.some(c => c.id === id) ? id : this.newId(), draft: copy(d) }); };
            v.copies = v.copies.filter(c => !same(c.draft, selected));
            for (const c of local.copies)
                keep(c.draft, c.id);
            keep(choice === 'LOCAL' ? saved.draft : local.draft);
            for (const a of local.attempts) {
                const i = v.attempts.findIndex(b => b.checksum === a.checksum);
                if (i < 0) {
                    check(!v.attempts.some(b => b.request.operation_id === a.request.operation_id), 'WORK_MAINTENANCE_ORIGINAL_RESULT_CHANGED');
                    v.attempts.push(copy(a));
                }
                else {
                    const b = v.attempts[i];
                    check(same(a.request, b.request) && (a.retained_note === null || b.retained_note === null || a.retained_note === b.retained_note));
                    if (a.result && b.result)
                        check(same(a.result.receipt, b.result.receipt), 'WORK_MAINTENANCE_ORIGINAL_RESULT_CHANGED');
                    const best = a.phase === 'RECORDED' ? a : b.phase === 'RECORDED' ? b : a;
                    v.attempts[i] = { ...copy(best), initial_unknown: a.initial_unknown || b.initial_unknown, retained_note: a.retained_note ?? b.retained_note };
                }
            }
            for (const a of v.attempts)
                if (a.phase === 'PENDING') {
                    a.phase = 'UNKNOWN';
                    a.initial_unknown = true;
                }
            v.revision = Math.max(saved.revision, local.revision) + 1;
            return this.write(v, raw);
        });
    }
}
const journals = new WeakMap();
async function maintenanceJournal(storage, s, root, locks) { let map = journals.get(storage); if (!map) {
    map = new Map();
    journals.set(storage, map);
} const key = maintenanceLocalKey(s, root), old = map.get(key); if (old)
    return old.validate(); if (map.size >= 64) {
    const free = [...map].find(([, j]) => !j.unsaved() && !j.running() && !j.observed());
    check(free, 'WORK_MAINTENANCE_LOCAL_CAPACITY');
    map.delete(free[0]);
} const j = new MaintenanceJournal(s, root, storage, locks); try {
    await j.validate();
}
catch (e) {
    dirty.delete(j);
    throw e;
} map.set(key, j); return j; }
class MaintenanceActions {
    journal;
    reader;
    newId;
    constructor(journal, reader = new OriginalWorkMaintenanceApi(), newId = () => crypto.randomUUID()) {
        this.journal = journal;
        this.reader = reader;
        this.newId = newId;
    }
    identity(s, a) { check(s.project_id === this.journal.owner.project_id && same(maintenanceOwner(s, a.request.root_operation_id), this.journal.owner) && s.generation >= a.request.generation, 'WORK_MAINTENANCE_SCOPE_CHANGED'); return { scope: workRequirementScope(s, a.request.root_operation_id), root_sha256: a.request.root_sha256, operation_id: a.request.operation_id, request_sha256: a.checksum }; }
    async bounded(run) { const abort = new AbortController(); let timer; try {
        return await Promise.race([run(abort.signal), new Promise((_, reject) => { timer = setTimeout(() => { abort.abort(); reject(Error('WORK_MAINTENANCE_TRANSPORT_UNKNOWN')); }, 25000); })]);
    }
    finally {
        clearTimeout(timer);
    } }
    async transmit(s, a) { let result; try {
        result = await this.bounded(signal => this.reader.command(s, a.request, signal));
    }
    catch {
        const latest = this.journal.value().attempts.find(b => b.checksum === a.checksum);
        this.journal.save({ ...latest, phase: 'UNKNOWN', initial_unknown: true });
        return null;
    } await this.journal.observe(a, result); return result; }
    submit(s, reviewed) { return this.journal.serial(async () => { await this.journal.validate(); const local = this.journal.current(), d = local.draft; check(d.query && d.review_sha256 === reviewed.review_sha256 && !local.attempts.some(maintenanceUnresolved), 'WORK_MAINTENANCE_PENDING'); const q = validateMaintenancePrepare(copy(d.query)); check(owned(q, this.journal.owner) && same(workRequirementScope(s, q.root_operation_id), { session_ref: q.session_ref, generation: q.generation, workspace_ref: q.workspace_ref, root_operation_id: q.root_operation_id }), 'WORK_MAINTENANCE_SCOPE_CHANGED'); await validateMaintenanceReview(reviewed, s.project_id, q); const fresh = await this.bounded(signal => this.reader.prepare(s, q, signal)); check(fresh.review_sha256 === reviewed.review_sha256 && same(this.journal.value().draft, d), 'WORK_MAINTENANCE_REVIEW_REQUIRED'); const r = validateMaintenanceRequest({ ...q, operation_id: this.newId(), review_sha256: fresh.review_sha256, review_confirmed: true }), a = { request: r, checksum: await hash(r), phase: 'PENDING', initial_unknown: false, intent_known: false, last_lookup: 'NOT_CHECKED', result: null, retained_note: null }; this.journal.save(a); return this.transmit(s, a); }); }
    async lookup(s, a) { let result; try {
        result = await this.bounded(signal => this.reader.operation(s, this.identity(s, a), signal));
    }
    catch (e) {
        if (e?.status === 404) {
            const latest = this.journal.value().attempts.find(b => b.checksum === a.checksum);
            this.journal.save({ ...latest, last_lookup: 'NOT_FOUND' });
            return null;
        }
        throw e;
    } await this.journal.observe(a, result); return result; }
    inspect(s, sum) { return this.journal.serial(async () => { await this.journal.validate(); const a = this.journal.current().attempts.find(b => b.checksum === sum); check(a); return this.lookup(s, a); }); }
    retry(s, sum) { return this.journal.serial(async () => { await this.journal.validate(); const a = this.journal.current().attempts.find(b => b.checksum === sum); check(a); const found = await this.lookup(s, a); if (found)
        return found; check(!a.intent_known && a.phase !== 'RECORDED' && a.retained_note === null && s.generation === a.request.generation, 'WORK_MAINTENANCE_ORIGINAL_RECORD_UNAVAILABLE'); const reviewed = await this.bounded(signal => this.reader.prepare(s, queryOf(a.request), signal)); check(reviewed.review_sha256 === a.request.review_sha256, 'WORK_MAINTENANCE_REVIEW_REQUIRED'); const latest = this.journal.value().attempts.find(b => b.checksum === sum); this.journal.save({ ...latest, phase: 'PENDING' }); return this.transmit(s, latest); }); }
    retain(s, sum, note) { return this.journal.serial(async () => { await this.journal.validate(); const a = this.journal.current().attempts.find(b => b.checksum === sum); check(a); const found = await this.lookup(s, a); if (found)
        return found; check(a.phase !== 'RECORDED' && !a.intent_known && text(note, 4000) && note.trim().length > 0, 'WORK_MAINTENANCE_PENDING'); const latest = this.journal.value().attempts.find(b => b.checksum === sum); this.journal.save({ ...latest, retained_note: note }); return null; }); }
    adopt(s, input) { const result = copy(input); return this.journal.serial(async () => { await this.journal.validate(); this.journal.current(); const r = result.original_request; check(owned(r, this.journal.owner)); const i = this.identity(s, { request: r, checksum: await hash(r) }); await validateMaintenanceOperation(result, s.project_id, i); const old = this.journal.value().attempts.find(a => a.checksum === i.request_sha256); if (old)
        await this.journal.observe(old, result);
    else
        this.journal.save({ request: r, checksum: i.request_sha256, phase: 'RECORDED', initial_unknown: true, intent_known: true, last_lookup: 'FOUND', result, retained_note: null }); return result; }); }
}
module.exports = { emptyMaintenanceDraft, maintenanceUnresolved, maintenanceOwner, maintenanceLocalKey, validateMaintenanceLocal, flushWorkMaintenanceDrafts, MaintenanceJournal, maintenanceJournal, MaintenanceActions };

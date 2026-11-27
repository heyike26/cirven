'use strict';
// Generated from desktop authority; do not edit independently.
// SHA256 apps/agent-shell/src/original-work-requirement-sources.ts 0911e42c476101fee60e6c9a466e0641bcc40881df43f1f2e875f1168e88c8ed
// SHA256 apps/agent-shell/src/work-source-editor.ts 324158759cfcc14e5aea9c1b46cd0bce84dab14e0c312c52fea104a46b75695e
const { webcrypto: crypto } = require('node:crypto');
const wire = require('./work-source-wire.cjs');
const { canonical, hash, workRequirementScope, OriginalWorkRequirementSourcesApi, sourceQuery, sourceDecision, validateSourceReceipt, sourceSame: same, sourceSha: sha } = wire;
const copy = (v) => structuredClone(v), id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v), count = (v, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) <= max, text = (v, max) => typeof v === 'string' && Array.from(v).length <= max && !v.includes('\0');
function check(v, code = 'WORK_SOURCE_LOCAL_INVALID') { if (!v)
    throw Error(code); }
function exact(v, names) { return !!v && typeof v === 'object' && !Array.isArray(v) && Object.keys(v).length === names.length && Object.keys(v).every(k => names.includes(k)); }
function ownerOf(s, root) { const { generation, ...rest } = workRequirementScope(s, root); return { project_id: s.project_id, ...rest }; }
function sourceLocalKey(s, root) { return 'cirven-work-source-v1:' + canonical(ownerOf(s, root)); }
function owned(q, o) { return q.source_project_id !== o.project_id && same(q.session_ref, o.session_ref) && same(q.workspace_ref, o.workspace_ref) && q.root_operation_id === o.root_operation_id; }
function draft(d, o) { return exact(d, ['query', 'target_revision', 'project_label', 'title', 'review_sha256']) && Array.isArray(d.query?.requirement_ids) && sourceQuery({ ...d.query, requirement_ids: d.query.requirement_ids.length ? d.query.requirement_ids : ['empty_private_selection'] }) && owned(d.query, o) && count(d.target_revision, 512) && text(d.project_label, 200) && text(d.title, 200) && (d.review_sha256 === null || sha(d.review_sha256)); }
function attempt(a, o) { return exact(a, ['request', 'checksum', 'project_label', 'title', 'phase', 'initial_unknown', 'last_lookup', 'receipt', 'retained']) && sourceDecision(a.request) && owned(a.request, o) && sha(a.checksum) && text(a.project_label, 200) && text(a.title, 200) && ['PENDING', 'UNKNOWN', 'RECORDED'].includes(a.phase) && typeof a.initial_unknown === 'boolean' && ['NOT_CHECKED', 'NOT_FOUND', 'RECORDED'].includes(a.last_lookup) && typeof a.retained === 'boolean' && (a.receipt === null ? a.phase !== 'RECORDED' : a.phase === 'RECORDED' && a.last_lookup === 'RECORDED' && a.receipt.operation_id === a.request.operation_id && a.receipt.request_sha256 === a.checksum); }
function validateSourceLocal(raw, o) { if (raw === null)
    return { version: 1, owner: copy(o), revision: 0, draft: null, copies: [], attempts: [] }; check(new TextEncoder().encode(raw).length <= 524288, 'WORK_SOURCE_LOCAL_CAPACITY'); const v = JSON.parse(raw); check(exact(v, ['version', 'owner', 'revision', 'draft', 'copies', 'attempts']) && v.version === 1 && same(v.owner, o) && count(v.revision) && (v.draft === null || draft(v.draft, o)) && Array.isArray(v.copies) && v.copies.length <= 4 && v.copies.every(r => exact(r, ['id', 'draft']) && id(r.id) && draft(r.draft, o)) && new Set(v.copies.map(r => r.id)).size === v.copies.length && Array.isArray(v.attempts) && v.attempts.length <= 128 && v.attempts.every(a => attempt(a, o)) && new Set(v.attempts.map(a => a.checksum)).size === v.attempts.length && new Set(v.attempts.map(a => a.request.operation_id)).size === v.attempts.length); return v; }
const dirty = new Set(), queued = new Set();
async function flushWorkSourceDrafts() { try {
    for (;;) {
        const all = [...new Set([...dirty, ...queued])];
        if (!all.length)
            return;
        await Promise.all(all.map(j => j.flush()));
    }
}
catch {
    throw Error('WORK_SOURCE_DRAFT_UNSAVED');
} }
class SourceJournal {
    session;
    root;
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
    activities = new Set();
    constructor(session, root, storage, locks = globalThis.navigator?.locks ?? null, newId = () => crypto.randomUUID()) {
        this.session = session;
        this.root = root;
        this.storage = storage;
        this.locks = locks;
        this.newId = newId;
        this.owner = ownerOf(session, root);
        this.key = sourceLocalKey(session, root);
        this.raw = storage.getItem(this.key);
        this.local = validateSourceLocal(this.raw, this.owner);
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
    emit() { for (const fn of this.listeners)
        fn(); }
    async validate() { for (const a of this.local.attempts) {
        check(await hash(a.request) === a.checksum);
        if (a.receipt)
            await validateSourceReceipt(a.receipt, this.session, this.root, a.request);
    } return this; }
    edit(d) { check(draft(d, this.owner)); const v = this.value(); if (v.draft && !same([v.draft.query.source_project_id, v.draft.query.requirement_ref], [d.query.source_project_id, d.query.requirement_ref]) && !v.copies.some(r => same(r.draft, v.draft))) {
        check(v.copies.length < 4, 'WORK_SOURCE_COPY_CAPACITY');
        v.copies.push({ id: this.newId(), draft: copy(v.draft) });
    } v.draft = copy(d); v.revision++; this.local = v; this.changed = true; dirty.add(this); this.emit(); }
    write(v, basis = this.raw) { const raw = JSON.stringify(v); check(new TextEncoder().encode(raw).length + v.attempts.filter(a => a.receipt === null).length * 33024 <= 524288, 'WORK_SOURCE_LOCAL_CAPACITY'); this.local = copy(v); this.changed = true; dirty.add(this); this.emit(); check(this.storage.getItem(this.key) === basis, 'WORK_SOURCE_DRAFT_CONFLICT'); this.storage.setItem(this.key, raw); check(this.storage.getItem(this.key) === raw, 'WORK_SOURCE_DRAFT_CONFLICT'); this.raw = raw; this.changed = false; dirty.delete(this); this.emit(); return this.value(); }
    current() { check(!this.changed, 'WORK_SOURCE_DRAFT_UNSAVED'); check(this.storage.getItem(this.key) === this.raw, 'WORK_SOURCE_DRAFT_CONFLICT'); return this.value(); }
    activity(run) { this.waiting++; queued.add(this); const p = Promise.resolve().then(run).finally(() => { this.activities.delete(p); this.waiting--; if (!this.waiting)
        queued.delete(this); this.emit(); }); this.activities.add(p); return p; }
    serial(run) { this.waiting++; queued.add(this); const next = this.tail.catch(() => { }).then(async () => { check(this.locks, 'WORK_SOURCE_LOCK_UNAVAILABLE'); const abort = new AbortController(), timer = setTimeout(() => abort.abort(), 5000); try {
        return await this.locks.request(this.key, { mode: 'exclusive', signal: abort.signal }, async () => { clearTimeout(timer); return run(); });
    }
    finally {
        clearTimeout(timer);
    } }).finally(() => { this.waiting--; if (!this.waiting)
        queued.delete(this); this.emit(); }); this.tail = next; return next; }
    async flush() { await Promise.all([...this.activities]); return this.serial(async () => this.changed ? this.write(this.local) : this.value()); }
    save(a) { check(attempt(a, this.owner)); const v = this.value(), i = v.attempts.findIndex(r => r.checksum === a.checksum); if (i < 0) {
        check(v.attempts.length < 128, 'WORK_SOURCE_HISTORY_CAPACITY');
        v.attempts.push(copy(a));
    }
    else {
        check(same(v.attempts[i].request, a.request));
        v.attempts[i] = copy(a);
    } v.revision++; return this.write(v); }
    accept(a, r) { const current = this.local.attempts.find(row => row.checksum === a.checksum); check(current); return this.save({ ...copy(current), phase: 'RECORDED', last_lookup: 'RECORDED', receipt: copy(r) }); }
    retain(sum) { return this.serial(async () => { const a = this.current().attempts.find(r => r.checksum === sum); check(a && a.last_lookup === 'NOT_FOUND' && a.phase === 'UNKNOWN', 'WORK_SOURCE_PENDING'); return this.save({ ...a, retained: true }); }); }
    copyAction(id, remove = false) { return this.serial(async () => { const v = this.value(), row = v.copies.find(r => r.id === id); check(row); if (remove)
        v.copies = v.copies.filter(r => r.id !== id);
    else {
        const prior = v.draft;
        v.draft = row.draft;
        if (prior)
            row.draft = prior;
        else
            v.copies = v.copies.filter(r => r.id !== id);
    } v.revision++; return this.write(v); }); }
    resolve(which) {
        return this.serial(async () => {
            const raw = this.storage.getItem(this.key), saved = validateSourceLocal(raw, this.owner), local = this.value(), selected = which === 'LOCAL' ? local.draft : saved.draft, result = { ...copy(saved), draft: copy(selected) };
            for (const a of saved.attempts) {
                check(await hash(a.request) === a.checksum);
                if (a.receipt)
                    await validateSourceReceipt(a.receipt, this.session, this.root, a.request);
            }
            const keep = (d, id) => { if (!d || same(d, selected) || result.copies.some(r => same(r.draft, d)))
                return; check(result.copies.length < 4, 'WORK_SOURCE_COPY_CAPACITY'); result.copies.push({ id: id && !result.copies.some(r => r.id === id) ? id : this.newId(), draft: copy(d) }); };
            for (const r of local.copies)
                keep(r.draft, r.id);
            keep(which === 'LOCAL' ? saved.draft : local.draft);
            for (const a of local.attempts) {
                const i = result.attempts.findIndex(r => r.checksum === a.checksum);
                if (i < 0) {
                    check(result.attempts.length < 128, 'WORK_SOURCE_HISTORY_CAPACITY');
                    result.attempts.push(copy(a));
                }
                else {
                    const other = result.attempts[i];
                    check(same(a.request, other.request));
                    if (a.receipt && other.receipt)
                        check(same(a.receipt, other.receipt));
                    const known = a.phase === 'RECORDED' ? a : other.phase === 'RECORDED' ? other : a;
                    result.attempts[i] = { ...copy(known), phase: known.phase === 'PENDING' ? 'UNKNOWN' : known.phase, initial_unknown: a.initial_unknown || other.initial_unknown || known.phase === 'PENDING', retained: a.retained || other.retained };
                }
            }
            result.revision = Math.max(local.revision, saved.revision) + 1;
            return this.write(result, raw);
        });
    }
}
class SourceActions {
    journal;
    reader;
    newId;
    constructor(journal, reader = new OriginalWorkRequirementSourcesApi(), newId = () => crypto.randomUUID()) {
        this.journal = journal;
        this.reader = reader;
        this.newId = newId;
    }
    async bounded(run) { const abort = new AbortController(); let timer; try {
        return await Promise.race([run(abort.signal), new Promise((_, reject) => { timer = setTimeout(() => { abort.abort(); reject(Error('WORK_SOURCE_TRANSPORT_UNKNOWN')); }, 25000); })]);
    }
    finally {
        clearTimeout(timer);
    } }
    async transmit(s, a) { let response; try {
        response = await this.bounded(signal => this.reader.command(s, a.request, signal));
    }
    catch {
        const current = this.journal.value().attempts.find(r => r.checksum === a.checksum);
        this.journal.save({ ...current, initial_unknown: true, phase: current.phase === 'RECORDED' ? 'RECORDED' : 'UNKNOWN' });
        return null;
    } this.journal.accept(a, response); return response; }
    submit(s, reviewed) { return this.journal.serial(async () => { await this.journal.validate(); const state = this.journal.current(), d = state.draft; check(d && d.review_sha256 === reviewed.review_sha256 && !state.attempts.some(a => a.phase !== 'RECORDED' && !a.retained), 'WORK_SOURCE_PENDING'); const fresh = await this.bounded(signal => this.reader.review(s, d.query, signal)); check(fresh.review_sha256 === reviewed.review_sha256, 'WORK_SOURCE_REVIEW_REQUIRED'); if (fresh.current_grant?.state === 'ACTIVE' && same(fresh.current_grant.requirement_ref, d.query.requirement_ref) && same(fresh.current_grant.requirement_ids, d.query.requirement_ids) && same(fresh.current_grant.acceptance_ids, d.query.acceptance_ids) && fresh.current_grant.selection_sha256 === fresh.basis.selection_sha256)
        return fresh.current_grant; const r = { ...copy(d.query), operation_id: this.newId(), expected_revision: fresh.basis.expected_revision, action: 'ALLOW', review_sha256: fresh.review_sha256 }, a = { request: r, checksum: await hash(r), title: d.title, project_label: d.project_label, phase: 'PENDING', initial_unknown: false, last_lookup: 'NOT_CHECKED', receipt: null, retained: false }; this.journal.save(a); return this.transmit(s, a); }); }
    revoke(s, target, labels) { return this.journal.serial(async () => { await this.journal.validate(); const state = this.journal.current(); check(!state.attempts.some(a => a.phase !== 'RECORDED' && !a.retained), 'WORK_SOURCE_PENDING'); await validateSourceReceipt(target, s, this.journal.root); check(target.state === 'ACTIVE', 'WORK_SOURCE_REVIEW_REQUIRED'); const r = { ...workRequirementScope(s, this.journal.root), root_sha256: target.root_sha256, source_project_id: target.source_project_id, requirement_ref: copy(target.requirement_ref), requirement_ids: copy(target.requirement_ids), acceptance_ids: copy(target.acceptance_ids), operation_id: this.newId(), expected_revision: target.revision, action: 'REVOKE', review_sha256: null }, a = { request: r, checksum: await hash(r), ...labels, phase: 'PENDING', initial_unknown: false, last_lookup: 'NOT_CHECKED', receipt: null, retained: false }; this.journal.save(a); return this.transmit(s, a); }); }
    async lookup(s, a) { let result; try {
        result = await this.bounded(signal => this.reader.operation(s, a.request, signal));
    }
    catch (error) {
        if (error?.status !== 404)
            throw error;
        const current = this.journal.value().attempts.find(r => r.checksum === a.checksum);
        if (current.phase !== 'RECORDED')
            this.journal.save({ ...current, phase: 'UNKNOWN', initial_unknown: true, last_lookup: 'NOT_FOUND' });
        return null;
    } this.journal.accept(a, result); return result; }
    inspect(s, sum) { return this.journal.serial(async () => { await this.journal.validate(); const a = this.journal.value().attempts.find(r => r.checksum === sum); check(a); return this.lookup(s, a); }); }
    continue(s, sum) { return this.journal.serial(async () => { await this.journal.validate(); let a = this.journal.current().attempts.find(r => r.checksum === sum); check(a); const found = await this.lookup(s, a); if (found)
        return found; check(a.phase !== 'RECORDED', 'WORK_SOURCE_ORIGINAL_RECORD_UNAVAILABLE'); check(a.request.generation === s.generation, 'WORK_SOURCE_REVIEW_REQUIRED'); if (a.request.action === 'ALLOW') {
        const { operation_id, expected_revision, action, review_sha256, ...q } = a.request;
        const fresh = await this.bounded(signal => this.reader.review(s, q, signal));
        check(fresh.review_sha256 === review_sha256, 'WORK_SOURCE_REVIEW_REQUIRED');
    } a = this.journal.value().attempts.find(r => r.checksum === sum); this.journal.save({ ...a, phase: 'PENDING' }); return this.transmit(s, a); }); }
}
module.exports = { SourceJournal, SourceActions, sourceLocalKey, validateSourceLocal, flushWorkSourceDrafts };

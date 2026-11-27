'use strict';
// Generated from renderer authority; edit that source and run the source generator.
// SHA256 apps/agent-shell/src/original-work-test-cases.ts 673435c5e7472b1ef9c301165b573596f189abb18b82e054292b99eb6856d1b6
// SHA256 apps/agent-shell/src/work-case-editor.ts a324ba8bed0c3cbab3efd183bdc5583c2056d5b731797cd076453a24bbae6e19
// SHA256 apps/agent-shell/src/original-work-requirements.ts 069eb91b1e7d58985943cea5f5a20a1f07f923ca84b1f9e3dd717c0ffba6bc85
// SHA256 apps/agent-shell/src/original-session-files.ts e7f0fb0a83b3317251cff5b34cf90c341c13b0db1cf104bf29baa6a2325fcfbe
const { webcrypto: crypto, randomUUID: operationId } = require('node:crypto');
const wire = require('./work-case-wire.cjs');
const { canonical, hash, workRequirementScope, OriginalWorkTestCasesApi, validateReceipt: validateWorkCaseReceipt, mappingKey: workCaseMappingKey } = wire;
const api = Object.freeze({ request() { throw Error('WORK_CASE_TRANSPORT_REQUIRED'); } });
function validateWorkCaseSource(v, generation) { if (!wire.validSource(v, generation))
    throw Error('ORIGINAL_WORK_CASE_MAPPING_CHANGED'); }
const PREFIX = 'cirven-work-case-v1:', copy = (v) => structuredClone(v), same = (a, b) => canonical(a) === canonical(b);
const sha = (v) => typeof v === 'string' && /^[a-f0-9]{64}$/.test(v), id = (v) => typeof v === 'string' && /^[A-Za-z0-9_-]{1,100}$/.test(v), count = (v, max = Number.MAX_SAFE_INTEGER) => Number.isSafeInteger(v) && Number(v) >= 0 && Number(v) <= max;
const object = (v) => v !== null && typeof v === 'object' && !Array.isArray(v), exact = (v, keys) => object(v) && Object.keys(v).length === keys.length && Object.keys(v).every(k => keys.includes(k));
function check(value, code = 'WORK_CASE_LOCAL_INVALID') { if (!value)
    throw Error(code); }
function ids(v) { return Array.isArray(v) && v.length <= 100 && v.every(x => typeof x === 'string' && /^[A-Za-z0-9_-]{1,40}$/.test(x)) && new Set(v).size === v.length; }
function reference(v) { return !!v && exact(v, ['id', 'revision', 'sha256']) && id(v.id) && count(v.revision, 2147483647) && v.revision >= 1 && sha(v.sha256); }
function source(v, generation = Number.MAX_SAFE_INTEGER) { try {
    check(exact(v, ['command_ref', 'command_generation', 'call_id', 'report_path', 'report_sha256', 'report_ref', 'case_ordinal', 'case_sha256']));
    validateWorkCaseSource(v, generation);
    return true;
}
catch {
    return false;
} }
function choice(v) { const c = v; return exact(c, ['source', 'link_id', 'link_sha256', 'requirement_ref', 'acceptance_ids']) && source(c.source) && ids(c.acceptance_ids) && (c.link_id === null ? c.link_sha256 === null && c.requirement_ref === null && c.acceptance_ids.length === 0 : id(c.link_id) && sha(c.link_sha256) && reference(c.requirement_ref)); }
function scope(v, o) { return exact(v, ['session_ref', 'generation', 'workspace_ref', 'root_operation_id']) && same(v.session_ref, o.session_ref) && same(v.workspace_ref, o.workspace_ref) && v.root_operation_id === o.root_operation_id && count(v.generation) && v.generation >= 1; }
function request(v, o) {
    const r = v;
    if (!exact(r, ['session_ref', 'generation', 'workspace_ref', 'root_operation_id', 'operation_id', 'root_sha256', 'expected_revision', 'expected_requirement_revision', 'action', 'mapping_key', 'selection']) || !scope({ session_ref: r.session_ref, generation: r.generation, workspace_ref: r.workspace_ref, root_operation_id: r.root_operation_id }, o) || !id(r.operation_id) || !sha(r.root_sha256) || !count(r.expected_revision, 512) || !count(r.expected_requirement_revision, 512) || !sha(r.mapping_key))
        return false;
    const s = r.selection;
    return r.action === 'REMOVE' ? s === null : r.action === 'MAP' && !!s && exact(s, ['requirement_link_operation_id', 'requirement_link_request_sha256', 'source', 'acceptance_ids']) && id(s.requirement_link_operation_id) && sha(s.requirement_link_request_sha256) && source(s.source, r.generation) && ids(s.acceptance_ids) && s.acceptance_ids.length > 0;
}
function draft(v, o) { const d = v; return exact(d, ['scope', 'root_sha256', 'revision', 'requirement_revision', 'choice', 'basis']) && scope(d.scope, o) && sha(d.root_sha256) && count(d.revision, 512) && count(d.requirement_revision, 512) && choice(d.choice) && source(d.choice.source, d.scope.generation) && object(d.basis); }
function basicReceipt(v, r, sum, o) { check(v && v.kind === 'ORIGINAL_WORK_CASE_OPERATION' && v.version === 1 && v.project_id === o.project_id && same(v.session_ref, o.session_ref) && v.root_operation_id === o.root_operation_id && v.root_sha256 === r.root_sha256 && v.operation_id === r.operation_id && v.request_sha256 === sum && v.revision === r.expected_revision + 1 && v.requirement_revision === r.expected_requirement_revision && v.action === r.action && v.mapping_key === r.mapping_key && same(v.selection, r.selection) && v.metadata_only === true && v.model_calls === 0 && v.commands_started === 0 && v.source_files_written === 0 && v.permission_granted === false && v.original_task_status_changed === false && v.whole_task_acceptance === 'NOT_ASSESSED' && v.automatic_replay === false && v.coverage === 'HUMAN_DECLARED_MAPPING_NOT_MEASURED_TEST_COVERAGE' && v.current_workspace_verified === false && v.target_environment_verified === false && v.writer_attribution === 'NOT_PROVEN', 'WORK_CASE_RECEIPT_CHANGED'); }
function validLabel(v) { return exact(v, ['case_name', 'case_ordinal', 'report_path', 'acceptance_ids', 'requirement_ref', 'target_operation_id', 'target_request_sha256']) && typeof v.case_name === 'string' && Array.from(v.case_name).length <= 160 && count(v.case_ordinal, 1999) && typeof v.report_path === 'string' && v.report_path.length <= 1024 && !v.report_path.includes('\0') && ids(v.acceptance_ids) && v.acceptance_ids.length > 0 && reference(v.requirement_ref) && (v.target_operation_id === null ? v.target_request_sha256 === null : id(v.target_operation_id) && sha(v.target_request_sha256)); }
function attempt(v, o) {
    const a = v;
    if (!exact(a, ['label', 'request', 'checksum', 'draft_sha256', 'phase', 'initial_transport_unknown', 'last_observation', 'error_code', 'receipt', 'receipt_sha256']) || !request(a.request, o) || !sha(a.checksum) || !(a.request.action === 'MAP' ? sha(a.draft_sha256) : a.draft_sha256 === null) || !['PENDING', 'UNKNOWN', 'REJECTED', 'RECORDED'].includes(a.phase) || typeof a.initial_transport_unknown !== 'boolean' || !['NOT_CHECKED', 'NOT_FOUND', 'RECORDED'].includes(a.last_observation) || !(a.error_code === null || typeof a.error_code === 'string' && /^[A-Z0-9_]{1,100}$/.test(a.error_code)))
        return false;
    const label = a.label;
    if (!validLabel(label))
        return false;
    if (a.request.action === 'MAP') {
        if (label.target_operation_id !== null || label.target_request_sha256 !== null || label.case_ordinal !== a.request.selection.source.case_ordinal || label.report_path !== a.request.selection.source.report_path || !same(label.acceptance_ids, a.request.selection.acceptance_ids))
            return false;
    }
    else if (!id(label.target_operation_id) || !sha(label.target_request_sha256))
        return false;
    if (a.receipt === null)
        return a.phase !== 'RECORDED' && a.receipt_sha256 === null;
    try {
        basicReceipt(a.receipt, a.request, a.checksum, o);
        if (a.request.action === 'MAP')
            check(same(a.receipt.proof?.requirement_ref, label.requirement_ref) && a.receipt.proof?.case_label.name === label.case_name);
        return a.phase === 'RECORDED' && a.last_observation === 'RECORDED' && sha(a.receipt_sha256);
    }
    catch {
        return false;
    }
}
function disposition(v) { return exact(v, ['root_sha256', 'generation', 'revision', 'requirement_revision']) && sha(v.root_sha256) && count(v.generation) && v.generation >= 1 && count(v.revision, 512) && count(v.requirement_revision, 512); }
function confirmed(v) { return exact(v, ['label', 'operation_id', 'request_sha256', 'receipt_sha256', 'root_sha256', 'original_generation', 'action', 'case_ordinal', 'initial_transport_unknown', 'draft_sha256', 'dispositions']) && validLabel(v.label) && id(v.operation_id) && sha(v.request_sha256) && sha(v.receipt_sha256) && sha(v.root_sha256) && count(v.original_generation) && v.original_generation >= 1 && ['MAP', 'REMOVE'].includes(v.action) && (v.case_ordinal === null || count(v.case_ordinal, 1999)) && typeof v.initial_transport_unknown === 'boolean' && (v.draft_sha256 === null || sha(v.draft_sha256)) && Array.isArray(v.dispositions) && v.dispositions.length <= 8 && v.dispositions.every(disposition); }
function parse(raw, o) { if (raw === null)
    return { version: 1, owner: copy(o), revision: 0, draft: null, recovery: [], attempt: null, retained: [], confirmed: [] }; check(new TextEncoder().encode(raw).length <= 524288, 'WORK_CASE_LOCAL_CAPACITY'); const v = JSON.parse(raw); check(exact(v, ['version', 'owner', 'revision', 'draft', 'recovery', 'attempt', 'retained', 'confirmed']) && v.version === 1 && same(v.owner, o) && count(v.revision) && (v.draft === null || draft(v.draft, o)) && (v.attempt === null || attempt(v.attempt, o)) && Array.isArray(v.recovery) && v.recovery.length <= 4 && v.recovery.every(r => exact(r, ['id', 'draft']) && id(r.id) && draft(r.draft, o)) && new Set(v.recovery.map(r => r.id)).size === v.recovery.length && Array.isArray(v.retained) && v.retained.length <= 8 && v.retained.every(r => exact(r, ['attempt', 'disposition']) && attempt(r.attempt, o) && (r.disposition === null || disposition(r.disposition) && r.disposition.root_sha256 === r.attempt.request.root_sha256 && r.disposition.generation >= r.attempt.request.generation && (r.attempt.phase === 'REJECTED' || r.disposition.generation > r.attempt.request.generation || r.disposition.revision > r.attempt.request.expected_revision || r.disposition.requirement_revision > r.attempt.request.expected_requirement_revision))) && Array.isArray(v.confirmed) && v.confirmed.length <= 512 && v.confirmed.every(confirmed) && new Set(v.confirmed.map(r => r.request_sha256)).size === v.confirmed.length); return v; }
function ownerOf(s, root) { const p = workRequirementScope(s, root); return { project_id: s.project_id, session_ref: copy(p.session_ref), workspace_ref: copy(p.workspace_ref), root_operation_id: root }; }
function workCaseLocalKey(s, root) { return PREFIX + canonical(ownerOf(s, root)); }
function scoped(o, v) { check(v.project_id === o.project_id && scope(v.scope, o), 'WORK_CASE_SCOPE_CHANGED'); }
function workCaseDraft(v, c) {
    check(choice(c) && v.selected_case && same(v.selected_case.source, c.source), 'WORK_CASE_SELECTION_UNAVAILABLE');
    const link = v.selected_requirement_link;
    check(c.link_id === null || link?.operation_id === c.link_id, 'WORK_CASE_SELECTION_UNAVAILABLE');
    const criteria = [...c.acceptance_ids].sort().map(id => { const row = link?.acceptance.find(r => r.id === id); return row ? { id, text_sha256: row.text_sha256, requirement_ids: [...row.requirement_ids].sort(), basis_status: row.eligible ? 'ELIGIBLE' : row.basis_status, eligible: row.eligible } : { id, missing: true }; });
    return { scope: copy(v.scope), root_sha256: v.root_sha256, revision: v.revision, requirement_revision: v.requirement_revision, choice: copy(c), basis: { case_ref: copy(v.selected_case.case_ref), command_status: v.selected_case.command_status, command_exit_code: v.selected_case.command_exit_code, association: v.selected_case.association, reason_codes: copy(v.selected_case.reason_codes), link: link && c.link_id ? { operation_id: link.operation_id, request_sha256: link.request_sha256, requirement_ref: link.requirement_ref, available: link.available } : null, criteria } };
}
function workCaseDraftMatches(v, d) { try {
    return same(workCaseDraft(v, d.choice), d);
}
catch {
    return false;
} }
function workCaseCanSave(v, d) { try {
    const c = d.choice, link = v.selected_requirement_link;
    check(workCaseDraftMatches(v, d) && c.link_id && link?.available && link.operation_id === c.link_id && link.request_sha256 === c.link_sha256 && same(link.requirement_ref, c.requirement_ref) && c.acceptance_ids.length > 0 && c.acceptance_ids.every(id => link.acceptance.some(r => r.id === id && r.eligible)), 'WORK_CASE_REVIEW_REQUIRED');
    return true;
}
catch {
    return false;
} }
function workCasePending(v) { return !!v.attempt && v.attempt.phase !== 'RECORDED' || v.retained.some(r => r.disposition === null && ['PENDING', 'UNKNOWN'].includes(r.attempt.phase)); }
const dirty = new Set(), activeJournals = new Set();
async function flushWorkCaseDrafts() { try {
    for (;;) {
        const targets = [...new Set([...dirty, ...activeJournals])];
        if (!targets.length)
            return;
        await Promise.all(targets.map(j => j.flush()));
    }
}
catch {
    throw Error('WORK_CASE_DRAFT_UNSAVED');
} }
class WorkCaseJournal {
    session;
    root;
    storage;
    locks;
    newId;
    owner;
    key;
    raw;
    local;
    changed = false;
    tail = Promise.resolve();
    listeners = new Set();
    active = false;
    constructor(session, root, storage, locks = globalThis.navigator?.locks ?? null, newId = () => crypto.randomUUID()) {
        this.session = session;
        this.root = root;
        this.storage = storage;
        this.locks = locks;
        this.newId = newId;
        this.owner = ownerOf(session, root);
        this.key = workCaseLocalKey(session, root);
        this.raw = storage.getItem(this.key);
        this.local = parse(this.raw, this.owner);
    }
    value() { return copy(this.local); }
    unsaved() { return this.changed; }
    running() { return this.active; }
    subscribe(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
    emit() { for (const fn of this.listeners)
        fn(); }
    async validate() { for (const a of [this.local.attempt, ...this.local.retained.map(r => r.attempt)])
        if (a) {
            check(await hash(a.request) === a.checksum);
            if (a.receipt) {
                await validateWorkCaseReceipt(a.receipt, this.session, workRequirementScope({ ...this.session, generation: a.request.generation }, this.root), a.request.root_sha256);
                check(await hash(a.receipt) === a.receipt_sha256);
            }
        } return this; }
    edit(next) { check(draft(next, this.owner)); const state = this.value(); if (state.draft && (!same(state.draft.choice.source, next.choice.source) || state.draft.choice.link_id !== next.choice.link_id) && !state.recovery.some(r => same(r.draft, state.draft))) {
        check(state.recovery.length < 4, 'WORK_CASE_DRAFT_CAPACITY');
        state.recovery.push({ id: this.newId(), draft: copy(state.draft) });
    } state.draft = copy(next); state.revision++; this.local = state; this.changed = true; dirty.add(this); this.emit(); return this.value(); }
    write(next, basis = this.raw) { this.local = copy(next); this.changed = true; dirty.add(this); this.emit(); const raw = JSON.stringify(next); check(new TextEncoder().encode(raw).length <= 524288, 'WORK_CASE_LOCAL_CAPACITY'); check(this.storage.getItem(this.key) === basis, 'WORK_CASE_DRAFT_CONFLICT'); this.storage.setItem(this.key, raw); check(this.storage.getItem(this.key) === raw, 'WORK_CASE_DRAFT_CONFLICT'); this.raw = raw; this.changed = false; dirty.delete(this); this.emit(); return this.value(); }
    async locked(run) { check(this.locks, 'WORK_CASE_LOCK_UNAVAILABLE'); const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 5000); try {
        return await this.locks.request(this.key, { mode: 'exclusive', signal: controller.signal }, async () => { clearTimeout(timer); this.active = true; activeJournals.add(this); try {
            this.emit();
            return await run();
        }
        finally {
            this.active = false;
            activeJournals.delete(this);
            this.emit();
        } });
    }
    finally {
        clearTimeout(timer);
    } }
    serial(run) { const next = this.tail.catch(() => { }).then(() => this.locked(run)); this.tail = next; return next; }
    flush() { return this.serial(async () => this.changed ? this.write(this.local) : this.value()); }
    observed() { return this.listeners.size > 0; }
    current() { check(!this.changed, 'WORK_CASE_DRAFT_UNSAVED'); check(this.storage.getItem(this.key) === this.raw, 'WORK_CASE_DRAFT_CONFLICT'); return this.value(); }
    compact(state, a, d = null) { check(a.phase === 'RECORDED' && a.receipt && a.receipt_sha256); const row = { label: copy(a.label), operation_id: a.request.operation_id, request_sha256: a.checksum, receipt_sha256: a.receipt_sha256, root_sha256: a.request.root_sha256, original_generation: a.request.generation, action: a.request.action, case_ordinal: a.label.case_ordinal, initial_transport_unknown: a.initial_transport_unknown, draft_sha256: a.draft_sha256, dispositions: d ? [copy(d)] : [] }; this.mergeConfirmed(state, row); }
    mergeConfirmed(state, row) { const old = state.confirmed.find(r => r.request_sha256 === row.request_sha256); if (old) {
        check(same({ ...old, dispositions: [], initial_transport_unknown: false }, { ...row, dispositions: [], initial_transport_unknown: false }));
        old.initial_transport_unknown ||= row.initial_transport_unknown;
        for (const d of row.dispositions)
            if (!old.dispositions.some(v => same(v, d))) {
                check(old.dispositions.length < 8, 'WORK_CASE_RECOVERY_CAPACITY');
                old.dispositions.push(copy(d));
            }
    }
    else {
        check(state.confirmed.length < 512, 'WORK_CASE_HISTORY_CAPACITY');
        state.confirmed.push(copy(row));
    } }
    saveAttempt(a) { check(attempt(a, this.owner)); const state = this.value(); if (state.attempt?.phase === 'RECORDED' && state.attempt.checksum !== a.checksum)
        this.compact(state, state.attempt); state.attempt = copy(a); state.revision++; return this.write(state); }
    saveRetained(a) { check(attempt(a, this.owner)); const state = this.value(), row = state.retained.find(r => r.attempt.checksum === a.checksum); check(row && same(row.attempt.request, a.request)); if (a.phase === 'RECORDED') {
        this.compact(state, a, row.disposition);
        state.retained = state.retained.filter(r => r.attempt.checksum !== a.checksum);
    }
    else
        row.attempt = copy(a); state.revision++; return this.write(state); }
    async accept(a, verified) { basicReceipt(verified, a.request, a.checksum, this.owner); const before = this.local.draft ? copy(this.local.draft) : null, [reviewed, receiptSha] = await Promise.all([before && a.draft_sha256 ? hash(before) : Promise.resolve(null), hash(verified)]), state = this.value(); state.attempt = { ...copy(a), phase: 'RECORDED', last_observation: 'RECORDED', error_code: null, receipt: copy(verified), receipt_sha256: receiptSha }; if (before && reviewed === a.draft_sha256 && same(state.draft, before) && a.request.action === 'MAP')
        state.draft = { ...before, revision: verified.revision }; state.revision++; return this.write(state); }
    async restore(id) { return this.serial(async () => { const state = this.value(), row = state.recovery.find(r => r.id === id); check(row && state.draft); const old = state.draft; state.draft = row.draft; row.draft = old; state.revision++; return this.write(state); }); }
    async deleteCopy(id) { return this.serial(async () => { const state = this.value(); check(state.recovery.some(r => r.id === id)); state.recovery = state.recovery.filter(r => r.id !== id); state.revision++; return this.write(state); }); }
    async restoreAttempt(checksum) { return this.serial(async () => { const state = this.current(), i = state.retained.findIndex(r => r.attempt.checksum === checksum); check(i >= 0 && (!state.attempt || ['RECORDED', 'REJECTED'].includes(state.attempt.phase)), 'WORK_CASE_PENDING'); const [row] = state.retained.splice(i, 1); if (state.attempt?.phase === 'RECORDED')
        this.compact(state, state.attempt);
    else if (state.attempt)
        state.retained.push({ attempt: state.attempt, disposition: null }); state.attempt = row.attempt; state.revision++; return this.write(state); }); }
    async retain(v) { return this.serial(async () => { const state = this.current(), a = state.attempt; scoped(this.owner, v); check(a && a.phase !== 'RECORDED' && v.root_sha256 === a.request.root_sha256, 'WORK_CASE_PENDING'); const advanced = v.scope.generation > a.request.generation || v.revision > a.request.expected_revision || v.requirement_revision > a.request.expected_requirement_revision; check(a.phase === 'REJECTED' || a.last_observation === 'NOT_FOUND' && advanced, 'WORK_CASE_PENDING'); check(state.retained.length < 8, 'WORK_CASE_RECOVERY_CAPACITY'); state.retained.push({ attempt: copy(a), disposition: advanced ? { root_sha256: v.root_sha256, generation: v.scope.generation, revision: v.revision, requirement_revision: v.requirement_revision } : null }); state.attempt = null; state.revision++; return this.write(state); }); }
    async resolve(which) {
        return this.serial(async () => {
            const raw = this.storage.getItem(this.key), saved = parse(raw, this.owner), local = this.value(), selected = which === 'LOCAL' ? local.draft : saved.draft, recovery = copy(saved.recovery), state = { ...copy(saved), draft: copy(selected), recovery };
            const keepDraft = (d, id) => { if (!d || same(d, selected) || recovery.some(r => same(r.draft, d)))
                return; check(recovery.length < 4, 'WORK_CASE_DRAFT_CAPACITY'); recovery.push({ id: id && !recovery.some(r => r.id === id) ? id : this.newId(), draft: copy(d) }); };
            for (const row of local.recovery)
                keepDraft(row.draft, row.id);
            keepDraft(which === 'LOCAL' ? saved.draft : local.draft);
            for (const row of local.confirmed)
                this.mergeConfirmed(state, row);
            const merge = (before, after) => { check(same(before.request, after.request) && same(before.label, after.label) && before.draft_sha256 === after.draft_sha256); if (after.phase === 'RECORDED' || before.phase === 'RECORDED') {
                const known = copy(after.phase === 'RECORDED' ? after : before);
                known.initial_transport_unknown ||= before.initial_transport_unknown || after.initial_transport_unknown;
                return known;
            } const unknown = before.initial_transport_unknown || after.initial_transport_unknown || before.phase === 'UNKNOWN' || after.phase === 'UNKNOWN'; return { ...copy(after), phase: unknown ? 'UNKNOWN' : after.phase, initial_transport_unknown: unknown, last_observation: before.last_observation === 'NOT_FOUND' || after.last_observation === 'NOT_FOUND' ? 'NOT_FOUND' : after.last_observation }; };
            const keep = (row) => { if (state.attempt?.checksum === row.attempt.checksum) {
                state.attempt = merge(state.attempt, row.attempt);
                return;
            } const prior = state.retained.find(r => r.attempt.checksum === row.attempt.checksum); if (prior) {
                prior.attempt = merge(prior.attempt, row.attempt);
                if (row.disposition && prior.disposition && !same(row.disposition, prior.disposition))
                    check(false, 'WORK_CASE_DISPOSITION_CONFLICT');
                if (!prior.disposition)
                    prior.disposition = copy(row.disposition);
                return;
            } if (row.attempt.phase === 'RECORDED') {
                this.compact(state, row.attempt, row.disposition);
                return;
            } check(state.retained.length < 8, 'WORK_CASE_RECOVERY_CAPACITY'); state.retained.push(copy(row)); };
            for (const row of local.retained)
                keep(row);
            if (local.attempt)
                keep({ attempt: local.attempt, disposition: null });
            for (const row of state.retained.filter(r => r.attempt.phase === 'RECORDED'))
                this.compact(state, row.attempt, row.disposition);
            state.retained = state.retained.filter(r => r.attempt.phase !== 'RECORDED');
            state.revision = Math.max(saved.revision, local.revision) + 1;
            return this.write(state, raw);
        });
    }
}
class WorkCaseActions {
    journal;
    reader;
    transport;
    newId;
    constructor(journal, reader = new OriginalWorkTestCasesApi(), transport = api, newId = operationId) {
        this.journal = journal;
        this.reader = reader;
        this.transport = transport;
        this.newId = newId;
    }
    async bounded(run) { const abort = new AbortController(); let timer; try {
        return await Promise.race([run(abort.signal), new Promise((_, reject) => { timer = setTimeout(() => { abort.abort(); reject(Error('WORK_CASE_TRANSPORT_UNKNOWN')); }, 25000); })]);
    }
    finally {
        if (timer !== undefined)
            clearTimeout(timer);
    } }
    async verify(v, a) { await validateWorkCaseReceipt(v, this.journal.session, { session_ref: a.request.session_ref, generation: a.request.generation, workspace_ref: a.request.workspace_ref, root_operation_id: a.request.root_operation_id }, a.request.root_sha256); basicReceipt(v, a.request, a.checksum, this.journal.owner); if (a.request.action === 'MAP')
        check(same(v.proof?.requirement_ref, a.label.requirement_ref) && v.proof?.case_label.name === a.label.case_name, 'WORK_CASE_RECEIPT_CHANGED'); return v; }
    async transmit(a) {
        check(await hash(a.request) === a.checksum);
        const o = this.journal.owner, path = '/api/projects/' + encodeURIComponent(o.project_id) + '/agent-sessions/' + encodeURIComponent(o.session_ref.id) + '/work-test-cases/mappings';
        let result;
        try {
            result = await this.bounded(signal => this.transport.request(path, 'POST', copy(a.request), signal, false));
        }
        catch (error) {
            const status = error?.status, rejected = !a.initial_transport_unknown && Number.isInteger(status) && Number(status) >= 400 && Number(status) < 500 && ![408, 429].includes(Number(status));
            this.journal.saveAttempt({ ...a, phase: rejected ? 'REJECTED' : 'UNKNOWN', initial_transport_unknown: a.initial_transport_unknown || !rejected, error_code: rejected ? 'HTTP_REJECTED' : 'TRANSPORT_UNKNOWN' });
            return this.journal.value().attempt;
        }
        let verified;
        try {
            verified = await this.verify(result, a);
        }
        catch {
            this.journal.saveAttempt({ ...a, phase: 'UNKNOWN', initial_transport_unknown: true, error_code: 'RECEIPT_CHANGED' });
            throw Error('WORK_CASE_RECEIPT_CHANGED');
        }
        await this.journal.accept(a, verified);
        return this.journal.value().attempt;
    }
    async pending() { const a = this.journal.value().attempt; check(a && a.phase !== 'RECORDED', 'WORK_CASE_NO_PENDING'); check(await hash(a.request) === a.checksum); return a; }
    async inspectAttempt(session, a, retained = false) {
        let result;
        try {
            result = await this.bounded(signal => this.reader.operation(session, this.journal.root, a.request.root_sha256, a.request.operation_id, a.checksum, signal));
        }
        catch (error) {
            if (error?.status !== 404)
                throw error;
            const next = { ...a, phase: a.phase === 'REJECTED' ? 'REJECTED' : 'UNKNOWN', initial_transport_unknown: a.phase === 'REJECTED' ? a.initial_transport_unknown : true, last_observation: 'NOT_FOUND' };
            if (retained)
                this.journal.saveRetained(next);
            else
                this.journal.saveAttempt(next);
            return next;
        }
        const verified = await this.verify(result, a);
        if (retained) {
            const next = { ...a, phase: 'RECORDED', last_observation: 'RECORDED', error_code: null, receipt: verified, receipt_sha256: await hash(verified) };
            this.journal.saveRetained(next);
            return next;
        }
        await this.journal.accept(a, verified);
        return this.journal.value().attempt;
    }
    inspectRemovalTarget(session) { return this.journal.serial(async () => { const a = this.journal.value().attempt; check(a?.request.action === 'REMOVE' && a.label.target_operation_id && a.label.target_request_sha256); const v = await this.bounded(signal => this.reader.operation(session, this.journal.root, a.request.root_sha256, a.label.target_operation_id, a.label.target_request_sha256, signal)); check(v.action === 'MAP' && v.mapping_key === a.request.mapping_key && v.proof?.case_label.ordinal === a.label.case_ordinal && v.proof?.case_label.name === a.label.case_name && v.selection?.source.report_path === a.label.report_path && same(v.proof?.acceptance_ids, a.label.acceptance_ids) && same(v.proof?.requirement_ref, a.label.requirement_ref), 'WORK_CASE_RECEIPT_CHANGED'); return v; }); }
    inspect(session) { return this.journal.serial(async () => this.inspectAttempt(session, await this.pending())); }
    inspectRetained(session, sum) { return this.journal.serial(async () => { const a = this.journal.value().retained.find(r => r.attempt.checksum === sum)?.attempt; check(a && await hash(a.request) === a.checksum); return this.inspectAttempt(session, a, true); }); }
    inspectConfirmed(session, sum) { return this.journal.serial(async () => { const row = this.journal.value().confirmed.find(r => r.request_sha256 === sum); check(row); const v = await this.bounded(signal => this.reader.operation(session, this.journal.root, row.root_sha256, row.operation_id, row.request_sha256, signal)); check(await hash(v) === row.receipt_sha256 && v.operation_id === row.operation_id && v.request_sha256 === row.request_sha256, 'WORK_CASE_RECEIPT_CHANGED'); const original = { session_ref: this.journal.owner.session_ref, generation: row.original_generation, workspace_ref: this.journal.owner.workspace_ref, root_operation_id: this.journal.root, operation_id: v.operation_id, root_sha256: v.root_sha256, expected_revision: v.revision - 1, expected_requirement_revision: v.requirement_revision, action: v.action, mapping_key: v.mapping_key, selection: v.selection }; check(await hash(original) === row.request_sha256, 'WORK_CASE_RECEIPT_CHANGED'); return v; }); }
    async submit(session, reviewed, action, target) {
        await this.journal.flush();
        return this.journal.serial(async () => {
            await this.journal.validate();
            const state = this.journal.current();
            check(!workCasePending(state), 'WORK_CASE_PENDING');
            scoped(this.journal.owner, reviewed);
            const d = state.draft;
            check(action === 'MAP' ? d && workCaseCanSave(reviewed, d) : target && (same(reviewed.selected_mapping, target) || reviewed.rows.some(r => same(r.operation, target))), 'WORK_CASE_REVIEW_REQUIRED');
            const fresh = await this.bounded(signal => this.reader.inspect(session, this.journal.root, { caseSource: action === 'MAP' ? d.choice.source : null, selectedLink: action === 'MAP' ? d.choice.link_id : null, mappingKey: action === 'REMOVE' ? target.mapping_key : null, offset: reviewed.page.offset }, signal));
            scoped(this.journal.owner, fresh);
            check(same(fresh.scope, reviewed.scope) && fresh.root_sha256 === reviewed.root_sha256 && fresh.revision === reviewed.revision && fresh.requirement_revision === reviewed.requirement_revision, 'WORK_CASE_REVIEW_REQUIRED');
            check(action === 'MAP' ? workCaseCanSave(fresh, d) : same(fresh.selected_mapping, target) || fresh.rows.some(r => same(r.operation, target)), 'WORK_CASE_REVIEW_REQUIRED');
            const c = d?.choice, selection = action === 'MAP' ? { requirement_link_operation_id: c.link_id, requirement_link_request_sha256: c.link_sha256, source: copy(c.source), acceptance_ids: [...c.acceptance_ids] } : null;
            const body = { ...copy(fresh.scope), operation_id: this.newId(), root_sha256: fresh.root_sha256, expected_revision: fresh.revision, expected_requirement_revision: fresh.requirement_revision, action, mapping_key: action === 'MAP' ? await workCaseMappingKey(fresh.root_sha256, c.requirement_ref.id, c.source) : target.mapping_key, selection };
            check(request(body, this.journal.owner));
            if (action === 'MAP')
                check(!([fresh.selected_mapping, ...fresh.rows.map(row => row.operation)].some(row => row?.mapping_key === body.mapping_key && row.selection?.requirement_link_operation_id === selection?.requirement_link_operation_id && row.selection?.requirement_link_request_sha256 === selection?.requirement_link_request_sha256 && same(row.selection?.source, selection?.source) && same([...(row.selection?.acceptance_ids ?? [])].sort(), [...(selection?.acceptance_ids ?? [])].sort()))), 'WORK_CASE_UNCHANGED');
            const label = { case_name: action === 'MAP' ? fresh.selected_case.case.name : target.proof.case_label.name, case_ordinal: action === 'MAP' ? c.source.case_ordinal : target.proof.case_label.ordinal, report_path: action === 'MAP' ? c.source.report_path : target.selection.source.report_path, acceptance_ids: action === 'MAP' ? [...c.acceptance_ids] : [...target.proof.acceptance_ids], requirement_ref: copy(action === 'MAP' ? c.requirement_ref : target.proof.requirement_ref), target_operation_id: action === 'REMOVE' ? target.operation_id : null, target_request_sha256: action === 'REMOVE' ? target.request_sha256 : null };
            const a = { label, request: body, checksum: await hash(body), draft_sha256: action === 'MAP' ? await hash(d) : null, phase: 'PENDING', initial_transport_unknown: false, last_observation: 'NOT_CHECKED', error_code: null, receipt: null, receipt_sha256: null };
            this.journal.saveAttempt(a);
            return this.transmit(a);
        });
    }
    async retry(session) { await this.journal.flush(); return this.journal.serial(async () => { const a = await this.pending(), found = await this.inspectAttempt(session, a); if (found.phase === 'RECORDED')
        return found; check(found.last_observation === 'NOT_FOUND' && found.phase !== 'REJECTED', 'WORK_CASE_PENDING'); const fresh = await this.bounded(signal => this.reader.inspect(session, this.journal.root, {}, signal)); scoped(this.journal.owner, fresh); check(same(fresh.scope, { session_ref: a.request.session_ref, generation: a.request.generation, workspace_ref: a.request.workspace_ref, root_operation_id: a.request.root_operation_id }) && fresh.root_sha256 === a.request.root_sha256 && fresh.revision === a.request.expected_revision && fresh.requirement_revision === a.request.expected_requirement_revision, 'WORK_CASE_REVIEW_REQUIRED'); const next = { ...found, phase: 'PENDING' }; this.journal.saveAttempt(next); return this.transmit(next); }); }
}
function workCaseUnchanged(v, d) { const old = v.selected_mapping, c = d.choice; return !!old && old.action === 'MAP' && old.selection?.requirement_link_operation_id === c.link_id && old.selection?.requirement_link_request_sha256 === c.link_sha256 && same(old.selection?.source, c.source) && same([...(old.selection?.acceptance_ids ?? [])].sort(), [...c.acceptance_ids].sort()); }
module.exports = { WorkCaseJournal, WorkCaseActions, workCaseLocalKey, workCaseDraft, workCaseDraftMatches, workCaseCanSave, workCasePending, workCaseUnchanged, flushWorkCaseDrafts, validateLocal: parse };

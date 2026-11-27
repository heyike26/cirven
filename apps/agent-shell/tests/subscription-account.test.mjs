import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import { resolve, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';
import ts from 'typescript';
import babel from '@babel/core';
import solidPreset from '../node_modules/vite-plugin-solid/node_modules/babel-preset-solid/index.js';
import { renderToString } from 'solid-js/web';
import { ApiClient } from '../src/api.ts';
import { beginSubscriptionLogin, canOpenSubscriptionLogin, observeSubscriptionAccount, openSubscriptionLogin, readSubscriptionHistory, signedOutSubscription, subscriptionAccount, SubscriptionAccountClient, SUBSCRIPTION_HISTORY_KEY, subscriptionLoginURL } from '../src/subscription-account.ts';

const app = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const storage = () => { const values = new Map(); return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), values }; };
const url = 'https://auth.openai.com/oauth/authorize?state=fixture-state&code_challenge=fixture-challenge';
const pending = (extra = {}) => ({ ...signedOutSubscription(), status: 'WAITING_USER', attempt_id: 'attempt_exact', login_url: url, phase: 'WAITING_USER', ...extra });
const authenticated = (extra = {}) => ({ ...pending(), status: 'AUTHENTICATED', models: [{ id: 'gpt-fixture-codex', name: 'Original catalog model' }], ...extra });
const profile = () => ({ profile_ref: { id: 'chatgpt-subscription', revision: 1, sha256: 'a'.repeat(64) }, provider_url: 'https://chatgpt.com/backend-api/codex', model_id: 'gpt-fixture-codex', auth_kind: 'OAUTH_SUBSCRIPTION', credential_present: false, model_access: 'NOT_VALIDATED', supports_images: true });

async function protocol(t, handle) {
  const calls = [];
  const server = createServer(async (request, response) => {
    let raw = ''; for await (const part of request) raw += part;
    const call = { path: request.url, method: request.method, headers: request.headers, body: raw ? JSON.parse(raw) : undefined }; calls.push(call);
    if (call.path === '/api/bootstrap') { response.setHeader('content-type', 'application/json'); response.end(JSON.stringify({ csrf_token: 'local-fixture-csrf' })); return; }
    try {
      const value = await handle(call);
      if (value === 'DROP') { response.destroy(); return; }
      response.writeHead(value?.http_status ?? 200, { 'content-type': 'application/json' }); response.end(JSON.stringify(value?.body ?? value));
    } catch { response.writeHead(500); response.end('{}'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); });
  const origin = `http://127.0.0.1:${server.address().port}`;
  return { api: new ApiClient(fetch, origin), calls };
}

test('real HTTP bootstrap/CSRF flow submits explicit login, complete and subscription profile with no API key', async t => {
  let state = signedOutSubscription();
  const local = storage();
  const f = await protocol(t, call => {
    if (call.method === 'GET') return state;
    assert.equal(call.headers['x-eeas-csrf'], 'local-fixture-csrf');
    if (call.path.endsWith('/login')) { assert.equal(call.body.method, 'browser'); assert.match(call.body.operation_id, /^subscription-/); return state = pending(); }
    if (call.path.endsWith('/complete')) { assert.deepEqual(call.body, {}); return state = authenticated(); }
    if (call.path.endsWith('/profiles')) { assert.deepEqual(Object.keys(call.body).sort(), ['id', 'model_id', 'operation_id']); return profile(); }
    assert.fail('unexpected request');
  });
  const client = new SubscriptionAccountClient(f.api, local);
  assert.equal((await client.refresh()).status, 'SIGNED_OUT');
  assert.equal((await client.login()).status, 'WAITING_USER');
  assert.equal((await client.complete('attempt_exact')).status, 'AUTHENTICATED');
  assert.deepEqual(await client.createProfile('chatgpt-subscription', 'gpt-fixture-codex'), profile());
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 3);
  assert.ok(f.calls.every(call => !(JSON.stringify(call.body) ?? '').includes('api_key')));
  const saved = local.getItem(SUBSCRIPTION_HISTORY_KEY);
  assert.ok(!saved.includes('auth.openai.com') && !saved.includes('fixture-state') && !saved.includes('code_challenge'));
  assert.equal(readSubscriptionHistory(local).length, 3);
  assert.ok(readSubscriptionHistory(local).every(item => item.acknowledged));
});

test('401 or lost POST outcome is never retried and reload performs GET only', async t => {
  for (const mode of ['UNAUTHORIZED', 'DROP']) {
    let state = signedOutSubscription(); const local = storage();
    const f = await protocol(t, call => {
      if (call.method === 'GET') return state;
      state = pending();
      return mode === 'DROP' ? 'DROP' : { http_status: 401, body: { error: { code: 'EXPIRED', message: 'expired' } } };
    });
    const first = new SubscriptionAccountClient(f.api, local); await first.refresh();
    await assert.rejects(first.login());
    assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
    const reloaded = new SubscriptionAccountClient(f.api, local);
    assert.equal((await reloaded.refresh()).attempt_id, 'attempt_exact');
    await assert.rejects(reloaded.login(), /已有尝试/);
    assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
    assert.equal(readSubscriptionHistory(local)[0].acknowledged, false);
  }
});

test('COMPLETING persists phase and the exact attempt complete/cancel each submit once across reload', async t => {
  let state = pending(); const local = storage();
  const f = await protocol(t, call => {
    if (call.method === 'GET') return state;
    if (call.path.endsWith('/complete')) return state = pending({ phase: 'COMPLETING', login_url: undefined });
    if (call.path.endsWith('/cancel')) return state = { ...signedOutSubscription(), attempt_id: 'attempt_exact', phase: 'CANCELLED' };
  });
  const first = new SubscriptionAccountClient(f.api, local); await first.refresh();
  assert.equal((await first.complete('attempt_exact')).phase, 'COMPLETING');
  const second = new SubscriptionAccountClient(f.api, local); await second.refresh();
  assert.equal(second.canComplete('attempt_exact'), false);
  await assert.rejects(second.complete('attempt_exact'), /不会重发/);
  await assert.rejects(second.cancel('different'), /当前账号/);
  await second.cancel('attempt_exact');
  const third = new SubscriptionAccountClient(f.api, local); await third.refresh();
  assert.equal(third.canCancel('attempt_exact'), false);
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 2);
});

test('concurrent duplicate intents and unsafe model selection are blocked before a second POST', async t => {
  let release; let received;
  const ready = new Promise(resolve => { received = resolve; });
  const f = await protocol(t, async call => {
    if (call.method === 'GET') return signedOutSubscription();
    received(); await new Promise(resolve => { release = resolve; }); return pending();
  });
  const client = new SubscriptionAccountClient(f.api, storage()); await client.refresh();
  const original = client.login(); await ready;
  await assert.rejects(client.login(), /仍在进行/); release(); await original;
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
  await assert.rejects(client.createProfile('saved', 'invented-paid-model'), /登录后选择/);
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
});

test('metadata corruption/storage failure blocks side effects and unresolved profile saves do not replay', async t => {
  let state = authenticated();
  const f = await protocol(t, call => call.method === 'GET' ? state : 'DROP');
  const broken = storage(); broken.setItem(SUBSCRIPTION_HISTORY_KEY, '{not json');
  const corrupt = new SubscriptionAccountClient(f.api, broken); await corrupt.refresh();
  await assert.rejects(corrupt.createProfile('saved', 'gpt-fixture-codex'), /索引已损坏/);
  const full = new SubscriptionAccountClient(f.api, { getItem: () => null, setItem: () => { throw new Error('full'); } }); await full.refresh();
  await assert.rejects(full.createProfile('saved', 'gpt-fixture-codex'), /索引无法保存/);
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 0);
  const local = storage(); const first = new SubscriptionAccountClient(f.api, local); await first.refresh();
  await assert.rejects(first.createProfile('saved', 'gpt-fixture-codex'));
  const reload = new SubscriptionAccountClient(f.api, local); await reload.refresh();
  await assert.rejects(reload.createProfile('saved', 'gpt-fixture-codex'), /不会重发/);
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
});

test('strict official URLs are used only on Web; desktop reopening uses exact attempt id even without a restored URL', async () => {
  for (const value of ['http://auth.openai.com/a', 'https://auth.openai.com.evil.test/a', 'https://user:pw@auth.openai.com/a', 'https://auth.openai.com:444/a', 'https://auth.openai.com/a#token', 'https://auth.openai.com\\@evil.test', 'javascript:alert(1)']) assert.throws(() => subscriptionLoginURL(value));
  assert.equal(subscriptionLoginURL(url), url);
  const opened = []; await openSubscriptionLogin(url, 'attempt_exact', { open: (...args) => opened.push(args) });
  assert.deepEqual(opened, [[url, '_blank', 'noopener,noreferrer']]);
  const desktop = { eeasDesktop: { openSubscriptionLogin: async id => { opened.push(id); } } };
  assert.equal(canOpenSubscriptionLogin(pending({ login_url: undefined }), desktop), true);
  await openSubscriptionLogin('', 'attempt_exact', desktop);
  assert.equal(opened.at(-1), 'attempt_exact');
  await assert.rejects(openSubscriptionLogin(url, 'attempt_exact', { eeasDesktop: {}, open: () => assert.fail('no browser fallback inside desktop') }), /桌面版本/);
});

test('public state decoding discards extra credentials and does not upgrade original catalog availability into model validation', () => {
  const actual = subscriptionAccount({ ...authenticated(), access_token: 'never-copy', refresh_token: 'never-copy', profile: { ...profile(), secret_value: 'never-copy' } });
  assert.equal(actual.model_access, 'NOT_VALIDATED'); assert.ok(!JSON.stringify(actual).includes('never-copy'));
  assert.throws(() => subscriptionAccount({ ...authenticated(), api_key_fallback: true }));
  assert.throws(() => subscriptionAccount({ ...authenticated(), models: [{ id: 'same', name: 'a' }, { id: 'same', name: 'b' }] }));
  assert.throws(() => subscriptionAccount({ ...authenticated(), profile: { ...profile(), auth_kind: 'API_KEY', credential_present: true } }));
  assert.throws(() => subscriptionAccount({ ...authenticated(), profile: { ...profile(), provider_url: 'https://other.example/v1' } }));
});

test('exact profile-operation GET recovers a lost save; INCOMPLETE/NO_RECORD or mismatched receipts never acknowledge or replay', async t => {
  for (const outcome of ['COMPLETED', 'INCOMPLETE', 'NO_RECORD', 'WRONG_OPERATION', 'WRONG_PROFILE']) {
    const local = storage(); let operationId;
    const f = await protocol(t, call => {
      if (call.path === '/api/subscription/account') return authenticated({ profile: profile() });
      if (call.method === 'POST') { operationId = call.body.operation_id; return 'DROP'; }
      if (call.path.startsWith('/api/subscription/profile-operations/')) return {
        operation_id: outcome === 'WRONG_OPERATION' ? 'different' : operationId,
        status: ['WRONG_OPERATION', 'WRONG_PROFILE'].includes(outcome) ? 'COMPLETED' : outcome,
        result: outcome === 'COMPLETED' || outcome === 'WRONG_OPERATION' ? profile() : outcome === 'WRONG_PROFILE' ? { ...profile(), model_id: 'different' } : null,
      };
    });
    const first = new SubscriptionAccountClient(f.api, local); await first.refresh();
    await assert.rejects(first.createProfile('chatgpt-subscription', 'gpt-fixture-codex'));
    const reload = new SubscriptionAccountClient(f.api, local); await reload.refresh();
    assert.equal(readSubscriptionHistory(local).at(-1).acknowledged, false, 'account profile alone cannot prove original operation');
    if (outcome.startsWith('WRONG')) await assert.rejects(reload.restoreProfiles(), /不匹配/);
    else {
      const restored = await reload.restoreProfiles();
      assert.equal(restored[0].status, outcome);
      assert.equal(readSubscriptionHistory(local).at(-1).acknowledged, outcome === 'COMPLETED');
      assert.equal(restored[0].result?.profile_ref.id ?? null, outcome === 'COMPLETED' ? 'chatgpt-subscription' : null);
    }
    assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
    assert.ok(f.calls.some(call => call.path === `/api/subscription/profile-operations/${operationId}`));
  }
});

test('actual Solid panel renders compact login/waiting/authenticated states and no token/key inputs or automatic POST on mount', async t => {
  const tempRoot = process.env.EEAS_SUBSCRIPTION_TEST_TEMP ?? process.env.TMPDIR;
  assert.ok(tempRoot, 'test temp root must be explicitly supplied on D');
  const directory = await mkdtemp(resolve(tempRoot, 'solid-')); t.after(() => rm(directory, { recursive: true, force: true }));
  const source = await readFile(resolve(app, 'src/ChatGPTSubscriptionPanel.tsx'), 'utf8');
  const transpiled = ts.transpileModule(source, { reportDiagnostics: true, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve } });
  assert.deepEqual(transpiled.diagnostics, []);
  const logoSource = await readFile(resolve(app,'src/BrandLogo.tsx'),'utf8');
  const logoTS = ts.transpileModule(logoSource,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext,jsx:ts.JsxEmit.Preserve}}).outputText;
  const icon = 'data:image/svg+xml;base64,'+(await readFile(resolve(app,'../../packages/branding/icon.svg'))).toString('base64');
  const logoJS = logoTS.replace(/import icon from ['"]\.\.\/\.\.\/\.\.\/packages\/branding\/icon\.svg['"];?/,`const icon=${JSON.stringify(icon)};`).replace(/from ['"]\.\/brand['"]/g,`from '${pathToFileURL(resolve(app,'src/brand.ts')).href}'`).replace(/from ['"]solid-js['"]/g,`from '${pathToFileURL(resolve(app,'node_modules/solid-js/dist/server.js')).href}'`);
  const logoCode = babel.transformSync(logoJS,{filename:'brand.jsx',presets:[[solidPreset,{generate:'ssr',hydratable:false}]],configFile:false,babelrc:false}).code.replace(/from ['"]solid-js\/web['"]/g,`from '${pathToFileURL(resolve(app,'node_modules/solid-js/web/dist/server.js')).href}'`);
  const logoFile=resolve(directory,'brand.mjs');await writeFile(logoFile,logoCode);
  const js = transpiled.outputText.replace(/from ['"]\.\/ui-language['"]/g,`from '${new URL('./fixture-ui-language.mjs',import.meta.url).href}'`).replace(/import ['"]\.\/(?:subscription-account|provider-layout)\.css['"];?/g, '').replace(/from ['"]\.\/subscription-account['"]/g, `from '${pathToFileURL(resolve(app, 'src/subscription-account.ts')).href}'`).replace(/from ['"]solid-js['"]/g, `from '${pathToFileURL(resolve(app, 'node_modules/solid-js/dist/server.js')).href}'`).replace(/from ['"]\.\/model-catalog['"]/g,`from '${pathToFileURL(resolve(app,'src/model-catalog.ts')).href}'`).replace(/from ['"]\.\/BrandLogo['"]/g,`from '${pathToFileURL(logoFile).href}'`);
  const code = babel.transformSync(js, { filename: 'subscription.jsx', presets: [[solidPreset, { generate: 'ssr', hydratable: false }]], configFile: false, babelrc: false }).code.replace(/from ['"]solid-js\/web['"]/g, `from '${pathToFileURL(resolve(app, 'node_modules/solid-js/web/dist/server.js')).href}'`);
  const file = resolve(directory, 'subscription.mjs'); await writeFile(file, code); const { default: Panel } = await import(pathToFileURL(file).href);
  const client = { refresh: () => assert.fail('SSR does not mount or GET'), canComplete: () => false, canCancel: () => true };
  const signedOut = renderToString(() => Panel({ client, initialAccount: signedOutSubscription() }));
  assert.match(signedOut, /账号登录/); assert.doesNotMatch(signedOut, /OpenCode|ChatGPT|OpenAI/); assert.doesNotMatch(signedOut, /type="password"|access_token|refresh_token/);
  const waiting = renderToString(() => Panel({ client, initialAccount: pending({ phase: 'COMPLETING' }), openLogin: () => {} }));
  assert.match(waiting, /等待登录完成/); assert.doesNotMatch(waiting, /<button[^>]*>完成登录<\/button>/); assert.match(waiting, /打开官方登录页/);
  const signedIn = renderToString(() => Panel({ client, initialAccount: authenticated({ profile: profile() }) }));
  assert.match(signedIn, /账号已登录/); assert.match(signedIn, /此模型尚未验证/); assert.match(signedIn, /选择模型/); assert.doesNotMatch(signedIn, /此模型已有成功响应|OpenCode/);
  const verified=renderToString(()=>Panel({client,initialAccount:authenticated({profile:profile(),model_access:'VERIFIED'})}));assert.match(verified,/此模型已有成功响应/);
  const expired=renderToString(()=>Panel({client,initialAccount:authenticated({profile:profile(),model_access:'NOT_VALIDATED',phase:'REAUTH_REQUIRED'})}));
  assert.match(expired,/请重新登录/);assert.match(expired,/不会自动重发请求/);
  assert.doesNotMatch(expired,/此模型已有成功响应|账号已登录|<select/);
  assert.equal((expired.match(/>重新登录<\/button>/g)??[]).length,1);
  const reconnect=renderToString(()=>Panel({client,initialAccount:pending({profile:profile(),model_access:'VERIFIED'})}));assert.match(reconnect,/aria-current="step"[^>]*>连接账号/);assert.doesNotMatch(reconnect,/此模型已有成功响应/);
  const mismatch=renderToString(()=>Panel({client,initialAccount:authenticated({profile:{...profile(),model_id:'not-in-catalog'},model_access:'VERIFIED'})}));assert.doesNotMatch(mismatch,/此模型已有成功响应/);
  const renderer = await readFile(resolve(app, 'src/ProviderRenderer.tsx'), 'utf8');
  assert.match(renderer, /<ChatGPTSubscriptionPanel/); assert.match(renderer, /auth_kind!==['"]OAUTH_SUBSCRIPTION['"]/);
  assert.match(source, /onMount\(\(\) => \{ void perform\('读取账号状态', refresh\)/);
  assert.doesNotMatch(source, /setInterval|setTimeout|localStorage\.setItem|\.login\(\).*onMount/s);
});

test('authenticated account explicitly reconnects once; mount and pending refresh never start another login', async t => {
  let state=authenticated({profile:profile(),phase:'REAUTH_REQUIRED'});
  const local=storage();
  const f=await protocol(t,call=>{
    if(call.path==='/api/subscription/account'&&call.method==='GET')return state;
    if(call.path==='/api/subscription/login'&&call.method==='POST')return state=pending();
    if(call.path==='/api/subscription/attempts/attempt_exact/complete'&&call.method==='POST')return state=pending({phase:'COMPLETING'});
    throw Error('Unexpected recovery request');
  });
  const client=new SubscriptionAccountClient(f.api,local);
  await client.refresh();assert.equal(f.calls.filter(c=>c.method==='POST').length,0);
  const opened=[];await beginSubscriptionLogin(client,(...args)=>opened.push(args));
  assert.equal(opened.length,1);assert.equal(f.calls.filter(c=>c.path==='/api/subscription/login').length,1);
  assert.equal(f.calls.filter(c=>c.path==='/api/subscription/attempts/attempt_exact/complete').length,1);
  await client.refresh();await assert.rejects(client.login(),/已有尝试/);
  assert.equal(f.calls.filter(c=>c.method==='POST').length,2);
});

test('new explicit login opens the official page, starts its callback once and observes authentication without a manual completion step', async t => {
  let state = signedOutSubscription(), observations = 0;
  const order = [], local = storage(), observed = [];
  const f = await protocol(t, call => {
    if (call.path === '/api/subscription/account') {
      if (state.phase === 'COMPLETING' && ++observations >= 3) state = authenticated();
      return state;
    }
    if (call.path === '/api/subscription/login') { order.push('LOGIN'); return state = pending(); }
    if (call.path.endsWith('/complete')) { order.push('COMPLETE'); return state = pending({ phase: 'COMPLETING' }); }
    assert.fail('no model/profile/cancel request is needed to observe login');
  });
  const client = new SubscriptionAccountClient(f.api, local); await client.refresh();
  const result = await beginSubscriptionLogin(client, (opened, id) => {
    order.push('OPEN'); assert.equal(opened, url); assert.equal(id, 'attempt_exact');
  }, value => observed.push(value.status));
  assert.equal(result.account.phase, 'COMPLETING');
  assert.deepEqual(order, ['LOGIN', 'OPEN', 'COMPLETE']);
  assert.equal(await observeSubscriptionAccount(client, { intervalMs: 5, timeoutMs: 1000, onAccount: value => observed.push(value.status) }), 'AUTHENTICATED');
  assert.equal(observed.at(-1), 'AUTHENTICATED');
  assert.equal(f.calls.filter(call => call.path.endsWith('/complete')).length, 1);
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 2);
  const reloaded = new SubscriptionAccountClient(f.api, local);
  await reloaded.refresh(); assert.equal(reloaded.canComplete('attempt_exact'), false);
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 2);
  assert.doesNotMatch(local.getItem(SUBSCRIPTION_HISTORY_KEY), /auth\.openai\.com|fixture-state|code_challenge/);
});

test('browser-open failure still registers one callback and remains recoverable without retrying authorization', async t => {
  let state = signedOutSubscription();
  const f = await protocol(t, call => {
    if (call.method === 'GET') return state;
    if (call.path.endsWith('/login')) return state = pending();
    if (call.path.endsWith('/complete')) return state = pending({ phase: 'COMPLETING' });
    assert.fail('unexpected mutation');
  });
  const client = new SubscriptionAccountClient(f.api, storage()); await client.refresh();
  const result = await beginSubscriptionLogin(client, () => { throw new Error('system browser unavailable'); });
  assert.equal(result.account.phase, 'COMPLETING'); assert.match(result.browserError, /未能自动打开/);
  await assert.rejects(client.complete('attempt_exact'), /不会重发/);
  assert.equal(await observeSubscriptionAccount(client, { intervalMs: 5, timeoutMs: 30, onAccount: () => {} }), 'TIMEOUT');
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 2);
});

test('reloaded pending login uses GET only, and explicit cancel aborts the pending observer without resurrecting it', async t => {
  let state = pending({ phase: 'COMPLETING', login_url: undefined });
  let waitingGet, releaseGet, block = false;
  const requested = new Promise(resolve => { waitingGet = resolve; });
  const f = await protocol(t, async call => {
    if (call.method === 'GET') {
      if (block) { waitingGet(); await new Promise(resolve => { releaseGet = resolve; }); }
      return state;
    }
    if (call.path.endsWith('/cancel')) return state = { ...signedOutSubscription(), attempt_id: 'attempt_exact', phase: 'CANCELLED' };
    assert.fail('reload cannot restart login or completion');
  });
  const local = storage(), original = new SubscriptionAccountClient(f.api, local);
  await original.refresh();
  const restored = new SubscriptionAccountClient(f.api, local); await restored.refresh();
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 0);
  const controller = new AbortController(), updates = [];
  block = true;
  const observed = observeSubscriptionAccount(restored, { intervalMs: 5, timeoutMs: 1000, signal: controller.signal, onAccount: value => updates.push(value) });
  await requested;
  controller.abort(); block = false;
  await restored.cancel('attempt_exact'); releaseGet();
  assert.equal(await observed, 'STOPPED'); assert.deepEqual(updates, []);
  assert.equal((await restored.refresh()).status, 'SIGNED_OUT');
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
  assert.ok(f.calls.filter(call => call.method === 'POST').every(call => call.path.endsWith('/cancel')));
});

test('a timed-out account read is aborted and produces no late update or automatic side effect', async t => {
  let received, release;
  const ready = new Promise(resolve => { received = resolve; });
  const f = await protocol(t, async call => {
    assert.equal(call.method, 'GET'); received(); await new Promise(resolve => { release = resolve; });
    return pending({ phase: 'COMPLETING' });
  });
  const updates = [], client = new SubscriptionAccountClient(f.api, storage());
  const observed = observeSubscriptionAccount(client, { timeoutMs: 40, intervalMs: 5, onAccount: value => updates.push(value) });
  await ready; assert.equal(await observed, 'TIMEOUT'); release();
  assert.deepEqual(updates, []); assert.equal(f.calls.filter(call => call.method === 'POST').length, 0);
});

test('actual provider chooser renders peer account/API entries and the chosen full API form without a credential dialog', async t => {
  const tempRoot = process.env.EEAS_SUBSCRIPTION_TEST_TEMP ?? process.env.TMPDIR;
  assert.ok(tempRoot);
  const directory = await mkdtemp(resolve(tempRoot, 'chooser-')); t.after(() => rm(directory, { recursive: true, force: true }));
  const files = new Map();
  for (const name of ['BrandLogo', 'ProviderConnectionForm', 'ChatGPTSubscriptionPanel', 'ProviderRenderer']) {
    const source = await readFile(resolve(app, `src/${name}.tsx`), 'utf8');
    let js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext, jsx: ts.JsxEmit.Preserve } }).outputText;
    js=js.replace(/import icon from ['"]\.\.\/\.\.\/\.\.\/packages\/branding\/icon\.svg['"];?/,`const icon=${JSON.stringify('data:image/svg+xml;base64,'+(await readFile(resolve(app,'../../packages/branding/icon.svg'))).toString('base64'))};`);
    js = js.replace(/import ['"]\.\/(?:subscription-account|provider-layout)\.css['"];?/g, '').replace(/from ['"]solid-js['"]/g, `from '${pathToFileURL(resolve(app, 'node_modules/solid-js/dist/server.js')).href}'`);
    js = js.replace(/from ['"]\.\/([^'"]+)['"]/g, (_whole, relative) => `from '${relative==='ui-language'?new URL('./fixture-ui-language.mjs',import.meta.url).href:files.get(relative) ?? pathToFileURL(resolve(app, `src/${relative}.ts`)).href}'`);
    const code = babel.transformSync(js, { filename: `${name}.jsx`, presets: [[solidPreset, { generate: 'ssr', hydratable: false }]], configFile: false, babelrc: false }).code.replace(/from ['"]solid-js\/web['"]/g, `from '${pathToFileURL(resolve(app, 'node_modules/solid-js/web/dist/server.js')).href}'`);
    const file = resolve(directory, `${name}.mjs`); await writeFile(file, code); files.set(name, pathToFileURL(file).href);
  }
  const { default: Renderer } = await import(files.get('ProviderRenderer'));
  const view = artifactId => renderToString(() => Renderer({ artifact: { artifactId, data: [] }, context: { dispatch: () => assert.fail('SSR cannot dispatch any operation') } }));
  const api = view('providers-api-explicit-user-entry');
  assert.match(api, /role="tablist"[^>]*aria-label="模型连接方式"/);
  assert.match(api, /aria-selected="false"[^>]*>订阅账号/);
  assert.match(api, /aria-selected="true"[^>]*>API \/ 内网模型/);
  assert.match(api, /API 连接/);
  assert.match(api, /aria-label="模型连接配置"/); assert.match(api, /aria-label="API 地址"/); assert.match(api, /aria-label="模型标识"/); assert.match(api, /aria-label="认证方式"/);
  assert.doesNotMatch(api, /账号登录/);
  const subscription = view('providers-subscription-explicit-user-entry');
  assert.match(subscription, /aria-selected="true"[^>]*>订阅账号/);
  assert.match(subscription, /aria-label="订阅账号"/); assert.match(subscription, /正在读取账号状态/); assert.doesNotMatch(subscription, /aria-label="本次 API 密钥"/);
  assert.match(subscription, /连接账号/); assert.match(subscription, /选择模型/); assert.match(subscription, /开始使用/);
  const base = { artifactId: 'providers', type: 'providers', revision: 1, scope: { projectId: '' }, data: [] };
  const hinted = { ...base, presentation: { entryId: 'first-click', initialTab: 'api' } };
  const samePanel = renderToString(() => Renderer({ artifact: hinted, context: { dispatch: () => assert.fail('SSR cannot dispatch') } }));
  assert.match(samePanel, /aria-selected="true"[^>]*>API \/ 内网模型/);
  assert.match(samePanel, /aria-label="模型连接配置"/);
  const { InteractionState } = await import('../src/interaction-state.ts');
  const panels = new InteractionState(() => {});
  const firstKey = panels.request(hinted, 'MODELESS_POPOVER', 'Connect model', true);
  panels.collapse(firstKey);
  const secondKey = panels.request({ ...base, presentation: { entryId: 'second-click', initialTab: 'subscription' } }, 'MODELESS_POPOVER', 'Connect model', true);
  assert.equal(secondKey, firstKey);
  assert.equal(panels.records.size, 1);
  assert.equal(panels.current()[0].artifact.presentation.initialTab, 'subscription');
});

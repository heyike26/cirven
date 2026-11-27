'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { createSubscriptionLoginOpener, loginUrl } = require('../subscription-login.cjs');
const id = 'a'.repeat(32);
const target = 'https://auth.openai.com/oauth/authorize?state=owned-pkce-state&code_challenge=owned-pkce-challenge';

test('Main resolves the exact pending native attempt and opens only the official URL', async () => {
  const calls = [], opened = [];
  const open = createSubscriptionLoginOpener({ request: async path => {
    calls.push(path); return { status: 'WAITING_USER', attempt_id: id, login_url: target };
  }, openExternal: async value => opened.push(value) });
  assert.deepEqual(await open(id), { status: 'BROWSER_OPENED', attempt_id: id, authentication_verified: false });
  assert.deepEqual(calls, ['/api/desktop-host/subscription/login-target?attempt_id=' + id]);
  assert.deepEqual(opened, [target]);
});

test('bad destinations, secret-bearing URLs and malformed attempts never reach shell', async () => {
  for (const value of ['https://auth.openai.com.evil.test/oauth/authorize', 'http://auth.openai.com/oauth/authorize',
    'file:///D:/login.html', 'javascript:alert(1)', 'https://evil@auth.openai.com/oauth/authorize',
    'https://auth.openai.com:8443/oauth/authorize', 'https://auth.openai.com/oauth/authorize#secret',
    'https://auth.openai.com/other', target + '&access_token=private', target + '&refresh_token=private',
    target + '&id_token=private', target + '\n']) assert.throws(() => loginUrl(value));
  let calls = 0;
  const open = createSubscriptionLoginOpener({ request: async () => { calls++; }, openExternal: async () => { calls++; } });
  for (const value of [target, '../attempt', '', 1, {}, 'A'.repeat(32)]) await assert.rejects(open(value));
  assert.equal(calls, 0);
});

test('a cancelled or replaced attempt cannot open an old browser destination', async () => {
  let opened = 0;
  for (const reply of [{ status: 'AUTHENTICATED', attempt_id: id, login_url: target },
    { status: 'WAITING_USER', attempt_id: 'b'.repeat(32), login_url: target }]) {
    const open = createSubscriptionLoginOpener({ request: async () => reply, openExternal: async () => { opened++; } });
    await assert.rejects(open(id), /no longer pending/);
  }
  assert.equal(opened, 0);
});

test('concurrent clicks share one read/open and failure permits explicit reopen without new OAuth', async () => {
  let release, reads = 0, opens = 0;
  const open = createSubscriptionLoginOpener({ request: async () => { reads++; await new Promise(resolve => { release = resolve; });
    return { status: 'WAITING_USER', attempt_id: id, login_url: target }; },
  openExternal: async () => { if (++opens === 1) throw new Error('own browser open failed'); } });
  const first = open(id), second = open(id); release();
  assert.deepEqual((await Promise.allSettled([first, second])).map(v => v.status), ['rejected', 'rejected']);
  assert.equal(reads, 1); assert.equal(opens, 1);
  const third = open(id); release(); await third;
  assert.equal(reads, 2); assert.equal(opens, 2);
});

test('shutdown between read and open fences the browser action', async () => {
  let closed = false, opened = 0;
  const open = createSubscriptionLoginOpener({ request: async () => { closed = true;
    return { status: 'WAITING_USER', attempt_id: id, login_url: target }; },
  isClosing: () => closed, openExternal: async () => { opened++; } });
  await assert.rejects(open(id), /stopped/); assert.equal(opened, 0);
});

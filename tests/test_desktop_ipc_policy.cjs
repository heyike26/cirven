'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { trustedSender, localOrigin, cssBounds, exactKeys } = require('../apps/desktop-host/ipc-policy.cjs');

test('only the owning top frame and exact local origin can invoke desktop actions', () => {
  const frame = { url: 'http://127.0.0.1:43219/' };
  const contents = { mainFrame: frame };
  const win = { webContents: contents, isDestroyed: () => false };
  assert.equal(trustedSender({ sender: contents, senderFrame: frame }, win, 'http://127.0.0.1:43219'), true);
  assert.equal(trustedSender({ sender: contents, senderFrame: { ...frame } }, win, 'http://127.0.0.1:43219'), false);
  assert.equal(trustedSender({ sender: {}, senderFrame: frame }, win, 'http://127.0.0.1:43219'), false);
  frame.url = 'http://127.0.0.1:43218/';
  assert.equal(trustedSender({ sender: contents, senderFrame: frame }, win, 'http://127.0.0.1:43219'), false);
  frame.url = 'file:///D:/another.html';
  assert.equal(trustedSender({ sender: contents, senderFrame: frame }, win, 'http://127.0.0.1:43219'), false);
});

test('controller origin refuses remote hosts, credentials and URL suffixes', () => {
  assert.equal(localOrigin('http://127.0.0.1:43219'), 'http://127.0.0.1:43219');
  for (const value of ['https://127.0.0.1:43219', 'http://localhost:43219', 'http://example.com:43219',
    'http://user@127.0.0.1:43219', 'http://127.0.0.1:43219/api', 'http://127.0.0.1:43219/?query=1']) {
    assert.throws(() => localOrigin(value));
  }
});

test('layout input accepts fractional CSS positions, refuses unbounded or extra authority', () => {
  assert.deepEqual(cssBounds({ x: 12.5, y: 34, width: 900, height: 600 }), { x: 12.5, y: 34, width: 900, height: 600 });
  for (const value of [{ x: 0, y: 0, width: Infinity, height: 1 }, { x: -1, y: 0, width: 1, height: 1 },
    { x: 0, y: 0, width: 900, height: 600, hwnd: 123 }, { x: 0, y: 0, width: '900', height: 1 }]) assert.throws(() => cssBounds(value));
  assert.throws(() => exactKeys({ projectId: 'p', viewRef: 'v', command: 'anything' }, ['projectId','viewRef']));
});

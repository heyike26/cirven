'use strict';

function loginUrl(value) {
  if (typeof value !== 'string' || value.length > 8192 || /[\u0000-\u0020\u007f]/.test(value)) {
    throw new Error('Official account login destination required');
  }
  const url = new URL(value);
  if (url.protocol !== 'https:' || url.hostname !== 'auth.openai.com' || url.port ||
      url.username || url.password || url.hash || url.pathname !== '/oauth/authorize') {
    throw new Error('Official account login destination required');
  }
  for (const name of ['access_token', 'refresh_token', 'id_token', 'id_token_hint']) {
    if (url.searchParams.has(name)) throw new Error('Credentials cannot be opened in a browser URL');
  }
  return url.href;
}

// The renderer supplies only the pending attempt identity. Main resolves the
// original PKCE URL from its authenticated local controller; no general shell
// or arbitrary openExternal primitive is exposed.
function createSubscriptionLoginOpener({ request, openExternal, isClosing = () => false }) {
  const active = new Map();
  return async function open(attemptId) {
    if (typeof attemptId !== 'string' || !/^[a-f0-9]{32}$/.test(attemptId)) {
      throw new Error('Exact pending account login attempt required');
    }
    if (isClosing()) throw new Error('Account login stopped with the owning app');
    if (active.has(attemptId)) return active.get(attemptId);
    const pending = (async () => {
      const value = await request('/api/desktop-host/subscription/login-target?attempt_id=' + attemptId);
      if (value?.status !== 'WAITING_USER' || value.attempt_id !== attemptId) {
        throw new Error('Account login is no longer pending');
      }
      const target = loginUrl(value.login_url);
      if (isClosing()) throw new Error('Account login stopped with the owning app');
      await openExternal(target);
      return { status: 'BROWSER_OPENED', attempt_id: attemptId, authentication_verified: false };
    })();
    active.set(attemptId, pending);
    try { return await pending; } finally { active.delete(attemptId); }
  };
}

module.exports = { createSubscriptionLoginOpener, loginUrl };

'use strict';

function trustedSender(event, window, origin) {
  if (!window || window.isDestroyed() || event.sender !== window.webContents ||
      !event.senderFrame || event.senderFrame !== window.webContents.mainFrame) return false;
  try { return new URL(event.senderFrame.url).origin === origin; } catch { return false; }
}

function localOrigin(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port ||
      url.username || url.password || url.pathname !== '/' || url.search || url.hash) {
    throw new Error('A dedicated localhost controller is required');
  }
  return url.origin;
}

function cssBounds(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join(',') !== 'height,width,x,y' ||
      Object.values(value).some(n => typeof n !== 'number' || !Number.isFinite(n)) ||
      value.x < 0 || value.y < 0 || value.width < 1 || value.height < 1 ||
      value.x + value.width > 32768 || value.y + value.height > 32768) {
    throw new Error('A finite workbench rectangle is required');
  }
  return { ...value };
}

function exactKeys(value, keys) {
  if (!value || typeof value !== 'object' || Array.isArray(value) ||
      Object.keys(value).sort().join(',') !== keys.sort().join(',')) {
    throw new Error('Unsupported desktop action fields');
  }
  return value;
}

module.exports = { trustedSender, localOrigin, cssBounds, exactKeys };

import fs from 'node:fs'
import path from 'node:path'
import crypto from 'node:crypto'
const stable = value => JSON.stringify(value, (_key, child) => child && typeof child === 'object' && !Array.isArray(child) ? Object.fromEntries(Object.keys(child).sort().map(k => [k, child[k]])) : child)
const sha = value => crypto.createHash('sha256').update(value).digest('hex')
const id = /^[A-Za-z0-9_-]{1,128}$/
function ordinary(filename, limit) {
  const p = path.resolve(filename)
  for (let cursor = p;; cursor = path.dirname(cursor)) {
    const info = fs.lstatSync(cursor)
    if (info.isSymbolicLink()) throw Error('REVIEW_LINKED_SCOPE')
    if (cursor === path.dirname(cursor)) break
  }
  const before = fs.statSync(p)
  if (!before.isFile() || before.nlink !== 1 || before.size > limit) throw Error('REVIEW_SCOPE_NOT_ORDINARY')
  const bytes = fs.readFileSync(p)
  const after = fs.statSync(p)
  if (before.dev !== after.dev || before.ino !== after.ino || before.size !== after.size || before.mtimeMs !== after.mtimeMs) throw Error('REVIEW_SCOPE_CHANGED')
  return bytes
}
export const NativeReviewPlugin = async input => {
  const filename = process.env.EEAS_NATIVE_REVIEW_CONFIG
  if (!filename || !path.isAbsolute(filename)) throw Error('REVIEW_SCOPE_REQUIRED')
  const scope = () => {
    const value = JSON.parse(ordinary(filename, 65536))
    const { scope_sha256, ...body } = value
    if (sha(stable(body)) !== scope_sha256 || value.schema !== 'eeas.native-review-scope.v1' || value.plugin_id !== 'eeas.native-review-context') throw Error('REVIEW_SCOPE_INTEGRITY')
    const actual = path.resolve(input.directory), expected = path.resolve(value.directory)
    if ((process.platform === 'win32' ? actual.toLowerCase() !== expected.toLowerCase() : actual !== expected)) throw Error('REVIEW_DIRECTORY_MISMATCH')
    const u = new URL(value.broker_url)
    if (u.protocol !== 'http:' || u.hostname !== '127.0.0.1' || u.pathname !== '/' || !u.port) throw Error('REVIEW_BROKER_DENIED')
    return value
  }
  const initial = scope()
  const readyPath = path.join(path.dirname(filename), 'ready.json')
  const receipt = stable({ schema: 'eeas.native-review-ready.v1', plugin_id: initial.plugin_id, plugin_manifest_sha256: initial.plugin_manifest_sha256, initial_scope_sha256: initial.scope_sha256 })
  try { fs.writeFileSync(readyPath, receipt, { flag: 'wx', mode: 0o600 }) }
  catch (error) { if (error.code !== 'EEXIST' || ordinary(readyPath, 4096).toString() !== receipt) throw error }
  async function invoke(kind, args, ctx) {
    const value = scope()
    if (!value.native_session_id || ctx.sessionID !== value.native_session_id || !id.test(ctx.messageID ?? '') || !id.test(ctx.callID ?? '')) throw Error('REVIEW_ORIGINAL_CONTEXT_REQUIRED')
    const response = await fetch(value.broker_url + '/v1/page', {
      method: 'POST', headers: { 'Authorization': 'Bearer ' + value.broker_key, 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, args, ctx: { sessionID: ctx.sessionID, messageID: ctx.messageID, callID: ctx.callID }, owner_ref: value.owner_ref, generation: value.generation }),
      signal: AbortSignal.timeout(15000), redirect: 'error'
    })
    const text = await response.text()
    if (text.length > 3 * 1024 * 1024 || !response.ok) throw Error('REVIEW_CALL_REJECTED')
    const result = JSON.parse(text)
    return { title: kind === 'visual' ? 'Frozen page image' : 'Frozen page context', output: result.output, metadata: result.metadata, ...(result.attachments?.length ? { attachments: result.attachments } : {}) }
  }
  return { tool: {
    eeas_review_context: { description: 'Read only this request’s frozen page text and source references; no current-page recapture.', args: {}, execute: (args, ctx) => invoke('text', args, ctx) },
    eeas_review_visual: { description: 'Read an optional structured image of this same frozen page. No new desktop input or capture.', args: {}, execute: (args, ctx) => invoke('visual', args, ctx) }
  } }
}
export default NativeReviewPlugin

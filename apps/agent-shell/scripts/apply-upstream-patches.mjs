import { readFileSync, writeFileSync, renameSync, unlinkSync, statSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const app = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const commit = '733b2c627e5aec39cb33cc22373bdce33053ef2c';
const patchSha = '9288191ef74b71771dc9299d822c0a3751ca743a88ab92987650e6a4f89d45e4';
const targets = ['dist/model/FileTreeController.js', 'dist/model/internalTypes.d.ts', 'dist/model/publicTypes.d.ts', 'dist/render/FileTree.js'];
const hash = bytes => createHash('sha256').update(bytes).digest('hex');

// Only the four-file patch declared by the pinned OpenCode source is accepted.
// Exact original and patched hashes permit repeat runs and interrupted recovery.
function patchedBytes(before, section, path) {
  const original = before.toString('utf8').split(/(?<=\n)/);
  const lines = section.split(/(?<=\n)/);
  if (lines[0].trim() !== `a/${path} b/${path}` || lines[2].trim() !== `--- a/${path}` || lines[3].trim() !== `+++ b/${path}`) throw new Error(`Patch target mismatch: ${path}`);
  const output = [];
  let cursor = 0;
  let index = 4;
  while (index < lines.length) {
    const hunk = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(lines[index++]);
    if (!hunk) throw new Error(`Invalid patch hunk: ${path}`);
    const start = Number(hunk[1]) - 1;
    if (start < cursor || start > original.length) throw new Error(`Patch hunk range mismatch: ${path}`);
    output.push(...original.slice(cursor, start));
    cursor = start;
    let removed = 0;
    let added = 0;
    while (index < lines.length && !lines[index].startsWith('@@ ')) {
      const line = lines[index++];
      const kind = line[0];
      if (kind === ' ' || kind === '-') {
        if (original[cursor] !== line.slice(1)) throw new Error(`Patch context mismatch: ${path}:${cursor + 1}`);
        if (kind === ' ') output.push(original[cursor]);
        cursor++;
        removed++;
      }
      if (kind === '+' || kind === ' ') {
        if (kind === '+') output.push(line.slice(1));
        added++;
      }
      if (![' ', '-', '+'].includes(kind)) throw new Error(`Unsupported patch line: ${path}`);
    }
    if (removed !== Number(hunk[2] ?? 1) || added !== Number(hunk[4] ?? 1)) throw new Error(`Patch hunk length mismatch: ${path}`);
  }
  output.push(...original.slice(cursor));
  return Buffer.from(output.join(''), 'utf8');
}

export function applyUpstreamPatches(packageDirectory = resolve(app, 'node_modules/@pierre/trees')) {
  const manifest = JSON.parse(readFileSync(resolve(app, 'patches/pierre-trees-manifest.json'), 'utf8'));
  const pkg = JSON.parse(readFileSync(resolve(packageDirectory, 'package.json'), 'utf8'));
  if (pkg.name !== '@pierre/trees' || pkg.version !== '1.0.0-beta.4') throw new Error('Pinned @pierre/trees package/version mismatch');
  if (manifest.schema_version !== 1 || manifest.package !== pkg.name || manifest.version !== pkg.version || manifest.source_commit !== commit || manifest.patch_sha256 !== patchSha || manifest.patch_file !== 'pierre-trees-1.0.0-beta.4.patch' || JSON.stringify(manifest.files.map(file => file.path)) !== JSON.stringify(targets)) throw new Error('Pinned upstream patch provenance mismatch');
  const patch = readFileSync(resolve(app, 'patches', manifest.patch_file));
  if (hash(patch) !== patchSha) throw new Error('Pinned upstream patch SHA mismatch');
  const sections = patch.toString('utf8').split('diff --git ');
  if (sections.shift() !== '' || sections.length !== targets.length) throw new Error('Pinned upstream patch structure mismatch');
  // Preflight every file before changing any installed byte.
  const plan = manifest.files.map((file, index) => {
    const path = resolve(packageDirectory, file.path);
    const before = readFileSync(path);
    const sha = hash(before);
    if (sha === file.after_sha256 && before.length === file.after_bytes) return { path, status: 'ALREADY_APPLIED', sha256: sha };
    if (sha !== file.before_sha256 || before.length !== file.before_bytes) throw new Error(`Installed dependency byte mismatch: ${file.path}`);
    const after = patchedBytes(before, sections[index], file.path);
    if (hash(after) !== file.after_sha256 || after.length !== file.after_bytes) throw new Error(`Patched dependency byte mismatch: ${file.path}`);
    return { path, after, status: 'APPLIED', sha256: file.after_sha256 };
  });
  for (const item of plan) {
    if (!item.after) continue;
    const temporary = `${item.path}.eeas-patch-${process.pid}`;
    try {
      writeFileSync(temporary, item.after, { flag: 'wx', mode: statSync(item.path).mode });
      renameSync(temporary, item.path);
    } finally {
      try { unlinkSync(temporary); } catch (error) { if (error.code !== 'ENOENT') throw error; }
    }
  }
  for (const [index, item] of plan.entries()) {
    const bytes = readFileSync(item.path);
    if (hash(bytes) !== manifest.files[index].after_sha256 || bytes.length !== manifest.files[index].after_bytes) throw new Error(`Installed patch verification failed: ${targets[index]}`);
  }
  return plan.map(({ status, sha256 }, index) => ({ path: targets[index], status, sha256 }));
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const receipt = applyUpstreamPatches();
  process.stdout.write(`Pinned OpenCode @pierre/trees expansion patch: ${receipt.filter(file => file.status === 'APPLIED').length} applied, ${receipt.filter(file => file.status === 'ALREADY_APPLIED').length} verified.\n`);
}

import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { applyUpstreamPatches } from '../scripts/apply-upstream-patches.mjs';

const app = resolve(dirname(fileURLToPath(import.meta.url)), '..');
applyUpstreamPatches();
const { FileTree } = await import('@pierre/trees');

test('real pinned FileTree expansion lazily reads nested Unicode directories and actual file names', async () => {
  const root = mkdtempSync(resolve(tmpdir(), 'eeas-directory-expansion-'));
  mkdirSync(resolve(root, '一级/二级/三级'), { recursive: true });
  writeFileSync(resolve(root, '一级/二级/三级/原始😀.txt'), '真实目录内容\r\n');
  const reads = [], pending = [];
  let tree;
  const load = path => {
    const key = path.replace(/\/+$/, '');
    reads.push(key);
    tree.batch(readdirSync(resolve(root, key), { withFileTypes: true }).map(entry => ({
      type: 'add', path: `${key}/${entry.name}${entry.isDirectory() ? '/' : ''}`,
    })));
  };
  tree = new FileTree({
    paths: ['一级/'], initialExpansion: 'closed', flattenEmptyDirectories: false,
    onExpansionChange(change) { if (change.expanded) pending.push(Promise.resolve().then(() => load(change.path))); },
  });
  try {
    assert.equal(tree.getItem('一级/二级/'), null);
    for (const path of ['一级/', '一级/二级/', '一级/二级/三级/']) {
      const folder = tree.getItem(path);
      assert.ok(folder?.isDirectory());
      assert.equal(folder.isExpanded(), false);
      folder.expand();
      await Promise.all(pending.splice(0));
      assert.equal(folder.isExpanded(), true);
    }
    assert.deepEqual(reads, ['一级', '一级/二级', '一级/二级/三级']);
    const file = tree.getItem('一级/二级/三级/原始😀.txt');
    assert.ok(file);
    assert.equal(file.isDirectory(), false);
    assert.equal(readFileSync(resolve(root, file.getPath()), 'utf8'), '真实目录内容\r\n');
    tree.getItem('一级/').collapse();
    assert.equal(reads.length, 3, 'collapse must not request another directory read');
  } finally { tree.cleanUp(); rmSync(root, { recursive: true, force: true }); }
});

test('real expansion callbacks coexist with selection, ignore unrelated changes and unsubscribe on cleanup', () => {
  const events = [], selections = [];
  const tree = new FileTree({
    paths: ['folder/file.txt'], initialExpansion: 'closed',
    onExpansionChange: change => events.push(change),
    onSelectionChange: paths => selections.push([...paths]),
  });
  const folder = tree.getItem('folder/');
  assert.ok(folder?.isDirectory());
  folder.expand();
  tree.getItem('folder/file.txt').select();
  tree.add('folder/new.txt');
  folder.collapse();
  folder.toggle();
  folder.toggle();
  assert.deepEqual(events, [
    { path: 'folder/', expanded: true }, { path: 'folder/', expanded: false },
    { path: 'folder/', expanded: true }, { path: 'folder/', expanded: false },
  ]);
  assert.deepEqual(selections, [['folder/file.txt']]);
  tree.cleanUp();
  folder.expand();
  assert.equal(events.length, 4);
});

test('offline patch provenance is idempotent and refuses a changed dependency before any write', () => {
  const packageDirectory = mkdtempSync(resolve(tmpdir(), 'eeas-tree-patch-'));
  const manifest = JSON.parse(readFileSync(resolve(app, 'patches/pierre-trees-manifest.json'), 'utf8'));
  try {
    writeFileSync(resolve(packageDirectory, 'package.json'), JSON.stringify({ name: manifest.package, version: manifest.version }));
    for (const file of manifest.files) {
      const target = resolve(packageDirectory, file.path);
      mkdirSync(dirname(target), { recursive: true });
      writeFileSync(target, readFileSync(resolve(app, 'node_modules/@pierre/trees', file.path)));
    }
    const receipts = applyUpstreamPatches(packageDirectory);
    assert.equal(receipts.length, 4);
    assert.ok(receipts.every(receipt => receipt.status === 'ALREADY_APPLIED'));
    const firstPath = resolve(packageDirectory, manifest.files[0].path);
    const firstBefore = readFileSync(firstPath);
    writeFileSync(resolve(packageDirectory, manifest.files.at(-1).path), 'unexpected manual edit');
    assert.throws(() => applyUpstreamPatches(packageDirectory), /Installed dependency byte mismatch/);
    assert.deepEqual(readFileSync(firstPath), firstBefore);
    writeFileSync(resolve(packageDirectory, 'package.json'), JSON.stringify({ name: manifest.package, version: '1.0.0-beta.5' }));
    assert.throws(() => applyUpstreamPatches(packageDirectory), /package\/version mismatch/);
  } finally { rmSync(packageDirectory, { recursive: true, force: true }); }
});

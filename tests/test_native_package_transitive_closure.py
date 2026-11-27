"""Real pinned plugin files, structural launcher; no Windows/runtime claim."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts.native_package_bootstrap import PackageError, verify_package
from test_native_package import bundle

PREFIX = 'toolchain/opencode-plugins/'
VENDOR = PREFIX + 'dynamic-workflow/vendor/zod/src/v3/tests/Mocker.ts'


@pytest.fixture
def plugin_package(bundle):
    root, manifest, _ = bundle
    source = Path(__file__).resolve().parents[1]
    # A minimal two-manifest graph preserves the actual failure topology.
    # Full pinned production closure is independently exercised by package
    # selection tests and the bundled Windows consumer before qualification.
    values = {}
    for plugin, members in [('dynamic-workflow', ['vendor/zod/index.js',
            'vendor/zod/src/v3/tests/Mocker.ts']), ('native-message-context', ['index.mjs'])]:
        base = PREFIX + plugin + '/'
        rows = []
        for name in members:
            path = base + name
            target = root/path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((source/path).read_bytes())
            manifest['files'].append({'path':path, 'bytes':target.stat().st_size,
                'sha256':hashlib.sha256(target.read_bytes()).hexdigest(), 'startup_critical':True})
            rows.append({'path':name, 'bytes':target.stat().st_size,
                'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
        value = {'id':'eeas.' + plugin, 'files':rows}
        if plugin == 'native-message-context':
            value['dependencies'] = [{'path':PREFIX + 'dynamic-workflow/manifest.json',
                'sha256':values['dynamic-workflow'], 'prefix':'vendor/zod/',
                'entry_sha256':next(row['sha256'] for row in manifest['files']
                    if row['path'] == PREFIX + 'dynamic-workflow/vendor/zod/index.js')}]
        raw = json.dumps(value).encode()
        path = base + 'manifest.json'
        (root/path).write_bytes(raw)
        values[plugin] = hashlib.sha256(raw).hexdigest()
        manifest['files'].append({'path':path, 'bytes':len(raw),
            'sha256':values[plugin], 'startup_critical':True})
    persist(root, manifest)
    return root, manifest


def persist(root, manifest):
    (root/'PACKAGE_MANIFEST.json').write_text(json.dumps(manifest), encoding='utf-8')


@pytest.mark.parametrize('full,workers', [(True, 4), (False, 1)])
def test_actual_complete_reviewed_closure_is_admitted(plugin_package, full, workers):
    root, manifest = plugin_package
    assert verify_package(root, full=full, workers=workers) == manifest


@pytest.mark.parametrize('fault', ['omitted-missing', 'omitted-present', 'rebound-pin',
    'missing-plugin-manifest', 'dependency-manifest-rebound', 'quick-unhashed-tamper'])
def test_declared_hashes_cannot_hide_incomplete_or_rebound_consumer_closure(plugin_package, fault):
    root, manifest = plugin_package
    rows = {row['path']:row for row in manifest['files']}
    if fault.startswith('omitted-'):
        manifest['files'].remove(rows[VENDOR])
        if fault == 'omitted-missing':
            (root/VENDOR).unlink()
    elif fault == 'rebound-pin':
        (root/VENDOR).write_bytes(b'rebound vendor')
        rows[VENDOR].update(bytes=14, sha256=hashlib.sha256(b'rebound vendor').hexdigest())
    elif fault == 'missing-plugin-manifest':
        name = PREFIX + 'dynamic-workflow/manifest.json'
        manifest['files'].remove(rows[name])
    elif fault == 'dependency-manifest-rebound':
        name = PREFIX + 'dynamic-workflow/manifest.json'
        value = json.loads((root/name).read_bytes())
        value['files'] = [row for row in value['files'] if row['path'] != VENDOR.split('dynamic-workflow/')[1]]
        raw = json.dumps(value).encode()
        (root/name).write_bytes(raw)
        rows[name].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    else:
        rows[VENDOR]['startup_critical'] = False
        raw = (root/VENDOR).read_bytes()
        (root/VENDOR).write_bytes(b'X' + raw[1:])
    persist(root, manifest)
    with pytest.raises(PackageError):
        verify_package(root, full=fault != 'quick-unhashed-tamper')

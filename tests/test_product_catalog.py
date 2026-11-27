"""Original scope-loss and false-qualification cases; no third-party packages."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('product_catalog', ROOT / 'scripts/product_catalog.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='EEAS 范围 ')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / 'docs').mkdir()
        (self.root / 'contract.md').write_text('original contract', encoding='utf-8')
        self.data = {'schema_version': 'eeas.product-requirements.v1',
                     'kind': 'DEVELOPMENT_SCOPE_NOT_RUNTIME_QUALIFICATION',
                     'user_max_children': 8, 'tool_total_concurrency': 4, 'capabilities': [
            {'id': area + '.fixture', 'area': area, 'title': 'Original ' + area,
             'status': 'PLANNED', 'contract': 'contract.md', 'depends_on': [],
             'evidence': [], 'required_verification': ['API_OR_NATIVE_CLIENT']}
            for area in sorted(module.AREAS)]}

    def save(self, data=None):
        (self.root / 'docs/PRODUCT_CAPABILITIES.json').write_text(
            json.dumps(self.data if data is None else data), encoding='utf-8')

    def rejected(self, mutation, message):
        data = deepcopy(self.data)
        mutation(data)
        self.save(data)
        with self.assertRaisesRegex(module.CatalogError, message):
            module.read_catalog(self.root)

    def test_seven_areas_and_actual_tool_limit(self):
        self.save()
        result = module.describe(module.read_catalog(self.root))
        self.assertEqual(result['effective_children'], 3)
        self.assertEqual(result['user_max_children'], 8)
        self.assertFalse(result['runtime_qualified'])
        self.assertEqual(module.describe(self.data, 'authoring')['count'], 1)

    def test_missing_user_workflow_area_is_rejected(self):
        self.rejected(lambda d: d['capabilities'].pop(), 'Missing product area')

    def test_duplicate_id_cannot_hide_a_requirement(self):
        self.rejected(lambda d: d['capabilities'].append(deepcopy(d['capabilities'][0])), 'duplicate')

    def test_unknown_dependency_is_rejected(self):
        self.rejected(lambda d: d['capabilities'][0].update(depends_on=['runtime.missing']), 'Unknown')

    def test_cycle_cannot_be_scheduled(self):
        def mutate(d):
            first, second = d['capabilities'][:2]
            first['depends_on'] = [second['id']]
            second['depends_on'] = [first['id']]
        self.rejected(mutate, 'Cycle')

    def test_manifest_cannot_promote_declared_scope_to_pass(self):
        self.rejected(lambda d: d['capabilities'][0].update(status='PASS'), 'qualification')

    def test_partial_needs_real_local_reference_and_scope(self):
        self.rejected(lambda d: d['capabilities'][0].update(status='PARTIAL'), 'evidence and scope')
        self.data['capabilities'][0].update(status='PARTIAL', evidence=['contract.md'], scope_note='Only original fixture')
        self.save()
        self.assertEqual(module.read_catalog(self.root)['capabilities'][0]['status'], 'PARTIAL')

    def test_path_escape_is_rejected(self):
        self.rejected(lambda d: d['capabilities'][0].update(contract='../secret.md'), 'stay inside')

    def test_symlink_to_external_artifact_is_rejected(self):
        outside = self.root.parent / (self.root.name + '-outside.md')
        outside.write_text('original outside', encoding='utf-8')
        self.addCleanup(outside.unlink)
        (self.root / 'link.md').symlink_to(outside)
        self.rejected(lambda d: d['capabilities'][0].update(contract='link.md'), 'escaped')

    def test_duplicate_json_key_and_nonfinite_are_rejected(self):
        path = self.root / 'docs/PRODUCT_CAPABILITIES.json'
        for raw, reason in [('{"kind":1,"kind":2}', 'Duplicate'), ('{"kind":NaN}', 'Nonfinite')]:
            with self.subTest(raw=raw):
                path.write_text(raw, encoding='utf-8')
                with self.assertRaisesRegex(module.CatalogError, reason):
                    module.read_catalog(self.root)

    def test_actual_cli_reports_scope_without_promoting_runtime(self):
        self.save()
        run = subprocess.run([sys.executable, '-I', '-S', str(ROOT / 'scripts/product_catalog.py'),
                              '--root', str(self.root), '--summary'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertEqual(report['count'], 7)
        self.assertEqual(report['effective_children'], 3)
        self.assertFalse(report['runtime_qualified'])
        (self.root / 'contract.md').unlink()
        failed = subprocess.run(run.args, capture_output=True, text=True)
        self.assertEqual(failed.returncode, 1)
        self.assertIn('Missing', failed.stderr)


if __name__ == '__main__':
    unittest.main()

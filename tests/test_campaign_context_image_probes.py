"""Execute long-text plus frozen derived-image SDK wire without claiming vision accuracy."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from scripts import public_campaign as campaign
from test_campaign_probes import bound_source

ROOT = Path(__file__).resolve().parents[1]


def test_long_source_and_frozen_image_selection_reach_sdk_with_exact_hashes(bound_source):
    text = ''.join(f'Original operation {n:04d} preserves its exact timestamp and audit trace.\r\n' for n in range(1400))
    text += 'Original final source boundary for selected text and image.\r\n'
    raw = text.encode()
    assert len(text) >= 70000
    bound_source['prepare']('source-context', raw)
    command = [sys.executable, '-m', 'pytest', 'tests/campaign_context_image_probes.py::test_source_context_images',
               '-q', '-ra', '-p', 'scripts.public_campaign']
    launcher = [sys.executable, 'scripts/record_slice.py', '--acceptance',
        'Actual >=70k original synthetic task-book source and explicit original-body derived PDF bitmap enter real source/context/task API and installed SDK synthetic JSON image wire. Exact source/context/PDF/font/image hashes, coverage/omission and default vision/tamper/duplicate selection guards; FormalRun remains awaiting approval and independent verification NOT_RUN. No corporate multimodal/OCR/model claim.']
    for path in [bound_source['binding_path'], *[bound_source['project'] / name for name in ('source.raw', 'source.json', 'license.raw')]]:
        launcher += ['--reference', path.relative_to(ROOT).as_posix()]
    launcher += ['E11-context-image-probe-' + uuid.uuid4().hex, '--', *command]
    actual = subprocess.run(launcher, cwd=ROOT, env={**os.environ, 'EEAS_CAMPAIGN_BINDING': str(bound_source['binding_path'])},
                            capture_output=True, check=False)
    paths = [line.removeprefix('Evidence: ') for line in actual.stdout.decode().splitlines() if line.startswith('Evidence: ')]
    assert len(paths) == 1, actual.stdout.decode() + actual.stderr.decode()
    report = ROOT / paths[0]
    frozen = json.loads((report / 'freeze.json').read_bytes())['code_sha256']
    assert all(campaign.digest((ROOT / p).read_bytes()) == digest for p, digest in frozen.items())
    (report / 'source-after.json').write_bytes(campaign.encode({'all_unchanged': True, 'frozen_files': len(frozen)}))
    if os.environ.get('EEAS_EVIDENCE_DIR'):
        (Path(os.environ['EEAS_EVIDENCE_DIR']) / 'actual-context-image-child-report.json').write_bytes(campaign.encode({
            'actual_report': paths[0], 'exit_code': actual.returncode, 'command': command}))
    assert actual.returncode == 0, actual.stdout.decode() + actual.stderr.decode()
    checked = campaign.checked_evidence(ROOT, report, bound_source['binding_path'], bound_source['source'], command,
                                         actual.returncode, observation_required=True)
    assert checked['test_counts'] == {'executed': 1, 'failed': 0, 'skipped': 0}
    assert (report / 'actual-original.bin').read_bytes() == raw
    data = json.loads((report / 'actual-long-image-context.json').read_bytes())
    assert data['parent_raw_sha256'] == campaign.digest(raw)
    assert data['full_context']['result']['coverage'] == 'COMPLETE'
    assert data['full_context']['result']['truncated'] is False
    assert data['request']['image_refs'] == [data['asset_ref']]
    assert data['model_vision'] == 'NOT_VALIDATED' and data['ocr'] == 'NOT_EXECUTED'
    assert data['company_model_calls'] == 0

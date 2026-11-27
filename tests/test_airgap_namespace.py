import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


@pytest.mark.skipif(sys.platform != 'linux' or not shutil.which('unshare') or not shutil.which('ip'),
                    reason='Linux namespace prerequisite absent; native Windows gate is separate')
def test_actual_namespace_blocks_external_route_but_preserves_local_api_network(tmp_path):
    eligibility = subprocess.run(['unshare', '-Urn', 'true'], capture_output=True)
    if eligibility.returncode:
        pytest.skip('OS denied user network namespaces; no airgap qualification claimed')
    root = Path(__file__).resolve().parents[1]
    original_namespace = os.readlink('/proc/self/ns/net')
    result = subprocess.run([sys.executable, str(root / 'scripts/probe_airgap_copy.py'),
                             '--probe-only', '--evidence', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    proof = json.loads((tmp_path / 'airgap-network.json').read_text())
    assert proof['parent_namespace'] == original_namespace
    assert proof['actual_namespace'] != original_namespace
    assert {row['ifname'] for row in proof['links']} == {'lo'}
    assert proof['loopback_connection'] == 'PASS'
    assert proof['package_acceptance'] == 'NOT_RUN'
    assert proof['native_windows'] == 'BLOCKED'
    assert proof['code_tool_os_isolation'] == 'NOT_VALIDATED'
    assert os.readlink('/proc/self/ns/net') == original_namespace

"""Reject unlisted and Windows-aliased jobs using the actual pinned source tree."""
from pathlib import Path
import pytest

from scripts import package_native_companion as builder
from scripts.native_package_bootstrap import PackageError

ROOT = Path(__file__).resolve().parents[1]


def test_exact_reviewed_reader_and_shared_vendor_remain_selectable():
    jobs = {}
    builder.add_native_message_plugin_closure(ROOT, {}, lambda path, name: jobs.update({name: path}))
    pins = builder.native_message_job_pins(jobs)
    assert set(pins) == set(jobs) and len(pins) == 661
    assert all(pin['bytes'] == jobs[name].stat().st_size for name, pin in pins.items())


@pytest.mark.parametrize('fault', ['unlisted-vendor', 'aliased-reader', 'aliased-vendor'])
def test_unreviewed_or_aliased_selected_namespace_is_rejected(tmp_path, fault):
    jobs = {}
    builder.add_native_message_plugin_closure(ROOT, {}, lambda path, name: jobs.update({name: path}))
    private = tmp_path / 'unlisted.mjs'
    private.write_bytes(b'unreviewed selection')
    if fault == 'unlisted-vendor':
        jobs['toolchain/opencode-plugins/dynamic-workflow/vendor/zod/unlisted.mjs'] = private
    elif fault == 'aliased-reader':
        jobs['toolchain/opencode-plugins/NATIVE-MESSAGE-CONTEXT/index.mjs'] = private
    else:
        jobs['toolchain/opencode-plugins/dynamic-workflow/vendor/ZOD/index.js'] = private
    with pytest.raises(PackageError):
        builder.native_message_job_pins(jobs)

"""Actual structural package copy/refresh binding, not Windows execution."""
import hashlib
import json
from pathlib import Path

from scripts import native_state_protocols as protocols
from scripts import package_native_companion as builder
from scripts.native_package_bootstrap import verify_package

from test_native_package_ide_capability import scene


def test_fresh_and_refreshed_program_bind_actual_state_readers_and_preserve_user_bytes(scene, tmp_path):
    source, runtime, plan = scene
    repository = Path(__file__).resolve().parents[1]
    for name in sorted({name for group in protocols.READERS.values() for name in group}):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((repository / name).read_bytes())
        plan['jobs'][name] = path
    for name in ('scripts/native_state_protocols.py', 'scripts/native_user_state.py'):
        plan['jobs'][name] = source / name
    output = tmp_path / 'program'
    initial = builder.copy_plan(plan, output)
    assert set(initial['durable_state_protocols']['protocols']) == set(protocols.READERS)
    assert verify_package(output) == initial
    readers = initial['durable_state_protocols']['protocols']
    for protocol, files in protocols.READERS.items():
        assert set(readers[protocol]['readers']) == set(files)
        for name in files:
            assert readers[protocol]['readers'][name] == hashlib.sha256((output / name).read_bytes()).hexdigest()
    own_data = output / 'user-data/original-private.txt'
    own_data.parent.mkdir()
    own_data.write_bytes('原有私稿与未知操作不改写。'.encode())
    reader = protocols.READERS[protocols.PAUSE][0]
    old_pin = readers[protocols.PAUSE]['readers'][reader]
    (source / reader).write_bytes((source / reader).read_bytes() + b'\n# structural source refresh fixture\n')
    refreshed = builder.refresh_source(builder.make_refresh_plan(source, runtime), output)
    assert verify_package(output) == refreshed
    assert refreshed['durable_state_protocols']['protocols'][protocols.PAUSE]['readers'][reader] != old_pin
    assert refreshed['durable_state_protocols']['protocols'][protocols.PAUSE]['readers'][reader] == hashlib.sha256((output / reader).read_bytes()).hexdigest()
    assert own_data.read_bytes() == '原有私稿与未知操作不改写。'.encode()
    history = output / refreshed['source_refresh']['history_relative_path']
    assert (history / 'payload.before' / reader).read_bytes() == (repository / reader).read_bytes()
    assert json.loads((history / 'PACKAGE_MANIFEST.before.json').read_text()) == initial

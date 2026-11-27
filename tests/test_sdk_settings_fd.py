"""Real filesystem replacements at the settings open boundary; no private caches."""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from packages.adapters.settings_profile import SettingsRouteError, resolve_route
from packages.adapters import settings_profile
from packages.domain.store import Conflict
from test_claude_sessions import NativeFactory, context
from test_sdk_settings import TOKEN, check_routed_credential, selected_environment, settings, started_meters


def replace_at_open(monkeypatch, selected, mutate):
    """Intercept the actual open syscall boundary, then mutate actual files once."""
    original_path_open, original_os_open = Path.open, os.open
    observed = []

    def before_open(path):
        if not observed and Path(path) == selected:
            observed.append(True)
            mutate()

    def path_open(path, *args, **kwargs):
        before_open(path)
        return original_path_open(path, *args, **kwargs)

    def os_open(path, *args, **kwargs):
        before_open(path)
        return original_os_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", path_open)
    monkeypatch.setattr(os, "open", os_open)
    if os.name == "nt":
        native_open = settings_profile._windows_open_nofollow
        def windows_open(path):
            before_open(path)
            return native_open(path)
        monkeypatch.setattr(settings_profile, "_windows_open_nofollow", windows_open)
    return observed


@pytest.mark.parametrize("kind", ["symlink", "regular_replacement", "parent_symlink", "same_inode_rewrite"])
def test_actual_preopen_replacement_is_rejected_without_secret_or_path(tmp_path, monkeypatch, kind):
    selected = settings(tmp_path)
    hidden = tmp_path / "auth.json"
    forbidden = "original-fixture-secret-in-prohibited-auth-file"
    replacement_body = json.dumps({"env": {"ANTHROPIC_AUTH_TOKEN": forbidden}})
    hidden.write_text(replacement_body, encoding="utf-8")
    replacement = tmp_path / "replacement.json"
    replacement.write_text(replacement_body, encoding="utf-8")
    if kind == "parent_symlink":
        selected_dir = tmp_path / "selected-dir"
        selected_dir.mkdir()
        selected.rename(selected_dir / selected.name)
        selected = selected_dir / selected.name
        other = tmp_path / "other-dir"
        other.mkdir()
        (other / selected.name).write_text(replacement_body, encoding="utf-8")

    def mutate():
        if kind == "symlink":
            selected.unlink()
            selected.symlink_to(hidden)
        elif kind == "regular_replacement":
            os.replace(replacement, selected)
        elif kind == "parent_symlink":
            selected.parent.rename(tmp_path / "parked-dir")
            selected.parent.symlink_to(other, target_is_directory=True)
        else:
            selected.write_text(replacement_body, encoding="utf-8")

    observed = replace_at_open(monkeypatch, selected, mutate)
    with pytest.raises(SettingsRouteError) as caught:
        resolve_route({"EEAS_CLAUDE_SETTINGS_PATH": str(selected)})
    assert observed == [True]
    assert forbidden not in str(caught.value) and str(selected) not in str(caught.value)


def test_atomic_settings_rotation_is_allowed_only_for_fresh_request(client, tmp_path, monkeypatch):
    meters = started_meters(monkeypatch)
    path = settings(tmp_path)
    selected_environment(monkeypatch, path)
    pid, bridge, request = context(client, tmp_path)
    factory = NativeFactory([0.002, 0.002])
    with patch("packages.adapters.claude_sdk.ClaudeSDKClient", side_effect=factory):
        parent = asyncio.run(bridge.submit(pid, "settings-before-atomic-rotation", request))
        old_events = bridge.stream_events(pid, parent["id"])
        before_hash = bridge._route().public()["settings_sha256"]
        rotated = tmp_path / "new-approved-settings.json"
        body = json.loads(path.read_bytes())
        body["env"]["ANTHROPIC_AUTH_TOKEN"] = "original-new-approved-secret"
        rotated.write_text(json.dumps(body), encoding="utf-8")
        os.replace(rotated, path)
        after_hash = bridge._route().public()["settings_sha256"]
        assert before_hash != after_hash
        with pytest.raises(Conflict, match="scope changed"):
            asyncio.run(bridge.resume(pid, parent["id"], "no-old-scope-after-rotation", "followup", expected_session_id=parent["session_id"]))
        fresh = asyncio.run(bridge.submit(pid, "settings-after-atomic-rotation", request))
    assert fresh["execution_status"] == "RETURNED" and len(factory.transports) == 2
    assert len(meters) == 2 and meters[0].key != meters[1].key
    check_routed_credential(factory.transports[0].options, meters[0], TOKEN)
    check_routed_credential(factory.transports[1].options, meters[1], "original-new-approved-secret")
    assert bridge.inspect(pid, parent["id"]) == parent and bridge.stream_events(pid, parent["id"]) == old_events


def test_special_file_is_rejected_before_open(tmp_path, monkeypatch):
    if not hasattr(os, "mkfifo"):
        pytest.skip("POSIX FIFO unavailable; Windows qualification remains external")
    path = tmp_path / "approved.json"
    os.mkfifo(path)
    observed = replace_at_open(monkeypatch, path, lambda: None)
    with pytest.raises(SettingsRouteError): resolve_route({"EEAS_CLAUDE_SETTINGS_PATH": str(path)})
    assert observed == []


@pytest.mark.parametrize("kind", ["replacement", "same_inode_rewrite"])
def test_changes_after_actual_fd_read_are_rejected_and_descriptor_closed(tmp_path, monkeypatch, kind):
    selected = settings(tmp_path)
    changed = tmp_path / "new-approved.json"
    changed.write_text('{"env":{"ANTHROPIC_AUTH_TOKEN":"original-changed-during-read"}}')
    original_open, original_fdopen = os.open, os.fdopen
    descriptors, mutated = [], []

    def tracking_open(path, *args, **kwargs):
        descriptor = original_open(path, *args, **kwargs)
        if Path(path) == selected: descriptors.append(descriptor)
        return descriptor

    class ActualStream:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): self.stream.__enter__(); return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def fileno(self): return self.stream.fileno()
        def read(self, maximum):
            raw = self.stream.read(maximum)
            if kind == "replacement": os.replace(changed, selected)
            else: selected.write_bytes(changed.read_bytes())
            mutated.append(True)
            return raw

    def observed_fdopen(descriptor, *args, **kwargs):
        stream = original_fdopen(descriptor, *args, **kwargs)
        return ActualStream(stream) if descriptor in descriptors else stream

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "fdopen", observed_fdopen)
    if os.name == "nt":
        native_open = settings_profile._windows_open_nofollow
        def windows_open(path):
            descriptor = native_open(path)
            descriptors.append(descriptor)
            return descriptor
        monkeypatch.setattr(settings_profile, "_windows_open_nofollow", windows_open)
    with pytest.raises(SettingsRouteError, match="SETTINGS_ROUTE_FILE_CHANGED"):
        resolve_route({"EEAS_CLAUDE_SETTINGS_PATH": str(selected)})
    assert mutated == [True] and len(descriptors) == 1
    with pytest.raises(OSError): os.fstat(descriptors[0])

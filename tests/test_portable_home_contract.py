"""Environment forwarding sentinel only; actual installation is a separate whole-suite run."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
from scripts import portable_acceptance as portable


def test_portable_every_command_gets_fresh_home_and_declared_offline_environment(tmp_path, monkeypatch):
    application = tmp_path / "original application"
    profile = application / "profiles/offline-dependencies.json"
    profile.parent.mkdir(parents=True)
    target = "windows-py310" if portable.os.name == "nt" else "linux-py310"
    profile.write_text(json.dumps({"targets": {target: "vendor/original"}}), encoding="utf-8")
    monkeypatch.setattr(portable, "validate_extracted", lambda root: {
        "target": target, "source_commit": "a" * 40, "source_snapshot_sha256": "b" * 64})
    monkeypatch.setenv("HOME", str(tmp_path / "never-use-inherited-home"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ORIGINAL_SENTINEL_NEVER_FORWARD")
    calls = []

    def original_contract_command(command, **kwargs):
        calls.append({"command": command, "environment": dict(kwargs["env"])})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(portable.subprocess, "run", original_contract_command)
    output = tmp_path / "actual environment observation"
    portable.probe(application, output)
    assert len(calls) == 3
    for call in calls:
        env = call["environment"]
        assert env["HOME"] == env["USERPROFILE"] == str(output / "empty-user-home")
        assert Path(env["HOME"]).is_dir() and list(Path(env["HOME"]).iterdir()) == []
        assert env["CLAUDE_CONFIG_DIR"] == str(output / "empty-claude-config")
        assert env["PIP_NO_INDEX"] == "1" and env["ANTHROPIC_API_KEY"] == ""
        assert "ORIGINAL_SENTINEL_NEVER_FORWARD" not in json.dumps(env)
    frozen = json.loads((output / "freeze.json").read_bytes())
    assert frozen["environment_variables"]["HOME"] == str(output / "empty-user-home")
    assert frozen["environment_variables"]["PIP_NO_INDEX"] == "1"
    assert "ORIGINAL_SENTINEL_NEVER_FORWARD" not in json.dumps(frozen)

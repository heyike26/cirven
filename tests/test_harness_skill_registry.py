"""Original counterexamples for explicit packaged, selectively read skills."""
from pathlib import Path

import pytest

from scripts.check_harness import BundleError, check_skill_registry, select_files


def skill(root, name, valid=True):
    relative = f".claude/skills/{name}/SKILL.md"
    target = root / relative
    target.parent.mkdir(parents=True)
    target.write_text(f"---\nname: {name}\ndescription: Original fixture\n---\n" if valid else "Original without metadata", encoding="utf-8")
    return relative


def test_unregistered_added_skill_remains_rejected(tmp_path):
    core = skill(tmp_path, "core")
    skill(tmp_path, "optional")
    with pytest.raises(BundleError, match="Orphan"):
        check_skill_registry(tmp_path, {core}, [])


def test_packaged_optional_is_not_silently_loaded_into_context(tmp_path):
    core, optional = skill(tmp_path, "core"), skill(tmp_path, "optional")
    assert check_skill_registry(tmp_path, {core}, [optional]) == {core, optional}
    config = {"baseline_read": ["AGENTS.md"], "optional_skills": [optional],
              "routes": {"intake": {"skill": core, "default_milestone": None, "references": []}}}
    assert select_files(config, {}, "intake", None) == ["AGENTS.md", core]


@pytest.mark.parametrize("kind", ["duplicate", "route-overlap", "missing", "outside", "traversal", "metadata", "not-list"])
def test_bad_optional_registry_does_not_expand_allowed_bundle(tmp_path, kind):
    core = skill(tmp_path, "core")
    optional = skill(tmp_path, "optional", valid=kind != "metadata")
    options = {"duplicate": [optional, optional], "route-overlap": [core, optional],
               "missing": [".claude/skills/missing/SKILL.md"], "outside": ["scripts/check_harness.py"],
               "traversal": [".claude/skills/../optional/SKILL.md"], "metadata": [optional], "not-list": optional}
    with pytest.raises(BundleError):
        check_skill_registry(tmp_path, {core}, options[kind])

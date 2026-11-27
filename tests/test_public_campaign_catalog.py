"""Catalog revision checks are metadata validation, not browser or public-project PASS."""
from copy import deepcopy
from pathlib import Path

import pytest

from scripts import public_campaign as campaign

ROOT = Path(__file__).resolve().parents[1]


def test_task_hero_revision_classifies_real_controls_regions_and_rejects_old_or_invalid_mapping():
    catalog = campaign.load(ROOT / campaign.CATALOG)
    assert catalog["catalog_revision"] == 6
    assert catalog["catalog_base"] == '108faffc872830f37c6c1c15ade652d71ba2e3ad'
    assert catalog["review"]["previous_catalog_sha256"] == '4acca2b280c8ffbb18e8bad740f999f0d5a58433e6ee839c6e1abeea3d2e1007'
    assert {change["path"] for change in catalog["review"]["source_changes"]} == {
        "scripts/portable_acceptance.py"}
    assert len(catalog["source_sha256"]) == 94
    assert catalog["surfaces"]["controls"]["task-select-requirement"] == ["ui-workspace", "requirements", "tasks"]
    parsed = campaign.Controls()
    parsed.feed((ROOT / "apps/web/index.html").read_text(encoding="utf-8"))
    expected = {"task-source-title": "strong", "task-source-note": "p",
                "task-source-status": "span", "task-list-count": "span"}
    assert {name: parsed.elements[name] for name in expected} == expected
    assert not set(expected) & parsed.names
    assert set(catalog["display_regions"]) == set(expected)
    assert all("ui-workspace" in value["cases"] for value in catalog["display_regions"].values())
    assert [c["id"] for c in catalog["cases"] if c.get("external_qualification")] == [
        "qual-company-model", "qual-company-search", "qual-native-windows", "qual-code-isolation"]
    assert campaign.check_catalog(ROOT, catalog)["meaning"] == "inventory consistency only; no feature was executed"

    # Previous source pins must continue to reject this new source; no automatic migration.
    previous = deepcopy(catalog)
    for change in previous["review"]["source_changes"]:
        previous["source_sha256"][change["path"]] = change["previous_sha256"]
    with pytest.raises(ValueError, match="catalog source changed"):
        campaign.check_catalog(ROOT, previous)
    bad = deepcopy(catalog)
    bad["display_regions"]["task-source-title"]["element"] = "button"
    with pytest.raises(ValueError, match="noninteractive display"):
        campaign.check_catalog(ROOT, bad)
    bad = deepcopy(catalog)
    bad["display_regions"]["task-source-note"]["cases"] = ["nonexistent-case"]
    with pytest.raises(ValueError, match="noninteractive display"):
        campaign.check_catalog(ROOT, bad)
    bad = deepcopy(catalog)
    bad["display_regions"]["task-select-requirement"] = {"element": "button", "cases": ["ui-workspace"]}
    with pytest.raises(ValueError, match="noninteractive display"):
        campaign.check_catalog(ROOT, bad)

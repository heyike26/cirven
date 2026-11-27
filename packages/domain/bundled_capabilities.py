"""Register reviewed original planning skills as data, never as tool authority."""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
import uuid

from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Conflict, InvalidInput
from packages.domain.tasks import digest, now

BUNDLE_VERSION = "eeas.planning-skills.v2"
# v1 receipts may describe a user-preserved skill, or a skill the earlier package could not seed.
V1_MANIFEST_SHA256 = "a6eed0308bf755ae980a5bd311f908501e8d30cc03871702371cc2d3e316015a"
# Identities are original frontmatter names; directory names are provenance.
# A reviewed upgrade changes this explicit manifest, never learns new hashes.
BUNDLED_SKILLS = (
    ("eeas-acceptance-review", "acceptance-review", 967, "b84899bb7b50d079c7f2b9566e782e8d98927cc6c71766b9a03b76441e0e71ad"),
    ("eeas-clarify-gaps", "clarify-gaps", 594, "69ee2042a65b2feaaa7304e8978062cc3e69603511f4dbfd7670902c12248ca0"),
    ("competitive-evidence", "competitive-evidence", 1057, "260f24ea9cdb35588450aa5363f8e11e47ef61478070ba88e4267632d7d16e3b"),
    ("diagram-evidence", "diagram-evidence", 908, "41498bd61500a65bf6270ac1f9149b4f65b64f416d4e0f13ac13f34ad1c33cbf"),
    ("engineering-requirements", "engineering-requirements", 1646, "fdd63061dcf69f7af9dca339cc4dc6d44a9bd3d831de9d8d7039d3fbc185b5a6"),
    ("needs-elicitation", "needs-elicitation", 1038, "bcde4e39d8b0659d3db43532a571bd6b262b1d0c532fc6dc945b9815454b5d9f"),
    ("problem-discovery", "problem-discovery", 947, "a10f73e444c5ac93f53e9535cea9d206062f6c2217b76e3a24f399c51003a9bc"),
    ("prompt-orchestration", "prompt-orchestration", 980, "dfda4112d34e721e698c51e01c839980060fd390b895a0aa31aa72dd3ef08569"),
    ("eeas-report-handoff", "report-handoff", 594, "929766206adf8919daeed7dd0c98a04abf5a7e7c1091fab5f6c5aebc9aafdfed"),
    ("eeas-source-review", "source-review", 488, "6696f6664dac111e48ce625d230bf8d75f5eab2d1fcd833f0d15ac2700d18ca6"),
    ("eeas-task-decomposition", "task-decomposition", 1028, "86ff61683f1e8cab5a819d4bfe762c44988658590347fb70aa0c1848afdbc23b"),
)


def bundled_manifest():
    return {"schema": "eeas.bundled-skill-manifest.v1", "bundle_version": BUNDLE_VERSION,
        "skills": [{"id": ident, "path": "packages/planning/skills/" + folder + "/SKILL.md",
                    "bytes": size, "sha256": sha} for ident, folder, size, sha in BUNDLED_SKILLS]}


def _source(source_root, entry):
    root = Path(source_root).absolute()
    path = root.joinpath(*entry["path"].split("/"))
    if (any(p.is_symlink() for p in (path, *path.parents))
            or not path.resolve().is_relative_to(root.resolve())):
        raise InvalidInput("Bundled skill source contains a link or escapes its reviewed root")
    if not path.is_file() or path.stat().st_size != entry["bytes"]:
        raise Conflict("Bundled original skill is missing or changed")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise Conflict("Bundled original skill differs from its reviewed SHA256")
    text = raw.decode("utf-8")
    if text.encode("utf-8") != raw:
        raise Conflict("Bundled skill original bytes cannot be preserved")
    return path, text


def _previous(registry, bundle_sha):
    latest = registry._path("bundled-installs", bundle_sha, "latest.json")
    if not latest.exists():
        return None
    pointer = registry._read(latest)
    if (set(pointer) != {"schema", "bundle_manifest_sha256", "record_id", "receipt_sha256"}
            or pointer["schema"] != "eeas.bundled-skill-pointer.v1"
            or pointer["bundle_manifest_sha256"] != bundle_sha
            or not re.fullmatch(r"[a-f0-9]{32}", str(pointer["record_id"]))):
        raise Conflict("Bundled installation pointer changed")
    previous = registry._read(registry._path("bundled-installs", bundle_sha, "records", pointer["record_id"] + ".json"))
    if (digest(previous) != pointer["receipt_sha256"]
            or previous.get("schema") != "eeas.bundled-skill-installation.v1"
            or previous.get("bundle_manifest_sha256") != bundle_sha
            or not isinstance(previous.get("skills"),list) or len(previous["skills"]) != len(BUNDLED_SKILLS)
            or {row.get("id") for row in previous["skills"] if isinstance(row,dict)} != {item[0] for item in BUNDLED_SKILLS}):
        raise Conflict("Bundled installation receipt changed")
    return previous


def _persist(registry, receipt):
    ident = uuid.uuid4().hex
    bundle_sha = receipt["bundle_manifest_sha256"]
    path = registry._path("bundled-installs", bundle_sha, "records", ident + ".json")
    registry._write(path, receipt)
    registry._write(registry._path("bundled-installs", bundle_sha, "latest.json"),
        {"schema": "eeas.bundled-skill-pointer.v1", "bundle_manifest_sha256": bundle_sha,
         "record_id": ident, "receipt_sha256": digest(receipt)}, replace=True)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "value_sha256": digest(receipt)}


def _preserved(registry, row):
    result = {"state": "PRESERVED_EXISTING", "config_ref": row["config_ref"], "enabled": row["enabled"]}
    if row["integrity"] != "CURRENT":
        return {**result, "gap": "EXISTING_SKILL_INTEGRITY_ERROR"}
    try:
        current = registry.skill_content(row["id"])
        result["current_content_sha256"] = hashlib.sha256(current["content"].encode("utf-8")).hexdigest()
    except (Conflict, InvalidInput, OSError) as error:
        result.update(gap="EXISTING_SKILL_UNREADABLE", error_type=type(error).__name__)
    return result


def ensure_bundled_skills(registry: NativeCapabilities, source_root):
    """First import only; every existing user choice and version takes priority.

    Availability is not execution permission or proof of loading in OpenCode.
    Receipts are append-only values behind a checked atomic latest pointer.
    """
    manifest = bundled_manifest()
    bundle_sha = digest(manifest)
    receipt = {"schema": "eeas.bundled-skill-installation.v1", "bundle_version": BUNDLE_VERSION,
        "bundle_manifest_sha256": bundle_sha, "created_at": now(), "skills": [],
        "permission_granted": False, "loaded_in_original_session": False, "model_started": False,
        "mcp_started": False, "upgrade_policy": "PRESERVE_EXISTING_IDS_REQUIRE_EXPLICIT_USER_UPDATE"}
    try:
        previous = _previous(registry, bundle_sha)
        if previous is None and bundle_sha != V1_MANIFEST_SHA256:
            previous = _previous(registry, V1_MANIFEST_SHA256)
        existing = {row["id"].casefold(): row for row in registry.get()["skills"]}
    except (Conflict, InvalidInput, OSError, ValueError, KeyError, TypeError) as error:
        return {**receipt, "status": "REGISTRY_OR_INSTALLATION_RECEIPT_UNAVAILABLE",
            "gap": "AUTOMATIC_IMPORT_DISABLED_PRESERVE_UNKNOWN_USER_STATE", "error_type": type(error).__name__,
            "installation_receipt_ref": None}
    known = {
        row["id"] for row in (previous["skills"] if previous else [])
        if row.get("config_ref") or row.get("existing", {}).get("config_ref")
        or row.get("state") == "PREVIOUSLY_INSTALLED_NOW_MISSING" or row.get("previously_installed")
    }
    for entry in manifest["skills"]:
        row = {"id": entry["id"], "source_ref": dict(entry), "permission_granted": False}
        if entry["id"] in known:
            row["previously_installed"] = True
        current = existing.get(entry["id"].casefold())
        try:
            path, text = _source(source_root, entry)
            row["source_ref"]["path"] = str(path)
            row["source_integrity"] = "REVIEWED_ORIGINAL_BYTES"
        except (Conflict, InvalidInput, OSError, UnicodeError) as error:
            row.update(state="SOURCE_UNAVAILABLE", gap="BUNDLED_ORIGINAL_SOURCE_MISSING_CHANGED_OR_LINKED",
                source_integrity="FAILED", error_type=type(error).__name__)
            if current is not None:
                row["existing"] = _preserved(registry, current)
            receipt["skills"].append(row);continue
        if current is not None:
            row.update(_preserved(registry, current))
            row["bundle_source_matches_current"] = row.get("current_content_sha256") == entry["sha256"]
        elif entry["id"] in known:
            row.update(state="PREVIOUSLY_INSTALLED_NOW_MISSING", gap="USER_STATE_NOT_RECREATED_AUTOMATICALLY")
        else:
            try:
                saved = registry.import_skill({"operation_id": "bundled-" + bundle_sha[:24] + "-" + entry["id"],
                    "id": entry["id"], "expected_revision": 0, "content": text, "enabled": True})
                current_content = registry.skill_content(entry["id"])
                if current_content["content"].encode("utf-8") != text.encode("utf-8"):
                    raise Conflict("Imported bundled skill bytes differ from reviewed source")
                state = "IMPORTED" if current_content["config_ref"] == saved["config_ref"] else "PRESERVED_EXISTING"
                row.update(state=state, config_ref=current_content["config_ref"], enabled=current_content["enabled"],
                    current_content_sha256=entry["sha256"], bundle_source_matches_current=True)
            except (Conflict, InvalidInput, OSError, ValueError) as error:
                # A concurrent human import wins; never retry with its revision.
                try:
                    fresh = {item["id"].casefold(): item for item in registry.get()["skills"]}.get(entry["id"].casefold())
                except (Conflict,InvalidInput,OSError,ValueError,KeyError,TypeError):
                    fresh = None
                if fresh is not None:
                    row.update(_preserved(registry, fresh))
                    row["bundle_source_matches_current"] = row.get("current_content_sha256") == entry["sha256"]
                else:
                    row.update(state="IMPORT_FAILED", gap="REGISTRY_IMPORT_NOT_COMPLETED", error_type=type(error).__name__)
        receipt["skills"].append(row)
    receipt["status"] = "REGISTERED_WITH_GAPS" if any(row.get("gap") for row in receipt["skills"]) else "REGISTERED"
    receipt["imported_count"] = sum(row["state"] == "IMPORTED" for row in receipt["skills"])
    receipt["preserved_count"] = sum(row["state"] == "PRESERVED_EXISTING" for row in receipt["skills"])
    try:
        reference = _persist(registry, receipt)
    except (Conflict, InvalidInput, OSError) as error:
        return {**receipt, "status": "INSTALLATION_RECEIPT_WRITE_FAILED", "installation_receipt_ref": None,
            "gap": "REGISTRY_IMPORTS_MAY_EXIST_RELOAD_BEFORE_RETRY", "error_type": type(error).__name__}
    return {**receipt, "installation_receipt_ref": reference}

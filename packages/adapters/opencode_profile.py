"""Explicit, pinned OpenCode pilot configuration; no account/config discovery."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from packages.adapters.settings_profile import SettingsRouteError, _read_selected_settings, _object, _invalid_constant
from packages.domain.tasks import digest

VERSION = "1.19.0"
COMMIT = "733b2c627e5aec39cb33cc22373bdce33053ef2c"
CLI_SHA = {
    "posix": "135ac3f86c43f2a22328a3554fec5f0bc57d71576077902661bc38dac34871fd",
    "nt": "bb731f311416df16691fa16763eb87657c4a5f5f3bcff5202718c416e8f7ab1a",
}


class ProfileError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def binary_hash(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


@dataclass(frozen=True)
class OpenCodeProfile:
    executable: Path
    executable_sha256: str
    raw_sha256: str
    provider_url: str
    model_id: str
    api_key: str
    credential_sha256: str
    fingerprint: str

    def public(self):
        return {"version": VERSION, "source_commit": COMMIT, "cli_sha256": self.executable_sha256,
                "profile_sha256": self.raw_sha256, "profile_fingerprint": self.fingerprint,
                "provider_url_sha256": digest(self.provider_url), "model_id": self.model_id,
                "credential_present": bool(self.api_key), "transport_kind": "MOCK_WIRE_DECLARED",
                "commercial_pricing": "UNKNOWN", "hard_usd_limit": "UNSUPPORTED_LIVE_BLOCKED",
                "native_windows": "NOT_VALIDATED", "code_tools_os_isolation": "NOT_QUALIFIED"}


def resolve_profile():
    selected = os.environ.get("EEAS_OPENCODE_PROFILE", "")
    if not selected:
        raise ProfileError("OPENCODE_PROFILE_NOT_CONFIGURED")
    try:
        path = Path(selected)
        if not path.is_absolute() or ".." in path.parts:
            raise ProfileError("OPENCODE_PROFILE_PATH_REJECTED")
        raw = _read_selected_settings(path)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object, parse_constant=_invalid_constant)
        keys = {"schema", "version", "source_commit", "executable", "executable_sha256", "transport_kind", "provider_url", "model_id"}
        if not isinstance(value, dict) or set(value) != keys or value["schema"] != "eeas.opencode-pilot-profile.v1":
            raise ProfileError("OPENCODE_PROFILE_INVALID")
        if value["version"] != VERSION or value["source_commit"] != COMMIT:
            raise ProfileError("OPENCODE_VERSION_NOT_APPROVED")
        # A local Mock designation never grants company/provider or commercial-budget qualification.
        if value["transport_kind"] != "MOCK_WIRE":
            raise ProfileError("OPENCODE_LIVE_COST_ENFORCEMENT_NOT_QUALIFIED")
        endpoint = urlsplit(value["provider_url"])
        if (endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or endpoint.username or
                endpoint.password or endpoint.path != "/v1" or endpoint.query or endpoint.fragment or not endpoint.port):
            raise ProfileError("OPENCODE_MOCK_ENDPOINT_REJECTED")
        if not isinstance(value["model_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value["model_id"]):
            raise ProfileError("OPENCODE_MODEL_ID_INVALID")
        executable = Path(value["executable"])
        if (not executable.is_absolute() or ".." in executable.parts or executable.is_symlink() or
                not executable.is_file() or any(p.is_symlink() for p in executable.parents)):
            raise ProfileError("OPENCODE_CLI_PATH_REJECTED")
        expected = CLI_SHA.get(os.name)
        if value["executable_sha256"] != expected or binary_hash(executable) != expected:
            raise ProfileError("OPENCODE_CLI_SHA_MISMATCH")
        credential = os.environ.get("EEAS_OPENCODE_API_KEY", "")
        if not credential or len(credential) > 8192 or any(ord(c) < 32 for c in credential):
            raise ProfileError("OPENCODE_NO_CREDENTIALS")
        raw_hash = hashlib.sha256(raw).hexdigest()
        credential_hash = digest(credential)
        fingerprint = digest({"profile_sha256": raw_hash, "credential_sha256": credential_hash})
        return OpenCodeProfile(executable, expected, raw_hash, value["provider_url"], value["model_id"], credential, credential_hash, fingerprint)
    except ProfileError:
        raise
    except (SettingsRouteError, OSError, ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        raise ProfileError("OPENCODE_PROFILE_INVALID") from None

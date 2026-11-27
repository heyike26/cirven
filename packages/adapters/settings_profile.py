"""Explicit company routing only; never load CLI login stores or execute settings."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit


ROUTE_ENV = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL",
             "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL")
MODEL_ALIASES = {"opus": "ANTHROPIC_DEFAULT_OPUS_MODEL", "sonnet": "ANTHROPIC_DEFAULT_SONNET_MODEL",
                 "haiku": "ANTHROPIC_DEFAULT_HAIKU_MODEL"}
MAX_SETTINGS_BYTES = 256 * 1024


def native_budget_float(approved):
    """Keep the installed CLI's str(float) cap within the Decimal authorization.

    Decimal remains the ledger value. A lower native cap is acceptable, while
    an unrepresentable positive cap must never silently become zero/unbounded.
    """
    try:
        amount = Decimal(approved)
        if not amount.is_finite() or not 0 < amount <= 100:
            raise ValueError
        native = float(amount)
        if not math.isfinite(native) or native <= 0:
            raise ValueError
        if Decimal(str(native)) > amount:
            native = math.nextafter(native, 0.0)
        if not math.isfinite(native) or native <= 0 or Decimal(str(native)) > amount:
            raise ValueError
        return native
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        raise ValueError("NATIVE_BUDGET_NOT_REPRESENTABLE") from None


class SettingsRouteError(ValueError):
    """Only fixed reason codes escape, including JSON and filesystem failures."""
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _sha(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SettingsRouteError("SETTINGS_ROUTE_DUPLICATE_KEY")
        result[key] = value
    return result


def _invalid_constant(value):
    raise SettingsRouteError("SETTINGS_ROUTE_INVALID_JSON")


def _text(value, *, maximum=8192):
    if not isinstance(value, str) or len(value) > maximum or any(ord(character) < 32 for character in value):
        raise SettingsRouteError("SETTINGS_ROUTE_INVALID_VALUE")
    return value


def _file_identity(metadata):
    return metadata.st_dev, metadata.st_ino, stat.S_IFMT(metadata.st_mode)


def _file_version(metadata):
    return (*_file_identity(metadata), metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns)


def _ordinary_path_chain(path):
    """lstat every component; Windows junctions are reparse points, too."""
    chain = {}
    for component in (*reversed(path.parents), path):
        metadata = component.lstat()
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise SettingsRouteError("SETTINGS_ROUTE_PATH_REJECTED")
        if component != path and not stat.S_ISDIR(metadata.st_mode):
            raise SettingsRouteError("SETTINGS_ROUTE_PATH_REJECTED")
        chain[component] = metadata
    return chain


def _windows_open_nofollow(path):
    """Open the reparse point itself, reject it, then transfer the real handle.

    This native Windows branch awaits Windows qualification; POSIX tests cannot
    qualify CreateFileW semantics. No settings or account discovery occurs here.
    """
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class FileInformation(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("creation", wintypes.FILETIME),
                    ("access", wintypes.FILETIME), ("write", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    info = kernel.GetFileInformationByHandle
    info.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    info.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # GENERIC_READ, share read/delete, OPEN_EXISTING. Deny concurrent writers:
    # Python3.10 Windows ctime is creation time, so timestamp comparisons alone
    # cannot detect a same-size overwrite that restores last-write time. Delete
    # sharing preserves atomic rotation, still checked against the pinned file.
    # FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN.
    handle = create(str(path), 0x80000000, 0x5, None, 3, 0x00200000 | 0x08000000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise OSError("SETTINGS_ROUTE_FILE_REJECTED")
    try:
        metadata = FileInformation()
        if not info(handle, ctypes.byref(metadata)) or metadata.attributes & (0x400 | 0x10 | 0x40):
            raise SettingsRouteError("SETTINGS_ROUTE_PATH_REJECTED")
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
        handle = None  # The Python descriptor now owns and closes the HANDLE.
        return descriptor
    finally:
        if handle is not None:
            close(handle)


def _read_selected_settings(path):
    """Read one prechecked regular file through a stable, no-follow descriptor."""
    try:
        before = _ordinary_path_chain(path)
        selected = before[path]
        if not stat.S_ISREG(selected.st_mode) or selected.st_size > MAX_SETTINGS_BYTES:
            raise SettingsRouteError("SETTINGS_ROUTE_FILE_REJECTED")
        if os.name == "nt":
            descriptor = _windows_open_nofollow(path)
        else:
            # O_NONBLOCK prevents a regular-file-to-FIFO swap from hanging open.
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode) or _file_version(opened) != _file_version(selected):
                raise SettingsRouteError("SETTINGS_ROUTE_FILE_CHANGED")
            after_open = _ordinary_path_chain(path)
            if any(_file_identity(after_open[part]) != _file_identity(metadata) for part, metadata in before.items()):
                raise SettingsRouteError("SETTINGS_ROUTE_FILE_CHANGED")
            with os.fdopen(descriptor, "rb") as stream:
                descriptor = None
                raw = stream.read(MAX_SETTINGS_BYTES + 1)
                after_read = os.fstat(stream.fileno())
            current = _ordinary_path_chain(path)
            if (len(raw) > MAX_SETTINGS_BYTES or len(raw) != selected.st_size
                    or _file_version(after_read) != _file_version(selected)
                    or _file_version(current[path]) != _file_version(selected)
                    or any(_file_identity(current[part]) != _file_identity(metadata) for part, metadata in before.items())):
                raise SettingsRouteError("SETTINGS_ROUTE_FILE_CHANGED")
            return raw
        finally:
            if descriptor is not None:
                os.close(descriptor)
    except SettingsRouteError:
        raise
    except OSError:
        raise SettingsRouteError("SETTINGS_ROUTE_FILE_REJECTED") from None


@dataclass(frozen=True)
class RouteProfile:
    environment: dict[str, str] = field(repr=False)
    model_id: str | None = field(repr=False)
    budget_usd: str | None
    provenance: dict

    @property
    def secrets(self):
        return tuple(self.environment[name] for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
                     if self.environment.get(name))

    @property
    def fingerprint(self):
        return _sha(json.dumps({"provenance": self.provenance, "route": self.environment,
                                "model": self.model_id}, sort_keys=True, separators=(",", ":")))

    def public(self):
        return {**self.provenance, "route_sha256": self.fingerprint,
                "credential_present": bool(self.secrets), "model_id_present": bool(self.model_id),
                "endpoint_present": bool(self.environment.get("ANTHROPIC_BASE_URL")),
                "budget_present": self.budget_usd is not None,
                "authentication_verified": False, "api_compatibility": "NOT_VALIDATED",
                "raw_settings_forwarded": False, "developer_login_used": False}


def resolve_route(environment=None):
    """EEAS process overrides > selected settings env/model; no ambient file discovery.

    The selected file is only parsed. Its settings/hook/helper/permission/MCP keys
    never reach native --settings. Native options.env receives the route allowlist.
    An explicitly empty process variable clears the corresponding file value.
    """
    environment = os.environ if environment is None else environment
    selected = environment.get("EEAS_CLAUDE_SETTINGS_PATH", "")
    settings = {}
    provenance = {"settings_selected": bool(selected), "settings_sha256": None,
                  "settings_path_sha256": None, "sources": {}, "model_sources": [],
                  "precedence": "EEAS_PROCESS_OVERRIDES_SELECTED_SETTINGS", "selection_is_approval": False}
    if selected:
        _text(selected, maximum=4096)
        path = Path(selected)
        if (not path.is_absolute() or path.name.lower() in {".claude.json", ".credentials.json", "credentials.json", "auth.json"}
                or ".." in path.parts):
            raise SettingsRouteError("SETTINGS_ROUTE_PATH_REJECTED")
        try:
            raw = _read_selected_settings(path)
            settings = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object, parse_constant=_invalid_constant)
        except SettingsRouteError:
            raise
        except (OSError, UnicodeError, ValueError, RecursionError):
            raise SettingsRouteError("SETTINGS_ROUTE_INVALID_JSON") from None
        if not isinstance(settings, dict) or not isinstance(settings.get("env", {}), dict):
            raise SettingsRouteError("SETTINGS_ROUTE_INVALID_SHAPE")
        provenance.update(settings_sha256=_sha(raw), settings_path_sha256=_sha(str(path)))

    routed = {}
    for name in ROUTE_ENV:
        maximum = 256 if "MODEL" in name else 8192
        if name in environment:
            routed[name] = _text(environment[name], maximum=maximum)
            provenance["sources"][name] = "PROCESS_ENVIRONMENT"
        elif name in settings.get("env", {}):
            routed[name] = _text(settings["env"][name], maximum=maximum)
            provenance["sources"][name] = "SELECTED_SETTINGS_ENV"
        if name in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"} and routed.get(name) and not routed[name].strip():
            raise SettingsRouteError("SETTINGS_ROUTE_INVALID_VALUE")
    endpoint = routed.get("ANTHROPIC_BASE_URL")
    if endpoint:
        try:
            address = urlsplit(endpoint)
            if (address.scheme not in {"http", "https"} or not address.hostname or address.username or address.password
                    or address.query or address.fragment or (address.port is not None and not 1 <= address.port <= 65535)
                    or any(character.isspace() for character in endpoint)):
                raise ValueError
        except ValueError:
            raise SettingsRouteError("SETTINGS_ROUTE_ENDPOINT_INVALID") from None

    if "EEAS_MODEL_ID" in environment:
        model = _text(environment["EEAS_MODEL_ID"], maximum=256)
        provenance["model_sources"] = ["PROCESS_EEAS_MODEL_ID"]
    elif "ANTHROPIC_MODEL" in routed:
        model = _text(routed["ANTHROPIC_MODEL"], maximum=256)
        provenance["model_sources"] = [provenance["sources"]["ANTHROPIC_MODEL"] + ":ANTHROPIC_MODEL"]
    elif "model" in settings:
        model = _text(settings["model"], maximum=256)
        provenance["model_sources"] = ["SELECTED_SETTINGS_MODEL"]
    else:
        model = ""
    if model in MODEL_ALIASES:
        variable = MODEL_ALIASES[model]
        model = _text(routed.get(variable, ""), maximum=256)
        provenance["model_sources"].append(provenance["sources"].get(variable, "MISSING") + ":" + variable)
    if (model in {*MODEL_ALIASES, "default", "inherit", "opusplan", "sonnet[1m]", "opus[1m]", "haiku[1m]"}
            or any(character.isspace() for character in model)):
        model = ""
    budget = None
    try:
        amount = Decimal(environment["EEAS_LIVE_MAX_COST_USD"])
        native_budget_float(amount)
        budget = str(amount)
    except (KeyError, InvalidOperation, ValueError):
        pass
    return RouteProfile(routed, model or None, budget, provenance)

"""Read relocated immutable records without rewriting their historical paths."""
from __future__ import annotations
import json
from pathlib import Path, PurePosixPath, PureWindowsPath

from packages.domain.store import InvalidInput


def resolve_data_path(stored, data_dir):
    root = Path(data_dir).resolve()
    raw = str(stored).replace("\\", "/")
    if not raw or any(ord(c) < 32 for c in raw) or ".." in PurePosixPath(raw).parts:
        raise InvalidInput("Unsafe stored data path")
    windows = bool(PureWindowsPath(raw).drive)
    absolute = raw.startswith("/") or windows
    current = str(root).replace("\\", "/").rstrip("/")

    def beneath(prefix):
        prefix = prefix.replace("\\", "/").rstrip("/")
        use_casefold = bool(PureWindowsPath(prefix).drive)
        left, right = (raw.casefold(), prefix.casefold()) if use_casefold else (raw, prefix)
        if left == right:
            return ""
        return raw[len(prefix) + 1:] if left.startswith(right + "/") else None

    suffix = beneath(current) if absolute else raw
    if suffix is None:
        marker = root / "restore-provenance.json"
        if marker.is_symlink() or not marker.is_file() or marker.stat().st_size > 65536:
            raise InvalidInput("Historical path has no trusted restore provenance")
        try:
            provenance = json.loads(marker.read_bytes())
            if provenance["schema_version"] != "eeas.restore-provenance.v1":
                raise ValueError
            sources = provenance["allowed_source_data_dirs"]
            if not isinstance(sources, list) or len(sources) > 50 or not all(isinstance(p, str) for p in sources):
                raise ValueError
            matches = [value for source in sources if (value := beneath(source)) is not None]
            if len(set(matches)) != 1:
                raise ValueError
            suffix = matches[0]
        except (OSError, ValueError, KeyError, TypeError):
            raise InvalidInput("Restore provenance does not authorize this historical path") from None
    if suffix and (PurePosixPath(suffix).is_absolute() or str(PurePosixPath(suffix)) != suffix or any(p in {".", ".."} or ":" in p for p in PurePosixPath(suffix).parts)):
        raise InvalidInput("Historical path mapping escaped its data root")
    path = root.joinpath(*PurePosixPath(suffix).parts) if suffix else root
    if not path.resolve().is_relative_to(root):
        raise InvalidInput("Data path escaped its current root")
    if any(p.is_symlink() for p in (path, *path.parents) if p == root or p.is_relative_to(root)):
        raise InvalidInput("Stored data path contains a symlink")
    return path

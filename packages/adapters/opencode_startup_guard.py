"""Admission for original OpenCode's unconditional V2 ancestor discovery.

Native tool permission rules do not constrain plugin imports or MCP startup.
This guard rejects discovered project origins; it is not an OS sandbox. Existing
Git workspaces require a separately reviewed adapter and are not admitted here.
"""
from pathlib import Path

from packages.domain.store import Conflict


def reject_unreviewed_project_origins(directory):
    """Check lexical and resolved ancestry, including dangling source links.

    The actual CLI must be spawned immediately after admission. Concurrent
    filesystem changes remain an explicit TOCTOU limitation, not an isolation
    guarantee; callers should also recheck before later original dispatches.
    """
    path = Path(directory).absolute()
    try:
        actual = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise Conflict("ORIGINAL_DIRECTORY_UNAVAILABLE") from None
    if not actual.is_dir():
        raise Conflict("ORIGINAL_DIRECTORY_UNAVAILABLE")
    seen = set()
    for start in (path, actual):
        for ancestor in (start, *start.parents):
            if ancestor in seen:
                continue
            seen.add(ancestor)
            for name in (".opencode", "opencode.json", "opencode.jsonc", ".git"):
                candidate = ancestor / name
                if candidate.exists() or candidate.is_symlink():
                    raise Conflict("UNREVIEWED_ORIGINAL_CONFIG_OR_VCS_ANCESTOR: " + str(candidate))

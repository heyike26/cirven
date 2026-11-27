"""Frozen source paths are observation authority, never workspace write grants."""
from packages.adapters.opencode_command_observation import declared_paths
from packages.domain.store import Conflict


def observation_sources(request, workspace, *, clean):
    explicit = request.get('command_observation_paths')
    if explicit is not None:
        paths = declared_paths(explicit)
        if any(clean(path) != path for path in paths):
            raise Conflict('An observation path includes private runtime material')
        return paths
    # Preserve the historical scope, including its ordering and >32 fallback.
    paths = sorted({name for name in workspace.get('allowed_write_paths', [])
        if isinstance(name, str) and not name.endswith('/')
        and not any(char in name for char in '*?[]') and clean(name) == name})
    return [] if len(paths) > 32 else paths

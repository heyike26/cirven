"""Pinned original MCP naming and conservative server namespace admission.

OpenCode 733b2c mcp/catalog.ts preserves ASCII letters, digits, '_' and '-'.
It joins server and tool with '_', which cannot encode ownership unambiguously
when one enabled server name is another server's underscore-delimited prefix.
The original public status API does not expose the unflattened tool catalog.
"""
import re

from packages.domain.store import Conflict


def original_mcp_identifier(value):
    return re.sub(r"[^A-Za-z0-9_-]", "_", value)


def admitted_server_prefixes(server_ids):
    """Reject potential ambiguity before any selected MCP process starts.

    Prefix overlap is a conservative namespace rejection, not proof that the
    actual servers' currently advertised tools collide. Hyphens remain intact.
    """
    prefixes = {}
    for ident in server_ids:
        prefix = original_mcp_identifier(ident) + "_"
        for old_id, old_prefix in prefixes.items():
            if prefix == old_prefix or prefix.startswith(old_prefix) or old_prefix.startswith(prefix):
                raise Conflict("AMBIGUOUS_ORIGINAL_MCP_SERVER_NAMESPACE: " + old_id + ", " + ident)
        prefixes[ident] = prefix
    return prefixes


def exact_tool_owner(tool_name, prefixes):
    matches = [ident for ident, prefix in prefixes.items()
               if tool_name.startswith(prefix) and len(tool_name) > len(prefix)]
    if len(matches) != 1:
        raise Conflict("ORIGINAL_MCP_TOOL_SOURCE_NOT_UNAMBIGUOUS: " + tool_name)
    return matches[0]

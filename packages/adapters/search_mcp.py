"""Explicit approved HTTP MCP binding; one search tool, no page-fetch transport."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from threading import Lock
import traceback
from urllib.parse import urlsplit

from packages.adapters.settings_profile import _object, _invalid_constant, _read_selected_settings, _text, SettingsRouteError

NATIVE_SEARCH_TOOL = "mcp__eeas_search__web_search"
SEARCH_ARGUMENT_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["query"],
                          "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 2048}}}

# MCP's parser logs ValidationError with the upstream body in exc_info. Task-local
# credentials also apply to its child tasks, without changing other MCP clients.
_log_secrets = ContextVar("eeas_search_log_secrets", default=())
_log_filter_lock = Lock()


def _secret_forms(secrets):
    forms = set()
    for secret in secrets:
        forms.update((secret, json.dumps(secret, ensure_ascii=True)[1:-1],
                      json.dumps(secret, ensure_ascii=False)[1:-1], repr(secret)[1:-1],
                      repr(secret.encode("utf-8"))[2:-1]))
    return tuple(sorted((value for value in forms if value), key=len, reverse=True))


def _parser_trace(exception, seen=None):
    """Keep trace/classes/error codes; never format a truncated upstream input."""
    from pydantic import ValidationError

    seen = set() if seen is None else seen
    if id(exception) in seen or len(seen) >= 32:
        return "[Repeated/deep exception chain omitted]\n"
    seen.add(id(exception))
    output = ""
    previous = exception.__cause__ or (exception.__context__ if not exception.__suppress_context__ else None)
    if previous is not None:
        output += _parser_trace(previous, seen) + "\nDuring the preceding exception:\n"
    output += "Traceback (most recent call last):\n" + "".join(traceback.format_tb(exception.__traceback__))
    if isinstance(exception, ValidationError):
        # Pydantic abbreviates input_value in str(exc), which can leave only
        # pieces of a credential. Its public error codes contain no input or
        # context; omit values, locations, and interpolated messages entirely.
        codes = sorted({entry["type"] for entry in exception.errors(include_input=False,
                        include_context=False, include_url=False)})
        output += f"{type(exception).__name__}: {exception.error_count()} validation errors ({', '.join(codes)}); upstream input [REDACTED]\n"
    elif isinstance(getattr(exception, "exceptions", None), tuple):
        output += type(exception).__name__ + "\n"
        for child in exception.exceptions:
            output += _parser_trace(child, seen)
    else:
        output += "".join(traceback.format_exception_only(type(exception), exception))
    return output


class _SelectedCredentialFilter(logging.Filter):
    def filter(self, record):
        secrets = _log_secrets.get()
        if not secrets:
            return True

        def safe(value):
            for secret in secrets:
                value = value.replace(secret, "[REDACTED]")
            return value

        # Redact once, before *any* handler formats the shared record. Keeping
        # raw exc_info would let a second formatter render the original input.
        record.msg, record.args = safe(record.getMessage()), ()
        if record.exc_info:
            record.exc_text = safe(_parser_trace(record.exc_info[1]))
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = safe(record.exc_text)
        if record.stack_info:
            record.stack_info = safe(record.stack_info)
        return True


_selected_credential_filter = _SelectedCredentialFilter()


def _install_parser_log_filter():
    logger = logging.getLogger("mcp.client.streamable_http")
    with _log_filter_lock:
        if _selected_credential_filter not in logger.filters:
            logger.addFilter(_selected_credential_filter)


class SearchMCPError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _hash(value):
    raw = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def search_arguments(arguments):
    if (not isinstance(arguments, dict) or set(arguments) != {"query"} or not isinstance(arguments["query"], str)
            or not arguments["query"].strip() or len(arguments["query"]) > 2048
            or any(ord(character) < 32 for character in arguments["query"])):
        raise SearchMCPError("SEARCH_MCP_INVALID_QUERY")
    return {"query": arguments["query"]}


@dataclass(frozen=True)
class SearchProfile:
    url: str = field(repr=False)
    headers: dict = field(repr=False)
    upstream_tool: str
    query_argument: str
    result_fields: dict
    limits: dict
    provenance: dict

    @property
    def secrets(self):
        values = {value for value in self.headers.values() if value}
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        if scheme.casefold() == "bearer" and token.strip(): values.add(token.strip())
        return tuple(values)

    @property
    def fingerprint(self):
        return _hash({"url": self.url, "headers": self.headers, "upstream_tool": self.upstream_tool,
                      "query_argument": self.query_argument, "result_fields": self.result_fields,
                      "limits": self.limits, "provenance": self.provenance})

    def public(self):
        return {**self.provenance, "status": "CONFIGURED_FOR_ATTEMPT", "profile_sha256": self.fingerprint,
                "endpoint_sha256": _hash(self.url), "credential_present": bool(self.secrets),
                "upstream_tool": self.upstream_tool, "native_tool": NATIVE_SEARCH_TOOL,
                "limits": self.limits, "transport": "APPROVED_HTTP_MCP", "api_compatibility": "NOT_VALIDATED",
                "developer_login_used": False, "remote_tools_exposed": False, "page_fetch_available": False,
                "result_kind": "SEARCH_LEAD", "original_document_read": False,
                "provider_charges": "UNKNOWN", "selection_is_approval": False}


def resolve_search_profile(environment=None):
    environment = os.environ if environment is None else environment
    selected = environment.get("EEAS_SEARCH_MCP_CONFIG_PATH", "")
    if not selected: return None
    try:
        _text(selected, maximum=4096)
        path = Path(selected)
        if (not path.is_absolute() or ".." in path.parts or path.name.lower() in
                {".claude.json", ".credentials.json", "credentials.json", "auth.json"}):
            raise ValueError
        raw = _read_selected_settings(path)
        body = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object, parse_constant=_invalid_constant)
        if (not isinstance(body, dict) or set(body) - {"schema_version", "capability", "transport", "upstream_tool_name",
                                                     "query_argument", "result_fields", "limits"}
                or body.get("schema_version") != "eeas.search-mcp.v1" or body.get("capability") != "search_only"):
            raise ValueError
        transport = body.get("transport")
        if (not isinstance(transport, dict) or set(transport) - {"type", "url", "headers", "header_env"}
                or transport.get("type") != "http"):
            raise ValueError
        url = _text(transport.get("url"), maximum=2048)
        address = urlsplit(url)
        if (address.scheme not in {"http", "https"} or not address.hostname or address.username or address.password
                or address.query or address.fragment or any(character.isspace() for character in url)
                or (address.port is not None and not 1 <= address.port <= 65535)):
            raise ValueError
        upstream = _text(body.get("upstream_tool_name"), maximum=128)
        query_argument = _text(body.get("query_argument", "query"), maximum=64)
        if (not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", upstream)
                or any(part in {"fetch", "webfetch", "download", "open", "bash", "exec", "execute", "read", "write", "edit"}
                       for part in re.split(r"[_.-]", upstream.casefold()))
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", query_argument)
                or query_argument.casefold() in {"url", "uri", "urls", "tool", "tool_name", "name", "method", "path"}):
            raise ValueError
        headers, header_sources = {}, {}
        for key in ("headers", "header_env"):
            values = transport.get(key, {})
            if not isinstance(values, dict) or set(values) - {"Authorization", "X-API-Key", "Api-Key"}: raise ValueError
            for name, value in values.items():
                if key == "header_env":
                    if not isinstance(value, str) or not re.fullmatch(r"EEAS_SEARCH_[A-Z0-9_]+", value): raise ValueError
                    value = environment.get(value, "")
                headers[name] = _text(value)
                header_sources[name] = "EXPLICIT_PROCESS_REFERENCE" if key == "header_env" else "SELECTED_CONFIG"
        headers = {name: value for name, value in headers.items() if value}
        limits = {"max_calls": 3, "timeout_seconds": 10, "max_result_bytes": 65536, "max_results": 10}
        supplied = body.get("limits", {})
        bounds = {"max_calls": (1, 20), "timeout_seconds": (1, 30), "max_result_bytes": (1024, 262144), "max_results": (1, 20)}
        if not isinstance(supplied, dict) or set(supplied) - set(limits): raise ValueError
        limits.update(supplied)
        if any(type(limits[name]) is not int or not bounds[name][0] <= limits[name] <= bounds[name][1] for name in limits): raise ValueError
        fields = {"collection": "results", "uri": "url", "title": "title", "snippet": "snippet"}
        supplied = body.get("result_fields", {})
        if not isinstance(supplied, dict) or set(supplied) - set(fields): raise ValueError
        fields.update(supplied)
        if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", value) for value in fields.values()): raise ValueError
        return SearchProfile(url, headers, upstream, query_argument, fields, limits,
                             {"settings_sha256": _hash(raw), "settings_path_sha256": _hash(str(path)),
                              "header_sources": header_sources, "raw_configuration_forwarded": False})
    except (SettingsRouteError, OSError, UnicodeError, ValueError, TypeError, RecursionError):
        raise SearchMCPError("SEARCH_MCP_INVALID_CONFIGURATION") from None


def _search_leads(result, profile):
    wire = result.model_dump(by_alias=True, mode="json", exclude_none=True)
    if wire.get("isError"): raise SearchMCPError("SEARCH_MCP_UPSTREAM_FAILED")
    body = wire.get("structuredContent")
    if body is None:
        content = wire.get("content", [])
        if len(content) != 1 or content[0].get("type") != "text": raise SearchMCPError("SEARCH_MCP_RESULT_UNSUPPORTED")
        try: body = json.loads(content[0]["text"], parse_constant=_invalid_constant)
        except (ValueError, RecursionError): raise SearchMCPError("SEARCH_MCP_RESULT_UNSUPPORTED") from None
    fields = profile.result_fields
    if not isinstance(body, dict) or not isinstance(body.get(fields["collection"]), list):
        raise SearchMCPError("SEARCH_MCP_RESULT_UNSUPPORTED")
    entries = body[fields["collection"]]
    if len(entries) > profile.limits["max_results"]: raise SearchMCPError("SEARCH_MCP_RESULT_LIMIT")
    leads = []
    for entry in entries:
        if not isinstance(entry, dict): raise SearchMCPError("SEARCH_MCP_RESULT_UNSUPPORTED")
        try:
            uri, title, snippet = (_text(entry.get(fields[key]), maximum=8192 if key == "snippet" else 2048)
                                   for key in ("uri", "title", "snippet"))
            address = urlsplit(uri)
            if address.scheme not in {"http", "https"} or not address.hostname or address.username or address.password:
                raise ValueError
        except (ValueError, TypeError): raise SearchMCPError("SEARCH_MCP_RESULT_UNSUPPORTED") from None
        leads.append({"source_uri": uri, "title": title, "snippet": snippet, "original_document_read": False})
    output = {"schema_version": "eeas.search-leads.v1", "kind": "SEARCH_LEAD", "original_document_read": False,
              "provider_profile_sha256": profile.fingerprint, "upstream_tool": profile.upstream_tool, "leads": leads}
    if len(json.dumps(output).encode()) > profile.limits["max_result_bytes"]: raise SearchMCPError("SEARCH_MCP_RESULT_LIMIT")
    return output


async def call_search(profile, arguments, *, resource_budget=None, admit=None):
    """Only the approved MCP endpoint and approved tools/call are reachable."""
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from packages.adapters.mcp_resources import MCPSourceTransport

    expected = httpx2.URL(profile.url)
    maximum = profile.limits["max_result_bytes"] * 4 + 8192

    class BoundedStream(httpx2.AsyncByteStream):
        def __init__(self, stream): self.stream = stream
        async def __aiter__(self):
            size = 0
            async for chunk in self.stream:
                size += len(chunk)
                if size > maximum: raise SearchMCPError("SEARCH_MCP_TRANSPORT_LIMIT")
                yield chunk
        async def aclose(self): await self.stream.aclose()

    async def endpoint_only(request):
        if request.url != expected: raise SearchMCPError("SEARCH_MCP_ENDPOINT_CHANGED")

    async def bound_response(response):
        # httpx counts raw chunks before its content decoder. Negotiate identity
        # and reject a server which compresses anyway, before reading its body.
        encodings = response.headers.get_list("content-encoding")
        if encodings and (len(encodings) != 1 or encodings[0].strip().casefold() != "identity"):
            raise SearchMCPError("SEARCH_MCP_CONTENT_ENCODING_UNSUPPORTED")
        response.stream = BoundedStream(response.stream)

    async def exchange():
        transport=httpx2.AsyncHTTPTransport(trust_env=False,retries=0)
        if resource_budget is not None or admit is not None:
            transport=MCPSourceTransport(transport,maximum,resource_budget,admit)
        async with httpx2.AsyncClient(headers={**profile.headers, "Accept-Encoding": "identity"}, trust_env=False, follow_redirects=False,transport=transport,
                                      timeout=profile.limits["timeout_seconds"],
                                      event_hooks={"request": [endpoint_only], "response": [bound_response]}) as http:
            async with streamable_http_client(profile.url, http_client=http, max_sse_event_size=maximum) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=float(profile.limits["timeout_seconds"])) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    if profile.upstream_tool not in {item.name for item in tools.tools}:
                        raise SearchMCPError("SEARCH_MCP_UPSTREAM_TOOL_UNAVAILABLE")
                    result = await session.call_tool(profile.upstream_tool,
                                                     arguments={profile.query_argument: search_arguments(arguments)["query"]})
                    return _search_leads(result, profile)
    _install_parser_log_filter()
    token = _log_secrets.set(_secret_forms(profile.secrets))
    try:
        return await asyncio.wait_for(exchange(), timeout=profile.limits["timeout_seconds"])
    finally:
        _log_secrets.reset(token)


def native_search_server(handler):
    from claude_agent_sdk import tool, create_sdk_mcp_server
    definition = tool("web_search", "Return source URIs and search snippets only. Original pages are not read or fetched.",
                      SEARCH_ARGUMENT_SCHEMA, annotations={"readOnlyHint": True, "destructiveHint": False})(handler)
    return create_sdk_mcp_server("eeas_search", version="1.0.0", tools=[definition])

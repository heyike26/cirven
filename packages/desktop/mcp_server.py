"""Execution-bound stdio bridge using the installed official MCP SDK.

Only a short-lived execution token is inherited. No host secret, native handle,
provider key or caller-selected scope crosses this interface.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

import httpx
from mcp.server import MCPServer
from mcp.types import CallToolResult, ToolAnnotations


def create_server(url, token, execution_id, request_sha256):
    endpoint = urlsplit(url)
    if (endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or
            not endpoint.port or endpoint.path or endpoint.query or endpoint.fragment or
            endpoint.username or endpoint.password):
        raise ValueError("An execution-private loopback bridge is required")
    if not token or len(token) != 64 or len(request_sha256) != 64 or not execution_id:
        raise ValueError("Execution bridge binding is incomplete")
    server = MCPServer("eeas-desktop", version="1.0.0", instructions=(
        "Use only this execution's declared methods. Choose or combine routes according "
        "to reliability, steps, latency and context cost; no route has a fixed priority. "
        "Prefer qualified quiet background work. Never assume a background-launched "
        "process is a background operation or automatically change focus/global input. "
        "Observations are not facts or input permission. Never invent scope or handles."))
    headers = {"Authorization": "Bearer " + token, "X-EEAS-Execution": execution_id,
               "X-EEAS-Request-SHA256": request_sha256}

    def request(path, body):
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=120) as client:
            response = client.post(url + path, json=body, headers=headers)
        if response.status_code != 200 or len(response.content) > 3 * 1024 * 1024:
            raise ValueError("Execution bridge rejected the exact request")
        return response.json()

    @server.tool(name="methods", annotations=ToolAnnotations(readOnlyHint=True))
    def methods() -> dict:
        """List the actual execution-bound methods, coverage and route costs."""
        return request("/methods", {})

    @server.tool(name="invoke", annotations=ToolAnnotations(readOnlyHint=False))
    def invoke(method: str, arguments: dict) -> CallToolResult:
        """Invoke one declared method; the private host independently checks scope."""
        return CallToolResult.model_validate(request("/invoke", {"method": method, "arguments": arguments}))

    return server


def main():
    server = create_server(os.environ["EEAS_DESKTOP_BRIDGE_URL"],
        os.environ["EEAS_DESKTOP_BRIDGE_TOKEN"], os.environ["EEAS_DESKTOP_EXECUTION_ID"],
        os.environ["EEAS_DESKTOP_REQUEST_SHA256"])
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

"""Real official stdio MCP server; startup/call receipts contain actual PIDs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--site-packages", required=True)
parser.add_argument("--receipt-dir", required=True)
parser.add_argument("--server-id", required=True)
parser.add_argument("--tool-name", required=True)
args = parser.parse_args()
sys.path.insert(0, args.site_packages)

from mcp.server import MCPServer

receipts = Path(args.receipt_dir)
root = Path.cwd().resolve()
startup = {"pid": os.getpid(), "ppid": os.getppid(), "root": str(root),
           "server_id": args.server_id, "tool_name": args.tool_name}
(receipts / ("startup-" + str(os.getpid()) + ".json")).write_text(json.dumps(startup))
server = MCPServer(args.server_id)


@server.tool(name=args.tool_name)
def read_actual_lines(path: str) -> dict:
    """Read one actual UTF8 file in the admitted selected workspace."""
    actual = (root / path).resolve(strict=True)
    if not actual.is_relative_to(root) or not actual.is_file():
        raise ValueError("Select one actual in-folder file")
    raw = actual.read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError("Fixture read bound exceeded")
    value = {"server_id": args.server_id, "tool_name": args.tool_name,
             "sha256": hashlib.sha256(raw).hexdigest(), "lines": len(raw.decode("utf-8").splitlines())}
    with (receipts / "calls.jsonl").open("a") as stream:
        stream.write(json.dumps(value) + "\n")
    return value


if __name__ == "__main__":
    server.run(transport="stdio")

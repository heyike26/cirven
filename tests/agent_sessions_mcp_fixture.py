"""Actual official MCP stdio server with one scoped, deterministic file reader."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--site-packages", required=True)
parser.add_argument("--receipt-dir", required=True)
args = parser.parse_args()
sys.path.insert(0, args.site_packages)

from mcp.server import MCPServer

server = MCPServer("original-session-fixture")
root = Path.cwd().resolve()
receipts = Path(args.receipt_dir)


@server.tool()
def local_line_count(path: str) -> dict:
    """Read one real UTF-8 file inside this server's selected working folder."""
    actual = (root / path).resolve()
    if not actual.is_relative_to(root) or actual.is_symlink() or not actual.is_file():
        raise ValueError("Select one actual in-folder file")
    raw = actual.read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError("Selected file exceeds the fixture's read bound")
    value = {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "lines": len(raw.decode("utf-8").splitlines())}
    with (receipts / "calls.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value) + "\n")
    return value


if __name__ == "__main__":
    server.run(transport="stdio")

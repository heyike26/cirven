"""Official stdio MCP with independent process-start and real file-call receipts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument('--site-packages', required=True)
parser.add_argument('--receipt-dir', required=True)
parser.add_argument('--server-id', required=True)
parser.add_argument('--tool-name', required=True)
parser.add_argument('--fail-start', action='store_true')
args = parser.parse_args()
receipts = Path(args.receipt_dir)
root = Path.cwd().resolve()
startup = {'pid': os.getpid(), 'ppid': os.getppid(), 'root': str(root),
           'server_id': args.server_id, 'tool_name': args.tool_name,
           'started_at': time.time(), 'fail_start': args.fail_start,
           'python_dont_write_bytecode': sys.dont_write_bytecode, 'python_optimization': sys.flags.optimize}
with (receipts / ('startup-' + str(os.getpid()) + '.json')).open('x', encoding='utf-8') as stream:
    json.dump(startup, stream)
if args.fail_start:
    raise SystemExit(23)
sys.path.insert(0, args.site_packages)
from mcp.server import MCPServer

server = MCPServer(args.server_id)


@server.tool(name=args.tool_name)
def read_selected_file(path: str) -> dict:
    """Read exact real UTF8 bytes in this MCP process's admitted workspace."""
    target = (root / path).resolve(strict=True)
    if not target.is_relative_to(root) or not target.is_file() or target.is_symlink():
        raise ValueError('Only an actual selected-workspace file is allowed')
    raw = target.read_bytes()
    if len(raw) > 1024 * 1024:
        raise ValueError('Selected file exceeds fixture bound')
    result = {'server_id': args.server_id, 'tool_name': args.tool_name,
              'pid': os.getpid(), 'ppid': os.getppid(), 'sha256': hashlib.sha256(raw).hexdigest(),
              'path': path, 'lines': len(raw.decode('utf-8').splitlines())}
    with (receipts / 'calls.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(result) + '\n')
    return result


if __name__ == '__main__':
    server.run(transport='stdio')

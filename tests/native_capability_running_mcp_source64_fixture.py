"""Official stdio MCP with one durable effect before a bounded held call.

This is an external tool fixture, never an application/controller replacement.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--site-packages', required=True)
    parser.add_argument('--receipts', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--server-id', required=True)
    args = parser.parse_args()
    sys.path.insert(0, args.site_packages)
    import psutil
    from mcp.server import MCPServer
    directory, source = Path(args.receipts), Path(args.source)
    with (directory / ('startup-' + str(os.getpid()) + '.json')).open('x') as stream:
        json.dump({'pid': os.getpid(), 'ppid': os.getppid(), 'birth': psutil.Process().create_time(),
            'server_id': args.server_id, 'dont_write_bytecode': sys.dont_write_bytecode,
            'optimization': sys.flags.optimize}, stream)
    server = MCPServer(args.server_id)

    @server.tool()
    def read_receipt(label: str, hold: bool = False) -> dict:
        """Commit one audit effect, then hold this actual external tool call."""
        if label != 'running-stop' or hold is not True:
            raise ValueError('Only the one bounded running-stop fixture input is admitted')
        raw = source.read_bytes()
        if len(raw) > 256000:
            raise ValueError('Bounded source required')
        row = {'audit': 1, 'label': label, 'pid': os.getpid(), 'ppid': os.getppid(),
            'server_id': args.server_id, 'birth': psutil.Process().create_time(),
            'source_sha256': hashlib.sha256(raw).hexdigest(), 'source_bytes': len(raw),
            'effect_committed_at': time.time()}
        with (directory / 'calls.jsonl').open('a') as stream:
            stream.write(json.dumps(row) + '\n'); stream.flush(); os.fsync(stream.fileno())
        deadline = time.monotonic() + 120
        while not (directory / 'release-tool').exists() and time.monotonic() < deadline:
            time.sleep(.025)
        with (directory / 'completed.json').open('x') as stream:
            json.dump({'pid': os.getpid(), 'completed_at': time.time(),
                'released_by_fixture_cleanup': (directory / 'release-tool').exists(),
                'audit_effect_rolled_back': False}, stream)
        return row

    server.run(transport='stdio')


if __name__ == '__main__':
    main()

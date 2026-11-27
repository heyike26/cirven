"""Run original offline-copy acceptance in a Linux namespace without external routes."""
from __future__ import annotations
import argparse
import errno
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys

sys.dont_write_bytecode = True


def inside(args):
    subprocess.run(['ip', 'link', 'set', 'lo', 'up'], check=True)
    namespace = os.readlink('/proc/self/ns/net')
    links = json.loads(subprocess.check_output(['ip', '-j', 'link', 'show']))
    routes = json.loads(subprocess.check_output(['ip', '-j', 'route', 'show', 'table', 'all']))
    if namespace == args.parent_namespace or {item['ifname'] for item in links} != {'lo'} or any(item.get('dst') == 'default' for item in routes):
        raise ValueError('External interfaces/routes or unchanged namespace invalidate airgap proof')
    # This bypasses Python package sources and exercises actual OS networking.
    with socket.socket() as external:
        external.settimeout(0.5)
        error = external.connect_ex(('198.51.100.1', 443))  # RFC5737 documentation address.
    if error not in {errno.ENETUNREACH, errno.EHOSTUNREACH}:
        raise ValueError('OS did not confirm an unreachable external route')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        with socket.create_connection(listener.getsockname(), timeout=2):
            connection, _ = listener.accept()
            connection.close()
    evidence = Path(args.evidence).absolute()
    if not evidence.is_dir():
        raise ValueError('Use an existing original evidence directory')
    receipt = {'schema_version': 'eeas.airgap-network.v1', 'host': platform.system(),
               'parent_namespace': args.parent_namespace, 'actual_namespace': namespace,
               'links': links, 'routes': routes, 'external_connect_errno': error,
               'loopback_connection': 'PASS', 'network_scope': 'OS_NAMESPACE_NO_EXTERNAL_ROUTES',
               'code_tool_os_isolation': 'NOT_VALIDATED', 'native_windows': 'BLOCKED',
               'package_acceptance': 'NOT_RUN' if args.probe_only else 'See portable/result.json'}
    with (evidence / 'airgap-network.json').open('x', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    if args.probe_only:
        return 0
    from probe_bundle_copy import probe
    return probe(Path(args.archive), args.sha256, Path(args.destination).absolute(), evidence)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', nargs='?')
    parser.add_argument('--sha256')
    parser.add_argument('--destination')
    parser.add_argument('--evidence', default=os.environ.get('EEAS_EVIDENCE_DIR'))
    parser.add_argument('--probe-only', action='store_true')
    parser.add_argument('--parent-namespace', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.evidence or not args.probe_only and not all((args.archive, args.sha256, args.destination)):
        parser.error('Evidence is required; copy acceptance also requires archive, digest and fresh destination')
    if os.name != 'posix' or platform.system() != 'Linux' or not all(shutil.which(name) for name in ('unshare', 'ip')):
        print('Airgap namespace BLOCKED: use a qualified Linux host with unshare/ip', file=sys.stderr)
        return 3
    if args.parent_namespace:
        try:
            return inside(args)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            print('Airgap acceptance failed: ' + str(exc), file=sys.stderr)
            return 1
    command = ['unshare', '--user', '--map-root-user', '--net', sys.executable, '-B', str(Path(__file__).resolve()),
               *sys.argv[1:], '--parent-namespace', os.readlink('/proc/self/ns/net')]
    return subprocess.run(command, check=False).returncode


if __name__ == '__main__':
    raise SystemExit(main())

"""Bounded stdlib source-archive closure check. Default/help reads no inputs.

No extraction, script execution, dependency install, network or build qualification.
"""
import argparse
import base64
import fnmatch
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import stat
import struct
import tarfile
import unicodedata
import zipfile

PREFIX = 'Cirven-Source35-source/'
MAX_MEMBERS = 10000
MAX_MEMBER = 32 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
MAX_JSON = 4 * 1024 * 1024


def need(ok, why):
    if not ok:
        raise ValueError(why)


def clean(value, directory=False):
    need(isinstance(value, str) and value, 'Empty/non-string archive path')
    name = value[:-1] if directory and value.endswith('/') else value
    need(name and not name.startswith('/') and '\\' not in name and ':' not in name,
         'Absolute, Windows stream or backslash archive path')
    parts = name.split('/')
    need(all(p not in ('', '.', '..') and p == p.rstrip(' .')
             and not any(ord(c) < 32 for c in p) for p in parts), 'Unsafe path component')
    need(unicodedata.normalize('NFC', name) == name, 'Noncanonical Unicode path')
    need(all(not re.fullmatch(r'(?i)(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p)
             for p in parts), 'Windows reserved path component')
    return name


def unique_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, 'Duplicate JSON key')
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs)


def sha_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def path_tree(files, names):
    file_keys = {n.casefold() for n in files}
    spelling = {}
    for name in names:
        parts = name.split('/')
        for i in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:i])
            key = prefix.casefold()
            need(key not in spelling or spelling[key] == prefix, 'Case-colliding directory spelling')
            spelling[key] = prefix
            if i < len(parts):
                need(key not in file_keys, 'File used as parent directory')


class Archive:
    def __init__(self, path):
        self.path = Path(path)
        need(self.path.is_file() and not self.path.is_symlink(), 'Regular ZIP required')
        need(self.path.stat().st_size <= MAX_TOTAL, 'Compressed archive bound')
        # These source artifacts need neither multipart nor ZIP64. Check the EOCD
        # before ZipFile allocates one object per central-directory entry.
        with self.path.open('rb') as stream:
            stream.seek(max(0, self.path.stat().st_size - 65557))
            tail = stream.read(65557)
        end = tail.rfind(b'PK\x05\x06')
        need(end >= 0 and end + 22 <= len(tail), 'ZIP end record unavailable')
        record = struct.unpack_from('<4s4H2LH', tail, end)
        need(record[1] == record[2] == 0 and record[3] == record[4] < MAX_MEMBERS
             and record[4] != 65535 and record[5] <= MAX_MEMBERS * 1024
             and end + 22 + record[7] == len(tail), 'Multipart/ZIP64/central-directory bound')
        self.z = zipfile.ZipFile(self.path)
        self.files = {}
        self.all_names = {}
        total = 0
        try:
            infos = self.z.infolist()
            need(len(infos) <= MAX_MEMBERS, 'Archive member-count bound')
            for item in infos:
                directory = item.is_dir()
                name = clean(item.filename, directory)
                need(name.startswith(PREFIX.rstrip('/') + '/') or name == PREFIX.rstrip('/'),
                     'Member outside exact source prefix')
                folded = name.casefold()
                need(folded not in self.all_names, 'Duplicate/case-colliding ZIP path')
                self.all_names[folded] = name
                mode = stat.S_IFMT(item.external_attr >> 16)
                need(mode in (0, stat.S_IFDIR if directory else stat.S_IFREG),
                     'Symlink or nonregular ZIP member')
                need(not item.flag_bits & 1 and item.compress_type in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED),
                     'Encrypted/unsupported ZIP member')
                need(0 <= item.file_size <= MAX_MEMBER, 'Single ZIP member bound')
                total += item.file_size
                need(total <= MAX_TOTAL, 'ZIP expanded-size bound')
                if not directory:
                    self.files[name] = item
            self.total = total
            self._parents()
        except BaseException:
            self.z.close()
            raise

    def _parents(self):
        path_tree(self.files, self.all_names.values())

    def close(self):
        self.z.close()


class Merged:
    def __init__(self, base, supplement):
        self.archives = [base, supplement]
        need(base.total + supplement.total <= MAX_TOTAL, 'Merged expanded-size bound')
        need(len(base.files) + len(supplement.files) <= MAX_MEMBERS, 'Merged member-count bound')
        self.files = {}
        case = {}
        for archive in self.archives:
            for folded, name in archive.all_names.items():
                need(folded not in case, 'Supplement overwrites/case-collides with frozen source')
                case[folded] = name
            for name, info in archive.files.items():
                self.files[name] = (archive, info)
        path_tree(self.files, case.values())
        self.checked = {}

    def read(self, name, limit=MAX_MEMBER):
        name = clean(name)
        need(name in self.files, 'Required merged member missing: ' + name)
        archive, info = self.files[name]
        need(info.file_size <= limit, 'Read bound: ' + name)
        # zipfile verifies CRC after the actual complete member read.
        data = archive.z.read(info)
        need(len(data) == info.file_size, 'Short ZIP member')
        return data

    def record(self, relative, expected):
        name = PREFIX + clean(relative)
        actual = self.checked.get(relative)
        if actual is None:
            data = self.read(name)
            actual = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        need(type(expected['bytes']) is int and expected['bytes'] == actual['bytes']
             and expected['sha256'] == actual['sha256'], 'Manifest SHA/bytes mismatch: ' + relative)
        self.checked[relative] = actual

    def json(self, relative):
        return unique_json(self.read(PREFIX + clean(relative), MAX_JSON))


def relative(base, value):
    need(isinstance(value, str) and value and not value.startswith(('/', '\\'))
         and '\\' not in value and ':' not in value, 'Unsafe declared local target')
    return clean(posixpath.normpath(posixpath.join(base, value)))


def export_targets(package):
    result = []
    for key in ('main', 'module', 'types', 'typings'):
        if key in package:
            need(isinstance(package[key], str), 'Non-string package entry')
            result.append(package[key])
    def leaves(value):
        if value is None:
            return
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, list):
            for item in value:
                leaves(item)
        elif isinstance(value, dict):
            for item in value.values():
                leaves(item)
        else:
            raise ValueError('Unsupported package exports value')
    if 'exports' in package:
        leaves(package['exports'])
    return result


def check_exports(package, names, folder):
    paths = set(names)
    for target in export_targets(package):
        wanted = relative(folder, target)
        need(wanted.startswith(folder + '/'), 'Package entry escapes its own directory')
        if '*' in wanted:
            need(any(fnmatch.fnmatchcase(p, wanted) for p in paths), 'Package export pattern has no file: ' + wanted)
        else:
            need(wanted in paths, 'Package main/types/exports file missing: ' + wanted)


def tar_package(raw):
    result = {}
    seen = set()
    names = []
    total = 0
    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
        expanded = compressed.read(MAX_MEMBER + 1)
    need(len(expanded) <= MAX_MEMBER, 'Local tgz decompressed-byte bound')
    with tarfile.open(fileobj=io.BytesIO(expanded), mode='r:') as archive:
        for index, item in enumerate(archive):
            need(index < 3000, 'Local tgz member-count bound')
            name = clean(item.name, item.isdir())
            need(name == 'package' or name.startswith('package/'), 'Local tgz outside package/')
            need(name.casefold() not in seen, 'Duplicate/case-colliding local tgz path')
            seen.add(name.casefold())
            names.append(name)
            need(item.isdir() or item.isfile(), 'Local tgz link/nonregular member')
            total += item.size
            need(0 <= item.size <= MAX_MEMBER and total <= MAX_MEMBER, 'Local tgz size bound')
            if item.isfile():
                stream = archive.extractfile(item)
                need(stream is not None, 'Unreadable local tgz member')
                result[name] = stream.read(MAX_MEMBER + 1)
                need(len(result[name]) == item.size, 'Short local tgz member')
    path_tree(result, names)
    need('package/package.json' in result, 'Local tgz package.json missing')
    package = unique_json(result['package/package.json'])
    check_exports(package, result, 'package')
    return package


def row_map(rows):
    need(isinstance(rows, list) and len(rows) <= MAX_MEMBERS, 'Manifest rows bound/type')
    result = {}
    folded = set()
    for row in rows:
        name = clean(row['path'])
        need(name.casefold() not in folded, 'Manifest duplicate/case collision')
        need(type(row['bytes']) is int and 0 <= row['bytes'] <= MAX_MEMBER
             and isinstance(row['sha256'], str) and re.fullmatch('[a-f0-9]{64}', row['sha256']),
             'Manifest bytes/SHA type')
        folded.add(name.casefold())
        result[name] = {'bytes': row['bytes'], 'sha256': row['sha256']}
    return result


def verify_lock_and_scripts(merged, checks):
    pkg_path, lock_path = checks['frontend_package_json'], checks['frontend_lock']
    folder = posixpath.dirname(pkg_path)
    package, lock = merged.json(pkg_path), merged.json(lock_path)
    need(lock['lockfileVersion'] == 3 and isinstance(lock['packages'], dict), 'Expected actual npm lock v3')
    root = lock['packages']['']
    for scope in ('dependencies', 'devDependencies', 'optionalDependencies'):
        need(root.get(scope, {}) == package.get(scope, {}), 'Root package/lock dependency declaration differs')
    names = {name[len(PREFIX):] for name in merged.files}
    local = []
    for scope in ('dependencies', 'devDependencies', 'optionalDependencies'):
        for name, value in package.get(scope, {}).items():
            if not value.startswith('file:'):
                continue
            target = relative(folder, value[5:])
            resolved = lock['packages'].get('node_modules/' + name)
            need(isinstance(resolved, dict), 'Local dependency missing from lock')
            if target.endswith('.tgz'):
                raw = merged.read(PREFIX + target)
                local_pkg = tar_package(raw)
                need(resolved.get('resolved') == value, 'Local tgz lock reference differs')
                integrity = resolved.get('integrity')
                if integrity:
                    need(isinstance(integrity, str), 'Invalid local integrity')
                    candidates = []
                    for token in integrity.split():
                        algorithm, separator, expected = token.partition('-')
                        need(separator and algorithm in ('sha512', 'sha256', 'sha1'), 'Unsupported local integrity algorithm')
                        candidates.append(base64.b64encode(hashlib.new(algorithm, raw).digest()).decode() == expected)
                    need(any(candidates), 'Local tgz lock integrity differs')
            else:
                local_pkg = merged.json(target + '/package.json')
                check_exports(local_pkg, names, target)
                location = posixpath.relpath(target, folder)
                need(resolved.get('link') is True and resolved.get('resolved') == location,
                     'Local folder link lock reference differs')
                declared = lock['packages'].get(location)
                need(isinstance(declared, dict) and declared.get('name') == local_pkg.get('name')
                     and declared.get('version') == local_pkg.get('version'), 'Local folder lock identity differs')
            need(local_pkg.get('name') == name, 'Local dependency package name differs')
            if 'version' in resolved:
                need(resolved['version'] == local_pkg.get('version'), 'Local package/lock version differs')
            local.append({'name': name, 'target': target, 'exports_checked': len(export_targets(local_pkg))})
    need({x['name'] for x in local} == {'@opencode-ai/client', 'ghostty-web'} and len(local) == 2,
         'Expected exact two current file dependencies')
    scripts = []
    for text in package.get('scripts', {}).values():
        # Actual package uses node scripts/*.mjs, node --test, and tsc. Registry
        # executable resolution remains an npm/toolchain check outside this tool.
        for found in re.findall(r'(?:^|[;&|]\s*)node\s+(?:--[\w-]+\s+)*([^\s;&|]+\.(?:mjs|cjs|js))', text):
            target = relative(folder, found.strip('"\''))
            need(target in names or '*' in target and any(fnmatch.fnmatchcase(n, target) for n in names),
                 'Declared node script missing: ' + target)
            scripts.append(target)
    for name in checks['required_files']:
        name = clean(name)
        need(name in names, 'Required build input missing: ' + name)
        if name.endswith('.mjs'):
            text = merged.read(PREFIX + name, MAX_JSON).decode('utf8')
            for imported in re.findall(r'(?:from\s*|import\s*)[\'\"](\.[^\'\"]+)[\'\"]', text):
                need(relative(posixpath.dirname(name), imported) in names, 'Relative script import missing')
    for workspace in ('app', 'ui', 'session-ui', 'schema', 'core'):
        target = 'staging-native-ui/upstream/packages/' + workspace + '/package.json'
        pkg = merged.json(target)
        need(isinstance(pkg.get('name'), str), 'Workspace package identity missing')
    patch_folder = folder + '/patches'
    patch = merged.json(patch_folder + '/pierre-trees-manifest.json')
    data = merged.read(PREFIX + patch_folder + '/' + clean(patch['patch_file']))
    need(hashlib.sha256(data).hexdigest() == patch['patch_sha256']
         and patch['package'] == '@pierre/trees' and patch['version'] == package['dependencies']['@pierre/trees'],
         'Pinned patch/package SHA or version differs')
    apply = merged.read(PREFIX + folder + '/scripts/apply-upstream-patches.mjs', MAX_JSON).decode('utf8')
    need(patch['patch_sha256'] in apply and patch['source_commit'] in apply, 'Patch script provenance differs')
    return {'local_dependencies': local, 'node_scripts': scripts, 'workspace_packages': 5,
            'registry_dependencies': 'LOCK_DECLARATIONS_ONLY_NOT_FETCHED_OR_INSTALLED'}


def verify(plan, plan_sha, base_path, supplement_path, supplement_sha):
    need(plan['schema'] == 'cirven.source35.source-build-supplement-plan.v1'
         and plan['logical_prefix'] == PREFIX and plan['first_stage_complete'] is False, 'Wrong actual plan schema/scope')
    base_pin = plan['base_source_archive']
    need(Path(base_path).stat().st_size == base_pin['bytes'] and sha_file(base_path) == base_pin['sha256'],
         'Frozen original source archive bytes/SHA differs')
    need(re.fullmatch('[a-f0-9]{64}', supplement_sha) and sha_file(supplement_path) == supplement_sha,
         'Actual supplement archive SHA required')
    base, supplement = Archive(base_path), None
    try:
        supplement = Archive(supplement_path)
        merged = Merged(base, supplement)
        old = plan['base_frozen_source_manifest']
        raw = merged.read(old['member'], MAX_JSON)
        need(len(raw) == old['bytes'] and hashlib.sha256(raw).hexdigest() == old['sha256'], 'Frozen base manifest differs')
        frozen = unique_json(raw)
        need(isinstance(frozen['files'], dict) and frozen['count'] == len(frozen['files']), 'Actual frozen source rows differ')
        need(set(base.files) == {PREFIX + clean(n) for n in frozen['files']} | {old['member']}, 'Base source inventory differs')
        for name, expected in frozen['files'].items():
            merged.record(name, expected)
        missing, reused = row_map(plan['payload_missing_only']), row_map(plan['existing_base_reused'])
        need(not set(missing) & set(reused) and len(missing) == plan['supplement_payload_files']
             and len(reused) == plan['existing_base_reused_files'], 'Plan missing/reused count or overlap')
        member = plan['generated_manifest_member']
        need(set(supplement.files) == {PREFIX + name for name in missing} | {member}, 'Supplement exact member allowlist differs')
        manifest = unique_json(merged.read(member, MAX_JSON))
        need(manifest['schema'] == 'cirven.source35.source-build-supplement.v1'
             and manifest['status'] == 'STATIC_SOURCE_LOCK_LOCAL_FILE_INPUTS_PACKAGED_NO_NPM_BUILD'
             and manifest['plan_sha256'] == plan_sha and manifest['logical_prefix'] == PREFIX
             and manifest['first_stage_complete'] is False, 'Supplement manifest binding/scope differs')
        need(manifest['base_source_archive']['bytes'] == base_pin['bytes']
             and manifest['base_source_archive']['sha256'] == base_pin['sha256'], 'Supplement base archive identity differs')
        need(row_map(manifest['files']) == missing and row_map(manifest['base_reused']) == reused
             and manifest['checks'] == plan['checks']
             and manifest['Root_authorized_exceptions'] == plan['Root_authorized_exceptions'], 'Supplement manifest inventories/policy differs')
        for name, expected in {**reused, **missing}.items():
            merged.record(name, expected)
        need(sum(r['bytes'] for r in missing.values()) == plan['supplement_payload_bytes']
             and sum(r['bytes'] for r in reused.values()) == plan['existing_base_reused_bytes'], 'Plan payload byte counts differ')
        checks = plan['checks']
        inventories = [
            (checks['source_manifest_path'], 'files', 'staging-native-ui/upstream/', plan['upstream_manifest_files'], plan['upstream_manifest_bytes']),
            (checks['ghostty_manifest_path'], 'files', 'staging-native-ui/ghostty/upstream/', plan['ghostty_manifest_files'], plan['ghostty_manifest_bytes']),
            (checks['build_inputs_path'], 'source_files', '', plan['actual_renderer_build_inputs'], None)]
        counts = []
        for path, field, prefix, count, size in inventories:
            value = merged.json(path)
            rows = row_map(value[field])
            need(len(rows) == count and (size is None or sum(r['bytes'] for r in rows.values()) == size), 'Producer manifest row count/bytes differs')
            for name, expected in rows.items():
                merged.record(prefix + name, expected)
            counts.append({'manifest': path, 'rows': len(rows)})
        exceptions = plan['Root_authorized_exceptions']
        ghostty_dist = set(exceptions['ghostty_fixed_third_party_dist']['paths'])
        licenses = set(exceptions['license_text_paths_containing_node_modules'])
        for full in merged.files:
            name = full[len(PREFIX):]
            parts = name.split('/')
            need(not name.startswith(('runtime/', 'user-data/', '.venv/', 'apps/agent-shell/dist/')),
                 'Excluded runtime/private/application dist member')
            need('node_modules' not in parts or name in licenses, 'Actual node_modules tree is not source')
            if name.startswith('staging-native-ui/ghostty/upstream/dist/'):
                need(name in ghostty_dist, 'Unapproved third-party release input')
        dependency = verify_lock_and_scripts(merged, checks)
        return {'status': 'PASS_STATIC_MERGED_SOURCE_LOCK_LOCAL_FILE_INPUT_CLOSURE_ONLY',
                'plan_sha256': plan_sha, 'base_zip_sha256': base_pin['sha256'], 'supplement_zip_sha256': supplement_sha,
                'base_members': len(base.files), 'supplement_members': len(supplement.files),
                'expanded_bytes': base.total + supplement.total, 'manifest_payloads_hashed_and_CRC_read': len(merged.checked),
                'producer_manifests': counts, **dependency, 'extraction': 0, 'network': 0, 'npm': 0, 'build': 0,
                'App_API_GUI_model_actions': 0, 'execution_os_name': os.name, 'offline_build': 'NOT_VALIDATED',
                'WASM_source_rebuild': 'NOT_VALIDATED_EXISTING_PINNED_THIRD_PARTY_RELEASE_INPUT', 'first_stage_complete': False}
    finally:
        base.close()
        if supplement is not None:
            supplement.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true')
    for name in ('plan', 'plan-sha256', 'base-zip', 'supplement-zip', 'supplement-sha256', 'output'):
        parser.add_argument('--' + name)
    args = parser.parse_args()
    if not args.verify:
        parser.print_help()
        return 0
    need(all((args.plan, args.plan_sha256, args.base_zip, args.supplement_zip, args.supplement_sha256)), 'Explicit actual archive/plan pins required')
    plan_path = Path(args.plan)
    need(plan_path.is_file() and plan_path.stat().st_size <= MAX_JSON
         and re.fullmatch('[a-f0-9]{64}', args.plan_sha256) and sha_file(plan_path) == args.plan_sha256, 'Actual frozen plan SHA required')
    result = verify(unique_json(plan_path.read_bytes()), args.plan_sha256, args.base_zip, args.supplement_zip, args.supplement_sha256)
    raw = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        with Path(args.output).open('x', encoding='utf8') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    print(raw, end='')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

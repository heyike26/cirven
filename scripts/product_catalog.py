#!/usr/bin/env python3
"""Inspect development requirements; this command grants no runtime capability."""
import argparse
import json
from pathlib import Path
import re


class CatalogError(ValueError):
    pass


AREAS = {'intake', 'authoring', 'workspace', 'runtime', 'capabilities', 'archive', 'updates'}


def checked_file(root, relative):
    if not isinstance(relative, str) or not relative or '\\' in relative:
        raise CatalogError('Expected relative artifact path')
    path = Path(relative)
    if path.is_absolute() or any(part in {'.', '..'} for part in relative.split('/')):
        raise CatalogError('Artifact path must stay inside the project')
    full = root / path
    if full.is_symlink() or not full.is_file() or not full.resolve().is_relative_to(root.resolve()):
        raise CatalogError('Missing or escaped artifact: ' + relative)
    return full


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise CatalogError('Duplicate JSON key: ' + key)
        result[key] = value
    return result


def _constant(value):
    raise CatalogError('Nonfinite JSON value: ' + value)


def read_catalog(root, relative='docs/PRODUCT_CAPABILITIES.json'):
    root = Path(root)
    data = json.loads(checked_file(root, relative).read_text(encoding='utf-8'),
                      object_pairs_hook=_pairs, parse_constant=_constant)
    if not isinstance(data, dict) or data.get('kind') != 'DEVELOPMENT_SCOPE_NOT_RUNTIME_QUALIFICATION':
        raise CatalogError('Development scope must not claim runtime qualification')
    if data.get('schema_version') != 'eeas.product-requirements.v1':
        raise CatalogError('Unsupported scope schema')
    for field in ('user_max_children', 'tool_total_concurrency'):
        if type(data.get(field)) is not int or data[field] < 1:
            raise CatalogError('Concurrency values must be positive integers')
    records = data.get('capabilities')
    if not isinstance(records, list) or not records:
        raise CatalogError('Empty capability scope')
    by_id = {}
    for record in records:
        if not isinstance(record, dict):
            raise CatalogError('Invalid capability record')
        cid = record.get('id')
        if not isinstance(cid, str) or not re.fullmatch(r'[a-z]+\.[a-z][a-z0-9-]*', cid) or cid in by_id:
            raise CatalogError('Invalid or duplicate capability ID')
        area = record.get('area')
        if area not in AREAS or not cid.startswith(area + '.'):
            raise CatalogError('Capability area disagrees with ID')
        if not isinstance(record.get('title'), str) or not record['title'].strip():
            raise CatalogError('Missing user-facing purpose')
        if record.get('status') not in {'PLANNED', 'PARTIAL'}:
            raise CatalogError('Runtime capability qualification belongs to actual component receipts')
        checked_file(root, record.get('contract'))
        evidence = record.get('evidence')
        dependencies = record.get('depends_on')
        if not isinstance(evidence, list) or not isinstance(dependencies, list):
            raise CatalogError('References must be lists')
        if record['status'] == 'PARTIAL' and (not evidence or not record.get('scope_note')):
            raise CatalogError('Partial implementation requires evidence and scope')
        for ref in evidence:
            checked_file(root, ref)
        if not all(isinstance(dep, str) for dep in dependencies) or len(set(dependencies)) != len(dependencies):
            raise CatalogError('Invalid or duplicate dependency')
        checks = record.get('required_verification')
        if not isinstance(checks, list) or not checks or not all(isinstance(c, str) and c.strip() for c in checks):
            raise CatalogError('Missing executable verification targets')
        by_id[cid] = record
    if {record['area'] for record in records} != AREAS:
        raise CatalogError('Missing product area from the user scope')
    visiting, complete = set(), set()

    def visit(cid):
        if cid not in by_id:
            raise CatalogError('Unknown capability dependency: ' + cid)
        if cid in visiting:
            raise CatalogError('Cycle in capability development dependencies')
        if cid in complete:
            return
        visiting.add(cid)
        for dependency in by_id[cid]['depends_on']:
            visit(dependency)
        visiting.remove(cid)
        complete.add(cid)

    for cid in by_id:
        visit(cid)
    return data


def describe(data, area=None):
    if area is not None and area not in AREAS:
        raise CatalogError('Unknown product area')
    records = [record for record in data['capabilities'] if area is None or record['area'] == area]
    return {'kind': data['kind'], 'runtime_qualified': False,
            'effective_children': min(data['user_max_children'], data['tool_total_concurrency'] - 1),
            'user_max_children': data['user_max_children'], 'count': len(records), 'capabilities': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--area', choices=sorted(AREAS))
    parser.add_argument('--summary', action='store_true')
    args = parser.parse_args()
    try:
        result = describe(read_catalog(args.root), args.area)
        if args.summary:
            records = result.pop('capabilities')
            result['areas'] = {area: sum(record['area'] == area for record in records) for area in sorted(AREAS)}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, TypeError) as error:
        parser.exit(1, 'FAIL: ' + str(error) + '\n')


if __name__ == '__main__':
    raise SystemExit(main())

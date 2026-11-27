"""Read-only comparison of two persisted workflow artifacts."""
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical


def _rows(artifact):
    rows = artifact['items']
    indexed = {row['id']: row for row in rows}
    if len(indexed) != len(rows):
        raise Conflict('成果中的条目编号重复，无法对齐两个版本')
    return indexed


def _changed_fields(before, after):
    return [key for key in sorted(before.keys() | after.keys())
            if key not in before or key not in after or canonical(before[key]) != canonical(after[key])]


def _field_values(before, after, keys):
    def value(source, key):
        return {'present': True, 'value': source[key]} if key in source else {'present': False}
    return [{'field': key, 'before': value(before, key), 'after': value(after, key)} for key in keys]


def compare(before, after, *, group=None, row_id=None, offset=0):
    """Match keys by type and ID, including explicit null versus absent fields."""
    original, current = _rows(before), _rows(after)
    earlier_fields = {key: value for key, value in before.items() if key != 'items'}
    later_fields = {key: value for key, value in after.items() if key != 'items'}
    if group == 'artifact':
        return _field_values(earlier_fields, later_fields, _changed_fields(earlier_fields, later_fields))
    if group == 'row':
        if row_id not in original and row_id not in current:
            raise NotFound('找不到所选对比条目')
        prior, latest = original.get(row_id, {}), current.get(row_id, {})
        return _field_values(prior, latest, [f for f in _changed_fields(prior, latest) if f != 'id'])

    differences = []
    counts = {'added': 0, 'modified': 0, 'removed': 0}
    for ident in dict.fromkeys((*current, *original)):
        prior, latest = original.get(ident), current.get(ident)
        if prior is not None and latest is not None and canonical(prior) == canonical(latest):
            continue
        change = 'added' if prior is None else 'removed' if latest is None else 'modified'
        counts[change] += 1
        differences.append({'id': ident, 'title': (latest or prior)['title'], 'change': change})
    common = original.keys() & current.keys()
    reordered = [i for i in original if i in common] != [i for i in current if i in common]
    limit = 30
    return {'counts': counts, 'artifact_changes': len(_changed_fields(earlier_fields, later_fields)),
            'reordered': reordered, 'rows': differences[offset:offset+limit],
            'next_offset': offset+limit if len(differences)>offset+limit else None}

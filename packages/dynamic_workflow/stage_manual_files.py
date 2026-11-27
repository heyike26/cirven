"""Immutable files explicitly selected by the person recording a manual check."""
import base64
import binascii
import hashlib
import re

from packages.domain.store import Conflict, InvalidInput, NotFound


MAX_FILES = 3
MAX_OBSERVATION_BYTES = 1024**2
MAX_RUN_BYTES = 32 * 1024**2
MAX_STORE_BYTES = 256 * 1024**2
FILE_ID = re.compile(r'[0-9a-f]{32}')
UNSAFE_FILENAME = re.compile(r'[\\/:\x00-\x1f\x7f]')


def save_manual_files(db, run_id, files):
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise InvalidInput('每次实测最多附加三个文件')
    records, contents, ids = [], {}, set()
    total = 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {'id', 'name', 'content_base64'}:
            raise InvalidInput('实测附件字段无效')
        ident, name, encoded = item['id'], item['name'], item['content_base64']
        if not isinstance(ident, str) or not FILE_ID.fullmatch(ident) or ident in ids:
            raise InvalidInput('实测附件编号无效或重复')
        if (not isinstance(name, str) or len(name) > 255 or not name.strip() or name in ('.', '..')
                or UNSAFE_FILENAME.search(name)):
            raise InvalidInput('实测附件需要有效的原文件名')
        if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_OBSERVATION_BYTES + 2) // 3):
            raise InvalidInput('实测附件合计最多 1 MiB')
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise InvalidInput('实测附件内容无法读取') from None
        if not content:
            raise InvalidInput('实测附件不能为空')
        total += len(content)
        if total > MAX_OBSERVATION_BYTES:
            raise InvalidInput('实测附件合计最多 1 MiB')
        sha = hashlib.sha256(content).hexdigest()
        ids.add(ident)
        contents[sha] = content
        records.append({'id': ident, 'name': name, 'bytes': len(content), 'sha256': sha})
    if not contents:
        return records

    added = 0
    for sha, content in contents.items():
        if db.execute('INSERT OR IGNORE INTO stage_workflow_manual_files VALUES (?,?,?,?)',
                      (run_id, sha, len(content), content)).rowcount:
            added += len(content)
    if added:
        run_bytes, store_bytes = db.execute(
            'SELECT sum(CASE WHEN run_id=? THEN bytes ELSE 0 END),sum(bytes) '
            'FROM stage_workflow_manual_files', (run_id,)).fetchone()
        if run_bytes > MAX_RUN_BYTES or store_bytes > MAX_STORE_BYTES:
            raise Conflict('实测证据已达到工作流（32 MiB）或本机总存储（256 MiB）上限')
    return records


def read_manual_file(db, run_id, record):
    row = db.execute('SELECT content FROM stage_workflow_manual_files WHERE run_id=? AND sha256=?',
                     (run_id, record['sha256'])).fetchone()
    content = row[0] if row else b''
    if len(content) != record['bytes'] or hashlib.sha256(content).hexdigest() != record['sha256']:
        raise NotFound('找不到实测回执对应的原文件')
    return record['name'], content

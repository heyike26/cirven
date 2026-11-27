"""Explicit schema020 qualification; historical schema015 test remains unchanged."""
import hashlib
from pathlib import Path
import sqlite3

import pytest

from packages.domain.intake import IntakeInput, IntakeService
from packages.domain.store import Conflict, Store

ROOT = Path(__file__).resolve().parents[1]


def test_schema13_prefix_upgrades_to_complete020_preserving_bytes_and_intake(tmp_path, migrate_through):
    database = tmp_path / 'eeas.sqlite3'
    raw = b'REQ: original revision\r\nACCEPT: untouched bytes\r\n'
    stamp = '2026-10-13T00:00:00+00:00'
    legacy = sorted((ROOT/'packages/domain/migrations').glob('*.sql'))[:13]
    assert legacy[-1].name == '013_task_native_reviews.sql'
    with sqlite3.connect(database) as connection:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)')
        for path in legacy:
            connection.executescript(path.read_text())
            connection.execute('INSERT INTO schema_migrations VALUES (?)', (path.name,))
        connection.execute('INSERT INTO projects VALUES (?,?,?,?)', ('original-project', '原创旧项目', '', stamp))
        connection.execute('INSERT INTO documents VALUES (?,?,?,?,?,?)',
            ('original-document', 'original-project', 'original.md', 1, None, stamp))
        connection.execute('INSERT INTO document_versions VALUES (?,?,?,?,?,?,?)',
            ('original-document', 1, hashlib.sha256(raw).hexdigest(), raw, raw.decode(), 'original:legacy', stamp))
    migrate_through(database, 20, '020_planning_proposals.sql')
    store = Store(database)
    document = store.document('original-project', 'original-document', 1)
    assert document['raw_bytes'] == raw and document['sha256'] == hashlib.sha256(raw).hexdigest()
    assert store.document_assets('original-project', 'original-document', 1) == []
    assert store.document_chunks('original-project', 'original-document', 1)['chunks'][0]['content'] == raw.decode()
    with store.connect() as connection:
        applied = [r[0] for r in connection.execute('SELECT version FROM schema_migrations ORDER BY version')]
        expected = [p.name for p in sorted((ROOT/'packages/domain/migrations').glob('*.sql'))]
        assert applied == expected
    service = IntakeService(store, ROOT)
    result = service.save('original-project', IntakeInput(idempotency_key='legacy-prefix', materials=[{
        'document_id': 'original-document', 'revision': 1, 'sha256': document['sha256']}]))
    material = result['snapshot']['materials'][0]
    assert material['chunks'][0]['content'] == raw.decode()
    assert material['parse_manifest_sha256'] == store.document_receipt(
        'original-project', 'original-document', 1)['parse_manifest_sha256']
    reopened = Store(database)
    assert IntakeService(reopened, ROOT).get('original-project', result['id'])['sha256'] == result['sha256']
    assert reopened.document('original-project', 'original-document', 1)['raw_bytes'] == raw


"""Upgrade an actual pre-023 database without changing old project/feedback bytes."""
import hashlib
from pathlib import Path
import sqlite3

import pytest

from packages.domain.store import Store


def test_schema023_upgrade_preserves_old_feedback_and_is_repeatable(tmp_path):
    database = tmp_path / 'pre023.sqlite'
    migrations = Path(__file__).resolve().parents[1] / 'packages/domain/migrations'
    with sqlite3.connect(database) as c:
        c.execute('CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)')
        for migration in sorted(migrations.glob('*.sql')):
            if migration.name.startswith('023_'):
                break
            c.executescript(migration.read_text(encoding='utf-8'))
            c.execute('INSERT INTO schema_migrations VALUES (?)', (migration.name,))
        c.execute('INSERT INTO projects VALUES (?,?,?,?)', ('old-project', '原始项目', '旧版本保持', '2026-10-01T00:00:00Z'))
        raw = '{"id":"old-feedback","project_id":"old-project","comment":"原始人工意见😀"}'
        hashed = hashlib.sha256(raw.encode()).hexdigest()
        c.execute('INSERT INTO workbench_feedback VALUES (?,?,?,?,?)',
            ('old-feedback', 'old-project', raw, hashed, '2026-10-01T00:00:00Z'))
    Store(database)
    store = Store(database)
    with store.connect() as c:
        row = c.execute('SELECT * FROM workbench_feedback WHERE id=?', ('old-feedback',)).fetchone()
        if row['payload'] != raw or row['sha256'] != hashed:
            raise AssertionError('Schema upgrade changed historical feedback bytes')
        if c.execute('SELECT count(*) FROM schema_migrations WHERE version=?', ('023_feedback_rework.sql',)).fetchone()[0] != 1:
            raise AssertionError('Schema 023 was not applied once')
        if c.execute('SELECT description FROM projects WHERE id=?', ('old-project',)).fetchone()[0] != '旧版本保持':
            raise AssertionError('Schema upgrade changed the original project')
        c.execute('INSERT INTO feedback_rework_records VALUES (?,?,?,?,?,?)',
            ('new-record', 'old-project', 'FEEDBACK', '{}', '0' * 64, '2026-10-14T00:00:00Z'))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE feedback_rework_records SET payload='changed' WHERE id='new-record'")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM feedback_rework_records WHERE id='new-record'")
    print('SCHEMA023_CHECK_RECEIPT=UPGRADE_22_TO_23_OLD_FEEDBACK_BYTES_PRESERVED_REPEATABLE_IMMUTABLE_NEW_RECORDS')

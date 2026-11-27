import sqlite3

import pytest

from packages.domain.store import Conflict, Store


@pytest.mark.parametrize('change', ['future', 'missing_history'])
def test_incompatible_database_is_rejected_before_migration_or_project_changes(tmp_path, change):
    path = tmp_path / 'eeas.sqlite3'
    store = Store(path)
    project = store.create_project('original immutable history', 'original fixture')
    with store.connect() as connection:
        if change == 'future':
            connection.execute("INSERT INTO schema_migrations VALUES ('999_original_future.sql')")
        else:
            connection.execute("DELETE FROM schema_migrations WHERE version='002_documents.sql'")
        history = [row[0] for row in connection.execute('SELECT version FROM schema_migrations ORDER BY version')]
        objects = list(connection.execute('SELECT name,sql FROM sqlite_master ORDER BY name'))
        original = [tuple(row) for row in connection.execute('SELECT * FROM projects')]
    with pytest.raises(Conflict, match='incompatible'):
        Store(path)
    with sqlite3.connect(path) as connection:
        assert history == [row[0] for row in connection.execute('SELECT version FROM schema_migrations ORDER BY version')]
        assert [tuple(row) for row in objects] == list(connection.execute('SELECT name,sql FROM sqlite_master ORDER BY name'))
        assert original == list(connection.execute('SELECT * FROM projects'))
        assert original[0][0] == project['id']

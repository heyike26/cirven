"""Real SQLite metadata index; authored for concentrated debug, NOT_RUN.

Saved SDK event fixtures are not live SDK, model or native qualification.
"""
import json

import pytest

from packages.agent_sessions import message_locations as locations
from packages.domain.store import Store
from packages.domain.tasks import digest


@pytest.fixture
def journal(tmp_path):
    store = Store(tmp_path / 'locations.sqlite')
    return store, store.create_project('Original events', '')['id']


def event(seq, kind='message.part.updated', *, text='UNIQUE_ORIGINAL_TEXT', part='prt_one'):
    properties = {'sessionID': 'ses_original', 'messageID': 'msg_answer', 'partID': part}
    if kind == 'message.part.updated':
        properties['part'] = {'id': part, 'messageID': 'msg_answer', 'sessionID': 'ses_original',
            'type': 'text', 'text': text}
    return {'seq': seq, 'type': 'ORIGINAL_EVENT', 'original_native_replay': False,
        'payload': {'generation': 1, 'native_payload': {'type': kind, 'properties': properties}}}


def test_latest_metadata_survives_backward_backfill_without_storing_original_text(journal):
    store, pid = journal
    with store.connect() as db:
        assert locations.index_message_event(db, pid, 'session', event(4)) == 'INDEXED'
        assert locations.index_message_event(db, pid, 'session', event(2, text='OLDER')) == 'UNCHANGED'
        row = dict(db.execute('SELECT * FROM original_message_locations').fetchone())
        assert row['event_seq'] == 4 and row['event_sha256'] == digest(event(4))
        assert 'UNIQUE_ORIGINAL_TEXT' not in json.dumps(row)
        assert locations.index_message_event(db, pid, 'session', event(5, 'message.part.delta')) == 'INDEXED'
        assert db.execute('SELECT kind FROM original_message_locations').fetchone()[0] == 'DIRTY'
        assert locations.index_message_event(db, pid, 'session', event(6, 'message.part.removed')) == 'INDEXED'
        assert db.execute('SELECT kind FROM original_message_locations').fetchone()[0] == 'REMOVED'


def test_unindexable_first_record_and_collision_keep_sticky_coverage_gap(journal, monkeypatch):
    store, pid = journal
    monkeypatch.setattr(locations, 'MAX_SESSION_ROWS', 0)
    with store.connect() as db:
        assert locations.index_message_event(db, pid, 'session', event(3)) == 'CAPACITY_REACHED'
        assert db.execute('SELECT count(*) FROM original_message_locations').fetchone()[0] == 0
        assert db.execute('SELECT first_gap_seq FROM original_message_location_gaps').fetchone()[0] == 3
        monkeypatch.setattr(locations, 'MAX_SESSION_ROWS', 10)
        assert locations.index_message_event(db, pid, 'session', event(2)) == 'INDEXED'
        assert locations.index_message_event(db, pid, 'session', event(2, text='COLLIDING')) == 'COLLISION'
        assert db.execute('SELECT first_gap_seq FROM original_message_location_gaps').fetchone()[0] == 2
        assert db.execute('SELECT value_sha256 FROM original_message_locations').fetchone()[0] == digest(
            event(2)['payload']['native_payload']['properties']['part'])


def test_capacity_keeps_existing_updates_but_records_unrepresented_membership(journal, monkeypatch):
    store, pid = journal
    monkeypatch.setattr(locations, 'MAX_SESSION_ROWS', 1)
    with store.connect() as db:
        assert locations.index_message_event(db, pid, 'session', event(1)) == 'INDEXED'
        assert locations.index_message_event(db, pid, 'session', event(2, part='prt_two')) == 'CAPACITY_REACHED'
        assert locations.index_message_event(db, pid, 'session', event(3, text='CURRENT')) == 'INDEXED'
        assert db.execute('SELECT event_seq FROM original_message_locations').fetchone()[0] == 3
        assert db.execute('SELECT reason FROM original_message_location_gaps').fetchone()[0] == 'CAPACITY_REACHED'

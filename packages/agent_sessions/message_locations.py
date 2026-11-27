"""Metadata locations into already committed original SDK events. No text copy."""
import re
from packages.domain.tasks import digest

MAX_SESSION_ROWS = 8192
MAX_TOTAL_ROWS = 32768
MESSAGE = re.compile(r'^msg_[A-Za-z0-9_-]{1,100}$')
PART = re.compile(r'^prt_[A-Za-z0-9_-]{1,100}$')
SESSION = re.compile(r'^ses_[A-Za-z0-9_-]{1,100}$')


def record_message_gap(db, project_id, session_id, seq, reason):
    db.execute('INSERT INTO original_message_location_gaps VALUES (?,?,?,?) '
        'ON CONFLICT(project_id,session_id) DO UPDATE SET first_gap_seq=min(first_gap_seq,excluded.first_gap_seq)',
        (project_id,session_id,seq,reason))


def index_message_event(db, project_id, session_id, event):
    result = _index_message_event(db, project_id, session_id, event)
    if result in {'CAPACITY_REACHED','UNVERIFIABLE','COLLISION'}:
        # A gap is sticky evidence, not a reason to delete original records or
        # silently certify a whole message using only its remaining old parts.
        record_message_gap(db, project_id, session_id, event['seq'], result)
    return result


def _index_message_event(db, project_id, session_id, event):
    """Caller holds the original event writer lock/SQLite transaction.

    The event is authoritative. This derived index may report incomplete or
    capacity without interrupting an original model/tool operation.
    """
    if event.get('type') != 'ORIGINAL_EVENT':
        return 'NOT_APPLICABLE'
    payload = event.get('payload', {})
    native = payload.get('native_payload', {})
    kind, props = native.get('type'), native.get('properties', {})
    generation = payload.get('generation')
    if type(generation) is not int or generation < 1 or not isinstance(props, dict):
        return 'UNVERIFIABLE'
    info = props.get('info', {}) if kind == 'message.updated' else {}
    part = props.get('part', {}) if kind == 'message.part.updated' else {}
    if not isinstance(info, dict) or not isinstance(part, dict):
        return 'UNVERIFIABLE'
    sid = props.get('sessionID') or info.get('sessionID') or part.get('sessionID')
    mid = info.get('id') or part.get('messageID') or props.get('messageID')
    if not isinstance(sid, str) or not SESSION.fullmatch(sid) or not isinstance(mid, str) or not MESSAGE.fullmatch(mid):
        return 'NOT_APPLICABLE'
    slot, category, value_hash = None, None, None
    if kind == 'message.updated' and info.get('role') in {'user', 'assistant'}:
        slot, category, value_hash = '@info', 'INFO', digest(info)
    elif kind == 'message.removed':
        slot, category, value_hash = '@info', 'MESSAGE_REMOVED', digest(props)
    elif kind in {'message.part.updated', 'message.part.removed', 'message.part.delta'}:
        slot = part.get('id') or props.get('partID')
        if not isinstance(slot, str) or not PART.fullmatch(slot):
            return 'UNVERIFIABLE'
        if kind == 'message.part.updated':
            category = 'TEXT' if part.get('type') == 'text' and isinstance(part.get('text'), str) else 'OTHER'
            value_hash = digest(part)
        else:
            category, value_hash = ('REMOVED' if kind.endswith('removed') else 'DIRTY'), digest(props)
    else:
        return 'NOT_APPLICABLE'
    identity = (project_id, session_id, sid, mid, slot)
    previous = db.execute('SELECT event_seq,event_sha256 FROM original_message_locations '
        'WHERE project_id=? AND session_id=? AND native_session_id=? AND message_id=? AND slot_id=?', identity).fetchone()
    fingerprint = digest(event)
    if previous is not None and previous['event_seq'] >= event['seq']:
        return 'UNCHANGED' if previous['event_seq'] != event['seq'] or previous['event_sha256'] == fingerprint else 'COLLISION'
    if previous is None:
        session_count = db.execute('SELECT count(*) FROM original_message_locations WHERE project_id=? AND session_id=?',
            (project_id, session_id)).fetchone()[0]
        total = db.execute('SELECT count(*) FROM original_message_locations').fetchone()[0]
        if session_count >= MAX_SESSION_ROWS or total >= MAX_TOTAL_ROWS:
            return 'CAPACITY_REACHED'
    db.execute('INSERT INTO original_message_locations VALUES (?,?,?,?,?,?,?,?,?,?) '
        'ON CONFLICT(project_id,session_id,native_session_id,message_id,slot_id) DO UPDATE SET '
        'kind=excluded.kind,event_seq=excluded.event_seq,event_sha256=excluded.event_sha256,'
        'value_sha256=excluded.value_sha256,generation=excluded.generation',
        (*identity, category, event['seq'], fingerprint, value_hash, generation))
    return 'INDEXED'

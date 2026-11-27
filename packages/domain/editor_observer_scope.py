"""Shared, bounded, fail-closed IDE observation checks; never a native write grant."""
import json
import time

from packages.domain.store import Conflict
from packages.domain.tasks import digest

MAX_ACTIVE = 16
MAX_WINDOWS = 128
MAX_OBSERVATIONS = 8192
MAX_ACTIONS = MAX_OBSERVATIONS + MAX_WINDOWS  # Preserve explicit retirement capacity.
MAX_WINDOW_OBSERVATIONS = 2048
MAX_PAYLOAD = 65536
FRESH_SECONDS = 15
PERMISSION_FRESH_SECONDS = 5


def decode_observer(row):
    if len(row['payload_json'].encode()) > MAX_PAYLOAD:
        raise Conflict('EDITOR_OBSERVATION_INVALID: oversized original IDE observation')
    value = json.loads(row['payload_json'])
    if (digest(value) != row['payload_sha256'] or value.get('id') != row['id']
            or any(value.get(key) != row[key] for key in ('project_id','workspace_id','editor_client_id',
                'editor_window_id','workspace_root','phase','revision'))):
        raise Conflict('EDITOR_OBSERVATION_INVALID: original IDE observation index differs')
    return value


def relevant_observers(db, root, *, identity=None):
    from packages.domain.editor_handoff_scope import same_workspace
    rows = db.execute("SELECT * FROM agent_editor_observers WHERE phase='ACTIVE' ORDER BY id LIMIT 17").fetchall()
    if len(rows) > MAX_ACTIVE:
        raise Conflict('EDITOR_OBSERVATION_CAPACITY: active original IDE window bound exceeded')
    return [v for v in map(decode_observer, rows)
            if same_workspace(root, identity, v['workspace_root'], v['root_identity'])]


def assert_observers_clean(db, root, *, identity=None, permission_ref=None,target=None):
    values = relevant_observers(db, root, identity=identity)
    if not values:
        return
    runtime = db.execute('SELECT runtime_id FROM agent_editor_observer_runtime WHERE singleton=1').fetchone()
    count = db.execute('SELECT COUNT(*) FROM agent_editor_observer_actions').fetchone()[0]
    if count >= MAX_OBSERVATIONS:
        raise Conflict('EDITOR_OBSERVATION_CAPACITY: observation storage is full; preserve IDE state and resolve its guard')
    current = time.time();capture_checks={}
    for value in values:
        snapshot = value['observation']
        elapsed = current - value['observed_at_epoch']
        if (not runtime or value['runtime_id'] != runtime['runtime_id'] or snapshot['availability'] != 'OBSERVED' or snapshot['documents_complete'] is not True
                or elapsed < 0 or elapsed > (PERMISSION_FRESH_SECONDS if permission_ref else FRESH_SECONDS)):
            raise Conflict('EDITOR_OBSERVATION_REQUIRED: refresh the original IDE window; no clean state was inferred')
        if snapshot['root_identity'] != {key:str(v) for key,v in value['root_identity'].items()}:
            raise Conflict('EDITOR_OBSERVATION_INVALID: original directory identity changed')
        if any(document['is_dirty'] for document in snapshot['documents']):
            raise Conflict('DIRTY_EDITOR: preserve or save original IDE content before workspace writing')
        if permission_ref is not None and snapshot['permission_ref'] != permission_ref:
            from packages.domain.editor_capture_scope import capture_for_permission,capture_ack,observation_fingerprint
            fingerprint=observation_fingerprint(snapshot);matched=False
            references=set(value.get('capture_acks',{}))|{snapshot.get('capture_ref')}
            for reference in references:
                if not isinstance(reference,str):continue
                ack=capture_ack(value,reference,fingerprint=fingerprint)
                if ack is None or not 0<=int(current*1000)-ack<=PERMISSION_FRESH_SECONDS*1000:continue
                if reference not in capture_checks:capture_checks[reference]=capture_for_permission(db,reference,root,identity,permission_ref,target)
                if capture_checks[reference]:matched=True;break
            if not matched:
                raise Conflict('EDITOR_PERMISSION_OBSERVATION_REQUIRED: observe this exact original permission in every paired IDE window')
        rows = db.execute('SELECT COUNT(*) FROM agent_editor_observer_actions WHERE observer_id=?', (value['id'],)).fetchone()[0]
        if rows >= MAX_WINDOW_OBSERVATIONS:
            raise Conflict('EDITOR_OBSERVATION_CAPACITY: this original IDE window reached its observation bound')

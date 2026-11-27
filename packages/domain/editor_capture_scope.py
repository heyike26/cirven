"""Decode capture intents and bind purpose; no observation/write authority added."""
import json
import time

from packages.domain.store import Conflict
from packages.domain.tasks import digest
from packages.domain.editor_handoff_scope import same_workspace

MAX_COHORTS = 2048
MAX_ACTIVE_COHORTS = 8
MAX_CAPTURE_PAYLOAD = 49152
CAPTURE_LIFETIME_MS = 12000


def decode_capture(row):
    if len(row['payload_json'].encode()) > MAX_CAPTURE_PAYLOAD:
        raise Conflict('EDITOR_CAPTURE_INVALID: original capture metadata is oversized')
    value = json.loads(row['payload_json'])
    target_key='workspace_id' if 'workspace_id' in row.keys() else 'session_id'
    if (digest(value) != row['payload_sha256'] or row['capture_ref'] != row['payload_sha256']
            or any(value.get(key) != row[key] for key in ('id','project_id',target_key,'operation_id',
                'editor_client_id','request_sha256','expires_at_ms','runtime_id'))):
        raise Conflict('EDITOR_CAPTURE_INVALID: original capture index differs')
    if target_key=='workspace_id' and (value.get('target_kind')!='WORKSPACE_EDIT' or
            'session_ref' in value.get('scope',{}) or value.get('permission_ref') is not None):
        raise Conflict('EDITOR_CAPTURE_INVALID: workspace metadata cannot imply a session or permission')
    if not isinstance(value.get('members'),list) or len(value['members']) > 16:
        raise Conflict('EDITOR_CAPTURE_INVALID: original capture member bound differs')
    return value


def member_identity(value):
    return {key:value[key] for key in ('id','project_id','workspace_ref','editor_client_id',
        'editor_window_id','root_identity','process_identity')}


def observation_fingerprint(snapshot):
    return digest({key:value for key,value in snapshot.items() if key not in {'captured_at_epoch_ms','capture_ref','permission_ref'}})


def capture_ack(value,reference,*,fingerprint=None):
    current=fingerprint or observation_fingerprint(value['observation'])
    ack=value.get('capture_acks',{}).get(reference)
    if ack and ack.get('fingerprint')==current:return ack['captured_at_epoch_ms']
    if value['observation'].get('capture_ref')==reference:return value['observation']['captured_at_epoch_ms']
    return None


def capture_for_permission(db, capture_ref, root, identity, permission_ref, target=None):
    if not isinstance(capture_ref,str):return False
    row = db.execute('SELECT * FROM agent_editor_capture_cohorts WHERE capture_ref=?',(capture_ref,)).fetchone()
    if row is None:return False
    value = decode_capture(row)
    runtime=db.execute('SELECT runtime_id FROM agent_editor_observer_runtime WHERE singleton=1').fetchone()
    if not runtime or runtime['runtime_id']!=value['runtime_id']:return False
    current_ms = int(time.time()*1000)
    if (value['permission_ref'] != permission_ref or not value['created_at_ms'] <= current_ms <= value['expires_at_ms']
            or not same_workspace(root,identity,value['workspace_root'],value['root_identity'])
            or target is not None and (value['project_id'] != target['project_id'] or value['session_id'] != target['session_id'])):
        return False
    from packages.domain.editor_observer_scope import relevant_observers
    members = relevant_observers(db,root,identity=identity)
    return sorted((member_identity(v) for v in members),key=lambda v:v['id']) == value['members']

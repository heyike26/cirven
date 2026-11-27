"""Shared writer gate for original engines and explicit workspace edits."""
import json
import ntpath
import posixpath

from packages.domain.store import Conflict
from packages.domain.tasks import digest

ACTIVE=('RESERVING','READY','ARMED','BUFFER_CHANGED','UNKNOWN_OUTCOME')


def overlaps(a,b):
    windows=bool(ntpath.splitdrive(a)[0])
    if windows!=bool(ntpath.splitdrive(b)[0]):return False
    flavor=ntpath if windows else posixpath
    a,b=(flavor.normcase(flavor.normpath(x)) for x in (a,b))
    try:return flavor.commonpath((a,b)) in (a,b)
    except ValueError:return False


def same_workspace(a,identity_a,b,identity_b):
    return overlaps(a,b) or bool(identity_a and identity_b and identity_a==identity_b)


def assert_handoff_free(db,root,*,identity=None,except_id=None):
    rows=db.execute("SELECT id,workspace_root,payload_json,payload_sha256 FROM agent_editor_changes WHERE phase IN ('RESERVING','READY','ARMED','BUFFER_CHANGED','UNKNOWN_OUTCOME') ORDER BY id LIMIT 17").fetchall()
    if len(rows)>16:raise Conflict('Original editor handoff active bound exceeded')
    for row in rows:
        if len(row['payload_json'].encode())>32768:raise Conflict('Editor handoff scope metadata is oversized')
        payload=json.loads(row['payload_json'])
        if digest(payload)!=row['payload_sha256'] or payload.get('id')!=row['id'] or payload.get('workspace_root')!=row['workspace_root']:
            raise Conflict('Editor handoff scope proof changed')
        if row['id']!=except_id and same_workspace(root,identity,row['workspace_root'],payload.get('root_identity')):
            raise Conflict('EDITOR_CHANGE_PENDING: resolve the original editor handoff before workspace writing or execution')
    from packages.domain.editor_observer_scope import assert_observers_clean
    assert_observers_clean(db,root,identity=identity)

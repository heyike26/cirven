"""Read-only current-routing evidence. An old UNKNOWN result stays UNKNOWN."""
from copy import deepcopy
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import digest


def _configuration_disposition(c,row):
    proof=row.get('configuration_confirmation')
    if proof is None:return None
    cid=row['conversation_ref']['id'];head=c._owner_head(cid)
    if head is None or head['phase']!='BOUND' or head['request']==row['request']:return None
    scope=c.threads.view(cid);current=c.current_owner(scope)
    if current.get('status')!='CURRENT' or current['connection_operation_id']!=head['request']['operation_id']:
        raise Conflict('The replacement configuration routing evidence changed')
    replacement=c._read(c._path('connections',[head['request']['project_id'],head['request']['operation_id']]))
    replacement_proof=replacement.get('configuration_confirmation')
    value={'kind':'SUPERSEDED_CONFIGURATION',
        'original':{'operation_id':row['request']['operation_id'],'project_id':row['request']['project_id'],
            'request_sha256':row['request_sha256'],'confirmation_sha256':proof['confirmation_sha256']},
        'current':{'operation_id':replacement['request']['operation_id'],'project_id':replacement['request']['project_id'],
            'request_sha256':replacement['request_sha256'],'provider_generation':current['provider_generation'],
            'confirmation_sha256':replacement_proof['confirmation_sha256'] if replacement_proof else None},
        'current_scope':{'conversation_ref':deepcopy(scope['conversation_ref']),'project_id':scope['project_id']},
        'original_source':deepcopy(current['original_source']),'provider_profile_ref':deepcopy(current['provider_profile_ref']),
        'read_only':True,'owner_started':False,'model_calls':0,'permission_granted':False,'automatic_message_replay':False,
        'old_execution_outcome_changed':False}
    return {**value,'basis_sha256':digest(value)}


def configuration_disposition(c,row):
    # Missing/bad new-routing metadata cannot rewrite or hide the old receipt.
    # Startup admission independently fails closed on that same unavailable head.
    try:return _configuration_disposition(c,row)
    except (Conflict,NotFound,OSError,KeyError,TypeError):return None

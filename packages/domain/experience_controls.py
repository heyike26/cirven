"""Bounded negative pause overlay for an exact pending native metadata save.

No configuration copy or executor lives here. A retained monotonic revision in
the binding prevents pre-pause owners from becoming current after a resume.
"""
from packages.domain.store import Conflict
from packages.domain.tasks import digest


def base(service):return getattr(service,'service',service)
def path(service,kind,ident,*parts):return service._path('experience-controls',digest([kind,ident.casefold()]),*parts)


def head(service,kind,ident):
    service=base(service);target=path(service,kind,ident,'head.json')
    if not target.exists():return None
    value=service._read(target)
    if (not isinstance(value,dict) or set(value)!={'schema','native_kind','capability_id','revision','state','record','record_sha256','finalized'}
            or value['schema']!='eeas.experience-capability-control.v1' or value['native_kind']!=kind
            or not isinstance(value['capability_id'],str) or value['capability_id'].casefold()!=ident.casefold()
            or type(value['revision']) is not int or not 1<=value['revision']<=65
            or value['state'] not in {'ACTIVE','PAUSED'} or type(value['finalized']) is not bool
            or not isinstance(value['record'],dict) or digest(value['record'])!=value['record_sha256']):
        raise Conflict('Experience pause control is corrupt; capability use remains blocked')
    record=value['record'];request=record.get('request')
    if (not isinstance(request,dict) or record.get('request_sha256')!=digest(request)
            or record.get('revision')!=value['revision'] or record.get('state')!=value['state']
            or record.get('native_kind')!=kind or record.get('capability_id')!=value['capability_id']
            or record.get('operation_id')!=request.get('operation_id')
            or not isinstance(record.get('target_native_operation_id'),str)):
        raise Conflict('Experience pause control differs from its exact decision')
    if value['finalized']:
        saved=service._read(path(service,kind,ident,'records',digest(record['operation_id'])+'.json'))
        if saved!=record:raise Conflict('Experience pause history differs from its control')
    return value


def view(service,kind,ident):
    value=head(service,kind,ident)
    if value is None:return None
    record=value['record']
    return {'revision':value['revision'],'state':value['state'],
        'effective_state':'PAUSED' if value['state']=='PAUSED' or not value['finalized'] else 'ACTIVE',
        'record_sha256':value['record_sha256'],'finalized':value['finalized'],
        'target_native_operation_id':record['target_native_operation_id'],
        'operation_id':record['operation_id'],'model_calls':0,'processes_started':0}


def blocked(service,kind,ident):
    state=view(service,kind,ident)
    return state is not None and state['effective_state']=='PAUSED'

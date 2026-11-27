"""Native revision provenance sidecars, independent of SDK configuration wire."""
from copy import deepcopy
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import digest


def _base(service):
    # Existing selected-capability views delegate to their authoritative service.
    return getattr(service,'service',service)


def path(service,kind,ref):return service._path('experience-provenance',kind,ref['id'],f"{ref['revision']:010d}.json")


def read(service,kind,ref):
    target=path(service,kind,ref)
    if not target.exists():return None
    reader=getattr(service,'_read',None) or _base(service)._read
    value=reader(target)
    if (not isinstance(value,dict) or set(value)!={'schema','configuration_ref','source'}
            or value['schema']!='eeas.native-experience-provenance.v1' or value['configuration_ref']!=ref
            or not isinstance(value['source'],dict)
            or set(value['source'])!={'project_id','session_id','candidate_ref','activation_operation_id','activation_request_sha256'}):
        raise Conflict('Native experience provenance metadata differs')
    return value


def status(service,kind,ref):
    record=read(service,kind,ref)
    if record is None:return None
    callback=getattr(_base(service),'experience_source_reader',None)
    if not callable(callback):return {'availability':'SOURCE_COORDINATOR_UNAVAILABLE','forgotten':False}
    try:return callback(record['source'])
    except (Conflict,NotFound,OSError,ValueError,TypeError,KeyError):return {'availability':'SOURCE_METADATA_UNAVAILABLE','forgotten':False}


def write(service,kind,ref,source):
    target=path(service,kind,ref);old=read(service,kind,ref)
    if old is not None:
        if source is not None and old['source']!=source:raise Conflict('Original native experience provenance differs')
        return old
    if source is None:return None
    value={'schema':'eeas.native-experience-provenance.v1','configuration_ref':deepcopy(ref),'source':deepcopy(source)}
    service._write(target,value);return value


def semantic_configuration(configuration):return digest({k:v for k,v in configuration.items() if k!='enabled'})

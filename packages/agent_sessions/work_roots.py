"""Bounded metadata discovery of original work, including commandless messages.

This is a view of original operation receipts, not an archive, task or execution
loop. Opening a selected root still goes through the existing root authority.
"""
from datetime import datetime,timezone
import os
from pathlib import Path
import re
import stat
from pydantic import Field
from packages.domain.questions import RequestModel
from packages.domain.tasks import canonical,digest
from packages.domain.native_capabilities import _json
from packages.domain.context_read_budget import context_read_scope,source_regular
from packages.domain.store import Conflict
from packages.adapters.opencode_change_review import _regular
from .models import SessionRef,WorkspaceRef
from . import work_requirements as requirements
from .work_deliveries import _require_dispatch_ack
from .work_retests import resolved_command_roots


class WorkRootsQuery(RequestModel):
    session_ref:SessionRef
    generation:int=Field(ge=1,strict=True)
    workspace_ref:WorkspaceRef
    offset:int=Field(default=0,ge=0,le=1024,strict=True)


def inspect_work_roots(sessions,pid,sid,body):
    body=WorkRootsQuery.model_validate(body)
    with context_read_scope():return _inspect(sessions,pid,sid,body)


def _scope(sessions,pid,sid,body):
    manifest,reference=sessions._manifest(pid,sid);state=sessions._state(pid,sid)
    if (body.session_ref.model_dump()!=reference or body.session_ref.id!=sid
            or state['generation']!=body.generation
            or manifest['request'].get('workspace_ref')!=body.workspace_ref.model_dump()):
        raise Conflict('Original work catalog scope changed')
    return manifest


def _inspect(sessions,pid,sid,body):
    manifest=_scope(sessions,pid,sid,body);scope=body.model_dump(exclude={'offset'})
    reasons=set();candidates=[];scanned=used=unacknowledged=0
    if manifest.get('native_review_admission') or manifest.get('native_message_admission'):
        reasons.add('NATIVE_WORK_USES_EXISTING_OBJECT_CONTEXT_ENTRY')
    else:
        directory=sessions._path(pid,sid,'operations')
        try:
            if directory.is_symlink() or not directory.is_dir():raise OSError('Operation directory unavailable')
            with os.scandir(directory) as rows:
                for row in rows:
                    scanned+=1
                    if scanned>1024:reasons.add('OPERATION_SCAN_CAPACITY_REACHED');break
                    if not re.fullmatch('[a-f0-9]{64}\\.json',row.name):continue
                    try:
                        info=Path(row.path).lstat()
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:raise ValueError
                        if info.st_size>8*1024*1024-used:reasons.add('OPERATION_BYTE_CAPACITY_REACHED');break
                        raw=source_regular(_regular,Path(row.path),8*1024*1024-used);used+=len(raw)
                        wrapper=_json(raw)
                        if (not isinstance(wrapper,dict) or set(wrapper)!={'value','sha256'}
                                or not isinstance(wrapper['value'],dict) or digest(wrapper['value'])!=wrapper['sha256']):raise ValueError
                        op=wrapper['value']
                        if row.name!=digest(op.get('id'))+'.json':raise ValueError
                        if op.get('kind')!='MESSAGE':continue
                        if op.get('native_dispatch_acknowledged') is not True:unacknowledged+=1;continue
                        request=op.get('request',{})
                        if (not isinstance(request,dict) or op.get('request_sha256')!=digest(request)
                                or not isinstance(request.get('raw_text'),str)
                                or op.get('session_ref')!=scope['session_ref']
                                or request.get('session_ref')!=scope['session_ref']
                                or type(op.get('generation'))is not int or type(request.get('generation'))is not int
                                or op['generation']!=request.get('generation') or not 1<=op['generation']<=body.generation):raise ValueError
                        prepared=requirements.WorkRequirementScope.model_validate({**scope,'root_operation_id':op['id']})
                        created=datetime.fromisoformat(op['created_at'])
                        if created.tzinfo is None:raise ValueError
                        candidates.append((created.astimezone(timezone.utc),op['id'],prepared,op['request_sha256'],op))
                    except (Conflict,OSError,ValueError,TypeError,KeyError):reasons.add('ORIGINAL_OPERATION_UNAVAILABLE')
        except (Conflict,OSError):reasons.add('OPERATION_DIRECTORY_UNAVAILABLE')
    # Collapse only verified original retest/context provenance, never a title.
    try:parents=resolved_command_roots(sessions,pid,sid,[row[4] for row in candidates])
    except (Conflict,OSError,ValueError,TypeError,KeyError):parents={};reasons.add('ORIGINAL_OPERATION_UNAVAILABLE')
    normalized={};parent_cache={}
    for row in candidates:
        created,operation_id,query,checksum,_=row;target=parents.get(operation_id,operation_id)
        if target!=operation_id:
            try:
                if target not in parent_cache:
                    parent_query=requirements.WorkRequirementScope.model_validate({**scope,'root_operation_id':target})
                    parent_root=requirements._root(sessions,pid,sid,parent_query)
                    _require_dispatch_ack(sessions,pid,sid,parent_root)
                    parent_cache[target]=(parent_query,parent_root['basis']['request_sha256'])
                query,checksum=parent_cache[target];operation_id=target
            except (Conflict,OSError,ValueError,KeyError):reasons.add('ORIGINAL_OPERATION_UNAVAILABLE')
        previous=normalized.get(operation_id)
        if previous is None or previous[0]<created:normalized[operation_id]=(created,operation_id,query,checksum)
    candidates=sorted(normalized.values(),key=lambda row:(row[0],row[1]),reverse=True)
    entries=[]
    for _,operation_id,query,checksum in candidates[body.offset:body.offset+8]:
        try:
            root=requirements._root(sessions,pid,sid,query);_require_dispatch_ack(sessions,pid,sid,root)
            if root['basis']['request_sha256']!=checksum:raise Conflict('Original work changed')
            entries.append({'operation_id':operation_id,'root_sha256':root['sha256'],
                'root_basis':root['basis'],'label':root['label'],'status':root['status'],
                'dispatch_acknowledged':True,'whole_task_acceptance':'NOT_ASSESSED'})
        except (Conflict,OSError,ValueError,KeyError):reasons.add('SELECTED_ROOT_UNAVAILABLE')
    _scope(sessions,pid,sid,body)
    coverage={'complete':not reasons,'reason_codes':sorted(reasons),
        'scanned_entries':min(scanned,1024),'operation_bytes_read':used,
        'unacknowledged_messages':unacknowledged,'candidate_count':len(candidates),
        'scope':'ORDINARY_ACKNOWLEDGED_MESSAGE_METADATA_NOT_TASK_ACCEPTANCE'}
    value={'kind':'ORIGINAL_WORK_ROOTS','version':1,'project_id':pid,'scope':scope,
        'query_sha256':digest(body.model_dump()),'entries':entries,'coverage':coverage,
        'next_offset':body.offset+8 if body.offset+8<len(candidates) else None,
        'read_only':True,'metadata_only':True,'model_calls':0,'commands_started':0,
        'source_files_written':0,'permission_granted':False,'automatic_replay':False,
        'whole_task_acceptance':'NOT_ASSESSED'}
    if len(canonical(value).encode())>65536:raise Conflict('Original work catalog exceeds its metadata bound')
    return value

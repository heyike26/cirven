"""Explicit foreign-requirement reads for one acknowledged original work root.

This ledger authorizes selected immutable data only. It does not grant source
workspace access, change an original owner or dispatch a model/tool. Requirement
and case links keep their original receipts when a source decision is revoked.
"""
import json
from typing import Literal
from pydantic import Field,field_validator,model_validator
from packages.domain.questions import Id,Hash
from packages.domain.requirements import Key
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import canonical,digest,now
from .models import ConfigurationRef
from . import work_requirements as requirements

MAX_OPERATIONS=512
MAX_GRANTS=128


WorkRequirementSourceAuthority=requirements.WorkRequirementSourceAuthority


class WorkRequirementSourceBrowse(requirements.WorkRequirementScope):
    source_project_id:Id
    catalog_offset:int=Field(default=0,ge=0,le=1000000,strict=True)
    selected_requirement_ref:ConfigurationRef|None=None


class WorkRequirementSourceReview(requirements.WorkRequirementScope):
    root_sha256:Hash
    source_project_id:Id
    requirement_ref:ConfigurationRef
    requirement_ids:list[Key]=Field(min_length=1,max_length=100)
    acceptance_ids:list[Key]=Field(default_factory=list,max_length=100)

    @field_validator('requirement_ids','acceptance_ids')
    @classmethod
    def unique_ids(cls,value):
        if len(set(value))!=len(value):raise ValueError('Select unique source items')
        return value


class WorkRequirementSourceChange(WorkRequirementSourceReview):
    operation_id:Id
    expected_revision:int=Field(ge=0,le=MAX_OPERATIONS,strict=True)
    action:Literal['ALLOW','REVOKE']
    review_sha256:Hash|None=None

    @model_validator(mode='after')
    def exact_review(self):
        if (self.action=='ALLOW')!=(self.review_sha256 is not None):
            raise ValueError('Allow requires a reviewed basis; revoke retains the exact original source selection')
        return self


class WorkRequirementSourceOperation(requirements.WorkRequirementOperation):pass


def _grant_id(pid,sid,root,source_pid,requirement_id):
    return digest({'schema':'eeas.original-work-requirement-source.v1','project_id':pid,
        'session_id':sid,'root_operation_id':root,'source_project_id':source_pid,'requirement_id':requirement_id})


def _flags():
    return {'metadata_only':True,'model_calls':0,'commands_started':0,'source_files_written':0,
        'permission_granted':False,'source_workspace_authorized':False,'original_project_changed':False,
        'original_task_status_changed':False,'automatic_adoption':False,'whole_task_acceptance':'NOT_ASSESSED',
        'authority':'READONLY_SELECTED_REQUIREMENTS_FOR_ONE_ORIGINAL_ROOT'}


def _foreign(connection,pid,source_pid):
    if source_pid==pid:raise Conflict('Use ordinary requirement associations within the execution project')
    if connection.execute('SELECT 1 FROM projects WHERE id=?',(source_pid,)).fetchone() is None:
        raise NotFound('The selected original source project is unavailable')


def _operation(connection,pid,sid,operation_id):
    row=connection.execute('''SELECT root_operation_id,grant_id,revision,request_json,request_sha256,
        receipt_json,receipt_sha256 FROM original_work_requirement_source_operations
        WHERE project_id=? AND session_id=? AND operation_id=?''',(pid,sid,operation_id)).fetchone()
    if row is None:raise NotFound('The original requirement-source decision was not recorded')
    try:
        request=json.loads(row['request_json']);body=WorkRequirementSourceChange.model_validate(request)
        value=json.loads(row['receipt_json'])
        if not isinstance(value,dict):raise ValueError
    except (ValueError,TypeError) as error:raise Conflict('The original source decision cannot be decoded') from error
    expected={key:request[key] for key in ['root_operation_id','root_sha256','operation_id',
        'action','requirement_ref','requirement_ids','acceptance_ids','source_project_id','review_sha256']}
    if (body.model_dump()!=request or body.session_ref.id!=sid or body.operation_id!=operation_id
            or body.source_project_id==pid or digest(request)!=row['request_sha256']
            or digest(value)!=row['receipt_sha256'] or value.get('kind')!='ORIGINAL_WORK_REQUIREMENT_SOURCE_OPERATION'
            or value.get('version')!=1 or value.get('project_id')!=pid or value.get('session_ref')!=request['session_ref']
            or value.get('request_sha256')!=row['request_sha256'] or value.get('revision')!=row['revision']
            or value.get('revision')!=body.expected_revision+1 or body.root_operation_id!=row['root_operation_id']
            or row['grant_id']!=_grant_id(pid,sid,body.root_operation_id,body.source_project_id,body.requirement_ref.id)
            or value.get('grant_id')!=row['grant_id'] or any(value.get(k)!=v for k,v in expected.items())
            or any(value.get(k)!=v for k,v in _flags().items())):
        raise Conflict('The original requirement-source decision integrity differs')
    if (value.get('state')!=('ACTIVE' if body.action=='ALLOW' else 'REVOKED')
            or (body.action=='ALLOW' and not isinstance(value.get('selection_sha256'),str))
            or body.action=='REVOKE' and value.get('selection_sha256') is not None):
        raise Conflict('The saved source authorization state differs')
    return value


def _head(connection,pid,sid,root,grant_id):
    row=connection.execute('''SELECT root_sha256,revision,operation_id FROM original_work_requirement_source_heads
        WHERE project_id=? AND session_id=? AND root_operation_id=? AND grant_id=?''',
        (pid,sid,root,grant_id)).fetchone()
    if row is None:return None
    value=_operation(connection,pid,sid,row['operation_id'])
    if (value['root_operation_id']!=root or value['root_sha256']!=row['root_sha256']
            or value['revision']!=row['revision'] or value['grant_id']!=grant_id):
        raise Conflict('The original source authorization head differs')
    return value


def requirement_project(connection,pid,link,*,bundles=None):
    """Validate exact active data authority; never infer foreign access from IDs."""
    reference=link.get('source_authority')
    if reference is None:return pid
    authority=WorkRequirementSourceAuthority.model_validate(reference)
    _foreign(connection,pid,authority.source_project_id)
    current=_head(connection,pid,link['session_ref']['id'],link['root_operation_id'],authority.grant_id)
    if (current is None or current['state']!='ACTIVE' or current['revision']!=authority.revision
            or digest(current)!=authority.receipt_sha256 or current['source_project_id']!=authority.source_project_id
            or current['session_ref']!=link['session_ref'] or current['root_sha256']!=link['root_sha256']
            or current['requirement_ref']!=link['requirement_ref']
            or set(link['requirement_ids'])-set(current['requirement_ids'])
            or set(link['acceptance_ids'])-set(current['acceptance_ids'])):
        raise Conflict('The exact selected foreign requirement authorization changed or was revoked')
    # Re-check the immutable allowed selection as well as the grant reference.
    bundle=requirements._bundle(connection,authority.source_project_id,current['requirement_ref'],cache=bundles)
    selected=requirements._selection(bundle,current['requirement_ids'],current['acceptance_ids'])
    if digest(selected)!=current['selection_sha256']:raise Conflict('The allowed source selection differs')
    return authority.source_project_id


def authorized_selection(connection,pid,sid,root,reference,authority):
    authority=WorkRequirementSourceAuthority.model_validate(authority)
    grant=_head(connection,pid,sid,root['basis']['root_operation_id'],authority.grant_id)
    if grant is None:raise Conflict('The selected source authorization is unavailable')
    selection={'project_id':pid,'session_ref':root['basis']['session_ref'],
        'root_operation_id':root['basis']['root_operation_id'],'root_sha256':root['sha256'],
        'requirement_ref':reference,'requirement_ids':grant['requirement_ids'],
        'acceptance_ids':grant['acceptance_ids'],'source_authority':authority.model_dump()}
    requirement_project(connection,pid,selection)
    return selection


def _review(connection,pid,sid,body,root):
    _foreign(connection,pid,body.source_project_id)
    if root['sha256']!=body.root_sha256:raise Conflict('Review the exact acknowledged original work root')
    reference=body.requirement_ref.model_dump();bundle=requirements._bundle(connection,body.source_project_id,reference)
    if bundle['current_revision']!=reference['revision'] or not bundle['payload']['confirmed']:
        raise Conflict('Review the current human-confirmed source requirements')
    selected=requirements._selection(bundle,body.requirement_ids,body.acceptance_ids)
    sources=requirements._source_status(connection,body.source_project_id,selected['requirements'],{})
    if any(row['status'] in {'UNAVAILABLE','SUPERSEDED'} for row in sources):
        raise Conflict('The selected original source documents need review')
    grant_id=_grant_id(pid,sid,body.root_operation_id,body.source_project_id,reference['id'])
    prior=_head(connection,pid,sid,body.root_operation_id,grant_id)
    if prior is not None and prior['root_sha256']!=root['sha256']:
        raise Conflict('The previous source decision belongs to another original work identity')
    basis={'schema':'eeas.original-work-requirement-source-review.v1','project_id':pid,
        'scope':{k:body.model_dump()[k] for k in ['session_ref','generation','workspace_ref','root_operation_id']},
        'root_sha256':root['sha256'],'source_project_id':body.source_project_id,'requirement_ref':reference,
        'requirement_ids':body.requirement_ids,'acceptance_ids':body.acceptance_ids,
        'selection_sha256':digest(selected),'grant_id':grant_id,'expected_revision':prior['revision'] if prior else 0,
        'source_statuses':sources,'authority':_flags()['authority']}
    return basis,selected,bundle


def browse_work_requirement_sources(sessions,pid,sid,body):
    body=WorkRequirementSourceBrowse.model_validate(body);root=requirements._root(sessions,pid,sid,body)
    with sessions.store.connect() as connection:
        connection.execute('BEGIN');_foreign(connection,pid,body.source_project_id)
        catalog=requirements._catalog(connection,body.source_project_id,body.catalog_offset)
        catalog['scope']='EXPLICIT_OTHER_PROJECT_READONLY_PREVIEW';selected=None
        if body.selected_requirement_ref:
            bundle=requirements._bundle(connection,body.source_project_id,body.selected_requirement_ref.model_dump())
            # Only one explicitly selected bounded immutable bundle, no history scan.
            selected={'ref':bundle['ref'],'current_revision':bundle['current_revision'],'payload':bundle['payload'],
                'sources':requirements._source_status(connection,body.source_project_id,bundle['payload']['requirements'],{})}
        rows=connection.execute('''SELECT grant_id,operation_id FROM original_work_requirement_source_heads
            WHERE project_id=? AND session_id=? AND root_operation_id=? ORDER BY grant_id LIMIT 9 OFFSET ?''',
            (pid,sid,body.root_operation_id,body.catalog_offset)).fetchall()
        grants=[_operation(connection,pid,sid,row['operation_id']) for row in rows[:8]]
        if any(row['root_sha256']!=root['sha256'] for row in grants):
            raise Conflict('Saved source decisions belong to another original root identity')
        requirements._unchanged(sessions,pid,sid,body,root)
    return requirements._bounded({'kind':'ORIGINAL_WORK_REQUIREMENT_SOURCE_CATALOG','version':1,
        'project_id':pid,'scope':{k:body.model_dump()[k] for k in ['session_ref','generation','workspace_ref','root_operation_id']},
        'source_project_id':body.source_project_id,'root_sha256':root['sha256'],'query_sha256':digest(body.model_dump()),
        'catalog':catalog,'selected':selected,'grants':{'entries':grants,'offset':body.catalog_offset,'limit':8,
            'next_offset':body.catalog_offset+8 if len(rows)>8 else None,'scope':'THIS_ORIGINAL_ROOT_ALL_SOURCE_PROJECTS'},
        'read_only':True,'grant_recorded':False,**_flags()})


def review_work_requirement_source(sessions,pid,sid,body):
    body=WorkRequirementSourceReview.model_validate(body);root=requirements._root(sessions,pid,sid,body)
    with sessions.store.connect() as connection:
        connection.execute('BEGIN');basis,selected,bundle=_review(connection,pid,sid,body,root)
        current=_head(connection,pid,sid,body.root_operation_id,basis['grant_id'])
        requirements._unchanged(sessions,pid,sid,body,root)
    return requirements._bounded({'kind':'ORIGINAL_WORK_REQUIREMENT_SOURCE_REVIEW','version':1,'basis':basis,
        'review_sha256':digest(basis),'query_sha256':digest(body.model_dump()),'title':bundle['payload']['title'],
        'origin':bundle['payload']['origin'],'selected':selected,'current_grant':current,
        'read_only':True,'grant_recorded':False,**_flags()})


def change_work_requirement_source(sessions,pid,sid,body):
    body=WorkRequirementSourceChange.model_validate(body);request=requirements._bounded(body.model_dump(),32768)
    root=requirements._root(sessions,pid,sid,body)
    if body.root_sha256!=root['sha256']:raise Conflict('Confirm the exact original work source decision')
    with sessions.store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        previous=connection.execute('''SELECT request_sha256 FROM original_work_requirement_source_operations
            WHERE project_id=? AND session_id=? AND operation_id=?''',(pid,sid,body.operation_id)).fetchone()
        if previous is not None:
            if previous['request_sha256']!=digest(request):raise Conflict('IDEMPOTENCY_CONFLICT: source decision input changed')
            requirements._unchanged(sessions,pid,sid,body,root);return _operation(connection,pid,sid,body.operation_id)
        grant_id=_grant_id(pid,sid,body.root_operation_id,body.source_project_id,body.requirement_ref.id)
        current=_head(connection,pid,sid,body.root_operation_id,grant_id)
        if (current['revision'] if current else 0)!=body.expected_revision:
            raise Conflict('The original source decision changed; review the current authorization')
        counts=connection.execute('''SELECT count(*) FROM original_work_requirement_source_operations
            WHERE project_id=? AND session_id=?''',(pid,sid)).fetchone()[0]
        heads=connection.execute('''SELECT count(*) FROM original_work_requirement_source_heads
            WHERE project_id=? AND session_id=?''',(pid,sid)).fetchone()[0]
        if counts>=MAX_OPERATIONS or current is None and heads>=MAX_GRANTS:
            raise Conflict('Original-work source decision capacity reached')
        selection_sha=None
        if body.action=='ALLOW':
            basis,_,_=_review(connection,pid,sid,body,root)
            if digest(basis)!=body.review_sha256:raise Conflict('The reviewed source selection changed; review it again')
            selection_sha=basis['selection_sha256']
        elif (current is None or current['state']!='ACTIVE' or current['root_sha256']!=root['sha256']
                or current['requirement_ref']!=body.requirement_ref.model_dump()
                or current['requirement_ids']!=body.requirement_ids or current['acceptance_ids']!=body.acceptance_ids):
            raise Conflict('Revoke only the exact saved original source selection')
        requirements._unchanged(sessions,pid,sid,body,root)
        receipt=requirements._bounded({'kind':'ORIGINAL_WORK_REQUIREMENT_SOURCE_OPERATION','version':1,
            'project_id':pid,'session_ref':body.session_ref.model_dump(),'root_operation_id':body.root_operation_id,
            'root_sha256':root['sha256'],'operation_id':body.operation_id,'request_sha256':digest(request),
            'grant_id':grant_id,'revision':body.expected_revision+1,'source_project_id':body.source_project_id,
            'requirement_ref':body.requirement_ref.model_dump(),'requirement_ids':body.requirement_ids,
            'acceptance_ids':body.acceptance_ids,'selection_sha256':selection_sha,'action':body.action,
            'state':'ACTIVE' if body.action=='ALLOW' else 'REVOKED','review_sha256':body.review_sha256,
            'recorded_at':now(),**_flags()},32768)
        connection.execute('''INSERT INTO original_work_requirement_source_operations VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (pid,sid,body.operation_id,body.root_operation_id,grant_id,body.expected_revision+1,
                canonical(request),digest(request),canonical(receipt),digest(receipt)))
        connection.execute('''INSERT INTO original_work_requirement_source_heads VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(project_id,session_id,root_operation_id,grant_id) DO UPDATE SET
            root_sha256=excluded.root_sha256,revision=excluded.revision,operation_id=excluded.operation_id''',
            (pid,sid,body.root_operation_id,grant_id,root['sha256'],body.expected_revision+1,body.operation_id))
    return receipt


def inspect_work_requirement_source_operation(sessions,pid,sid,body):
    body=WorkRequirementSourceOperation.model_validate(body);root=requirements._root(sessions,pid,sid,body)
    with sessions.store.connect() as connection:
        receipt=_operation(connection,pid,sid,body.operation_id)
        if (receipt['request_sha256']!=body.request_sha256 or receipt['root_sha256']!=root['sha256']
                or receipt['root_operation_id']!=body.root_operation_id or receipt['session_ref']!=body.session_ref.model_dump()):
            raise Conflict('Inspect the exact original requirement-source decision')
        requirements._unchanged(sessions,pid,sid,body,root);return requirements._bounded(receipt,32768)

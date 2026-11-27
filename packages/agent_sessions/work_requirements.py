"""Reviewed requirements on an existing original root operation.

No legacy TaskSpec runner, SDK, model, workspace file body or report verdict is
used. Requirement versions remain the sole specification authority. The SQL
ledger stores references/selected IDs and immutable metadata receipts only.
"""
from copy import deepcopy
import hashlib
import json
import re
from typing import Literal
from pydantic import Field,model_validator,field_validator,model_serializer
from packages.domain.questions import RequestModel,Id,Hash
from packages.domain.requirements import RequirementBundle,Key
from packages.domain.store import Conflict,InvalidInput,NotFound
from packages.domain.tasks import canonical,digest,now
from .models import SessionRef,WorkspaceRef,ConfigurationRef

MAX_BUNDLE_BYTES=1024*1024
MAX_RESPONSE_BYTES=1024*1024
MAX_OPERATIONS=512
MAX_ROOTS=128
MAX_LINKS=10
CATALOG_PAGE=8
_NATIVE=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}\Z')


class WorkRequirementScope(RequestModel):
    session_ref:SessionRef
    generation:int=Field(ge=1,strict=True)
    workspace_ref:WorkspaceRef
    root_operation_id:Id


class WorkRequirementSourceAuthority(RequestModel):
    source_project_id:Id
    grant_id:Hash
    revision:int=Field(ge=1,le=MAX_OPERATIONS,strict=True)
    receipt_sha256:Hash


class WorkRequirementInspect(WorkRequirementScope):
    include_catalog:bool=Field(default=True,strict=True)
    catalog_offset:int=Field(default=0,ge=0,le=1000000,strict=True)
    selected_requirement_ref:ConfigurationRef|None=None
    detail_kind:Literal['requirement','acceptance']|None=None
    detail_id:Key|None=None
    selected_source_authority:WorkRequirementSourceAuthority|None=None

    @model_serializer(mode='wrap')
    def preserve_original_query(self,handler):
        value=handler(self)
        if self.selected_source_authority is None:value.pop('selected_source_authority',None)
        return value

    @model_validator(mode='after')
    def paired_detail(self):
        if (self.detail_kind is None)!=(self.detail_id is None):
            raise ValueError('An exact detail kind and item are required together')
        if self.selected_source_authority is not None and self.selected_requirement_ref is None:
            raise ValueError('Select an exact requirement version with its source authority')
        return self


class WorkRequirementLink(WorkRequirementScope):
    operation_id:Id
    root_sha256:Hash
    expected_revision:int=Field(ge=0,le=MAX_OPERATIONS,strict=True)
    action:Literal['ASSOCIATE','REMOVE']
    requirement_ref:ConfigurationRef
    requirement_ids:list[Key]=Field(default_factory=list,max_length=100)
    acceptance_ids:list[Key]=Field(default_factory=list,max_length=100)
    source_authority:WorkRequirementSourceAuthority|None=None

    @model_serializer(mode='wrap')
    def preserve_original_request(self,handler):
        value=handler(self)
        if self.source_authority is None:value.pop('source_authority',None)
        return value

    @field_validator('requirement_ids','acceptance_ids')
    @classmethod
    def unique_ids(cls,value):
        if len(set(value))!=len(value):raise ValueError('Selected IDs must be unique')
        return value

    @model_validator(mode='after')
    def action_scope(self):
        if self.action=='ASSOCIATE' and not self.requirement_ids:
            raise ValueError('Select at least one requirement')
        if self.action=='REMOVE' and (self.requirement_ids or self.acceptance_ids):
            raise ValueError('Removing a link does not propose new selected items')
        return self


class WorkRequirementOperation(WorkRequirementScope):
    operation_id:Id
    request_sha256:Hash


def _bounded(value,maximum=MAX_RESPONSE_BYTES):
    if len(canonical(value).encode())>maximum:raise Conflict('Original work metadata exceeds its byte bound')
    return value


def _root(sessions,pid,sid,body):
    manifest,reference=sessions._manifest(pid,sid)
    state=sessions._state(pid,sid)
    if (body.session_ref.model_dump()!=reference or body.session_ref.id!=sid
            or state['generation']!=body.generation
            or manifest['request'].get('workspace_ref')!=body.workspace_ref.model_dump()):
        raise Conflict('Original work scope changed')
    op=sessions.operation(pid,sid,body.root_operation_id)
    request=op.get('request')
    if (op.get('id')!=body.root_operation_id or op.get('kind')!='MESSAGE'
            or not isinstance(request,dict) or op.get('session_ref')!=reference
            or request.get('session_ref')!=reference or type(op.get('generation')) is not int
            or type(request.get('generation')) is not int
            or request.get('generation')!=op['generation'] or not 1<=op['generation']<=body.generation
            or op.get('request_sha256')!=digest(request)
            or not isinstance(op.get('native_message_id'),str) or not _NATIVE.fullmatch(op['native_message_id'])
            or not isinstance(op.get('original_session_id'),str) or not _NATIVE.fullmatch(op['original_session_id'])):
        raise Conflict('An exact acknowledged original root message is required')
    basis={'project_id':pid,'session_ref':reference,'workspace_ref':body.workspace_ref.model_dump(),
        'root_operation_id':body.root_operation_id,'root_generation':op['generation'],
        'native_session_id':op['original_session_id'],'native_message_id':op['native_message_id'],
        'request_sha256':op['request_sha256']}
    return {'basis':basis,'sha256':digest(basis),'status':op.get('status','UNKNOWN_OUTCOME'),
        'label':str(request.get('raw_text',''))[:280]}


def _unchanged(sessions,pid,sid,body,root):
    if _root(sessions,pid,sid,body)['basis']!=root['basis']:
        raise Conflict('Original work identity changed during metadata inspection')


class _RequirementReadBudget(Exception):pass


def _bundle(connection,pid,reference,*,cache=None):
    key=(pid,reference['id'],reference['revision'],reference['sha256'])
    if cache is not None:
        if key in cache:
            if cache[key] is None:raise Conflict('Original requirement version was unavailable in this inspection')
            return cache[key]
        if len(cache)>=32:raise _RequirementReadBudget()
        cache[key]=None
    row=connection.execute('''SELECT r.current_revision,v.sha256,
        CASE WHEN length(CAST(v.payload AS BLOB))<=1048576 THEN v.payload ELSE NULL END AS payload,
        length(CAST(v.payload AS BLOB)) AS bytes FROM requirement_sets r
        JOIN requirement_versions v ON v.requirement_id=r.id AND v.revision=?
        WHERE r.project_id=? AND r.id=?''',(reference['revision'],pid,reference['id'])).fetchone()
    if row is None:raise NotFound('The selected immutable requirements are unavailable')
    if row['bytes']>MAX_BUNDLE_BYTES:raise Conflict('The requirements exceed the bounded reader')
    try:
        raw=json.loads(row['payload'])
        if digest(raw)!=row['sha256'] or row['sha256']!=reference['sha256']:
            raise Conflict('Immutable requirement SHA differs')
        payload=RequirementBundle.model_validate(raw).model_dump()
    except (ValueError,TypeError) as error:raise Conflict('Immutable requirement structure changed') from error
    result={'ref':deepcopy(reference),'current_revision':row['current_revision'],'payload':payload}
    if cache is not None:cache[key]=result
    return result


def _current_ref(connection,pid,rid):
    row=connection.execute('''SELECT r.current_revision,v.sha256 FROM requirement_sets r
        JOIN requirement_versions v ON v.requirement_id=r.id AND v.revision=r.current_revision
        WHERE r.project_id=? AND r.id=?''',(pid,rid)).fetchone()
    if row is None:raise NotFound('Requirement head is unavailable')
    return {'id':rid,'revision':row['current_revision'],'sha256':row['sha256']}


def _selection(bundle,requirement_ids,acceptance_ids):
    requirements={r['id']:r for r in bundle['payload']['requirements']}
    criteria={a['id']:a for a in bundle['payload']['acceptance']}
    if set(requirement_ids)-requirements.keys() or set(acceptance_ids)-criteria.keys():
        raise Conflict('The selected requirement or acceptance item changed')
    if any(set(criteria[key]['requirement_ids'])-set(requirement_ids) for key in acceptance_ids):
        raise Conflict('Select every requirement belonging to the chosen acceptance criterion')
    return {'requirements':[requirements[key] for key in sorted(requirement_ids)],
        'acceptance':[criteria[key] for key in sorted(acceptance_ids)]}


def _source_status(connection,pid,requirements,cache):
    result=[]
    for item in requirements:
        statuses=[]
        for ref in item['source_refs']:
            key=(pid,ref['document_id'],ref['revision'],ref['sha256'])
            if key not in cache:
                row=connection.execute('''SELECT d.current_revision,d.deleted_at,v.sha256
                    FROM documents d LEFT JOIN document_versions v
                    ON v.document_id=d.id AND v.revision=? WHERE d.project_id=? AND d.id=?''',
                    (ref['revision'],pid,ref['document_id'])).fetchone()
                cache[key]=('UNAVAILABLE' if row is None or row['deleted_at'] or row['sha256']!=ref['sha256']
                    else 'CURRENT' if row['current_revision']==ref['revision'] else 'SUPERSEDED')
            statuses.append(cache[key])
        status=('UNAVAILABLE' if 'UNAVAILABLE' in statuses else 'SUPERSEDED' if 'SUPERSEDED' in statuses
            else 'CURRENT' if statuses else 'UNSOURCED')
        result.append({'id':item['id'],'status':status})
    return result


def _operation(connection,pid,sid,operation_id):
    row=connection.execute('''SELECT root_operation_id,revision,request_json,request_sha256,
        receipt_json,receipt_sha256 FROM original_work_requirement_operations
        WHERE project_id=? AND session_id=? AND operation_id=?''',(pid,sid,operation_id)).fetchone()
    if row is None:raise NotFound('Original requirement-link receipt was not recorded')
    try:
        request=json.loads(row['request_json']);receipt=json.loads(row['receipt_json'])
        if (not isinstance(request,dict) or not isinstance(receipt,dict)
                or WorkRequirementLink.model_validate(request).model_dump()!=request):raise ValueError
    except (ValueError,TypeError) as error:raise Conflict('Original requirement-link receipt cannot be decoded') from error
    if (digest(request)!=row['request_sha256'] or digest(receipt)!=row['receipt_sha256']
            or receipt.get('request_sha256')!=row['request_sha256'] or receipt.get('revision')!=row['revision']
            or receipt.get('project_id')!=pid or receipt.get('session_ref')!=request.get('session_ref')
            or receipt.get('operation_id')!=operation_id or receipt.get('root_operation_id')!=row['root_operation_id']
            or request.get('root_operation_id')!=row['root_operation_id']):
        raise Conflict('Original requirement-link receipt integrity failed')
    expected={key:request.get(key) for key in ('action','requirement_ref','requirement_ids','acceptance_ids','root_sha256')}
    if any(receipt.get(key)!=value for key,value in expected.items()):
        raise Conflict('Requirement-link receipt differs from its original request')
    if receipt.get('source_authority')!=request.get('source_authority'):
        raise Conflict('Requirement-link source authority differs from its original request')
    return receipt


def _head(connection,pid,sid,root):
    row=connection.execute('''SELECT root_sha256,revision,operation_id,active_json
        FROM original_work_requirement_heads WHERE project_id=? AND session_id=? AND root_operation_id=?''',
        (pid,sid,root['basis']['root_operation_id'])).fetchone()
    if row is None:return 0,[]
    try:active=json.loads(row['active_json'])
    except (ValueError,TypeError) as error:raise Conflict('Original requirement head cannot be decoded') from error
    if (row['root_sha256']!=root['sha256'] or not isinstance(active,list) or len(active)>MAX_LINKS
            or any(not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',key) for key in active)
            or len(set(active))!=len(active)):
        raise Conflict('Original work requirement head changed')
    last=_operation(connection,pid,sid,row['operation_id'])
    if last['root_sha256']!=root['sha256'] or last['revision']!=row['revision']:
        raise Conflict('Original work requirement revision differs')
    links=[_operation(connection,pid,sid,key) for key in active]
    if (any(link['action']!='ASSOCIATE' or link['root_sha256']!=root['sha256'] for link in links)
            or len({link['requirement_ref']['id'] for link in links})!=len(links)):
        raise Conflict('Active original requirement references differ')
    return row['revision'],links


def _requirement_project(connection,pid,link,*,bundles=None):
    # Lazy import keeps the existing original-work schema the common authority.
    from .work_requirement_sources import requirement_project
    return requirement_project(connection,pid,link,bundles=bundles)


def _linked_bundle(connection,pid,link,*,cache=None):
    source_pid=_requirement_project(connection,pid,link,bundles=cache)
    return _bundle(connection,source_pid,link['requirement_ref'],cache=cache)


def _impact(connection,pid,link,cache,*,bundles=None):
    reference=link['requirement_ref']
    base={'operation_id':link['operation_id'],'requirement_ref':reference,
        'requirement_ids':link['requirement_ids'],'acceptance_ids':link['acceptance_ids'],
        'selection_sha256':link['selection_sha256'],'automatic_adoption':False}
    try:
        source_pid=_requirement_project(connection,pid,link,bundles=bundles)
        old=_bundle(connection,source_pid,reference,cache=bundles);selected=_selection(old,link['requirement_ids'],link['acceptance_ids'])
        if digest(selected)!=link['selection_sha256']:raise Conflict('Frozen selected requirements differ')
        current_ref=_current_ref(connection,source_pid,reference['id']);current=_bundle(connection,source_pid,current_ref,cache=bundles)
        current_requirements={r['id']:r for r in current['payload']['requirements']}
        current_criteria={a['id']:a for a in current['payload']['acceptance']}
        changed_requirements=[r['id'] for r in selected['requirements'] if current_requirements.get(r['id'])!=r]
        changed_acceptance=[a['id'] for a in selected['acceptance'] if current_criteria.get(a['id'])!=a]
        sources=_source_status(connection,source_pid,selected['requirements'],cache)
        stale=[s['id'] for s in sources if s['status'] in {'UNAVAILABLE','SUPERSEDED'}]
        ready=current['payload']['confirmed']
        status=('NEEDS_REVIEW' if changed_requirements or changed_acceptance or stale or not ready
            else 'CURRENT' if current_ref==reference else 'SELECTED_UNCHANGED_NEWER_BUNDLE')
        return {**base,'title':old['payload']['title'],'current_ref':current_ref,'review_status':status,
            'changed_requirement_ids':changed_requirements,'changed_acceptance_ids':changed_acceptance,
            'stale_source_requirement_ids':stale,'sources':sources,'current_bundle_confirmed':ready,
            'requires_explicit_adoption':current_ref!=reference}
    except (Conflict,InvalidInput,NotFound,ValueError,KeyError,TypeError):
        return {**base,'title':None,'current_ref':None,'review_status':'UNAVAILABLE',
            'changed_requirement_ids':[],'changed_acceptance_ids':[],'stale_source_requirement_ids':[],
            'sources':[],'current_bundle_confirmed':False,'requires_explicit_adoption':True}


def _catalog(connection,pid,offset):
    rows=connection.execute('''SELECT r.id,r.current_revision,v.sha256,length(CAST(v.payload AS BLOB)) AS bytes
        FROM requirement_sets r JOIN requirement_versions v
        ON v.requirement_id=r.id AND v.revision=r.current_revision
        WHERE r.project_id=? ORDER BY r.id LIMIT 9 OFFSET ?''',(pid,offset)).fetchall()
    entries=[]
    for row in rows[:CATALOG_PAGE]:
        reference={'id':row['id'],'revision':row['current_revision'],'sha256':row['sha256']}
        entry={'ref':reference,'title':None,'origin':None,'confirmed':None,'eligible':False,'reason':'UNAVAILABLE'}
        try:
            bundle=_bundle(connection,pid,reference);p=bundle['payload']
            entry.update(title=p['title'],origin=p['origin'],confirmed=p['confirmed'],eligible=p['confirmed'],
                reason='READY' if p['confirmed'] else 'NOT_CONFIRMED')
        except (Conflict,InvalidInput,NotFound,ValueError):pass
        entries.append(entry)
    return {'entries':entries,'offset':offset,'limit':CATALOG_PAGE,
        'next_offset':offset+CATALOG_PAGE if len(rows)>CATALOG_PAGE else None,'scope':'SAME_EXECUTION_PROJECT_ONLY'}


def _selected_view(connection,pid,bundle,body,cache):
    p=bundle['payload']
    def item(value):
        raw=value['text'];return {'id':value['id'],'text_excerpt':raw[:240],
            'text_length':len(raw),'text_sha256':hashlib.sha256(raw.encode()).hexdigest(),
            'text_truncated':len(raw)>240}
    result={'ref':bundle['ref'],'title':p['title'],'confirmed':p['confirmed'],'origin':p['origin'],
        'current_revision':bundle['current_revision'],
        'requirements':[{**item(r),'source_count':len(r['source_refs'])} for r in p['requirements']],
        'acceptance':[{**item(a),'requirement_ids':a['requirement_ids']} for a in p['acceptance']],
        'sources':_source_status(connection,pid,p['requirements'],cache),'detail':None}
    if body.detail_kind is not None:
        candidates=p['requirements'] if body.detail_kind=='requirement' else p['acceptance']
        found=next((value for value in candidates if value['id']==body.detail_id),None)
        if found is None:raise NotFound('The exact selected requirement detail is unavailable')
        result['detail']={'kind':body.detail_kind,'id':body.detail_id,'text':found['text']}
    return result


def inspect_work_requirements(sessions,pid,sid,body):
    body=WorkRequirementInspect.model_validate(body);root=_root(sessions,pid,sid,body)
    with sessions.store.connect() as connection:
        connection.execute('BEGIN')
        revision,links=_head(connection,pid,sid,root);cache={}
        impacts=[_impact(connection,pid,link,cache) for link in links]
        catalog=(_catalog(connection,pid,body.catalog_offset) if body.include_catalog else
            {'entries':[],'offset':body.catalog_offset,'limit':CATALOG_PAGE,'next_offset':None,
                'scope':'SAME_EXECUTION_PROJECT_ONLY'})
        catalog['included']=body.include_catalog
        selected_ref=body.selected_requirement_ref.model_dump() if body.selected_requirement_ref else (
            links[-1]['requirement_ref'] if links else None)
        selected=None;selected_issue=None
        if body.detail_kind is not None and selected_ref is None:
            raise NotFound('Choose an exact original requirement version before reading an item')
        if selected_ref:
            try:
                selected_link=next((link for link in links if link['requirement_ref']==selected_ref),None)
                if body.selected_source_authority is not None:
                    from .work_requirement_sources import authorized_selection
                    selected_link=authorized_selection(connection,pid,sid,root,selected_ref,body.selected_source_authority)
                source_pid=_requirement_project(connection,pid,selected_link) if selected_link else pid
                bundle=_bundle(connection,source_pid,selected_ref)
                if selected_link and selected_link.get('source_authority') is not None:
                    # A linked foreign view never exposes unselected source items.
                    bundle=deepcopy(bundle)
                    selection=_selection(bundle,selected_link['requirement_ids'],selected_link['acceptance_ids'])
                    bundle['payload'].update(selection)
                selected=_selected_view(connection,source_pid,bundle,body,cache)
                if selected_link and selected_link.get('source_authority') is not None:
                    selected['source_authority']=deepcopy(selected_link['source_authority'])
            except (Conflict,InvalidInput,NotFound,ValueError):
                if body.selected_requirement_ref is not None:raise
                selected_issue='IMMUTABLE_REQUIREMENT_VIEW_UNAVAILABLE'
        _unchanged(sessions,pid,sid,body,root)
    scope={key:body.model_dump()[key] for key in ('session_ref','generation','workspace_ref','root_operation_id')}
    return _bounded({'kind':'ORIGINAL_WORK_REQUIREMENTS','version':1,'project_id':pid,'scope':scope,
        'query_sha256':digest(body.model_dump()),'root_basis':root['basis'],'root_sha256':root['sha256'],
        'root_status':root['status'],'label':root['label'],'revision':revision,'links':links,'impacts':impacts,
        'catalog':catalog,'selected':selected,'selected_issue':selected_issue,'read_only':True,'model_calls':0,'commands_started':0,
        'source_files_written':0,'automatic_adoption':False,'whole_task_acceptance':'NOT_ASSESSED',
        'current_workspace_verified':False,'target_environment_verified':False,
        'requirement_source_scope':'REGISTERED_DOCUMENT_VERSIONS_ONLY',
        'coverage':'EXPLICIT_SELECTED_REQUIREMENTS_ONLY_NOT_TEST_CASE_COVERAGE_OR_TARGET_ACCEPTANCE'})


def link_work_requirements(sessions,pid,sid,body):
    body=WorkRequirementLink.model_validate(body);request=_bounded(body.model_dump(),32768)
    root=_root(sessions,pid,sid,body)
    if body.root_sha256!=root['sha256']:raise Conflict('Confirm the exact original root operation')
    with sessions.store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        previous=connection.execute('''SELECT request_sha256 FROM original_work_requirement_operations
            WHERE project_id=? AND session_id=? AND operation_id=?''',(pid,sid,body.operation_id)).fetchone()
        if previous is not None:
            if previous['request_sha256']!=digest(request):raise Conflict('IDEMPOTENCY_CONFLICT: original requirement-link input changed')
            _unchanged(sessions,pid,sid,body,root)
            return _operation(connection,pid,sid,body.operation_id)
        revision,links=_head(connection,pid,sid,root)
        if revision!=body.expected_revision:raise Conflict('Original work requirement links changed; review the saved selection')
        counts=connection.execute('''SELECT count(*) FROM original_work_requirement_operations
            WHERE project_id=? AND session_id=?''',(pid,sid)).fetchone()[0]
        roots=connection.execute('''SELECT count(*) FROM original_work_requirement_heads
            WHERE project_id=? AND session_id=?''',(pid,sid)).fetchone()[0]
        if counts>=MAX_OPERATIONS or revision==0 and roots>=MAX_ROOTS:raise Conflict('Original work requirement metadata capacity reached')
        reference=body.requirement_ref.model_dump();existing=next((link for link in links if link['requirement_ref']['id']==reference['id']),None)
        selected_sha=None
        if body.action=='ASSOCIATE':
            candidate={'project_id':pid,'session_ref':body.session_ref.model_dump(),
                'root_operation_id':body.root_operation_id,'root_sha256':body.root_sha256,
                'requirement_ref':reference,'requirement_ids':body.requirement_ids,'acceptance_ids':body.acceptance_ids,
                **({'source_authority':body.source_authority.model_dump()} if body.source_authority else {})}
            source_pid=_requirement_project(connection,pid,candidate)
            bundle=_bundle(connection,source_pid,reference)
            if bundle['current_revision']!=reference['revision'] or not bundle['payload']['confirmed']:
                raise Conflict('Select the current human-confirmed requirements')
            selected=_selection(bundle,body.requirement_ids,body.acceptance_ids)
            if any(row['status'] in {'UNAVAILABLE','SUPERSEDED'} for row in _source_status(connection,source_pid,selected['requirements'],{})):
                raise Conflict('The selected requirement sources need review')
            selected_sha=digest(selected)
            if existing is None and len(links)>=MAX_LINKS:raise Conflict('Original work already has ten requirement-set associations')
        elif existing is None or existing['requirement_ref']!=reference:
            raise Conflict('Remove the exact original saved requirement association')
        _unchanged(sessions,pid,sid,body,root)
        receipt=_bounded({'kind':'ORIGINAL_WORK_REQUIREMENT_OPERATION','version':1,'project_id':pid,
            'session_ref':body.session_ref.model_dump(),'root_operation_id':body.root_operation_id,
            'root_sha256':root['sha256'],'operation_id':body.operation_id,'request_sha256':digest(request),
            'revision':revision+1,'action':body.action,'requirement_ref':reference,
            'requirement_ids':body.requirement_ids,'acceptance_ids':body.acceptance_ids,
            'selection_sha256':selected_sha,'recorded_at':now(),'metadata_only':True,'model_calls':0,
            'commands_started':0,'source_files_written':0,'permission_granted':False,
            'original_task_status_changed':False,'whole_task_acceptance':'NOT_ASSESSED',
            **({'source_authority':body.source_authority.model_dump()} if body.source_authority else {})},32768)
        connection.execute('''INSERT INTO original_work_requirement_operations
            VALUES (?,?,?,?,?,?,?,?,?)''',(pid,sid,body.operation_id,body.root_operation_id,revision+1,
                canonical(request),digest(request),canonical(receipt),digest(receipt)))
        active=[link['operation_id'] for link in links if link['requirement_ref']['id']!=reference['id']]
        if body.action=='ASSOCIATE':active.append(body.operation_id)
        connection.execute('''INSERT INTO original_work_requirement_heads VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(project_id,session_id,root_operation_id) DO UPDATE SET
            root_sha256=excluded.root_sha256,revision=excluded.revision,
            operation_id=excluded.operation_id,active_json=excluded.active_json''',
            (pid,sid,body.root_operation_id,root['sha256'],revision+1,body.operation_id,canonical(active)))
    return receipt


def inspect_work_requirement_operation(sessions,pid,sid,body):
    body=WorkRequirementOperation.model_validate(body);root=_root(sessions,pid,sid,body)
    with sessions.store.connect() as connection:
        receipt=_operation(connection,pid,sid,body.operation_id)
        if (receipt['request_sha256']!=body.request_sha256 or receipt['root_sha256']!=root['sha256']
                or receipt['root_operation_id']!=body.root_operation_id or receipt['session_ref']!=body.session_ref.model_dump()):
            raise Conflict('Inspect the exact original requirement-link operation')
        _unchanged(sessions,pid,sid,body,root)
        return _bounded(receipt,32768)

"""Human-declared acceptance-item mappings to exact saved JUnit case ordinals.

The requirement ledger and original observer stay authoritative. This records a
mapping, not measured coverage, a retest, writer attribution or task acceptance.
"""
from copy import deepcopy
import json
import hashlib
from typing import Literal
from pydantic import Field,field_validator,model_validator
from packages.domain.questions import RequestModel,Id,Hash
from packages.domain.requirements import Key
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import canonical,digest,now
from packages.domain.local_workspaces import relative
from .models import SessionRef
from . import work_requirements as requirements
from .test_reports import CommandTestReportInput,_load_command_report,MAX_CASES

MAX_OPERATIONS=512
MAX_ROOTS=128
MAX_MAPPINGS=128
PAGE_SIZE=20
MAX_REPORTS=8
MAX_REPORT_BYTES=8*262144


class WorkCaseSource(RequestModel):
    command_ref:SessionRef
    command_generation:int=Field(ge=1,strict=True)
    call_id:str=Field(min_length=1,max_length=192,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    report_path:str=Field(min_length=1,max_length=1024)
    report_sha256:Hash
    report_ref:SessionRef
    case_ordinal:int=Field(ge=0,lt=MAX_CASES,strict=True)
    case_sha256:Hash

    @field_validator('report_path')
    @classmethod
    def literal_relative_path(cls,value):
        if any(char in value for char in '*?[]'):raise ValueError('An exact literal report path is required')
        return relative(value)


class WorkCaseSelection(RequestModel):
    requirement_link_operation_id:Id
    requirement_link_request_sha256:Hash
    source:WorkCaseSource
    acceptance_ids:list[Key]=Field(min_length=1,max_length=100)

    @field_validator('acceptance_ids')
    @classmethod
    def unique_ids(cls,value):
        if len(set(value))!=len(value):raise ValueError('Acceptance IDs must be unique')
        return value


class WorkCaseInspect(requirements.WorkRequirementScope):
    offset:int=Field(default=0,ge=0,le=MAX_MAPPINGS,strict=True)
    case_source:WorkCaseSource|None=None
    selected_requirement_link_operation_id:Id|None=None
    mapping_key:Hash|None=None


class WorkCaseMap(requirements.WorkRequirementScope):
    operation_id:Id
    root_sha256:Hash
    expected_revision:int=Field(ge=0,le=MAX_OPERATIONS,strict=True)
    expected_requirement_revision:int=Field(ge=0,le=requirements.MAX_OPERATIONS,strict=True)
    action:Literal['MAP','REMOVE']
    mapping_key:Hash
    selection:WorkCaseSelection|None=None

    @model_validator(mode='after')
    def exact_action(self):
        if (self.action=='MAP')!=(self.selection is not None):
            raise ValueError('MAP needs an exact selection; REMOVE keeps the original mapping key only')
        return self


class WorkCaseOperation(requirements.WorkRequirementOperation):pass


def mapping_key(root_sha256,requirement_id,source):
    source=WorkCaseSource.model_validate(source)
    return digest({'schema':'eeas.work-case-slot.v1','root_sha256':root_sha256,
        'requirement_id':requirement_id,'report_ref':source.report_ref.model_dump(),'case_ordinal':source.case_ordinal})


def _flags():
    return {'model_calls':0,'commands_started':0,'source_files_written':0,'permission_granted':False,
        'original_task_status_changed':False,'whole_task_acceptance':'NOT_ASSESSED',
        'automatic_replay':False,'coverage':'HUMAN_DECLARED_MAPPING_NOT_MEASURED_TEST_COVERAGE',
        'current_workspace_verified':False,'target_environment_verified':False,'writer_attribution':'NOT_PROVEN'}


class _Reports:
    """One inspection owns this bounded cache; no global historical body cache."""
    def __init__(self,sessions,pid,sid,scope):
        self.sessions=sessions;self.pid=pid;self.sid=sid;self.scope=scope;self.values={};self.bytes=0

    def inspect(self,source):
        source=WorkCaseSource.model_validate(source)
        locator=source.model_dump(exclude={'case_ordinal','case_sha256','report_ref'})
        key=digest(locator)
        if key not in self.values:
            if len(self.values)>=MAX_REPORTS:raise _Budget()
            body=CommandTestReportInput(**{key:value for key,value in self.scope.model_dump().items()
                if key in {'session_ref','generation','workspace_ref'}},**locator,offset=0,limit=1,outcome='ALL')
            # Reserve a full source read before loading, including failures.
            self.values[key]=None
            if self.bytes+262144>MAX_REPORT_BYTES:raise _Budget()
            self.bytes+=262144
            try:self.values[key]=_load_command_report(self.sessions,self.pid,self.sid,body)
            except (Conflict,NotFound):raise
        loaded=self.values[key]
        if loaded is None:raise Conflict('Original report source was unavailable in this inspection')
        if (loaded['report_ref']!=source.report_ref.model_dump()
                or loaded['basis']['root_operation_id']!=self.scope.root_operation_id
                or source.case_ordinal>=len(loaded['parsed']['cases'])):
            raise Conflict('Select the exact saved report and original-work case ordinal')
        case=loaded['parsed']['cases'][source.case_ordinal]
        if case['ordinal']!=source.case_ordinal or digest(case)!=source.case_sha256:
            raise Conflict('Original test case differs from the reviewed item')
        case_ref={'id':digest({'schema':'eeas.junit.case.v1','report_ref':loaded['report_ref'],
            'ordinal':source.case_ordinal}),'revision':1,'sha256':digest(case)}
        return {'source':source.model_dump(),'case_ref':case_ref,'case':deepcopy(case),
            'basis':loaded['basis'],'summary':loaded['parsed']['summary'],
            'command_status':loaded['command']['status'],'command_exit_code':loaded['command']['exit_code'],
            'association':loaded['association'],'reason_codes':loaded['reasons'],
            'source_scope':'IMMUTABLE_AFTER_COMMAND_SNAPSHOT','writer_attribution':'NOT_PROVEN'}


class _Budget(Exception):pass


def _operation(connection,pid,sid,operation_id):
    row=connection.execute('''SELECT root_operation_id,revision,request_json,request_sha256,
        receipt_json,receipt_sha256 FROM original_work_case_operations
        WHERE project_id=? AND session_id=? AND operation_id=?''',(pid,sid,operation_id)).fetchone()
    if row is None:raise NotFound('Original work case mapping was not recorded')
    try:
        request=json.loads(row['request_json']);receipt=json.loads(row['receipt_json'])
        parsed=WorkCaseMap.model_validate(request)
    except (TypeError,ValueError) as error:raise Conflict('Original case mapping cannot be decoded') from error
    if (receipt.get('kind')!='ORIGINAL_WORK_CASE_OPERATION' or receipt.get('version')!=1
            or receipt.get('metadata_only') is not True or parsed.operation_id!=operation_id
            or receipt.get('requirement_revision')!=parsed.expected_requirement_revision
            or digest(request)!=row['request_sha256'] or digest(receipt)!=row['receipt_sha256']
            or receipt.get('request_sha256')!=row['request_sha256'] or receipt.get('revision')!=row['revision']
            or receipt.get('revision')!=parsed.expected_revision+1 or receipt.get('project_id')!=pid
            or receipt.get('session_ref')!=request['session_ref'] or receipt.get('operation_id')!=operation_id
            or receipt.get('root_operation_id')!=row['root_operation_id']
            or request['root_operation_id']!=row['root_operation_id']
            or any(receipt.get(key)!=request[key] for key in ['action','mapping_key','root_sha256'])
            or receipt.get('selection')!=request['selection']
            or any(receipt.get(key)!=value for key,value in _flags().items())):
        raise Conflict('Original case mapping integrity failed')
    if parsed.selection is not None:
        proof=receipt.get('proof')
        source=parsed.selection.source
        expected_case={'id':digest({'schema':'eeas.junit.case.v1','report_ref':source.report_ref.model_dump(),'ordinal':source.case_ordinal}),
            'revision':1,'sha256':source.case_sha256}
        if (not isinstance(proof,dict) or proof.get('case_ref')!=expected_case
                or proof.get('case_label',{}).get('ordinal')!=source.case_ordinal
                or proof.get('requirement_link_operation_id')!=parsed.selection.requirement_link_operation_id
                or proof.get('requirement_link_request_sha256')!=parsed.selection.requirement_link_request_sha256
                or proof.get('acceptance_ids')!=parsed.selection.acceptance_ids
                or mapping_key(parsed.root_sha256,proof.get('requirement_ref',{}).get('id'),parsed.selection.source)!=parsed.mapping_key):
            raise Conflict('Original case mapping proof differs')
    elif receipt.get('proof') is not None:raise Conflict('Removal cannot assert new test evidence')
    return receipt


def _head(connection,pid,sid,root):
    row=connection.execute('''SELECT root_sha256,revision,operation_id,active_json FROM original_work_case_heads
        WHERE project_id=? AND session_id=? AND root_operation_id=?''',(pid,sid,root['basis']['root_operation_id'])).fetchone()
    if row is None:return 0,[]
    try:active=json.loads(row['active_json'])
    except (TypeError,ValueError) as error:raise Conflict('Original case head cannot be decoded') from error
    if (row['root_sha256']!=root['sha256'] or not isinstance(active,list) or len(active)>MAX_MAPPINGS
            or any(not isinstance(key,str) for key in active) or len(set(active))!=len(active)):
        raise Conflict('Original case mapping head changed')
    last=_operation(connection,pid,sid,row['operation_id'])
    if last['root_sha256']!=root['sha256'] or last['revision']!=row['revision']:
        raise Conflict('Original case mapping head differs from its operation')
    mappings=[_operation(connection,pid,sid,key) for key in active]
    if (any(row['action']!='MAP' or row['root_sha256']!=root['sha256'] or row['revision']>last['revision'] for row in mappings)
            or len({row['mapping_key'] for row in mappings})!=len(mappings)):
        raise Conflict('Active original case mappings differ')
    return row['revision'],mappings


def _selected(connection,pid,link,acceptance_ids,*,bundles=None):
    if set(acceptance_ids)-set(link['acceptance_ids']):raise Conflict('Select acceptance items from this original requirement association')
    bundle=requirements._linked_bundle(connection,pid,link,cache=bundles)
    rows={row['id']:row for row in bundle['payload']['acceptance']}
    if set(acceptance_ids)-rows.keys():raise Conflict('Original acceptance item is unavailable')
    needed=sorted({key for item in acceptance_ids for key in rows[item]['requirement_ids']})
    if set(needed)-set(link['requirement_ids']):raise Conflict('Acceptance parents are outside the original selected requirements')
    selected=requirements._selection(bundle,needed,acceptance_ids)
    partial={**link,'requirement_ids':needed,'acceptance_ids':acceptance_ids,'selection_sha256':digest(selected)}
    return partial,bundle


def _proof(connection,pid,link,selection,case,source_cache):
    partial,_=_selected(connection,pid,link,selection.acceptance_ids)
    impact=requirements._impact(connection,pid,partial,source_cache)
    if impact['review_status'] not in {'CURRENT','SELECTED_UNCHANGED_NEWER_BUNDLE'}:
        raise Conflict('The selected acceptance basis needs review before mapping a case')
    return {'requirement_link_operation_id':link['operation_id'],'requirement_link_request_sha256':link['request_sha256'],
        'requirement_ref':link['requirement_ref'],'requirement_ids':partial['requirement_ids'],
        'acceptance_ids':selection.acceptance_ids,'selection_sha256':partial['selection_sha256'],
        'case_ref':case['case_ref'],'case_label':{key:case['case'][key] for key in ['ordinal','suite','classname','name','status','text_truncated']},
        'report_basis':case['basis'],'report_outcome':case['summary']['outcome'],
        'command_status':case['command_status'],'command_exit_code':case['command_exit_code'],
        'association':case['association'],'reason_codes':case['reason_codes']}


def map_work_test_case(sessions,pid,sid,body):
    body=WorkCaseMap.model_validate(body);request=requirements._bounded(body.model_dump(),32768)
    root=requirements._root(sessions,pid,sid,body)
    if root['sha256']!=body.root_sha256:raise Conflict('Confirm the exact original work root')
    # Original-key recovery reads no current report/body, and never moves a head.
    with sessions.store.connect() as connection:
        previous=connection.execute('''SELECT request_sha256 FROM original_work_case_operations
            WHERE project_id=? AND session_id=? AND operation_id=?''',(pid,sid,body.operation_id)).fetchone()
        if previous is not None:
            if previous['request_sha256']!=digest(request):raise Conflict('IDEMPOTENCY_CONFLICT: original case mapping input changed')
            receipt=_operation(connection,pid,sid,body.operation_id)
            requirements._unchanged(sessions,pid,sid,body,root);return receipt
    case=_Reports(sessions,pid,sid,body).inspect(body.selection.source) if body.selection else None
    with sessions.store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        # Another writer may have completed this same key during source reading.
        previous=connection.execute('''SELECT request_sha256 FROM original_work_case_operations
            WHERE project_id=? AND session_id=? AND operation_id=?''',(pid,sid,body.operation_id)).fetchone()
        if previous is not None:
            if previous['request_sha256']!=digest(request):raise Conflict('IDEMPOTENCY_CONFLICT: original case mapping input changed')
            requirements._unchanged(sessions,pid,sid,body,root);return _operation(connection,pid,sid,body.operation_id)
        revision,mappings=_head(connection,pid,sid,root)
        requirement_revision,links=requirements._head(connection,pid,sid,root)
        if revision!=body.expected_revision or requirement_revision!=body.expected_requirement_revision:
            raise Conflict('Original case or requirement associations changed; review current records')
        operations=connection.execute('SELECT count(*) FROM original_work_case_operations WHERE project_id=? AND session_id=?',(pid,sid)).fetchone()[0]
        roots=connection.execute('SELECT count(*) FROM original_work_case_heads WHERE project_id=? AND session_id=?',(pid,sid)).fetchone()[0]
        if operations>=MAX_OPERATIONS or revision==0 and roots>=MAX_ROOTS:raise Conflict('Original case metadata capacity reached')
        existing=next((row for row in mappings if row['mapping_key']==body.mapping_key),None);proof=None
        if body.selection is not None:
            link=next((row for row in links if row['operation_id']==body.selection.requirement_link_operation_id
                and row['request_sha256']==body.selection.requirement_link_request_sha256),None)
            if link is None:raise Conflict('Select the exact active original requirement association')
            if mapping_key(root['sha256'],link['requirement_ref']['id'],body.selection.source)!=body.mapping_key:
                raise Conflict('The case mapping key belongs to another report or requirement set')
            proof=_proof(connection,pid,link,body.selection,case,{})
            if existing is None and len(mappings)>=MAX_MAPPINGS:raise Conflict('Original work case mapping capacity reached')
        elif existing is None:raise Conflict('Remove the exact active original case mapping')
        requirements._unchanged(sessions,pid,sid,body,root)
        receipt=requirements._bounded({'kind':'ORIGINAL_WORK_CASE_OPERATION','version':1,'project_id':pid,
            'session_ref':body.session_ref.model_dump(),'root_operation_id':body.root_operation_id,'root_sha256':root['sha256'],
            'operation_id':body.operation_id,'request_sha256':digest(request),'revision':revision+1,
            'requirement_revision':requirement_revision,'action':body.action,'mapping_key':body.mapping_key,
            'selection':request['selection'],'proof':proof,'recorded_at':now(),'metadata_only':True,**_flags()},32768)
        connection.execute('''INSERT INTO original_work_case_operations VALUES (?,?,?,?,?,?,?,?,?)''',
            (pid,sid,body.operation_id,body.root_operation_id,revision+1,canonical(request),digest(request),canonical(receipt),digest(receipt)))
        active=[row['operation_id'] for row in mappings if row['mapping_key']!=body.mapping_key]
        if body.action=='MAP':active.append(body.operation_id)
        connection.execute('''INSERT INTO original_work_case_heads VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(project_id,session_id,root_operation_id) DO UPDATE SET root_sha256=excluded.root_sha256,
            revision=excluded.revision,operation_id=excluded.operation_id,active_json=excluded.active_json''',
            (pid,sid,body.root_operation_id,root['sha256'],revision+1,body.operation_id,canonical(active)))
    return receipt


def inspect_work_case_operation(sessions,pid,sid,body):
    body=WorkCaseOperation.model_validate(body);root=requirements._root(sessions,pid,sid,body)
    with sessions.store.connect() as connection:
        value=_operation(connection,pid,sid,body.operation_id)
        if (value['request_sha256']!=body.request_sha256 or value['root_sha256']!=root['sha256']
                or value['root_operation_id']!=body.root_operation_id or value['session_ref']!=body.session_ref.model_dump()):
            raise Conflict('Inspect the exact original case mapping operation')
        requirements._unchanged(sessions,pid,sid,body,root);return requirements._bounded(value,32768)


def _link_catalog(connection,pid,links,bundles):
    result=[]
    for link in links:
        row={'operation_id':link['operation_id'],'request_sha256':link['request_sha256'],
            'requirement_ref':link['requirement_ref'],'acceptance_count':len(link['acceptance_ids'])}
        try:
            bundle=requirements._linked_bundle(connection,pid,link,cache=bundles)
            row.update(title=bundle['payload']['title'],available=True,origin=bundle['payload']['origin'])
        except requirements._RequirementReadBudget:row.update(title=None,available=False,origin=None,reason='NOT_CHECKED_BUDGET')
        except (Conflict,NotFound,ValueError,KeyError,TypeError):row.update(title=None,available=False,origin=None,reason='UNAVAILABLE')
        result.append(row)
    return result


def _link_detail(connection,pid,link,bundles,source_cache):
    if link is None:return None
    base={'operation_id':link['operation_id'],'request_sha256':link['request_sha256'],
        'requirement_ref':link['requirement_ref'],'requirement_ids':link['requirement_ids']}
    try:
        bundle=requirements._linked_bundle(connection,pid,link,cache=bundles)
        rows={row['id']:row for row in bundle['payload']['acceptance']};criteria=[]
        for key in link['acceptance_ids']:
            partial,_=_selected(connection,pid,link,[key],bundles=bundles)
            impact=requirements._impact(connection,pid,partial,source_cache,bundles=bundles)
            item=rows[key]
            criteria.append({'id':key,'text_excerpt':item['text'][:240],'text_length':len(item['text']),
                'text_sha256':hashlib.sha256(item['text'].encode()).hexdigest(),
                'text_truncated':len(item['text'])>240,'requirement_ids':partial['requirement_ids'],
                'basis_status':impact['review_status'],'eligible':impact['review_status'] in {'CURRENT','SELECTED_UNCHANGED_NEWER_BUNDLE'}})
        return {**base,'title':bundle['payload']['title'],'origin':bundle['payload']['origin'],
            'available':True,'acceptance':criteria}
    except requirements._RequirementReadBudget:return {**base,'title':None,'origin':None,'available':False,'acceptance':[],'reason':'NOT_CHECKED_BUDGET'}
    except (Conflict,NotFound,ValueError,KeyError,TypeError):return {**base,'title':None,'origin':None,'available':False,'acceptance':[],'reason':'UNAVAILABLE'}


def inspect_work_test_cases(sessions,pid,sid,body):
    body=WorkCaseInspect.model_validate(body);root=requirements._root(sessions,pid,sid,body)
    reports=_Reports(sessions,pid,sid,body);selected_case=reports.inspect(body.case_source) if body.case_source else None
    bundles={};source_cache={}
    with sessions.store.connect() as connection:
        connection.execute('BEGIN')
        revision,mappings=_head(connection,pid,sid,root)
        requirement_revision,links=requirements._head(connection,pid,sid,root)
        catalog=_link_catalog(connection,pid,links,bundles)
        selected=next((link for link in links if link['operation_id']==body.selected_requirement_link_operation_id),None)
        if body.selected_requirement_link_operation_id is not None and selected is None:
            raise Conflict('Select an exact active original requirement association')
        if body.selected_requirement_link_operation_id is None:selected=next((link for link in links if link['acceptance_ids']),None)
        detail=_link_detail(connection,pid,selected,bundles,source_cache)
        selected_key=body.mapping_key
        if selected_key is None and body.case_source is not None and selected is not None:
            selected_key=mapping_key(root['sha256'],selected['requirement_ref']['id'],body.case_source)
        selected_mapping=next((row for row in mappings if row['mapping_key']==selected_key),None)
        rows=[]
        for saved in mappings[body.offset:body.offset+PAGE_SIZE]:
            selection=WorkCaseSelection.model_validate(saved['selection']);proof=saved['proof']
            active=next((link for link in links if link['operation_id']==selection.requirement_link_operation_id
                and link['request_sha256']==selection.requirement_link_request_sha256),None)
            impact=None
            if active is None:requirement_status='REQUIREMENT_LINK_CHANGED'
            else:
                try:
                    partial,_=_selected(connection,pid,active,selection.acceptance_ids,bundles=bundles)
                    if partial['selection_sha256']!=proof['selection_sha256']:
                        raise Conflict('Saved case mapping declaration differs')
                    impact=requirements._impact(connection,pid,partial,source_cache,bundles=bundles)
                    requirement_status=impact['review_status']
                except requirements._RequirementReadBudget:requirement_status='NOT_CHECKED_BUDGET'
                except (Conflict,NotFound,ValueError,KeyError,TypeError):requirement_status='UNAVAILABLE'
            report_status='VERIFIED_SAVED_CASE'
            try:
                current=reports.inspect(selection.source)
                if current['case_ref']!=proof['case_ref']:raise Conflict('Original case interpretation changed')
            except _Budget:report_status='NOT_CHECKED_BUDGET'
            except (Conflict,NotFound,ValueError,KeyError,TypeError):report_status='UNAVAILABLE'
            rows.append({'operation':saved,'requirement_status':requirement_status,'requirement_impact':impact,
                'report_status':report_status,'automatic_adoption':False,'new_execution_proven':False})
        requirements._unchanged(sessions,pid,sid,body,root)
    return requirements._bounded({'kind':'ORIGINAL_WORK_CASE_MAPPINGS','version':1,'project_id':pid,
        'scope':{key:body.model_dump()[key] for key in ['session_ref','generation','workspace_ref','root_operation_id']},
        'query_sha256':digest(body.model_dump()),'root_basis':root['basis'],'root_sha256':root['sha256'],
        'root_status':root['status'],'revision':revision,'requirement_revision':requirement_revision,
        'requirement_links':catalog,'selected_requirement_link':detail,'selected_case':selected_case,'selected_mapping':selected_mapping,'rows':rows,
        'page':{'offset':body.offset,'limit':PAGE_SIZE,'total':len(mappings),
            'next_offset':body.offset+len(rows) if body.offset+len(rows)<len(mappings) else None},
        'source_budget':{'report_sources_limit':MAX_REPORTS,'report_sources_reserved':len(reports.values),
            'reserved_report_bytes':reports.bytes,'report_bytes_limit':MAX_REPORT_BYTES,
            'requirement_versions_limit':32,'requirement_versions_reserved':len(bundles)},
        'read_only':True,'metadata_only':True,**_flags()})

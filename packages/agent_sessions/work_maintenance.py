"""Maintenance metadata on the same original work, frozen delivery provenance.

An association does not reproduce/resolve an issue, execute commands, restore a
release, grant access to another workspace, or establish target acceptance.
Only same-project completed delivery records are read, even across old sessions.
"""
import json
import hashlib
import re
from typing import Literal
from pydantic import Field,field_validator

from packages.domain.context_read_budget import context_read_scope,current_context_read_budget,ContextReadLimit
from packages.domain.local_workspaces import canonical,digest
from packages.domain.questions import RequestModel,Id,Hash,stamp
from packages.domain.store import Conflict,InvalidInput,NotFound
from .models import SessionRef
from . import work_requirements as requirements
from . import work_deliveries as deliveries

MAX_OPERATIONS=512
MAX_ROOT_OPERATIONS=128
MAX_LINKS=8
PAGE=8
MAX_RECORD=32768
MAX_RESPONSE=131072


class MaintenanceDeliveryRef(RequestModel):
    session_ref:SessionRef
    root_operation_id:Id
    root_sha256:Hash
    operation_id:Id
    request_sha256:Hash
    manifest_sha256:Hash
    revision:int=Field(ge=1,le=deliveries.MAX_DELIVERIES,strict=True)
    receipt_sha256:Hash


class WorkMaintenanceBrowse(requirements.WorkRequirementScope):
    offset:int=Field(default=0,ge=0,le=deliveries.MAX_PRIVATE_INTENTS,strict=True)


class WorkMaintenanceDelivery(requirements.WorkRequirementScope):
    delivery_ref:MaintenanceDeliveryRef
    section:Literal['FILES','REQUIREMENTS','CASES','REPORTS','NOTES']='FILES'
    offset:int=Field(default=0,ge=0,le=10000,strict=True)


class WorkMaintenancePrepare(requirements.WorkRequirementScope):
    root_sha256:Hash
    expected_revision:int=Field(ge=0,le=MAX_ROOT_OPERATIONS,strict=True)
    action:Literal['ASSOCIATE','REMOVE']
    delivery_ref:MaintenanceDeliveryRef
    note:str=Field(min_length=1,max_length=4000)

    @field_validator('note')
    @classmethod
    def meaningful_note(cls,value):
        if not value.strip():raise ValueError('Describe the maintenance reason or preservation decision')
        return value


class WorkMaintenanceChange(WorkMaintenancePrepare):
    operation_id:Id
    review_sha256:Hash
    review_confirmed:bool=Field(strict=True)

    @field_validator('review_confirmed')
    @classmethod
    def explicit_review(cls,value):
        if value is not True:raise ValueError('Confirm the exact original delivery and maintenance scope')
        return value


class WorkMaintenanceInspect(requirements.WorkRequirementScope):
    history_offset:int=Field(default=0,ge=0,le=MAX_ROOT_OPERATIONS,strict=True)


class WorkMaintenanceOperation(requirements.WorkRequirementOperation):pass


def _flags():
    return {'read_only_sources':True,'model_calls':0,'commands_started':0,'source_files_written':0,
        'artifact_files_written':0,'permission_granted':False,'deployment_performed':False,
        'restore_performed':False,'original_task_status_changed':False,'issue_resolution':'NOT_ASSESSED',
        'automatic_replay':False,'whole_task_acceptance':'NOT_ASSESSED','target_environment_verified':False,
        'current_workspace_verified':False,'current_baseline_bytes_verified':False,
        'provenance_scope':'FROZEN_SAME_PROJECT_DELIVERY_METADATA_ONLY'}


def _bounded(v,limit=MAX_RESPONSE):
    if len(canonical(v).encode())>limit:raise Conflict('Maintenance metadata exceeds its bounded response')
    return v


def _charge(size):
    budget=current_context_read_budget()
    if budget is not None:budget.charge(size)


def _checked(raw,checksum,limit=MAX_RECORD):
    if not isinstance(raw,str) or len(raw.encode())>limit:raise Conflict('Maintenance record exceeds its bound')
    _charge(len(raw.encode()))
    try:v=json.loads(raw)
    except (ValueError,TypeError):raise Conflict('Maintenance record is unreadable')from None
    if digest(v)!=checksum:raise Conflict('Maintenance record SHA differs')
    return v


def _scope(body):return {key:body.model_dump()[key] for key in requirements.WorkRequirementScope.model_fields}
def _query(body):return {key:body.model_dump()[key] for key in WorkMaintenancePrepare.model_fields}
def _same_flags(value,expected):return all(type(value.get(k))is type(v) and value.get(k)==v for k,v in expected.items())


class WorkMaintenance:
    def __init__(self,sessions):
        self.sessions,self.store=sessions,sessions.store
        self.deliveries=deliveries.WorkDeliveries(sessions)

    def _baseline(self,db,pid,ref):
        """Read frozen SQL payloads only; never current owner, files, ZIP or SDK."""
        ref=MaintenanceDeliveryRef.model_validate(ref);sid=ref.session_ref.id
        row=db.execute('''SELECT length(CAST(i.request_json AS BLOB)) AS request_bytes,
            length(CAST(i.manifest_json AS BLOB)) AS manifest_bytes,
            length(CAST(r.receipt_json AS BLOB)) AS receipt_bytes,r.receipt_sha256
            FROM original_work_delivery_intents i JOIN original_work_delivery_receipts r
            USING(project_id,session_id,operation_id)
            WHERE i.project_id=? AND i.session_id=? AND i.operation_id=?''',(pid,sid,ref.operation_id)).fetchone()
        if row is None:raise NotFound('The exact completed delivery is unavailable in this project')
        if (row['request_bytes']>131072 or row['manifest_bytes']>deliveries.MAX_MANIFEST
                or row['receipt_bytes']>131072 or row['receipt_sha256']!=ref.receipt_sha256):
            raise Conflict('Frozen delivery size or receipt SHA differs')
        _charge(row['request_bytes']+row['manifest_bytes']+row['receipt_bytes'])
        intent=self.deliveries._intent(db,pid,sid,ref.operation_id)
        receipt=self.deliveries._receipt(db,pid,sid,ref.operation_id,intent)
        body=intent['body'];manifest=intent['manifest'];basis=manifest['root_basis']
        if (receipt is None or digest(receipt)!=ref.receipt_sha256 or body.session_ref!=ref.session_ref
                or body.root_operation_id!=ref.root_operation_id or body.root_sha256!=ref.root_sha256
                or intent['request_sha256']!=ref.request_sha256 or intent['manifest_sha256']!=ref.manifest_sha256
                or receipt['root_sha256']!=ref.root_sha256 or receipt['revision']!=ref.revision
                or receipt['version_label']!=body.version_label or receipt['notes']!=body.notes
                or receipt['warnings']!=manifest['warnings'] or receipt['file_bytes']!=manifest['file_bytes']
                or manifest.get('schema')!='cirven.original-work-delivery.v1'
                or digest(basis)!=ref.root_sha256 or basis.get('project_id')!=pid
                or basis.get('session_ref')!=ref.session_ref.model_dump()
                or basis.get('root_operation_id')!=ref.root_operation_id
                or basis.get('workspace_ref')!=body.workspace_ref.model_dump()
                or receipt.get('kind')!='ORIGINAL_WORK_DELIVERY' or receipt.get('version')!=1
                or not _same_flags(receipt,deliveries._flags()) or not _same_flags(manifest,deliveries._flags())
                or any(f['source_ref']['project_id']!=pid for f in manifest['files'])):
            raise Conflict('Frozen delivery provenance differs from the selected reference')
        artifact=receipt['artifact']
        if (set(artifact)!={'path','sha256','byte_length','filename'}
                or not re.fullmatch(r'attempts/0[1-4]/package.zip',artifact['path'])
                or not re.fullmatch(r'[a-f0-9]{64}',artifact['sha256'])
                or type(artifact['byte_length'])is not int or not 1<=artifact['byte_length']<=deliveries.MAX_ARCHIVE
                or artifact['filename']!='cirven-delivery-'+str(ref.revision)+'.zip'):
            raise Conflict('Original delivery artifact descriptor differs')
        evidence=manifest['evidence']
        sections={'FILES':manifest['files'],'REQUIREMENTS':evidence['requirements'],
            'CASES':evidence['case_mappings'],'REPORTS':list(evidence['reports'].values()),'NOTES':[{'text':receipt['notes']}]}
        summary=_bounded({'reference':ref.model_dump(),'version_label':receipt['version_label'],
            'notes_excerpt':receipt['notes'][:240],'notes_sha256':hashlib.sha256(receipt['notes'].encode()).hexdigest(),
            'notes_length':len(receipt['notes']),'parent':receipt['parent'],'artifact':artifact,
            'recorded_at':receipt['recorded_at'],'warnings':receipt['warnings'],
            'evidence_included':body.include_evidence,
            'counts':{key:len(rows) for key,rows in sections.items()}},16384)
        return summary,sections

    def _reference(self,db,pid,sid,op):
        row=db.execute('''SELECT r.receipt_json,r.receipt_sha256,i.root_sha256,i.manifest_sha256,i.request_sha256
            FROM original_work_delivery_receipts r JOIN original_work_delivery_intents i
            USING(project_id,session_id,operation_id) WHERE r.project_id=? AND r.session_id=? AND r.operation_id=?''',
            (pid,sid,op)).fetchone()
        if row is None:raise NotFound('Original completed delivery disappeared')
        r=_checked(row['receipt_json'],row['receipt_sha256'],131072)
        return MaintenanceDeliveryRef(session_ref=r['session_ref'],root_operation_id=r['root_operation_id'],
            root_sha256=row['root_sha256'],operation_id=op,request_sha256=row['request_sha256'],
            manifest_sha256=row['manifest_sha256'],revision=r['revision'],receipt_sha256=row['receipt_sha256'])

    def browse(self,pid,sid,body):
        body=WorkMaintenanceBrowse.model_validate(body)
        with context_read_scope():
            root=requirements._root(self.sessions,pid,sid,body)
            with self.store.connect() as db:
                rows=db.execute('''SELECT session_id,operation_id FROM original_work_delivery_receipts
                    WHERE project_id=? ORDER BY session_id,root_operation_id,revision,operation_id LIMIT 9 OFFSET ?''',
                    (pid,body.offset)).fetchall()
                entries=[];unavailable=0
                for row in rows[:PAGE]:
                    try:
                        ref=self._reference(db,pid,row['session_id'],row['operation_id'])
                        summary,_=self._baseline(db,pid,ref)
                        entries.append({'eligible':True,'reference':ref.model_dump(),'version_label':summary['version_label'],
                            'notes_excerpt':summary['notes_excerpt'],'warnings':summary['warnings'],
                            'counts':summary['counts'],'summary_sha256':digest(summary)})
                    except ContextReadLimit:raise
                    except (Conflict,InvalidInput,NotFound,ValueError,KeyError,TypeError):
                        unavailable+=1;entries.append({'eligible':False,'locator':dict(row),'reason':'FROZEN_DELIVERY_UNAVAILABLE'})
            requirements._unchanged(self.sessions,pid,sid,body,root)
            return _bounded({'kind':'ORIGINAL_WORK_MAINTENANCE_DELIVERIES','version':1,'project_id':pid,
                'scope':_scope(body),'query_sha256':digest(body.model_dump()),'root_basis':root['basis'],
                'root_sha256':root['sha256'],'entries':entries,'unavailable':unavailable,
                'next_offset':body.offset+PAGE if len(rows)>PAGE else None,'read_only':True,**_flags()})

    def delivery(self,pid,sid,body):
        body=WorkMaintenanceDelivery.model_validate(body)
        with context_read_scope():
            root=requirements._root(self.sessions,pid,sid,body)
            with self.store.connect() as db:summary,sections=self._baseline(db,pid,body.delivery_ref)
            source=sections[body.section];rows=[]
            for ordinal,item in enumerate(source[body.offset:body.offset+PAGE],body.offset):
                value={'ordinal':ordinal,'record_sha256':digest(item)}
                if body.section=='REQUIREMENTS':
                    op=item['operation'];value.update(requirement_ref=op['requirement_ref'],
                        requirement_ids=op['requirement_ids'],acceptance_ids=op['acceptance_ids'],
                        source_authority=op.get('source_authority'),review_status=item['impact']['review_status'],
                        selected_snapshot_included=item['selected_snapshot'] is not None)
                elif body.section=='CASES':
                    op=item['operation'];proof=op['proof'];value.update(operation_id=op['operation_id'],request_sha256=op['request_sha256'],
                        mapping_key=op['mapping_key'],selection=op['selection'],
                        requirement_status=item['requirement_status'],report_status=item['report_status'],
                        report_key=item['report_key'],proof_sha256=digest(proof),
                        case_label=proof['case_label'],command_status=proof['command_status'],
                        command_exit_code=proof['command_exit_code'],report_outcome=proof['report_outcome'],
                        reason_codes=proof['reason_codes'])
                else:value['record']=item
                rows.append(value)
            requirements._unchanged(self.sessions,pid,sid,body,root)
            return _bounded({'kind':'ORIGINAL_WORK_MAINTENANCE_DELIVERY','version':1,'project_id':pid,
                'scope':_scope(body),'query_sha256':digest(body.model_dump()),'root_sha256':root['sha256'],
                'summary':summary,'summary_sha256':digest(summary),'section':body.section,'entries':rows,
                'total':len(source),'next_offset':body.offset+PAGE if body.offset+PAGE<len(source) else None,
                'read_only':True,**_flags()})

    def _operation(self,db,pid,sid,op):
        row=db.execute('''SELECT * FROM original_work_maintenance_operations
            WHERE project_id=? AND session_id=? AND operation_id=?''',(pid,sid,op)).fetchone()
        if row is None:raise NotFound('Original maintenance decision does not exist')
        request=_checked(row['request_json'],row['request_sha256']);body=WorkMaintenanceChange.model_validate(request)
        r=_checked(row['receipt_json'],row['receipt_sha256'])
        summary=r['baseline'];review={'schema':'cirven.original-work-maintenance-review.v1',
            'query':_query(body),'baseline_sha256':digest(summary)}
        if (request!=body.model_dump() or body.operation_id!=op or body.session_ref.id!=sid
                or r['project_id']!=pid or r['operation_id']!=op or r['request_sha256']!=row['request_sha256']
                or r['root_sha256']!=body.root_sha256 or digest(r['root_basis'])!=body.root_sha256
                or r['root_basis']['session_ref']!=body.session_ref.model_dump()
                or r['root_basis']['workspace_ref']!=body.workspace_ref.model_dump()
                or r['root_basis']['project_id']!=pid or r['root_basis']['root_operation_id']!=body.root_operation_id
                or type(r['revision'])is not int or r['revision']!=row['revision'] or r['revision']!=body.expected_revision+1
                or row['root_operation_id']!=body.root_operation_id or r['action']!=body.action
                or r.get('kind')!='ORIGINAL_WORK_MAINTENANCE_DECISION' or r.get('version')!=1
                or r['note']!=body.note or summary['reference']!=body.delivery_ref.model_dump()
                or r['baseline_sha256']!=digest(summary) or body.review_sha256!=digest(review)
                or not _same_flags(r,_flags())):
            raise Conflict('Original maintenance request, receipt or baseline lineage differs')
        return r,request

    def _head(self,db,pid,sid,root):
        rows=db.execute('''SELECT operation_id FROM original_work_maintenance_operations
            WHERE project_id=? AND session_id=? AND root_operation_id=? ORDER BY revision LIMIT 129''',
            (pid,sid,root['basis']['root_operation_id'])).fetchall()
        if len(rows)>MAX_ROOT_OPERATIONS:raise Conflict('Maintenance history capacity exceeded')
        active={};history=[]
        for revision,row in enumerate(rows,1):
            r,_=self._operation(db,pid,sid,row['operation_id'])
            if r['revision']!=revision or r['root_sha256']!=root['sha256']:
                raise Conflict('Maintenance history revision or original root differs')
            key=digest(r['baseline']['reference'])
            if r['action']=='ASSOCIATE':
                if key in active:raise Conflict('Maintenance baseline was already active')
                active[key]=r
            else:
                if key not in active:raise Conflict('Maintenance removal has no exact original association')
                del active[key]
            history.append(r)
        if len(active)>MAX_LINKS:raise Conflict('Maintenance baseline count exceeds its bound')
        return len(history),list(active.values()),history

    def frozen_context(self,db,pid,sid,root):
        """Bounded original-work provenance, without reopening a baseline.

        Consumers freeze this value into their own reviewed intent. The exact
        decision/receipt and original delivery hashes keep full notes/evidence
        discoverable in the existing maintenance reader; they are not new file
        or command authority. Do not recursively embed old delivery manifests.
        """
        revision,active,_=self._head(db,pid,sid,root)
        associations=[]
        for receipt in active:
            summary=receipt['baseline'];note=receipt['note']
            associations.append({'decision_ref':{'operation_id':receipt['operation_id'],
                'request_sha256':receipt['request_sha256'],'receipt_sha256':digest(receipt),
                'revision':receipt['revision']},'delivery_ref':summary['reference'],
                'baseline_sha256':receipt['baseline_sha256'],'version_label':summary['version_label'],
                'note_excerpt':note[:240],'note_sha256':hashlib.sha256(note.encode('utf-8')).hexdigest(),
                'note_length':len(note),'warnings_excerpt':summary['warnings'][:8],
                'warnings_sha256':digest(summary['warnings']),'warnings_count':len(summary['warnings']),
                'evidence_included':summary['evidence_included'],'counts':summary['counts']})
        return _bounded({'schema':'cirven.original-work-maintenance-context.v1',
            'project_id':pid,'root_sha256':root['sha256'],'revision':revision,
            'associations':associations,'read_only':True,**_flags()},MAX_RECORD)

    def require_context(self,db,pid,sid,root,expected):
        # Only a NEW intent/admission uses this CAS. Read-only historical
        # recovery and an already frozen delivery never adopt a newer head.
        if self.frozen_context(db,pid,sid,root)!=expected:
            raise Conflict('Original maintenance associations changed; review the exact basis again')

    def _review(self,db,pid,sid,body,root):
        if body.root_sha256!=root['sha256']:raise Conflict('Review the exact maintenance work')
        revision,active,_=self._head(db,pid,sid,root)
        if revision!=body.expected_revision:raise Conflict('Maintenance associations changed; review again')
        ref=body.delivery_ref.model_dump();old=next((r for r in active if r['baseline']['reference']==ref),None)
        if body.action=='REMOVE':
            if old is None:raise Conflict('Remove an exact active maintenance baseline')
            summary=old['baseline']
        else:
            if old is not None:raise Conflict('This exact delivery is already associated')
            if len(active)>=MAX_LINKS:raise Conflict('Maintenance already references eight baselines')
            summary,_=self._baseline(db,pid,body.delivery_ref)
        basis={'schema':'cirven.original-work-maintenance-review.v1','query':_query(body),
            'baseline_sha256':digest(summary)}
        return summary,basis

    def prepare(self,pid,sid,body):
        body=WorkMaintenancePrepare.model_validate(body);_bounded(body.model_dump(),MAX_RECORD)
        with context_read_scope():
            root=requirements._root(self.sessions,pid,sid,body)
            with self.store.connect() as db:summary,basis=self._review(db,pid,sid,body,root)
            requirements._unchanged(self.sessions,pid,sid,body,root)
            return _bounded({'kind':'ORIGINAL_WORK_MAINTENANCE_REVIEW','version':1,'project_id':pid,
                'scope':_scope(body),'root_basis':root['basis'],'root_sha256':root['sha256'],
                'query_sha256':digest(body.model_dump()),'basis':basis,'review_sha256':digest(basis),
                'baseline':summary,'baseline_sha256':digest(summary),'read_only':True,**_flags()})

    def change(self,pid,sid,body):
        body=WorkMaintenanceChange.model_validate(body);request=_bounded(body.model_dump(),MAX_RECORD)
        with context_read_scope():
            root=requirements._root(self.sessions,pid,sid,body)
            if body.root_sha256!=root['sha256']:raise Conflict('Maintenance work identity changed')
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                previous=db.execute('''SELECT request_sha256 FROM original_work_maintenance_operations
                    WHERE project_id=? AND session_id=? AND operation_id=?''',(pid,sid,body.operation_id)).fetchone()
                if previous is not None:
                    if previous['request_sha256']!=digest(request):raise Conflict('Original maintenance ID has different content')
                    r,_=self._operation(db,pid,sid,body.operation_id)
                    return self._lookup_response(pid,body,r,request)
                count=db.execute('SELECT count(*) FROM original_work_maintenance_operations').fetchone()[0]
                if count>=MAX_OPERATIONS or body.expected_revision>=MAX_ROOT_OPERATIONS:
                    raise Conflict('Maintenance history reached its storage bound')
                summary,basis=self._review(db,pid,sid,body,root)
                if body.review_sha256!=digest(basis):raise Conflict('Review the exact frozen delivery and maintenance reason')
                requirements._unchanged(self.sessions,pid,sid,body,root)
                r=_bounded({'kind':'ORIGINAL_WORK_MAINTENANCE_DECISION','version':1,'project_id':pid,
                    'operation_id':body.operation_id,'request_sha256':digest(request),'root_basis':root['basis'],
                    'root_sha256':root['sha256'],'revision':body.expected_revision+1,'action':body.action,
                    'note':body.note,'baseline':summary,'baseline_sha256':digest(summary),
                    'recorded_at':stamp(),**_flags()},MAX_RECORD)
                db.execute('''INSERT INTO original_work_maintenance_operations VALUES (?,?,?,?,?,?,?,?,?)''',
                    (pid,sid,body.operation_id,body.root_operation_id,r['revision'],canonical(request),
                        digest(request),canonical(r),digest(r)))
                return self._lookup_response(pid,body,r,request,metadata_writes=1)

    def _lookup_response(self,pid,body,receipt,request,*,metadata_writes=0):
        q={**_scope(body),'operation_id':receipt['operation_id'],'request_sha256':digest(request)}
        if (receipt['root_sha256']!=digest(receipt['root_basis'])
                or receipt['root_basis']['root_operation_id']!=body.root_operation_id
                or receipt['root_basis']['session_ref']!=body.session_ref.model_dump()
                or receipt['root_basis']['workspace_ref']!=body.workspace_ref.model_dump()
                or body.generation<request['generation']):raise Conflict('Original maintenance scope differs')
        return _bounded({'kind':'ORIGINAL_WORK_MAINTENANCE_OPERATION','version':1,'project_id':pid,
            'scope':q,'query_sha256':digest(q),'original_request':request,'receipt':receipt,
            'receipt_sha256':digest(receipt),'read_only':metadata_writes==0,
            'current_call_metadata_writes':metadata_writes,'metadata_only':True,**_flags()})

    def operation(self,pid,sid,body):
        body=WorkMaintenanceOperation.model_validate(body)
        with context_read_scope():
            root=requirements._root(self.sessions,pid,sid,body)
            with self.store.connect() as db:r,request=self._operation(db,pid,sid,body.operation_id)
            if digest(request)!=body.request_sha256 or r['root_sha256']!=root['sha256']:
                raise Conflict('Original maintenance request SHA or work differs')
            requirements._unchanged(self.sessions,pid,sid,body,root)
            return self._lookup_response(pid,body,r,request)

    def inspect(self,pid,sid,body):
        body=WorkMaintenanceInspect.model_validate(body)
        with context_read_scope():
            root=requirements._root(self.sessions,pid,sid,body)
            with self.store.connect() as db:revision,active,history=self._head(db,pid,sid,root)
            entries=[{**{key:r[key] for key in ['operation_id','request_sha256','revision','action',
                'recorded_at','baseline_sha256']},'note_excerpt':r['note'][:240]}
                for r in history[body.history_offset:body.history_offset+PAGE]]
            active_entries=[]
            for r in active:
                baseline={key:r['baseline'][key] for key in ['reference','version_label','artifact','counts','warnings']}
                active_entries.append({**{key:r[key] for key in ['operation_id','request_sha256','revision',
                    'recorded_at','baseline_sha256']},'note_excerpt':r['note'][:240],'baseline':baseline})
            requirements._unchanged(self.sessions,pid,sid,body,root)
            return _bounded({'kind':'ORIGINAL_WORK_MAINTENANCE','version':1,'project_id':pid,
                'scope':_scope(body),'query_sha256':digest(body.model_dump()),'root_basis':root['basis'],
                'root_sha256':root['sha256'],'revision':revision,'active':active_entries,'history':entries,
                'next_offset':body.history_offset+PAGE if body.history_offset+PAGE<len(history) else None,
                'read_only':True,**_flags()})

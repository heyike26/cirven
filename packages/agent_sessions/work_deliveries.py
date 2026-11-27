"""Portable saved-file deliveries on the existing original work root.

The bundle is an immutable export, not a build, deployment, restore operation,
automatic acceptance or a new execution loop. Failed/unknown evidence survives.
Only explicit source references are read; no tree scan, shell or extraction.
"""
from io import BytesIO
import json
import os
import re
import shutil
import unicodedata
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED,BadZipFile
from urllib.parse import quote,urlencode
from pydantic import Field,model_validator
from packages.domain.questions import Id,Hash,stamp
from packages.domain.local_workspaces import FileSource,relative,canonical,digest,sha
from packages.domain.local_materials import LocalMaterials
from packages.domain.paths import resolve_data_path
from packages.domain.context_read_budget import context_read_scope,source_bytes
from packages.domain.store import Conflict,InvalidInput,NotFound
from . import work_requirements as requirements,work_test_cases as cases
from .test_reports import _load_command_report,CommandTestReportInput

MAX_FILES=64
MAX_PAYLOAD=16*1024*1024
MAX_MANIFEST=2*1024*1024
MAX_ARCHIVE=24*1024*1024
MAX_DELIVERIES=32
MAX_ATTEMPTS=4
MAX_REPORTS=8
MAX_PRIVATE_INTENTS=256
MAX_PRIVATE_RESERVED_BYTES=512*1024*1024
README=('Cirven saved-file delivery\n\nThis ZIP contains explicitly selected saved versions. '
    'It is not a build, deployment, automatic restore or acceptance result. '
    'Unsaved editor contents are excluded. Keep manifest.json and verify the SHA-256 of each file. '
    'Failed, skipped, unknown and unavailable evidence remains in the manifest. '
    'Requirement text is an exported snapshot, not a replacement authoritative specification. '
    'Maintenance references are frozen historical metadata, not resolved issues or source permissions. '
    'Their excerpts and hashes locate the full original decisions and delivery evidence. '
    'Only file bytes are exported; ACLs, timestamps and executable permissions are not restored.\n\n'
    '此包包含明确选择的已保存文件版本与证据快照；不含编辑器未保存内容。'
    '打包不代表构建、部署、恢复或任务验收通过。请保留 manifest.json 并核对文件 SHA-256。'
    '失败、跳过、未知及不可用证据不会改成通过；文件权限和时间等属性未作恢复。'
    '维护关联仅为冻结的历史元数据，不代表故障解决或旧工作区授权；摘录与哈希用于找回完整原说明及交付证据。\n').encode()


def archive_name(path):
    relative(path)
    for part in path.split('/'):
        device=part.split('.')[0].upper()
        if (part.rstrip(' .')!=part or re.search(r'[<>"|?*\x00-\x1f]',part)
                or device in {'CON','PRN','AUX','NUL',*[f'COM{i}' for i in range(1,10)],*[f'LPT{i}' for i in range(1,10)]}
                or device in {'COM¹','COM²','COM³','LPT¹','LPT²','LPT³'}):
            raise InvalidInput('The selected filename is not portable to the Windows delivery target')
    return 'files/'+path


class WorkDeliveryPrepare(requirements.WorkRequirementScope):
    root_sha256:Hash
    expected_revision:int=Field(ge=0,le=MAX_DELIVERIES,strict=True)
    version_label:str=Field(min_length=1,max_length=100)
    notes:str=Field(min_length=1,max_length=4000)
    files:list[FileSource]=Field(min_length=1,max_length=MAX_FILES)
    include_evidence:bool=Field(default=True,strict=True)

    @model_validator(mode='after')
    def exact_files(self):
        names=[]
        for f in self.files:
            if (f.workspace_id!=self.workspace_ref.id or f.binding_revision!=self.workspace_ref.revision
                    or f.binding_sha256!=self.workspace_ref.binding_sha256):
                raise ValueError('Each selected file must use this original workspace binding')
            names.append(unicodedata.normalize('NFC',archive_name(f.path)).casefold())
        if len(set(names))!=len(names):raise ValueError('Selected archive paths collide on the delivery target')
        return self


class WorkDeliveryCreate(WorkDeliveryPrepare):
    operation_id:Id
    review_sha256:Hash
    review_confirmed:bool=Field(strict=True)
    known_gaps_acknowledged:bool=Field(default=False,strict=True)

    @model_validator(mode='after')
    def explicit_review(self):
        if not self.review_confirmed:raise ValueError('Review the selected saved files and evidence first')
        return self


class WorkDeliveryLookup(requirements.WorkRequirementOperation):pass


class WorkDeliveryRetain(WorkDeliveryLookup):
    statement:str=Field(min_length=1,max_length=4000)


class WorkDeliveryList(requirements.WorkRequirementScope):
    offset:int=Field(default=0,ge=0,le=MAX_DELIVERIES,strict=True)


def _query(body):
    return WorkDeliveryPrepare.model_validate({key:body.model_dump()[key] for key in WorkDeliveryPrepare.model_fields})


def _flags():
    return {'model_calls':0,'commands_started':0,'source_files_written':0,'permission_granted':False,
        'build_performed':False,'deployment_performed':False,'restore_performed':False,
        'original_task_status_changed':False,'whole_task_acceptance':'NOT_ASSESSED',
        'target_environment_verified':False,'all_dependencies_captured':False,
        'unsaved_editor_contents_included':False,'atomic_workspace_snapshot':False,
        'file_metadata_restoration':False,'source_writer_attribution':'NOT_PROVEN'}


def _require_dispatch_ack(sessions,pid,sid,root):
    """A preallocated native message ID is not evidence of an acknowledged POST.

    Keep original metadata lookup/download/retention readable without this gate.
    Only new archive work requires the exact persisted original acknowledgement.
    This acknowledgement is not an execution, test or whole-task success claim.
    """
    basis=root['basis'];operation=sessions.operation(pid,sid,basis['root_operation_id'])
    if (operation.get('native_dispatch_acknowledged') is not True
            or operation.get('request_sha256')!=basis['request_sha256']
            or operation.get('generation')!=basis['root_generation']
            or operation.get('original_session_id')!=basis['native_session_id']
            or operation.get('native_message_id')!=basis['native_message_id']):
        raise Conflict('The original message dispatch acknowledgement is unavailable; no new delivery archive was started')


def _checked_json(raw,checksum,limit):
    try:
        if len(raw.encode())>limit:raise ValueError
        v=json.loads(raw)
        if digest(v)!=checksum:raise ValueError
        return v
    except (ValueError,TypeError,KeyError):raise Conflict('Original delivery record integrity failed')from None


class WorkDeliveries:
    def __init__(self,sessions):
        self.sessions=sessions;self.store=sessions.store;self.ws=sessions.workspaces
        self.materials=LocalMaterials(self.ws)

    def _path(self,pid,sid,operation,*parts):
        return self.sessions._path(pid,sid,'work-deliveries',digest(operation),*parts)

    def _lock(self,pid,sid,root):
        return self.materials._lock(self.sessions._path(pid,sid,'work-delivery-locks',digest(root)),blocking=False)

    def _intent(self,connection,pid,sid,op):
        row=connection.execute('''SELECT * FROM original_work_delivery_intents
            WHERE project_id=? AND session_id=? AND operation_id=?''',(pid,sid,op)).fetchone()
        if row is None:raise NotFound('Original delivery operation does not exist')
        request=_checked_json(row['request_json'],row['request_sha256'],131072)
        manifest=_checked_json(row['manifest_json'],row['manifest_sha256'],MAX_MANIFEST)
        body=WorkDeliveryCreate.model_validate(request)
        basis={'schema':'cirven.original-work-delivery-review.v1','query':_query(body).model_dump(),
            'manifest_sha256':row['manifest_sha256'],'root_sha256':body.root_sha256,'expected_revision':body.expected_revision}
        if (body.operation_id!=op or body.root_operation_id!=row['root_operation_id']
                or body.root_sha256!=row['root_sha256'] or body.expected_revision!=row['expected_revision']
                or digest(basis)!=body.review_sha256 or manifest['project_id']!=pid
                or manifest['session_ref']!=body.session_ref.model_dump()
                or manifest['workspace_ref']!=body.workspace_ref.model_dump()
                or manifest['revision']!=body.expected_revision+1
                or manifest['version_label']!=body.version_label or manifest['notes']!=body.notes
                or manifest['root_sha256']!=body.root_sha256 or manifest['files']!=[
                    {**item,'archive_path':archive_name(item['source_ref']['path'])} for item in manifest['files']]
                or [item['source_ref'] for item in manifest['files']]!=[f.model_dump() for f in body.files]):
            raise Conflict('Delivery original intent differs from its selected files and root')
        if (any(type(f['byte_length'])is not int or not 0<=f['byte_length']<=MAX_PAYLOAD for f in manifest['files'])
                or manifest['file_bytes']!=sum(f['byte_length'] for f in manifest['files'])
                or manifest['file_bytes']>MAX_PAYLOAD):raise Conflict('Delivery frozen byte inventory differs')
        format=manifest.get('archive_format')
        if (not isinstance(format,dict) or format.get('version')!=1 or not isinstance(format.get('readme_utf8'),str)
                or len(format['readme_utf8'].encode())>8192):raise Conflict('Frozen delivery archive format is unavailable')
        return {'request':request,'body':body,'request_sha256':row['request_sha256'],
            'manifest':manifest,'manifest_sha256':row['manifest_sha256'],'created_at':row['created_at']}

    def _receipt(self,connection,pid,sid,op,intent=None):
        row=connection.execute('''SELECT revision,receipt_json,receipt_sha256 FROM original_work_delivery_receipts
            WHERE project_id=? AND session_id=? AND operation_id=?''',(pid,sid,op)).fetchone()
        if row is None:return None
        intent=intent or self._intent(connection,pid,sid,op)
        value=_checked_json(row['receipt_json'],row['receipt_sha256'],131072)
        if (value['operation_id']!=op or value['project_id']!=pid or value['session_ref']!=intent['request']['session_ref']
                or value['root_operation_id']!=intent['body'].root_operation_id
                or value['revision']!=row['revision'] or value['revision']!=intent['body'].expected_revision+1
                or value['request_sha256']!=intent['request_sha256'] or value['manifest_sha256']!=intent['manifest_sha256']
                or value['parent']!=intent['manifest']['parent'] or value['files']!=intent['manifest']['files']):
            raise Conflict('Delivery result differs from its original immutable intent')
        return value

    def _retention(self,connection,pid,sid,op,intent):
        row=connection.execute('''SELECT receipt_json,receipt_sha256 FROM original_work_delivery_retentions
            WHERE project_id=? AND session_id=? AND operation_id=?''',(pid,sid,op)).fetchone()
        if row is None:return None
        v=_checked_json(row['receipt_json'],row['receipt_sha256'],16384)
        if v['request_sha256']!=intent['request_sha256'] or v['operation_id']!=op:
            raise Conflict('Retained delivery outcome differs from its original intent')
        return v

    def _head(self,connection,pid,sid,root):
        rows=connection.execute('''SELECT operation_id FROM original_work_delivery_intents
            WHERE project_id=? AND session_id=? AND root_operation_id=? ORDER BY created_at,operation_id''',
            (pid,sid,root['basis']['root_operation_id'])).fetchall()
        if len(rows)>MAX_DELIVERIES:raise Conflict('Delivery history exceeds its bound')
        receipts=[];pending=[]
        for row in rows:
            intent=self._intent(connection,pid,sid,row['operation_id'])
            if intent['body'].root_sha256!=root['sha256']:raise Conflict('Delivery history belongs to another original root')
            result=self._receipt(connection,pid,sid,row['operation_id'],intent)
            if result is not None:receipts.append(result)
            elif self._retention(connection,pid,sid,row['operation_id'],intent) is None:
                pending.append({'operation_id':row['operation_id'],'request_sha256':intent['request_sha256']})
        receipts.sort(key=lambda r:r['revision'])
        if len(pending)>1 or [r['revision'] for r in receipts]!=list(range(1,len(receipts)+1)):
            raise Conflict('Delivery version history or pending original operation is inconsistent')
        for i,receipt in enumerate(receipts):
            parent=None if i==0 else {key:receipts[i-1][key] for key in ['operation_id','request_sha256','manifest_sha256','revision']}
            if receipt['parent']!=parent:raise Conflict('Delivery original parent version differs')
        return receipts,pending,len(rows)

    def _saved(self,pid,source):
        if source.project_id!=pid:raise InvalidInput('Delivery file belongs to another project')
        # Historical private version bytes do not need a live source directory.
        # Preparation separately checks that the selected saved sources are current.
        binding=self.ws.binding_record(pid,source.workspace_id,source.binding_revision)
        if binding['binding_sha256']!=source.binding_sha256:raise Conflict('Delivery workspace binding changed')
        self.ws._private(binding,source.path)
        version,raw=self.ws._version(binding,source.path,source.revision,_exact_revision=True,_with_bytes=True)
        if not version or self.ws._ref(version)!=source.model_dump():raise Conflict('Selected saved file version changed')
        if len(raw)>MAX_PAYLOAD or sha(raw)!=source.sha256:raise Conflict('Selected saved file bytes changed or exceed the delivery bound')
        return raw

    def _evidence(self,pid,sid,body,root):
        # Local import avoids the maintenance reader/delivery validator cycle.
        from .work_maintenance import WorkMaintenance
        warnings=[];reports={};report_blobs={};bundles={};source_cache={};requirement_rows=[]
        with self.store.connect() as connection:
            connection.execute('BEGIN')
            req_revision,links=requirements._head(connection,pid,sid,root)
            case_revision,mappings=cases._head(connection,pid,sid,root)
            maintenance=WorkMaintenance(self.sessions).frozen_context(connection,pid,sid,root)
            for link in links:
                try:impact=requirements._impact(connection,pid,link,source_cache,bundles=bundles)
                except requirements._RequirementReadBudget:impact={'operation_id':link['operation_id'],'review_status':'NOT_CHECKED_BUDGET'}
                selected=None
                if body.include_evidence:
                    try:
                        bundle=requirements._linked_bundle(connection,pid,link,cache=bundles)
                        selected=requirements._selection(bundle,link['requirement_ids'],link['acceptance_ids'])
                    except (Conflict,InvalidInput,NotFound,requirements._RequirementReadBudget):pass
                requirement_rows.append({'operation':link,'impact':impact,'selected_snapshot':selected})
                if impact['review_status'] not in {'CURRENT','SELECTED_UNCHANGED_NEWER_BUNDLE'}:
                    warnings.append('REQUIREMENT_BASIS_NEEDS_REVIEW')
            if not links:warnings.append('NO_REQUIREMENTS_ASSOCIATED')
        mapping_rows=[]
        for mapping in mappings:
            linked=next((r for r in requirement_rows if r['operation']['operation_id']==mapping['selection']['requirement_link_operation_id']
                and r['operation']['request_sha256']==mapping['selection']['requirement_link_request_sha256']),None)
            requirement_status='REQUIREMENT_LINK_CHANGED' if linked is None else linked['impact']['review_status']
            if requirement_status not in {'CURRENT','SELECTED_UNCHANGED_NEWER_BUNDLE'}:warnings.append('CASE_REQUIREMENT_ASSOCIATION_NEEDS_REVIEW')
            item={'operation':mapping,'requirement_status':requirement_status,'report_status':'NOT_INCLUDED','report_key':None}
            if body.include_evidence:
                source=cases.WorkCaseSource.model_validate(mapping['selection']['source'])
                key=digest(source.model_dump(exclude={'case_ordinal','case_sha256','report_ref'}))
                if key not in reports:
                    if len(reports)>=MAX_REPORTS:raise Conflict('Delivery selects more than eight original report snapshots')
                    try:
                        q=CommandTestReportInput(**{k:body.model_dump()[k] for k in ['session_ref','generation','workspace_ref']},
                            **source.model_dump(exclude={'case_ordinal','case_sha256','report_ref'}))
                        loaded=_load_command_report(self.sessions,pid,sid,q,include_raw=True)
                        if loaded['basis']['root_operation_id']!=body.root_operation_id or loaded['report_ref']!=source.report_ref.model_dump():
                            raise Conflict('Mapped report belongs to another exact original work')
                        reports[key]={'status':'SAVED_REPORT','basis':loaded['basis'],'report_ref':loaded['report_ref'],
                            'summary':loaded['parsed']['summary'],'command_status':loaded['command']['status'],
                            'command_exit_code':loaded['command']['exit_code'],'association':loaded['association'],
                            'reason_codes':loaded['reasons'],'raw_sha256':sha(loaded['raw_bytes']),
                            'byte_length':len(loaded['raw_bytes']),'archive_path':'evidence/reports/'+key+'.xml'}
                        report_blobs[sha(loaded['raw_bytes'])]=loaded['raw_bytes']
                    except (Conflict,InvalidInput,NotFound):
                        reports[key]={'status':'UNAVAILABLE','source':source.model_dump(),'archive_path':None}
                report=reports[key];item.update(report_status=report['status'],report_key=key)
                if report['status']!='SAVED_REPORT':warnings.append('REPORT_UNAVAILABLE')
                elif (report['summary']['outcome']!='PASSED' or report['command_status']!='EXITED'
                        or report['command_exit_code']!=0 or report['reason_codes']):warnings.append('REPORT_OR_COMMAND_NEEDS_REVIEW')
            mapping_rows.append(item)
        if not mappings:warnings.append('NO_TEST_CASES_ASSOCIATED')
        if not body.include_evidence:warnings.append('EVIDENCE_CONTENT_NOT_INCLUDED')
        return {'requirement_revision':req_revision,'case_revision':case_revision,'maintenance':maintenance,
            'requirements':requirement_rows,'case_mappings':mapping_rows,'reports':reports},warnings,report_blobs

    def _require_reviewed_evidence(self,connection,pid,sid,root,evidence):
        from .work_maintenance import WorkMaintenance
        if (requirements._head(connection,pid,sid,root)[0]!=evidence['requirement_revision']
                or cases._head(connection,pid,sid,root)[0]!=evidence['case_revision']):
            raise Conflict('Original requirement or test-case associations changed during review')
        WorkMaintenance(self.sessions).require_context(connection,pid,sid,root,evidence['maintenance'])

    def _prepare(self,pid,sid,body):
        if len(canonical(body.model_dump()).encode())>131072:raise InvalidInput('Delivery review selection exceeds its request bound')
        root=requirements._root(self.sessions,pid,sid,body)
        if body.root_sha256!=root['sha256']:raise Conflict('Review the exact original work before preparing a delivery')
        _require_dispatch_ack(self.sessions,pid,sid,root)
        with self.store.connect() as connection:
            receipts,pending,count=self._head(connection,pid,sid,root)
        if body.expected_revision!=len(receipts):raise Conflict('Delivery versions changed; review the latest saved version')
        files=[];total=0
        for source in body.files:
            raw=self._saved(pid,source);total+=len(raw)
            if total>MAX_PAYLOAD:raise Conflict('Selected delivery files exceed 16 MiB in total')
            files.append({'source_ref':source.model_dump(),'byte_length':len(raw),'archive_path':archive_name(source.path)})
        if any(row['status']!='CURRENT' for row in self.ws.check_saved_sources(pid,body.files,_exact_revisions=True)):
            raise Conflict('A selected saved file changed during delivery preparation')
        evidence,warnings,report_blobs=self._evidence(pid,sid,body,root)
        if root['status'] not in {'COMPLETED','SUCCEEDED'}:warnings.append('ROOT_STATUS_IS_NOT_TASK_ACCEPTANCE')
        # Regardless of command success, target/semantic acceptance is independent.
        warnings.append('TARGET_AND_WHOLE_TASK_ACCEPTANCE_NOT_ASSESSED')
        manifest={'schema':'cirven.original-work-delivery.v1','project_id':pid,'session_ref':body.session_ref.model_dump(),
            'root_basis':root['basis'],'root_sha256':root['sha256'],'workspace_ref':body.workspace_ref.model_dump(),
            'revision':len(receipts)+1,'parent':None if not receipts else {
                k:receipts[-1][k] for k in ['operation_id','request_sha256','manifest_sha256','revision']},
            'version_label':body.version_label,'notes':body.notes,'files':files,'file_bytes':total,
            'archive_format':{'version':1,'readme_utf8':README.decode('utf-8')},
            'evidence':evidence,'warnings':sorted(set(warnings)),'scope':'EXPLICIT_SAVED_FILE_SNAPSHOTS_AND_ORIGINAL_EVIDENCE',
            'saved_bytes_observed_current_at_preparation':True,'current_at_download':'NOT_CHECKED',**_flags()}
        if len(canonical(manifest).encode())>MAX_MANIFEST:raise Conflict('The frozen delivery metadata exceeds 2 MiB')
        requirements._unchanged(self.sessions,pid,sid,body,root)
        # Metadata heads are checked again; there is no cross-process atomic file lock.
        with self.store.connect() as connection:
            connection.execute('BEGIN')
            self._require_reviewed_evidence(connection,pid,sid,root,evidence)
        basis={'schema':'cirven.original-work-delivery-review.v1','query':body.model_dump(),
            'manifest_sha256':digest(manifest),'root_sha256':root['sha256'],'expected_revision':len(receipts)}
        review={'kind':'ORIGINAL_WORK_DELIVERY_REVIEW','version':1,'project_id':pid,
            'query_sha256':digest(body.model_dump()),'basis':basis,'review_sha256':digest(basis),
            'manifest_sha256':digest(manifest),'root_basis':root['basis'],'root_sha256':root['sha256'],
            'revision':len(receipts),'parent':manifest['parent'],'version_label':body.version_label,'notes':body.notes,
            'files':files,'file_bytes':total,'warnings':manifest['warnings'],'pending':pending,
            'history_capacity_remaining':MAX_DELIVERIES-count,'read_only':True,'artifact_files_written':0,**_flags()}
        if len(canonical(review).encode())>131072:raise Conflict('Delivery review response exceeds its metadata bound; select fewer files')
        projected={key:manifest[key] for key in ['files','notes','version_label','warnings','parent']}
        if len(canonical(projected).encode())+8192>131072:raise Conflict('Delivery receipt would exceed its metadata bound; select fewer files')
        return root,manifest,review,report_blobs

    def prepare(self,pid,sid,body):
        body=WorkDeliveryPrepare.model_validate(body)
        with context_read_scope():return self._prepare(pid,sid,body)[2]

    def _archive(self,pid,sid,intent):
        manifest=intent['manifest'];data=BytesIO()
        with ZipFile(data,'w',compression=ZIP_DEFLATED,compresslevel=6,allowZip64=False) as archive:
            def add(name,raw):
                info=ZipInfo(name,(1980,1,1,0,0,0));info.create_system=3;info.compress_type=ZIP_DEFLATED;info.external_attr=0o100644<<16
                archive.writestr(info,raw)
                if data.tell()>MAX_ARCHIVE:raise Conflict('Delivery archive exceeds its bound')
            add('manifest.json',(canonical(manifest)+'\n').encode())
            add('READ_ME.txt',manifest['archive_format']['readme_utf8'].encode('utf-8'))
            for f in manifest['files']:
                raw=self._saved(pid,FileSource.model_validate(f['source_ref']))
                if len(raw)!=f['byte_length']:raise Conflict('Frozen delivery file length changed')
                add(f['archive_path'],raw)
            with self.store.connect() as connection:
                for report in manifest['evidence']['reports'].values():
                    if report['status']!='SAVED_REPORT':continue
                    row=connection.execute('''SELECT raw_bytes FROM original_work_delivery_report_blobs
                        WHERE project_id=? AND sha256=?''',(pid,report['raw_sha256'])).fetchone()
                    raw=None if row is None else row['raw_bytes']
                    if not isinstance(raw,bytes) or len(raw)!=report['byte_length'] or sha(raw)!=report['raw_sha256']:
                        raise Conflict('Frozen delivery report bytes changed')
                    add(report['archive_path'],raw)
        raw=data.getvalue()
        if len(raw)>MAX_ARCHIVE:raise Conflict('Delivery archive exceeds its bound')
        return raw

    def _verify_archive(self,raw,manifest):
        expected={'manifest.json':((canonical(manifest)+'\n').encode(),None),
            'READ_ME.txt':(manifest['archive_format']['readme_utf8'].encode('utf-8'),None)}
        for item in manifest['files']:expected[item['archive_path']]=(item['byte_length'],item['source_ref']['sha256'])
        for key,report in manifest['evidence']['reports'].items():
            if report['status']=='SAVED_REPORT':
                if not re.fullmatch('[a-f0-9]{64}',key) or report['archive_path']!='evidence/reports/'+key+'.xml':
                    raise Conflict('Frozen report archive name differs')
                if type(report['byte_length'])is not int or not 0<=report['byte_length']<=262144:
                    raise Conflict('Frozen report byte length exceeds its source bound')
                expected[report['archive_path']]=(report['byte_length'],report['raw_sha256'])
        if len(raw)>MAX_ARCHIVE:raise Conflict('Original archive exceeds its bound')
        try:
            with ZipFile(BytesIO(raw),'r',allowZip64=False) as archive:
                names=archive.namelist()
                if len(names)!=len(expected) or set(names)!=set(expected):raise ValueError
                for name,(size,checksum) in expected.items():
                    info=archive.getinfo(name);maximum=len(size) if isinstance(size,bytes) else size
                    if info.file_size!=maximum or info.compress_type!=ZIP_DEFLATED or info.flag_bits&1:raise ValueError
                    with archive.open(info) as stream:content=stream.read(maximum+1)
                    if len(content)!=maximum or (checksum is None and content!=size) or (checksum is not None and sha(content)!=checksum):raise ValueError
        except (BadZipFile,ValueError,RuntimeError,OSError,KeyError):raise Conflict('Original archive does not match its immutable manifest')from None

    def _finish(self,pid,sid,intent,root,scope):
        op=intent['body'].operation_id;attempts=self._path(pid,sid,op,'attempts');attempts.mkdir(parents=True,exist_ok=True)
        names=sorted(attempts.iterdir())
        if len(names)>MAX_ATTEMPTS:raise Conflict('Original delivery attempt count exceeds its bound')
        if any(p.name!=f'{i:02d}' or p.is_symlink() or not p.is_dir() for i,p in enumerate(names,1)):
            raise Conflict('Original delivery attempt history is invalid')
        raw=None;number=0
        for i,folder in reversed(list(enumerate(names,1))):
            path=resolve_data_path(folder/'package.zip',self.sessions.data_dir)
            if not path.is_file():continue
            try:
                claim=self.sessions._read(resolve_data_path(folder/'request.json',self.sessions.data_dir))
                if (claim['operation_id']!=op or claim['request_sha256']!=intent['request_sha256']
                        or claim['manifest_sha256']!=intent['manifest_sha256'] or claim['attempt']!=i):raise Conflict('Original archive attempt differs')
                candidate=source_bytes(path,MAX_ARCHIVE);self._verify_archive(candidate,intent['manifest'])
            except (Conflict,NotFound,OSError,KeyError):continue
            raw=candidate;number=i;break
        if raw is None:
            if len(names)>=MAX_ATTEMPTS:raise Conflict('Original delivery attempts reached their bound; keep the outcome and review a new request')
            number=len(names)+1;folder=resolve_data_path(attempts/f'{number:02d}',self.sessions.data_dir)
            self.sessions._write(folder/'request.json',{'operation_id':op,'request_sha256':intent['request_sha256'],
                'manifest_sha256':intent['manifest_sha256'],'attempt':number,'started_at':stamp()})
            raw=self._archive(pid,sid,intent);self._verify_archive(raw,intent['manifest'])
            path=resolve_data_path(folder/'package.zip',self.sessions.data_dir)
            with path.open('xb') as stream:
                if stream.write(raw)!=len(raw):raise Conflict('Original delivery archive write is incomplete')
                stream.flush();os.fsync(stream.fileno())
                if os.fstat(stream.fileno()).st_size!=len(raw):raise Conflict('Original delivery archive length changed')
        archive_sha=sha(raw);relative_artifact=f'attempts/{number:02d}/package.zip'
        receipt={'kind':'ORIGINAL_WORK_DELIVERY','version':1,'project_id':pid,'session_ref':intent['body'].session_ref.model_dump(),
            'root_operation_id':intent['body'].root_operation_id,'root_sha256':intent['body'].root_sha256,
            'operation_id':op,'request_sha256':intent['request_sha256'],'revision':intent['body'].expected_revision+1,
            'manifest_sha256':intent['manifest_sha256'],'parent':intent['manifest']['parent'],
            'version_label':intent['body'].version_label,'notes':intent['body'].notes,'recorded_at':stamp(),
            'artifact':{'path':relative_artifact,'sha256':archive_sha,'byte_length':len(raw),'filename':'cirven-delivery-'+str(intent['body'].expected_revision+1)+'.zip'},
            'download_url':'/api/projects/'+quote(pid,safe='')+'/agent-sessions/'+quote(sid,safe='')+'/work-deliveries/'+quote(op,safe='')+'/archive?'+urlencode({
                'root_operation_id':intent['body'].root_operation_id,'request_sha256':intent['request_sha256']}),
            'files':intent['manifest']['files'],'file_bytes':intent['manifest']['file_bytes'],
            'warnings':intent['manifest']['warnings'],'artifact_files_written':1,**_flags()}
        if len(canonical(receipt).encode())>131072:raise Conflict('The completed delivery metadata exceeds its record bound')
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            receipts,pending,_=self._head(connection,pid,sid,root)
            if len(receipts)!=intent['body'].expected_revision or pending!=[{'operation_id':op,'request_sha256':intent['request_sha256']}]:
                raise Conflict('Original delivery version or pending ownership changed')
            requirements._unchanged(self.sessions,pid,sid,scope,root)
            _require_dispatch_ack(self.sessions,pid,sid,root)
            connection.execute('''INSERT INTO original_work_delivery_receipts VALUES (?,?,?,?,?,?,?)''',
                (pid,sid,op,intent['body'].root_operation_id,receipt['revision'],canonical(receipt),digest(receipt)))
        return receipt

    def create(self,pid,sid,body):
        body=WorkDeliveryCreate.model_validate(body);request=body.model_dump()
        if len(canonical(request).encode())>131072:raise InvalidInput('Delivery original request exceeds its bound')
        root=requirements._root(self.sessions,pid,sid,body)
        with self._lock(pid,sid,body.root_operation_id),context_read_scope():
            with self.store.connect() as connection:
                try:existing=self._intent(connection,pid,sid,body.operation_id)
                except NotFound:existing=None
                if existing is not None:
                    if existing['request_sha256']!=digest(request):raise Conflict('IDEMPOTENCY_CONFLICT: delivery original request changed')
                    return self._operation(connection,pid,sid,body,existing,root)
            _,manifest,review,report_blobs=self._prepare(pid,sid,_query(body))
            if review['review_sha256']!=body.review_sha256:raise Conflict('The delivery selection or evidence changed; review again')
            if manifest['warnings'] and not body.known_gaps_acknowledged:raise InvalidInput('Acknowledge the displayed known gaps before packaging')
            with self.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE');receipts,pending,count=self._head(connection,pid,sid,root)
                if pending or count>=MAX_DELIVERIES or len(receipts)!=body.expected_revision:
                    raise Conflict('Resolve the original pending delivery or review the current version')
                self._capacity(connection,manifest)
                requirements._unchanged(self.sessions,pid,sid,body,root)
                _require_dispatch_ack(self.sessions,pid,sid,root)
                self._require_reviewed_evidence(connection,pid,sid,root,manifest['evidence'])
                for checksum,raw in report_blobs.items():
                    previous=connection.execute('''SELECT raw_bytes FROM original_work_delivery_report_blobs
                        WHERE project_id=? AND sha256=?''',(pid,checksum)).fetchone()
                    if previous is not None:
                        if previous['raw_bytes']!=raw:raise Conflict('Existing frozen report bytes differ')
                    else:connection.execute('''INSERT INTO original_work_delivery_report_blobs VALUES (?,?,?)''',(pid,checksum,raw))
                connection.execute('''INSERT INTO original_work_delivery_intents VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (pid,sid,body.operation_id,body.root_operation_id,body.root_sha256,body.expected_revision,
                        canonical(request),digest(request),canonical(manifest),digest(manifest),stamp()))
                intent=self._intent(connection,pid,sid,body.operation_id)
            return self._finish(pid,sid,intent,root,body)

    def _capacity(self,connection,manifest):
        # This shared database allowance covers delivery exports only. It is
        # not the whole application's model/read/storage budget or a cleanup.
        def reserve(m):
            reports=sum(r.get('byte_length',0) for r in m['evidence']['reports'].values())
            # One additional allowance covers frozen SQL metadata/report bytes;
            # private source snapshots already belong to the existing registry.
            return min(MAX_ARCHIVE,m['file_bytes']+reports+len(canonical(m).encode())+524288)*(MAX_ATTEMPTS+1)
        count=connection.execute('SELECT count(*) FROM original_work_delivery_intents').fetchone()[0]
        if count>=MAX_PRIVATE_INTENTS:raise Conflict('Private delivery history capacity reached; original records are kept')
        required=reserve(manifest);total=required
        for row in connection.execute('SELECT manifest_json,manifest_sha256 FROM original_work_delivery_intents'):
            total+=reserve(_checked_json(row['manifest_json'],row['manifest_sha256'],MAX_MANIFEST))
            if total>MAX_PRIVATE_RESERVED_BYTES:raise Conflict('Private delivery storage reservation reached; original packages are kept')
        if total>MAX_PRIVATE_RESERVED_BYTES or shutil.disk_usage(self.sessions.data_dir).free<required+16*1024*1024:
            raise Conflict('Private delivery disk allowance is unavailable; no source files changed')

    def _operation(self,connection,pid,sid,body,intent,root):
        expected=body.request_sha256 if isinstance(body,WorkDeliveryLookup) else digest(body.model_dump())
        if intent['request_sha256']!=expected:
            raise Conflict('Original delivery request SHA differs')
        if intent['body'].root_operation_id!=body.root_operation_id or intent['body'].root_sha256!=root['sha256']:
            raise Conflict('Original delivery belongs to another exact root')
        receipt=self._receipt(connection,pid,sid,intent['body'].operation_id,intent)
        if receipt is not None:return receipt
        return {'kind':'ORIGINAL_WORK_DELIVERY_PENDING','version':1,'project_id':pid,
            'operation_id':intent['body'].operation_id,'request_sha256':intent['request_sha256'],
            'manifest_sha256':intent['manifest_sha256'],'root_sha256':root['sha256'],
            'status':'INCOMPLETE_REVIEW_REQUIRED','retention':self._retention(connection,pid,sid,intent['body'].operation_id,intent),
            'automatic_retry':False,**_flags()}

    def operation(self,pid,sid,body):
        body=WorkDeliveryLookup.model_validate(body);root=requirements._root(self.sessions,pid,sid,body)
        with self.store.connect() as connection:return self._operation(connection,pid,sid,body,self._intent(connection,pid,sid,body.operation_id),root)

    def recovery(self,pid,sid,body):
        """Readonly original packet recovery; no archive, current files or model.

        An absent local journal must not force a new version or a new POST. The
        original frozen manifest and request remain the only continuation input.
        """
        body=WorkDeliveryLookup.model_validate(body)
        with context_read_scope():return self._recovery(pid,sid,body)

    def _recovery(self,pid,sid,body):
        root=requirements._root(self.sessions,pid,sid,body)
        with self.store.connect() as connection:
            intent=self._intent(connection,pid,sid,body.operation_id)
            outcome=self._operation(connection,pid,sid,body,intent,root)
        manifest=intent['manifest']
        preview={key:manifest[key] for key in ['version_label','notes','parent','files','file_bytes','warnings']}
        value={'kind':'ORIGINAL_WORK_DELIVERY_RECOVERY','version':1,'project_id':pid,
            'query_sha256':digest(body.model_dump()),'root_basis':root['basis'],
            'root_sha256':root['sha256'],'manifest_sha256':intent['manifest_sha256'],
            'original_request':intent['request'],'preview':preview,'preview_sha256':digest(preview),
            'outcome':outcome,'read_only':True,'metadata_only':True,'artifact_files_written':0,**_flags()}
        if len(canonical(value).encode())>524288:raise Conflict('Original delivery recovery metadata exceeds its bounded output')
        return value

    def continue_original(self,pid,sid,body):
        body=WorkDeliveryLookup.model_validate(body);root=requirements._root(self.sessions,pid,sid,body)
        with self._lock(pid,sid,body.root_operation_id),context_read_scope():
            with self.store.connect() as connection:
                intent=self._intent(connection,pid,sid,body.operation_id);value=self._operation(connection,pid,sid,body,intent,root)
                if value['kind']=='ORIGINAL_WORK_DELIVERY':return value
                if value['retention'] is not None:raise Conflict('This unknown original delivery was retained; review a new request')
            # Continue the original immutable snapshot, never silently use newer files.
            _require_dispatch_ack(self.sessions,pid,sid,root)
            return self._finish(pid,sid,intent,root,body)

    def retain(self,pid,sid,body):
        body=WorkDeliveryRetain.model_validate(body);root=requirements._root(self.sessions,pid,sid,body)
        with self._lock(pid,sid,body.root_operation_id),self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');intent=self._intent(connection,pid,sid,body.operation_id)
            value=self._operation(connection,pid,sid,body,intent,root)
            if value['kind']=='ORIGINAL_WORK_DELIVERY':return value
            if value['retention'] is not None:
                if value['retention']['statement']!=body.statement:raise Conflict('Original retained-outcome decision already has another statement')
                return value
            receipt={'operation_id':body.operation_id,'request_sha256':body.request_sha256,
                'statement':body.statement,'retained_at':stamp(),'status':'UNKNOWN_RETAINED_NOT_CANCELLED_OR_ROLLED_BACK'}
            if len(canonical(receipt).encode())>16384:raise InvalidInput('Retained outcome statement exceeds its durable byte bound')
            connection.execute('''INSERT INTO original_work_delivery_retentions VALUES (?,?,?,?,?)''',
                (pid,sid,body.operation_id,canonical(receipt),digest(receipt)))
            return {**value,'retention':receipt}

    def listing(self,pid,sid,body):
        body=WorkDeliveryList.model_validate(body);root=requirements._root(self.sessions,pid,sid,body)
        with self.store.connect() as connection:
            receipts,pending,count=self._head(connection,pid,sid,root)
            retained=[]
            rows=connection.execute('''SELECT i.operation_id FROM original_work_delivery_intents i
                JOIN original_work_delivery_retentions r USING(project_id,session_id,operation_id)
                WHERE i.project_id=? AND i.session_id=? AND i.root_operation_id=? ORDER BY i.created_at,i.operation_id''',
                (pid,sid,body.root_operation_id)).fetchall()
            for row in rows:
                intent=self._intent(connection,pid,sid,row['operation_id'])
                kept=self._retention(connection,pid,sid,row['operation_id'],intent)
                retained.append({'operation_id':row['operation_id'],'request_sha256':intent['request_sha256'],
                    'manifest_sha256':intent['manifest_sha256'],'retention':{
                        'status':kept['status'],'retained_at':kept['retained_at'],'statement_excerpt':kept['statement'][:240],
                        'statement_sha256':sha(kept['statement'].encode()),'statement_length':len(kept['statement'])}})
        entries=[]
        for receipt in receipts[body.offset:body.offset+10]:
            entries.append({**{key:receipt[key] for key in ['operation_id','request_sha256','manifest_sha256','revision',
                'parent','version_label','recorded_at','artifact','download_url','warnings','file_bytes']},
                'file_count':len(receipt['files']),'notes_excerpt':receipt['notes'][:240],
                'notes_sha256':sha(receipt['notes'].encode()),'notes_length':len(receipt['notes'])})
        value={'kind':'ORIGINAL_WORK_DELIVERIES','version':1,'project_id':pid,'scope':body.model_dump(exclude={'offset'}),
            'query_sha256':digest(body.model_dump()),'root_basis':root['basis'],'root_sha256':root['sha256'],
            'revision':len(receipts),'pending':pending,'retained':retained,'entries':entries,
            'next_offset':body.offset+10 if body.offset+10<len(receipts) else None,
            'history_capacity_remaining':MAX_DELIVERIES-count,'read_only':True,**_flags()}
        if len(canonical(value).encode())>131072:raise Conflict('Delivery history projection exceeds its bounded output')
        return value

    def download(self,pid,sid,root_operation_id,operation_id,request_sha256):
        # Historical private bundle reads do not touch the current workspace or
        # claim that its editor, native owner or source versions are still current.
        self.store.project(pid)
        with self.store.connect() as connection:
            intent=self._intent(connection,pid,sid,operation_id)
            if intent['request_sha256']!=request_sha256 or intent['body'].root_operation_id!=root_operation_id:
                raise Conflict('Download belongs to another original delivery request')
            receipt=self._receipt(connection,pid,sid,operation_id,intent)
        if receipt is None:raise Conflict('The original delivery has no verified completed package')
        relative_path=receipt['artifact']['path']
        if not re.fullmatch(r'attempts/0[1-4]/package\.zip',relative_path):raise Conflict('Delivery artifact path differs')
        with context_read_scope():
            raw=source_bytes(self._path(pid,sid,operation_id,*relative_path.split('/')),MAX_ARCHIVE)
        if len(raw)!=receipt['artifact']['byte_length'] or sha(raw)!=receipt['artifact']['sha256']:
            raise Conflict('Delivery archive integrity failed; the original receipt is retained')
        return raw,receipt

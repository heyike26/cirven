"""Immutable Word and pending-question artifacts bound to actual proposal sources."""
import base64
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
from typing import Literal
import uuid

from pydantic import BaseModel,ConfigDict,Field
from packages.domain.discovery_schema import RevisionRef

from packages.domain.local_workspaces import FileSource,canonical,digest,sha
from packages.domain.paths import resolve_data_path
from packages.domain.planning_docx import DOCX_MIME,provenance,render_docx,template_info
from packages.domain.store import Conflict,InvalidInput,NotFound


class PlanningReportInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    operation_id:str=Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    expected_revision:int=Field(ge=1,strict=True)
    format:Literal['docx']='docx'
    template_ref:FileSource|None=None
    experience_ref:RevisionRef|None=None
    diagram_refs:list[RevisionRef]=Field(default_factory=list,max_length=20)


class PlanningReports:
    def __init__(self,store,data_dir,proposals,workspaces=None,*,experience_reader=None,discovery_reader=None,diagram_reader=None):
        self.store,self.data_dir,self.proposals,self.workspaces=store,Path(data_dir),proposals,workspaces
        self.experience_reader,self.discovery_reader,self.diagram_reader=experience_reader,discovery_reader,diagram_reader
        if self.data_dir.is_symlink():raise InvalidInput('Report registry root must not be a symlink')

    def _path(self,pid,ident,*parts):
        if not all(re.fullmatch(r'[A-Za-z0-9_-]{1,100}',v) for v in (pid,ident)):
            raise InvalidInput('Unsafe report project/proposal identity')
        return resolve_data_path('/'.join(('planning-reports',pid,ident,*parts)),self.data_dir)

    @staticmethod
    def _json(path):
        if not path.is_file() or path.stat().st_size>16*1024*1024:raise Conflict('Report record is missing or too large')
        try:
            wrapper=json.loads(path.read_bytes());value=wrapper['payload']
            if digest(value)!=wrapper['sha256']:raise ValueError
            return value
        except (ValueError,KeyError,TypeError):raise Conflict('Report record integrity failed') from None

    @staticmethod
    def _bytes(path,raw):
        with path.open('xb') as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())

    @classmethod
    def _write(cls,path,value):cls._bytes(path,(canonical({'payload':value,'sha256':digest(value)})+'\n').encode())

    def _artifact(self,pid,ident,aid):
        if not re.fullmatch(r'[0-9a-f]{64}',aid):raise NotFound('Report artifact does not exist')
        directory=self._path(pid,ident,'artifacts',aid)
        if not directory.exists():raise NotFound('Report artifact does not exist in this project/proposal')
        value=self._json(self._path(pid,ident,'artifacts',aid,'manifest.json'))
        if (value.get('id'),value.get('project_id'),value.get('proposal_id'))!=(aid,pid,ident):
            raise Conflict('Report artifact scope integrity failed')
        for item in value['files'].values():
            path=self._path(pid,ident,'artifacts',aid,item['stored_name'])
            if not path.is_file() or path.stat().st_size!=item['byte_length'] or sha(path.read_bytes())!=item['sha256']:
                raise Conflict('Report artifact bytes failed integrity checking')
        try:
            snapshot=json.loads(self._path(pid,ident,'artifacts',aid,value['files']['source_snapshot']['stored_name']).read_bytes())
            primary=snapshot['discovery'] if snapshot.get('source_kind')=='DISCOVERY' else snapshot['proposal']
            pending=snapshot['pending'];ref=value.get('session_ref') or value['proposal_ref']
            if (digest(snapshot)!=aid or digest(primary['payload'])!=ref['sha256'] or
                    (pending.get('session_ref') or pending.get('proposal_ref'))!=ref or
                    digest({k:v for k,v in pending.items() if k!='sha256'})!=pending['sha256'] or
                    pending['sha256']!=value['pending_snapshot_sha256'] or
                    digest(primary['answers'])!=value['answer_view_sha256']):
                raise ValueError
        except (ValueError,KeyError,TypeError):raise Conflict('Report artifact source binding integrity failed') from None
        return value

    def _result(self,pid,ident,manifest,replayed):
        base=(f'/api/projects/{pid}/discovery/sessions/{ident}/reports/{manifest["id"]}' if manifest.get('source_kind')=='DISCOVERY'
              else f'/api/projects/{pid}/planning/proposals/{ident}/reports/{manifest["id"]}')
        def part(key,suffix):
            value=manifest['files'][key]
            return {k:value[k] for k in ('filename','media_type','sha256','byte_length')}|{'download_url':base+suffix}
        return {'artifact':{'id':manifest['id'],**part('docx','/download')},
                'pending':part('pending_markdown','/pending/download'),
                'pending_json':part('pending_json','/pending/json/download'),
                'source_snapshot':part('source_snapshot','/source/download'),
                'proposal_ref':manifest.get('proposal_ref'),'session_ref':manifest.get('session_ref'),'status':manifest['status'],
                'source_kind':manifest.get('source_kind','PROPOSAL'),'experience_ref':manifest.get('experience_ref'),
                'diagram_refs':manifest.get('diagram_refs',[]),
                'review_status':manifest['review_status'],'answer_view_sha256':manifest['answer_view_sha256'],
                'pending_snapshot_sha256':manifest['pending_snapshot_sha256'],'replayed':replayed,
                **({'coverage_sha256':manifest['coverage_sha256']} if 'coverage_sha256' in manifest else {})}

    def _operation(self,pid,ident,body,request_hash):
        operation=self._path(pid,ident,'operations',body.operation_id)
        if not operation.exists():return None
        request=self._json(operation/'request.json')
        if request['request_sha256']!=request_hash:raise Conflict('IDEMPOTENCY_CONFLICT: report operation binds different input')
        if (operation/'result.json').exists():
            saved=self._json(operation/'result.json')
            return self._result(pid,ident,self._artifact(pid,ident,saved['artifact_id']),True)
        return None

    def _template(self,pid,ref):
        if ref is None:return None
        if self.workspaces is None:raise Conflict('Local template workspace is unavailable')
        if ref.project_id!=pid:raise InvalidInput('Template belongs to another project')
        if not ref.path.lower().endswith(('.docx','.dotx')):raise InvalidInput('Word template must be an inert DOCX or DOTX file')
        value=self.workspaces.read(pid,ref.workspace_id,ref.path,ref.revision)
        if value['source_ref']!=ref.model_dump() or not value['current']:
            raise Conflict('Template source revision/SHA or live original changed')
        raw=base64.b64decode(value['content']) if value['encoding']=='base64' else value['content'].encode()
        if sha(raw)!=ref.sha256:raise Conflict('Template raw source SHA does not match')
        info=template_info(raw)
        if info['normalization']['source_type']!=('DOTX' if ref.path.lower().endswith('.dotx') else 'DOCX'):
            raise InvalidInput('Word template filename does not match its actual DOCX/DOTX package type')
        return raw

    def _check_template(self,pid,ref):
        if ref and self.workspaces.check_sources(pid,[ref])[0]['status']!='CURRENT':
            raise Conflict('Template original changed during report generation')

    @staticmethod
    def _discovery_pending(session):
        payload=session['payload'];resolved={r['question_id'] for r in payload['resolutions']};questions=[]
        for q in payload['candidate']['questions']:
            if q['id'] in resolved:continue
            answers=[a for a in session['answers'] if a['question_id']==q['id'] and a.get('question_sha256')==digest(q)]
            questions.append({**q,'answers':answers,'status':'ANSWERED_PENDING_ACCEPTANCE' if answers else 'AWAITING_ANSWER'})
        value={'session_ref':{k:session[k] for k in ('id','revision','sha256')},'source_sha256':payload['source_receipt']['sha256'],
               'source_current':session['source_current'],'questions':questions,'pending_count':len(questions),'silence_is_approval':False}
        pending={**value,'sha256':digest(value)}
        lines=['# 待确认事项','',f"需求版本：{session['revision']}",'']
        for q in questions:
            lines+=['## '+q['text'],'','原因：'+q['reason'],'状态：'+q['status'],'影响要求：'+', '.join(q['affects_claim_ids']),
                    '所需资料：'+'；'.join(q['requested_materials'])]
            lines+=['已有回答（未接受）：'+a['text'] for a in q['answers']]
        if not questions:lines.append('本版本没有未决问题；不代表工程完整性已验证。')
        return pending,'\n'.join(lines)+'\n'

    def _primary(self,pid,ident,revision,kind):
        if kind=='DISCOVERY':
            if self.discovery_reader is None:raise Conflict('Discovery report source reader is unavailable')
            value=self.discovery_reader(pid,ident,revision)
            if value['project_id']!=pid or value['id']!=ident:raise Conflict('Discovery source scope mismatch')
            return value
        return self.proposals.get(pid,ident,revision)

    def _attachments(self,pid,body,primary,kind):
        experience=None;discovery=primary if kind=='DISCOVERY' else None
        if body.experience_ref:
            if self.experience_reader is None:raise Conflict('Reviewed writing experience reader is unavailable')
            experience=self.experience_reader(pid,body.experience_ref.model_dump())
            if experience['profile_ref']!=body.experience_ref.model_dump() or experience.get('usage')!='EXPERIENCE_ONLY_NOT_PROJECT_FACTS':
                raise Conflict('Writing experience scope/revision/SHA mismatch')
        discovery_ref=({k:primary[k] for k in ('id','revision','sha256')} if kind=='DISCOVERY' else primary['payload'].get('discovery_ref'))
        if kind=='PROPOSAL' and discovery_ref:
            if self.discovery_reader is None:raise Conflict('Discovery context reader is unavailable')
            discovery=self.discovery_reader(pid,discovery_ref['id'],discovery_ref['revision'])
            if discovery['project_id']!=pid or {k:discovery[k] for k in ('id','revision','sha256')}!=discovery_ref or digest(discovery['payload'])!=discovery_ref['sha256']:
                raise Conflict('Proposal discovery context scope/revision/SHA mismatch')
        diagrams=[]
        if len({digest(r.model_dump()) for r in body.diagram_refs})!=len(body.diagram_refs):raise InvalidInput('Diagram artifact refs must be unique')
        for ref in body.diagram_refs:
            if not discovery_ref or self.diagram_reader is None:raise Conflict('Diagram needs a server artifact reader and exact discovery source')
            value=self.diagram_reader(pid,ref.model_dump())
            if value['artifact_ref']!=ref.model_dump() or value['session_ref']!=discovery_ref:
                raise Conflict('Diagram artifact belongs to another discovery snapshot')
            raw=value['png_bytes']
            if sha(raw)!=value['png_sha256'] or digest(value['graph'])!=value['model_sha256']:
                raise Conflict('Diagram bytes/model SHA mismatch')
            actual=next((g for g in discovery['payload']['candidate']['graphs'] if g['id']==value['graph']['id']),None)
            if actual is None or digest(actual)!=value['model_sha256']:raise Conflict('Diagram graph differs from exact discovery source')
            diagrams.append({k:value[k] for k in ('artifact_ref','session_ref','graph','model_sha256','png_sha256')}|
                            {'png_base64':base64.b64encode(raw).decode()}|
                            ({'render_geometry':value['render_geometry'],'render_source':value['render_source']} if 'render_geometry' in value else {}))
        return experience,discovery,diagrams

    def generate_discovery(self,pid,ident,body):return self.generate(pid,ident,body,source_kind='DISCOVERY')

    def generate(self,pid,ident,body,*,source_kind='PROPOSAL'):
        self.store.project(pid)
        request_hash=digest({'source_id':ident,'source_kind':source_kind,**body.model_dump(exclude={'operation_id'})})
        # Replay first: later source changes do not erase a genuine saved artifact.
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay=self._operation(pid,ident,body,request_hash)
            if replay:return replay
            operation=self._path(pid,ident,'operations',body.operation_id)
            operation.mkdir(parents=True,exist_ok=True)
            if not (operation/'request.json').exists():self._write(operation/'request.json',{'request_sha256':request_hash,'input':body.model_dump()})
        try:
            template_raw=self._template(pid,body.template_ref)
        except Exception as exc:
            with self.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                self._write(operation/(uuid.uuid4().hex+'-failure.json'),{'status':'FAILED','exception':type(exc).__name__,
                    'message':str(exc)[:2000],'created_at':datetime.now(timezone.utc).isoformat()})
            raise
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay=self._operation(pid,ident,body,request_hash)
            if replay:return replay
            operation=self._path(pid,ident,'operations',body.operation_id)
            operation.mkdir(parents=True,exist_ok=True)
            if not (operation/'request.json').exists():self._write(operation/'request.json',{'request_sha256':request_hash,'input':body.model_dump()})
            attempt=uuid.uuid4().hex
            try:
                proposal=self._primary(pid,ident,body.expected_revision,source_kind)
                if proposal['current_revision']!=body.expected_revision or proposal['revision']!=body.expected_revision:
                    raise Conflict('Report needs the exact current proposal revision; reload the plan')
                if source_kind=='DISCOVERY':pending,markdown=self._discovery_pending(proposal)
                else:
                    pending=self.proposals.pending(pid,ident,body.expected_revision)
                    markdown=self.proposals.pending_markdown(pid,ident,body.expected_revision)
                ref={'id':ident,'revision':proposal['revision'],'sha256':proposal['sha256']}
                if (pending.get('session_ref') or pending.get('proposal_ref'))!=ref or digest(proposal['payload'])!=proposal['sha256']:
                    raise Conflict('Report proposal/pending snapshot integrity failed')
                self._check_template(pid,body.template_ref)
                snapshot={'schema_version':'eeas.planning-report-snapshot.v1','project_id':pid,
                    'source_kind':source_kind,'pending':pending,'pending_markdown':markdown,
                    'template_ref':body.template_ref.model_dump() if body.template_ref else None,'generator':provenance()}
                experience,discovery,diagrams=self._attachments(pid,body,proposal,source_kind)
                snapshot.update(experience=experience,discovery=discovery,diagrams=diagrams,
                    experience_ref=body.experience_ref.model_dump() if body.experience_ref else None)
                if source_kind=='PROPOSAL':
                    snapshot['proposal']=proposal
                    if proposal.get('coverage') is not None:
                        snapshot['requirement_coverage']=proposal['payload']['requirement_coverage']
                        snapshot['coverage_sha256']=proposal['coverage']['snapshot_sha256']
                aid=digest(snapshot);directory=self._path(pid,ident,'artifacts',aid)
                reused=directory.exists()
                if not reused:
                    raw,render_receipt=render_docx(snapshot,template_raw)
                    self._check_template(pid,body.template_ref)
                    after=self._primary(pid,ident,body.expected_revision,source_kind)
                    review=lambda v:v['readiness']['review_status'] if source_kind=='DISCOVERY' else v['review_status']
                    if (after['current_revision'],after['sha256'],review(after))!=(
                            proposal['current_revision'],proposal['sha256'],review(proposal)):
                        raise Conflict('Proposal source state changed during report generation; retry a reviewed snapshot')
                    if len(raw)>32*1024*1024:raise InvalidInput('Generated Word report exceeds 32 MiB')
                    stage=self._path(pid,ident,'artifacts','.pending-'+uuid.uuid4().hex);stage.mkdir(parents=True)
                    title=re.sub(r'[\x00-\x1f/\\:*?"<>|]','_',proposal['payload']['candidate']['title'])[:70].rstrip(' .') or 'project-plan'
                    status=proposal['payload']['status'];filename=title+'-'+status.lower()+'.docx'
                    blobs={'docx':(raw,filename,DOCX_MIME,'report.docx'),
                        'pending_markdown':(markdown.encode(),'pending-confirmation.md','text/markdown; charset=utf-8','pending.md'),
                        'pending_json':((canonical(pending)+'\n').encode(),'pending-confirmation.json','application/json','pending.json'),
                        'source_snapshot':((canonical(snapshot)+'\n').encode(),'report-source-snapshot.json','application/json','source.json')}
                    files={}
                    for key,(content,name,mime,stored) in blobs.items():
                        self._bytes(stage/stored,content)
                        files[key]={'filename':name,'media_type':mime,'sha256':sha(content),'byte_length':len(content),'stored_name':stored}
                    manifest={'schema_version':'eeas.planning-report-artifact.v1','id':aid,'project_id':pid,'proposal_id':ident,
                        'source_kind':source_kind,'proposal_ref':ref if source_kind=='PROPOSAL' else None,
                        'session_ref':ref if source_kind=='DISCOVERY' else None,'status':status,'review_status':review(proposal),
                        'source_receipt_sha256':proposal['payload']['source_receipt']['sha256'],
                        'answer_view_sha256':digest(proposal['answers']),'pending_snapshot_sha256':pending['sha256'],
                        'template_ref':snapshot['template_ref'],'generator':snapshot['generator'],'render_receipt':render_receipt,
                        'experience_ref':snapshot['experience_ref'],'diagram_refs':[r.model_dump() for r in body.diagram_refs],
                        'files':files,'created_at':datetime.now(timezone.utc).isoformat(),'execution_authorized':False}
                    if 'coverage_sha256' in snapshot:manifest['coverage_sha256']=snapshot['coverage_sha256']
                    self._write(stage/'manifest.json',manifest);stage.rename(directory)
                manifest=self._artifact(pid,ident,aid)
                self._write(operation/(attempt+'-success.json'),{'status':'SAVED','artifact_id':aid,'reused':reused})
                self._write(operation/'result.json',{'artifact_id':aid})
                return self._result(pid,ident,manifest,reused)
            except Exception as exc:
                # Keep every original failure; only verified saved bytes get result.json.
                self._write(operation/(attempt+'-failure.json'),{'status':'FAILED','exception':type(exc).__name__,
                    'message':str(exc)[:2000],'created_at':datetime.now(timezone.utc).isoformat()})
                raise

    def download(self,pid,ident,aid,kind='docx',*,source_kind='PROPOSAL'):
        self.store.project(pid);manifest=self._artifact(pid,ident,aid)
        if manifest.get('source_kind','PROPOSAL')!=source_kind:raise NotFound('Report belongs to another source kind')
        if kind not in manifest['files']:raise NotFound('Report part does not exist')
        item=manifest['files'][kind];raw=self._path(pid,ident,'artifacts',aid,item['stored_name']).read_bytes()
        if sha(raw)!=item['sha256']:raise Conflict('Report download changed after integrity checking')
        return raw,item

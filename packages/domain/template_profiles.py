"""Source-bound, reviewed writing experience. Examples never become project facts."""
import base64
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import re
from typing import Literal
import uuid

from docx import Document
from pydantic import Field, model_validator
from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import FileSource, canonical, digest, sha
from packages.domain.paths import resolve_data_path
from packages.domain.planning_docx import normalize_template
from packages.domain.questions import Hash, Id, RequestModel
from packages.domain.store import Conflict, InvalidInput, NotFound


class ProfileSource(RequestModel):
    role: Literal['TEMPLATE','WRITING_EXAMPLE']
    file_source_ref: FileSource


class ProfileAnchor(RequestModel):
    source_index: int = Field(ge=0,strict=True)
    part: str = Field(min_length=1,max_length=200)
    path: str = Field(min_length=1,max_length=1000)
    quote_sha256: Hash


class WritingRule(RequestModel):
    id: Id
    kind: Literal['STRUCTURE','TONE','TERMINOLOGY','EVIDENCE_PRESENTATION','LAYOUT']
    instruction: str = Field(min_length=1,max_length=4000)
    source_anchors: list[ProfileAnchor] = Field(min_length=1,max_length=32)
    section_key: Literal['OVERVIEW','TASKS','QUESTIONS','SOURCES'] | None = None
    heading: str | None = Field(default=None,min_length=1,max_length=300)
    style_id: str | None = Field(default=None,min_length=1,max_length=100)
    origin: Literal['OBSERVED_CONVENTION','MODEL_SUGGESTION','USER_STATEMENT'] = 'MODEL_SUGGESTION'

    @model_validator(mode='after')
    def structural_mapping(self):
        if self.section_key is not None and (self.kind!='STRUCTURE' or self.heading is None):
            raise ValueError('A section mapping must be a STRUCTURE rule with a heading')
        return self


class ProfileCreate(RequestModel):
    operation_id: Id
    name: str = Field(min_length=1,max_length=200)
    sources: list[ProfileSource] = Field(min_length=1,max_length=8)
    candidate_rules: list[WritingRule] = Field(default_factory=list,max_length=100)

    @model_validator(mode='after')
    def unique(self):
        if len({digest(x.file_source_ref.model_dump()) for x in self.sources})!=len(self.sources):
            raise ValueError('Profile sources must be unique')
        if len({r.id for r in self.candidate_rules})!=len(self.candidate_rules):raise ValueError('Rule IDs must be unique')
        return self


class ProfileReview(RequestModel):
    operation_id: Id
    expected_revision: int = Field(ge=1,strict=True)
    accepted_rule_ids: list[Id] = Field(min_length=1,max_length=100)
    edits: list[WritingRule] = Field(default_factory=list,max_length=100)
    reason: str = Field(min_length=1,max_length=4000)

    @model_validator(mode='after')
    def unique(self):
        if len(set(self.accepted_rule_ids))!=len(self.accepted_rule_ids) or len({r.id for r in self.edits})!=len(self.edits):
            raise ValueError('Review rules must be unique')
        if not {r.id for r in self.edits}<=set(self.accepted_rule_ids):raise ValueError('Edits may only name accepted real rules')
        return self


class ProfileImport(RequestModel):
    operation_id: Id
    name: str = Field(min_length=1,max_length=200)
    source_project_id: Id
    source_profile_ref: RevisionRef
    selected_rule_ids: list[Id] = Field(min_length=1,max_length=100)
    user_requested_import: bool = Field(strict=True)

    @model_validator(mode='after')
    def explicit_selection(self):
        if self.user_requested_import is not True:
            raise ValueError('Writing experience import needs an explicit user request')
        if len(set(self.selected_rule_ids))!=len(self.selected_rule_ids):
            raise ValueError('Imported rule IDs must be unique')
        return self


class ProfileOperationQuery(RequestModel):
    operation_id: Id
    request_sha256: Hash


def inspect_source(raw,ref,index,*,reference_only=False):
    suffix=Path(ref.path).suffix.lower();observations=[];rules=[]
    def observe(kind,text,part,path,**extra):
        anchor={'source_index':index,'part':part,'path':path,'quote_sha256':sha(text.encode())}
        observations.append({'kind':kind,'text':text,'source_anchor':anchor,**extra})
        return anchor
    if suffix in {'.md','.txt'}:
        if len(raw)>1024*1024:raise InvalidInput('Writing text example exceeds 1 MiB')
        try:text=raw.decode('utf-8')
        except UnicodeError as exc:raise InvalidInput('Writing example must be UTF-8') from exc
        if '\x00' in text:raise InvalidInput('Writing example contains binary NUL')
        for i,line in enumerate(text.splitlines(),1):
            if line.strip():observe('HEADING' if re.match(r'^#{1,6} ',line) else 'PARAGRAPH',line,'text',f'/line[{i}]')
        layout=None
    elif suffix in {'.docx','.dotx'}:
        normalized,receipt=normalize_template(raw,allow_reference_hyperlinks=reference_only)
        if receipt['source_type']!=('DOTX' if suffix=='.dotx' else 'DOCX'):
            raise InvalidInput('Writing source filename does not match its actual DOCX/DOTX package type')
        document=Document(BytesIO(normalized));layout={'sections':[],'normalization':receipt}
        for section in document.sections:
            layout['sections'].append({key:int(getattr(section,key)) for key in
                ('page_width','page_height','top_margin','bottom_margin','left_margin','right_margin')})
        # Read original XML, retaining paragraphs inside tables and tracked changes.
        from packages.domain.diagram_import import _Archive, DiagramLimits
        archive=_Archive(normalized,DiagramLimits(max_archive_bytes=32*1024*1024,max_member_bytes=32*1024*1024))
        for part,root in archive.xml.items():
            if not part.startswith('word/') or not part.endswith('.xml'):continue
            for i,p in enumerate(e for e in root.iter() if e.tag.endswith('}p')):
                text=''.join(e.text or '' for e in p.iter() if e.tag.endswith(('}t','}delText')))
                if not text.strip():continue
                style=next((e.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val')
                            for e in p.iter() if e.tag.endswith('}pStyle')),None)
                heading=bool(style and ('heading' in style.casefold() or 'title' in style.casefold()))
                anchor=observe('HEADING' if heading else 'PARAGRAPH',text,part,f'/paragraph[{i+1}]',style_id=style,
                    contains_tracked_text=any(e.tag.endswith(('}ins','}del','}moveFrom','}moveTo')) for e in p.iter()))
                if heading:
                    major=style.replace(' ','').casefold() in {'heading1','title','subtitle'}
                    if not major and any(r.get('style_id')==style for r in rules):continue
                    rule={'id':'heading-'+digest(anchor)[:16],'kind':'STRUCTURE',
                        'instruction':'同级章节标题使用参考文稿的标题样式；标题内容由当前项目资料决定。',
                        'source_anchors':[anchor],'style_id':style,'origin':'OBSERVED_CONVENTION'}
                    for key,words in [('OVERVIEW',('目标','范围','overview','scope')),('TASKS',('任务','task')),
                                      ('QUESTIONS',('待确认','问题','question')),('SOURCES',('来源','资料','source','reference'))]:
                        if major and any(word in text.casefold() for word in words):
                            rule.update(section_key=key,heading=text,origin='MODEL_SUGGESTION');break
                    rules.append(rule)
        layout['styles']=[{'id':s.style_id,'name':s.name,'type':int(s.type),
            'font_name':s.font.name if hasattr(s,'font') else None,
            'font_size_pt':s.font.size.pt if hasattr(s,'font') and s.font.size else None} for s in document.styles]
    else:raise InvalidInput('Profiles support UTF-8 MD/TXT and inert DOCX/DOTX only')
    if len(observations)>4000 or len(canonical(observations).encode())>2*1024*1024:
        raise InvalidInput('Writing example observations exceed the explicit size bound')
    if not observations:raise InvalidInput('Writing example contains no observable text')
    if not rules:
        rules=[{'id':'evidence-'+digest(observations[0]['source_anchor'])[:16],'kind':'EVIDENCE_PRESENTATION',
            'instruction':'保留可核对的章节和来源记录；参考文稿中的项目数字、对象与结论不可作为新项目事实。',
            'source_anchors':[observations[0]['source_anchor']],'origin':'OBSERVED_CONVENTION'}]
    return {'file_source_ref':ref.model_dump(),'format':suffix[1:],'observations':observations,'layout':layout},rules


class TemplateProfiles:
    def __init__(self,store,data_dir,workspaces):
        self.store,self.data_dir,self.workspaces=store,Path(data_dir),workspaces
        if self.data_dir.is_symlink():raise InvalidInput('Profile registry cannot be a symlink')

    def _path(self,pid,*parts):
        self.store.project(pid)
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',pid):raise InvalidInput('Unsafe profile project identity')
        return resolve_data_path('/'.join(('template-profiles',pid,*parts)),self.data_dir)

    @staticmethod
    def _read(path):
        if not path.is_file() or path.stat().st_size>8*1024*1024:raise Conflict('Profile record missing or oversized')
        try:
            wrapper=json.loads(path.read_bytes());payload=wrapper['payload']
            if digest(payload)!=wrapper['sha256']:raise ValueError
            return payload
        except (OSError,ValueError,KeyError,TypeError):raise Conflict('Profile record integrity failed') from None

    @staticmethod
    def _write(path,payload,replace=False):
        path.parent.mkdir(parents=True,exist_ok=True)
        raw=(canonical({'payload':payload,'sha256':digest(payload)})+'\n').encode()
        if replace:
            stage=path.with_name('.pending-'+uuid.uuid4().hex)
            with stage.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(stage,path)
        else:
            with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())

    @staticmethod
    def _validate_rules(rules,observations):
        known={canonical(row['source_anchor']) for observation in observations for row in observation['observations']}
        styles={s['id'] for observation in observations if observation['layout'] for s in observation['layout']['styles']}
        for rule in rules:
            if any(canonical(a.model_dump()) not in known for a in rule.source_anchors):
                raise InvalidInput('Writing rule cites an observation outside these exact source bytes')
            if rule.style_id is not None and rule.style_id not in styles:raise InvalidInput('Writing rule names an unobserved style')

    @staticmethod
    def _validate_import_rules(rules,provenance):
        """Origin anchors prove the copied rule, never a target document grant."""
        if (not isinstance(provenance,dict) or provenance.get('schema')!='eeas.template-profile-import.v1'
                or provenance.get('copy_scope')!='REVIEWED_RULES_ONLY'
                or provenance.get('user_requested_import') is not True):
            raise Conflict('Imported writing experience provenance is invalid')
        try:
            RevisionRef.model_validate(provenance['source_profile_ref'])
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',provenance['source_project_id']):raise ValueError
            if not re.fullmatch(r'[0-9a-f]{64}',provenance['source_review_sha256']):raise ValueError
            snapshots=provenance['rules']
            if not isinstance(snapshots,list) or not 1<=len(snapshots)<=100:raise ValueError
            known={}
            for row in snapshots:
                source=WritingRule.model_validate(row['snapshot'])
                if source.id in known or digest(source.model_dump())!=row['sha256']:raise ValueError
                known[source.id]=source
        except (KeyError,TypeError,ValueError):
            raise Conflict('Imported writing rule snapshot integrity failed') from None
        for rule in rules:
            source=known.get(rule.id)
            if source is None or rule.source_anchors!=source.source_anchors:
                raise InvalidInput('Imported rule must retain its exact selected origin anchors')
            if rule.style_id is not None:
                raise InvalidInput('An imported origin style does not authorize a target template style')

    def _operation(self,pid,key,request):
        directory=self._path(pid,'operations',digest(key));directory.mkdir(parents=True,exist_ok=True)
        if (directory/'request.json').exists():
            if self._read(directory/'request.json')['sha256']!=digest(request):raise Conflict('IDEMPOTENCY_CONFLICT: profile operation changed input')
        else:self._write(directory/'request.json',{'sha256':digest(request),'input':request})
        return directory

    def _failure(self,operation,exc):
        self._write(operation/(uuid.uuid4().hex+'-failure.json'),{'status':'FAILED','type':type(exc).__name__,'message':str(exc)[:2000]})

    def _publish_import_record(self,path,payload,*,expected=None):
        """Publish complete private metadata; immutable final names are exclusive.

        Callers hold the store's write transaction. Failed staging bytes remain
        available for diagnosis; neither a partial record nor a mismatching final
        record is adopted. Only a known mutable head can use compare/replace.
        """
        if path.exists():
            current=self._read(path)
            if current==payload:return
            if expected is None or current!=expected:
                raise Conflict('Immutable import record differs; existing bytes were not overwritten')
        elif expected is not None:
            raise Conflict('Imported profile head disappeared before compare/replace')
        path.parent.mkdir(parents=True,exist_ok=True)
        stage=path.with_name('.pending-import-'+path.name+'-'+uuid.uuid4().hex)
        self._write(stage,payload)
        if expected is None:
            # An atomic exclusive link publishes the already-fsynced inode.
            # It is never a persistent dependency/user-file hardlink or a copy.
            try:os.link(stage,path)
            except FileExistsError:
                if self._read(path)!=payload:
                    raise Conflict('Immutable import record appeared with different bytes') from None
        else:
            if self._read(path)!=expected:
                raise Conflict('Imported profile head changed before compare/replace')
            os.replace(stage,path)
        if stage.exists():stage.unlink()
        if os.name=='posix':
            directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
            try:os.fsync(directory)
            finally:os.close(directory)

    def _import_operation(self,pid,key,request):
        directory=self._path(pid,'operations',digest(key));directory.mkdir(parents=True,exist_ok=True)
        request_sha=digest(request);claims=directory/'claims'
        if claims.is_symlink():raise Conflict('Imported operation claim cannot be a link')
        claims.mkdir(exist_ok=True)
        # The empty digest directory binds an intent even if request JSON cannot
        # be flushed. A retry with different input must not hijack that intent.
        for index,claim in enumerate(claims.iterdir()):
            if index or claim.name!=request_sha or not claim.is_dir() or claim.is_symlink():
                raise Conflict('IDEMPOTENCY_CONFLICT: imported profile operation changed input')
        (claims/request_sha).mkdir(exist_ok=True)
        self._publish_import_record(directory/'request.json',{'sha256':request_sha,'input':request})
        return directory

    def _import_failure(self,operation,exc):
        try:self._publish_import_record(operation/(uuid.uuid4().hex+'-failure.json'),
            {'status':'FAILED','type':type(exc).__name__,'message':str(exc)[:2000]})
        except Exception:
            # Preserve the original write/flush error when even its failure
            # record cannot be persisted; any failed staging bytes stay intact.
            pass

    @staticmethod
    def _query_claim(claims,request_sha256,*,required=False):
        if not claims.exists():
            if required:raise Conflict('Profile operation intent claim is missing')
            return False
        if not claims.is_dir() or claims.is_symlink():raise Conflict('Profile operation claim is invalid')
        count=0
        for entry in claims.iterdir():
            count+=1
            if (count!=1 or entry.name!=request_sha256 or not entry.is_dir() or
                    entry.is_symlink() or any(entry.iterdir())):
                raise Conflict('Profile operation claim does not match the frozen request')
        if count!=1:raise Conflict('Profile operation claim is empty')
        return True

    def _query_version(self,pid,ident,revision):
        value=self._core(pid,ident,revision);payload=value['payload']
        if (type(payload.get('revision'))is not int or payload['revision']!=revision or
                type(value['current_revision'])is not int or value['current_revision']<revision or
                payload.get('schema_version')!='eeas.template-profile.v1'):
            raise Conflict('Profile operation version identity is invalid')
        return value

    def operation(self,pid,operation_id,request_sha256):
        """Read original metadata only; missing results never imply not applied."""
        query=ProfileOperationQuery(operation_id=operation_id,request_sha256=request_sha256)
        hashed=digest(query.operation_id);directory=self._path(pid,'operations',hashed)
        response={'operation_hash':hashed,'request_sha256':query.request_sha256,'method':None,
            'status':'NOT_FOUND','profile_id':None,'result_profile_ref':None,'recovery':None}
        if not directory.exists():return response
        if not directory.is_dir():raise Conflict('Profile operation directory is invalid')
        request_path=self._path(pid,'operations',hashed,'request.json')
        result_path=self._path(pid,'operations',hashed,'result.json')
        claims=self._path(pid,'operations',hashed,'claims')
        preparation=self._path(pid,'operations',hashed,'prepared.json')
        try:
            if not request_path.exists():
                self._query_claim(claims,query.request_sha256,required=True)
                if result_path.exists() or preparation.exists():
                    raise Conflict('Profile operation has later records without its request')
                return {**response,'status':'INTENT_ONLY','recovery':'ORIGINAL_OPERATION_ONLY'}
            record=self._read(request_path)
            if (not isinstance(record,dict) or set(record)!={'sha256','input'} or
                    record['sha256']!=query.request_sha256 or digest(record['input'])!=query.request_sha256):
                raise Conflict('Profile operation request SHA mismatch')
            request=record['input']
            if not isinstance(request,dict):raise Conflict('Profile operation request is invalid')
            method=request.get('method');model={'CREATE':ProfileCreate,'IMPORT_REVIEWED':ProfileImport,'REVIEW':ProfileReview}.get(method)
            if model is None:raise Conflict('Profile operation method is invalid')
            body=model.model_validate({'operation_id':query.operation_id,
                **{k:v for k,v in request.items() if k not in {'method','profile_id'}}})
            reconstructed={'method':method,**body.model_dump(exclude={'operation_id'})}
            if method=='REVIEW':
                ident=request.get('profile_id')
                if not isinstance(ident,str) or not re.fullmatch(r'[0-9a-f]{64}',ident):
                    raise Conflict('Review operation profile identity is invalid')
                reconstructed['profile_id']=ident
            else:ident=digest({'project_id':pid,'request':request})
            if digest(reconstructed)!=query.request_sha256:
                raise Conflict('Profile operation request schema does not match its frozen SHA')
            if method=='CREATE' and any(s.file_source_ref.project_id!=pid for s in body.sources):
                raise Conflict('Create operation source belongs to another project')
            if method=='IMPORT_REVIEWED' and not re.fullmatch(r'[0-9a-f]{64}',body.source_profile_ref.id):
                raise Conflict('Import operation source identity is invalid')
            base=None;expected_review=None
            if method=='REVIEW':
                base=self._query_version(pid,ident,body.expected_revision);payload=base['payload']
            self._query_claim(claims,query.request_sha256,required=method=='IMPORT_REVIEWED' or
                (base is not None and 'import_provenance'in base['payload']))
            response={**response,'method':method,'status':'PENDING','profile_id':ident,'recovery':'ORIGINAL_OPERATION_ONLY'}
            # A valid frozen request can itself have been rejected by semantic
            # review checks. With no published preparation/result, do not infer
            # either success or rejection: an explicit original POST resolves it.
            if not result_path.exists() and not preparation.exists():return response
            if method=='REVIEW':
                draft={r['id']:r for r in payload['draft_rules']}
                if not set(body.accepted_rule_ids)<=set(draft):raise Conflict('Review operation names an unknown basis rule')
                edits={r.id:r for r in body.edits}
                approved=[edits.get(key,WritingRule.model_validate(draft[key])) for key in body.accepted_rule_ids]
                if 'import_provenance' in payload:
                    self._validate_import_rules(approved,payload['import_provenance'])
                    approved=[r.model_copy(update={'origin':'USER_STATEMENT'}) if r.id in edits else r for r in approved]
                else:self._validate_rules(approved,payload['observations'])
                keys=[r.section_key for r in approved if r.section_key]
                if len(keys)!=len(set(keys)):raise Conflict('Review operation repeats a section mapping')
                expected_review={**payload,'revision':body.expected_revision+1,'status':'REVIEWED',
                    'approved_rules':[r.model_dump() for r in approved],
                    'review':{'reason':body.reason,'accepted_rule_ids':body.accepted_rule_ids,'source_revision':body.expected_revision}}
            if preparation.exists():
                if expected_review is None or 'import_provenance'not in base['payload']:
                    raise Conflict('Profile operation preparation has no imported-review basis')
                expected={'request_sha256':query.request_sha256,'base_profile_ref':base['profile_ref'],
                    'target_profile_ref':{'id':ident,'revision':body.expected_revision+1,'sha256':digest(expected_review)}}
                if digest(self._read(preparation))!=digest(expected):
                    raise Conflict('Profile operation preparation differs from its exact review basis')
            if not result_path.exists():return response
            saved=self._read(result_path)
            expected_revision=body.expected_revision+1 if method=='REVIEW' else 1
            if (not isinstance(saved,dict) or set(saved)!={'id','revision'} or saved['id']!=ident or
                    type(saved['revision'])is not int or saved['revision']!=expected_revision):
                raise Conflict('Profile operation result differs from its request identity or revision')
            value=self._query_version(pid,ident,saved['revision']);payload=value['payload']
            if method=='REVIEW':
                if digest(payload)!=digest(expected_review):raise Conflict('Review result differs from its frozen basis and decisions')
            else:
                if (payload.get('status')!='DRAFT' or payload.get('review')is not None or payload.get('approved_rules')!=[] or
                        payload.get('name')!=body.name):raise Conflict('Creation result is not its exact original draft')
                if method=='CREATE':
                    if (digest(payload['sources'])!=digest([s.model_dump() for s in body.sources]) or
                            (body.candidate_rules and digest(payload['draft_rules'])!=digest([r.model_dump()for r in body.candidate_rules]))):
                        raise Conflict('Create result differs from its frozen sources or selected rules')
                else:
                    provenance=payload['import_provenance'];snapshots=[r['snapshot']for r in provenance['rules']]
                    expected_draft=[WritingRule.model_validate(r).model_copy(update={'style_id':None}).model_dump()for r in snapshots]
                    if (provenance['source_project_id']!=body.source_project_id or
                            provenance['source_profile_ref']!=body.source_profile_ref.model_dump() or
                            [r['id']for r in snapshots]!=body.selected_rule_ids or digest(payload['draft_rules'])!=digest(expected_draft)):
                        raise Conflict('Import result differs from its exact selected source snapshot')
            return {**response,'status':'COMPLETED','result_profile_ref':value['profile_ref'],'recovery':None}
        except (KeyError,TypeError,ValueError,OSError,NotFound,InvalidInput):
            raise Conflict('Profile operation metadata cannot be verified for this project/request') from None

    def _core(self,pid,ident,revision=None):
        if not re.fullmatch(r'[0-9a-f]{64}',ident):raise InvalidInput('Profile identity must be SHA256')
        root=self._path(pid,ident)
        if not (root/'head.json').exists():raise NotFound('Template profile does not exist in this project')
        head=self._read(root/'head.json');revision=revision or head['revision']
        if type(revision)is not int or revision<1:raise InvalidInput('Profile revision must be a positive integer')
        path=self._path(pid,ident,'versions',str(revision)+'.json')
        if not path.exists():raise NotFound('Template profile revision does not exist')
        payload=self._read(path)
        if (payload['project_id'],payload['id'],payload['revision'])!=(pid,ident,revision):raise Conflict('Profile scope integrity failed')
        if revision==head['revision'] and head['sha256']!=digest(payload):raise Conflict('Profile head SHA mismatch')
        if 'import_provenance' in payload:
            if payload['sources'] or payload['observations']:
                raise Conflict('Imported writing experience must not carry source files or observations')
            self._validate_import_rules([WritingRule.model_validate(r) for r in
                payload['draft_rules']+payload['approved_rules']],payload['import_provenance'])
        return {'id':ident,'project_id':pid,'revision':revision,'current_revision':head['revision'],'sha256':digest(payload),
                'profile_ref':{'id':ident,'revision':revision,'sha256':digest(payload)},'payload':payload,
                'status':payload['status'],'approved_rules':payload['approved_rules'],'draft_rules':payload['draft_rules']}

    def get(self,pid,ident,revision=None):
        value=self._core(pid,ident,revision)
        value['source_checks']=self.workspaces.check_sources(pid,[FileSource.model_validate(s['file_source_ref']) for s in value['payload']['sources']])
        return value

    def _import_snapshot(self,pid,body,request):
        ref=body.source_profile_ref;source=self._core(body.source_project_id,ref.id,ref.revision)
        if source['sha256']!=ref.sha256:raise Conflict('Imported source profile SHA mismatch')
        if source['status']!='REVIEWED' or not source['payload'].get('review'):
            raise Conflict('Only explicitly reviewed writing experience may be imported')
        approved={r['id']:r for r in source['approved_rules']}
        if not set(body.selected_rule_ids)<=set(approved):
            raise InvalidInput('Import must select actual approved rules from the exact source revision')
        snapshots=[WritingRule.model_validate(approved[ident]).model_dump() for ident in body.selected_rule_ids]
        provenance={'schema':'eeas.template-profile-import.v1','source_project_id':body.source_project_id,
            'source_profile_ref':ref.model_dump(),'source_review_sha256':digest(source['payload']['review']),
            'copy_scope':'REVIEWED_RULES_ONLY','user_requested_import':True,
            'origin_anchor_authority':'PROVENANCE_ONLY_NOT_TARGET_DOCUMENT',
            'rules':[{'snapshot':rule,'sha256':digest(rule)} for rule in snapshots]}
        draft=[WritingRule.model_validate(rule).model_copy(update={'style_id':None}) for rule in snapshots]
        self._validate_import_rules(draft,provenance)
        ident=digest({'project_id':pid,'request':request})
        return ident,{'schema_version':'eeas.template-profile.v1','id':ident,'project_id':pid,'revision':1,
            'name':body.name,'sources':[],'observations':[],
            'draft_rules':[r.model_dump() for r in draft],'approved_rules':[],'status':'DRAFT',
            'usage':'EXPERIENCE_ONLY_NOT_PROJECT_FACTS','review':None,'import_provenance':provenance}

    def _pending_import(self,pid,ident):
        """Recognize an incomplete exact import, never suppress corrupt profiles."""
        root=self._path(pid,ident);head=self._path(pid,ident,'head.json')
        if head.exists() or head.is_symlink():raise Conflict('Pending import unexpectedly has a head')
        versions=self._path(pid,ident,'versions')
        if not versions.is_dir():raise Conflict('Incomplete profile has no import version directory')
        for path in root.iterdir():
            if path.name!='versions' and not re.fullmatch(r'\.pending-import-head\.json-[0-9a-f]{32}',path.name):
                raise Conflict('Unrecognized incomplete profile metadata')
            if path.is_symlink():raise Conflict('Pending import metadata cannot be a link')
        for path in versions.iterdir():
            if path.is_symlink() or not path.is_file() or (path.name!='1.json' and not
                    re.fullmatch(r'\.pending-import-1\.json-[0-9a-f]{32}',path.name)):
                raise Conflict('Pending import has unrecognized or successor version metadata')
        operations=self._path(pid,'operations');matches=[];expected_payload=None
        if not operations.is_dir():raise Conflict('Incomplete profile has no import intent')
        for index,operation in enumerate(operations.iterdir()):
            if index>=1000:raise Conflict('Pending import intent lookup exceeds 1000 operations')
            if not re.fullmatch(r'[0-9a-f]{64}',operation.name) or not operation.is_dir() or operation.is_symlink():
                raise Conflict('Pending import operation identity is invalid')
            request_path=self._path(pid,'operations',operation.name,'request.json')
            result_path=self._path(pid,'operations',operation.name,'result.json')
            if result_path.exists():
                completed=self._read(result_path)
                if not isinstance(completed,dict):raise Conflict('Profile operation result is invalid')
                if completed.get('id')==ident:
                    raise Conflict('A completed profile cannot be treated as a pending import')
            if not request_path.exists():continue
            record=self._read(request_path)
            if not isinstance(record,dict) or set(record)!={'sha256','input'} or digest(record['input'])!=record['sha256']:
                raise Conflict('Pending import request integrity failed')
            request=record['input']
            if not isinstance(request,dict):raise Conflict('Pending import request must be an object')
            if request.get('method')=='REVIEW' and request.get('profile_id')==ident:
                raise Conflict('A reviewed profile cannot be treated as a pending initial import')
            if request.get('method')!='IMPORT_REVIEWED' or digest({'project_id':pid,'request':request})!=ident:continue
            try:body=ProfileImport.model_validate({'operation_id':'pending-list-validation',
                **{k:v for k,v in request.items() if k!='method'}})
            except ValueError:raise Conflict('Pending import request schema is invalid') from None
            if request!={'method':'IMPORT_REVIEWED',**body.model_dump(exclude={'operation_id'})}:
                raise Conflict('Pending import request contains unbound fields')
            claims=self._path(pid,'operations',operation.name,'claims')
            if not claims.is_dir() or claims.is_symlink():raise Conflict('Pending import intent claim is missing')
            claim_entries=list(claims.iterdir())
            if (len(claim_entries)!=1 or claim_entries[0].name!=record['sha256'] or
                    not claim_entries[0].is_dir() or claim_entries[0].is_symlink() or any(claim_entries[0].iterdir())):
                raise Conflict('Pending import intent claim does not match this exact request')
            if (operation/'result.json').exists() or (operation/'prepared.json').exists():
                raise Conflict('A completed or later operation cannot be treated as pending import')
            actual_ident,payload=self._import_snapshot(pid,body,request)
            if actual_ident!=ident:raise Conflict('Pending import project/identity mismatch')
            if expected_payload is not None and expected_payload!=payload:raise Conflict('Pending import requests disagree')
            expected_payload=payload
            matches.append({'id':operation.name,'request_sha256':record['sha256']})
            if len(matches)>20:raise Conflict('Pending import has more than 20 matching operations')
        if not matches:raise Conflict('Incomplete profile has no verified matching import intent')
        version=self._path(pid,ident,'versions','1.json')
        if version.exists():
            payload=self._read(version);created=payload.get('created_at') if isinstance(payload,dict) else None
            try:
                if not isinstance(created,str) or len(created)>100 or datetime.fromisoformat(created).tzinfo is None:raise ValueError
            except ValueError:raise Conflict('Pending import version timestamp is invalid') from None
            if digest({k:v for k,v in payload.items() if k!='created_at'})!=digest(expected_payload):
                raise Conflict('Pending import version differs from the exact approved-source draft')
        return {'profile_id':ident,'status':'PENDING_IMPORT','published_version':version.exists(),
                'retry':'ORIGINAL_OPERATION_ONLY','operation_refs':sorted(matches,key=lambda r:r['id'])}

    def listing(self,pid):
        # Metadata only, serialized against import/review publication; no raw
        # source documents or model calls occur in this short transaction.
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');root=self._path(pid);root.mkdir(parents=True,exist_ok=True)
            complete=[];pending=[]
            for path in sorted(root.iterdir()):
                if not re.fullmatch(r'[0-9a-f]{64}',path.name):continue
                head=self._path(pid,path.name,'head.json')
                if head.exists() or head.is_symlink():
                    self._core(pid,path.name);complete.append(path.name)
                else:
                    if len(pending)>=100:raise Conflict('Pending import listing exceeds 100 entries')
                    pending.append(self._pending_import(pid,path.name))
        # Existing current-source checks may read selected files, so keep them
        # outside the metadata publication transaction.
        return {'profiles':[self.get(pid,ident) for ident in complete],'pending_imports':pending}

    def list(self,pid):return self.listing(pid)['profiles']

    def create(self,pid,body):
        body=ProfileCreate.model_validate(body);request={'method':'CREATE',**body.model_dump(exclude={'operation_id'})}
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');operation=self._operation(pid,body.operation_id,request)
            if (operation/'result.json').exists():
                saved=self._read(operation/'result.json');return {**self.get(pid,saved['id'],saved['revision']),'replayed':True}
        try:
            observations=[];rules=[]
            for index,item in enumerate(body.sources):
                ref=item.file_source_ref
                if ref.project_id!=pid:raise InvalidInput('Writing example belongs to another project')
                value=self.workspaces.read(pid,ref.workspace_id,ref.path,ref.revision)
                if value['source_ref']!=ref.model_dump() or not value['current']:raise Conflict('Writing source revision/SHA changed')
                raw=base64.b64decode(value['content']) if value['encoding']=='base64' else value['content'].encode()
                if sha(raw)!=ref.sha256:raise Conflict('Writing source raw hash mismatch')
                observed,derived=inspect_source(raw,ref,index,reference_only=item.role=='WRITING_EXAMPLE');observations.append(observed);rules.extend(derived)
            if not body.candidate_rules and len(rules)>100:raise InvalidInput('Derived writing rules exceed 100; split sources explicitly')
            candidate=body.candidate_rules or [WritingRule.model_validate(r) for r in rules]
            self._validate_rules(candidate,observations)
            if any(row['status']!='CURRENT' for row in self.workspaces.check_sources(pid,[s.file_source_ref for s in body.sources])):
                raise Conflict('Writing examples changed during profile analysis')
            ident=digest({'project_id':pid,'request':request})
            with self.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                if (operation/'result.json').exists():
                    saved=self._read(operation/'result.json');return {**self.get(pid,saved['id'],saved['revision']),'replayed':True}
                root=self._path(pid,ident)
                if not (root/'head.json').exists():
                    payload={'schema_version':'eeas.template-profile.v1','id':ident,'project_id':pid,'revision':1,'name':body.name,
                        'sources':[s.model_dump() for s in body.sources],'observations':observations,
                        'draft_rules':[r.model_dump() for r in candidate],'approved_rules':[],'status':'DRAFT',
                        'usage':'EXPERIENCE_ONLY_NOT_PROJECT_FACTS','review':None,'created_at':datetime.now(timezone.utc).isoformat()}
                    self._write(root/'versions/1.json',payload);self._write(root/'head.json',{'revision':1,'sha256':digest(payload)})
                self._write(operation/'result.json',{'id':ident,'revision':1})
            return {**self.get(pid,ident,1),'replayed':False}
        except Exception as exc:
            with self.store.connect() as connection:connection.execute('BEGIN IMMEDIATE');self._failure(operation,exc)
            raise

    def import_reviewed(self,pid,body):
        body=ProfileImport.model_validate(body)
        request={'method':'IMPORT_REVIEWED',**body.model_dump(exclude={'operation_id'})}
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');operation=self._path(pid,'operations',digest(body.operation_id))
            try:
                operation=self._import_operation(pid,body.operation_id,request)
                if (operation/'result.json').exists():
                    saved=self._read(operation/'result.json')
                    return {**self.get(pid,saved['id'],saved['revision']),'replayed':True}
                ident,payload=self._import_snapshot(pid,body,request);root=self._path(pid,ident)
                version=root/'versions/1.json'
                # Reuse an interrupted or equivalent creation only if every bound
                # input matches. Never reset a target head already reviewed later.
                if version.exists():
                    existing=self._read(version)
                    if {k:v for k,v in existing.items() if k!='created_at'}!=payload:
                        raise Conflict('Existing imported profile differs from this exact request')
                    payload=existing
                else:
                    payload['created_at']=datetime.now(timezone.utc).isoformat()
                    if len(canonical(payload).encode())>2*1024*1024:
                        raise InvalidInput('Imported writing experience snapshot exceeds 2 MiB')
                    self._publish_import_record(version,payload)
                if not (root/'head.json').exists():
                    self._publish_import_record(root/'head.json',{'revision':1,'sha256':digest(payload)})
                self._core(pid,ident,1)
                self._publish_import_record(operation/'result.json',{'id':ident,'revision':1})
            except Exception as exc:self._import_failure(operation,exc);raise
        return {**self.get(pid,ident,1),'replayed':False}

    def _review_imported(self,pid,ident,body):
        request={'method':'REVIEW','profile_id':ident,**body.model_dump(exclude={'operation_id'})}
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');operation=self._path(pid,'operations',digest(body.operation_id))
            try:
                operation=self._import_operation(pid,body.operation_id,request)
                if (operation/'result.json').exists():
                    saved=self._read(operation/'result.json')
                    return {**self.get(pid,ident,saved['revision']),'replayed':True}
                value=self._core(pid,ident,body.expected_revision);payload=value['payload']
                if 'import_provenance' not in payload:raise Conflict('Imported review needs an actual imported profile')
                draft={r['id']:r for r in payload['draft_rules']}
                if not set(body.accepted_rule_ids)<=set(draft):
                    raise InvalidInput('Review must accept actual rules from this exact revision')
                edits={r.id:r.model_dump() for r in body.edits}
                approved=[WritingRule.model_validate(edits.get(key,draft[key])) for key in body.accepted_rule_ids]
                self._validate_import_rules(approved,payload['import_provenance'])
                approved=[r.model_copy(update={'origin':'USER_STATEMENT'}) if r.id in edits else r for r in approved]
                keys=[r.section_key for r in approved if r.section_key]
                if len(keys)!=len(set(keys)):raise InvalidInput('A report section may have only one approved mapping')
                revision=body.expected_revision+1
                result={**payload,'revision':revision,'status':'REVIEWED','approved_rules':[r.model_dump() for r in approved],
                    'review':{'reason':body.reason,'accepted_rule_ids':body.accepted_rule_ids,'source_revision':body.expected_revision}}
                base_head={'revision':body.expected_revision,'sha256':value['sha256']}
                next_head={'revision':revision,'sha256':digest(result)}
                prepared={'request_sha256':digest(request),'base_profile_ref':value['profile_ref'],
                    'target_profile_ref':{'id':ident,**next_head}}
                root=self._path(pid,ident);preparation=operation/'prepared.json'
                recovering=preparation.exists()
                if recovering:
                    if self._read(preparation)!=prepared or self._read(root/'head.json') not in (base_head,next_head):
                        raise Conflict('Interrupted imported review no longer owns this exact head transition')
                elif value['current_revision']!=body.expected_revision:
                    raise Conflict('Profile revision changed; reload before review')
                self._publish_import_record(preparation,prepared)
                self._publish_import_record(root/'versions'/f'{revision}.json',result)
                self._publish_import_record(root/'head.json',next_head,expected=base_head)
                self._publish_import_record(operation/'result.json',{'id':ident,'revision':revision})
            except Exception as exc:self._import_failure(operation,exc);raise
        return {**self.get(pid,ident,revision),'replayed':recovering}

    def review(self,pid,ident,body):
        body=ProfileReview.model_validate(body)
        if 'import_provenance' in self._core(pid,ident)['payload']:
            return self._review_imported(pid,ident,body)
        request={'method':'REVIEW','profile_id':ident,**body.model_dump(exclude={'operation_id'})}
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE');operation=self._operation(pid,body.operation_id,request)
            if (operation/'result.json').exists():
                saved=self._read(operation/'result.json');return {**self.get(pid,ident,saved['revision']),'replayed':True}
            try:
                value=self._core(pid,ident,body.expected_revision)
                if value['current_revision']!=body.expected_revision:raise Conflict('Profile revision changed; reload before review')
                payload=value['payload'];draft={r['id']:r for r in payload['draft_rules']}
                if not set(body.accepted_rule_ids)<=set(draft):raise InvalidInput('Review must accept actual rules from this exact revision')
                approved=[WritingRule.model_validate(next((e.model_dump() for e in body.edits if e.id==ident),draft[ident]))
                          for ident in body.accepted_rule_ids]
                self._validate_rules(approved,payload['observations'])
                keys=[r.section_key for r in approved if r.section_key]
                if len(keys)!=len(set(keys)):raise InvalidInput('A report section may have only one approved mapping')
                revision=body.expected_revision+1
                result={**payload,'revision':revision,'status':'REVIEWED','approved_rules':[r.model_dump() for r in approved],
                        'review':{'reason':body.reason,'accepted_rule_ids':body.accepted_rule_ids,'source_revision':body.expected_revision}}
                root=self._path(pid,ident);self._write(root/'versions'/f'{revision}.json',result)
                self._write(root/'head.json',{'revision':revision,'sha256':digest(result)},replace=True)
                self._write(operation/'result.json',{'id':ident,'revision':revision})
            except Exception as exc:self._failure(operation,exc);raise
        return {**self.get(pid,ident,revision),'replayed':False}

    def instructions(self,pid,ref):
        ref=RevisionRef.model_validate(ref);value=self.get(pid,ref.id,ref.revision)
        if value['sha256']!=ref.sha256:raise Conflict('Writing experience SHA mismatch')
        if value['status']!='REVIEWED':raise Conflict('Writing experience must be explicitly reviewed before reuse')
        result={'profile_ref':value['profile_ref'],'usage':'EXPERIENCE_ONLY_NOT_PROJECT_FACTS',
                'instructions':[r['instruction'] for r in value['approved_rules']],
                'approved_rules':value['approved_rules'],'source_refs':[s['file_source_ref'] for s in value['payload']['sources']],
                'source_checks':value['source_checks']}
        if 'import_provenance' in value['payload']:
            # Prompt/report consumers need hashes and origin, not unaccepted
            # source-rule text kept in the target's review snapshot.
            provenance=value['payload']['import_provenance']
            result['import_provenance']={**{k:v for k,v in provenance.items() if k!='rules'},
                'rules':[{'id':r['snapshot']['id'],'sha256':r['sha256']} for r in provenance['rules']]}
        return result

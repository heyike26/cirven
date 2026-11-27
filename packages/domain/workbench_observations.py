"""Immutable offline OCR observations over exact registered bitmaps."""
from pathlib import Path
import json
import re
from packages.domain.paths import resolve_data_path
from packages.domain.local_workspaces import canonical,digest,LocalWorkspaces
from packages.domain.store import Conflict,InvalidInput,NotFound


class WorkbenchObservations:
    def __init__(self,store,data_dir,ocr,bitmap_reader):
        self.store,self.data_dir,self.ocr,self.bitmap_reader=store,Path(data_dir),ocr,bitmap_reader

    def _path(self,pid,ident):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',pid) or not re.fullmatch(r'[0-9a-f]{64}',ident):
            raise InvalidInput('Observation identity is invalid')
        return resolve_data_path(f'workbench-observations/{pid}/{ident}.json',self.data_dir)

    def get(self,pid,ident):
        self.store.project(pid);path=self._path(pid,ident)
        if not path.is_file():raise NotFound('OCR observation is outside this project')
        wrapper=LocalWorkspaces._json(path);payload=wrapper['payload']
        if digest(payload)!=wrapper['sha256'] or payload['project_id']!=pid:
            raise Conflict('OCR observation receipt changed')
        return {'observation_ref':{'id':ident,'revision':1,'sha256':wrapper['sha256']},'payload':payload}

    def recognize(self,pid,bitmap_ref):
        self.store.project(pid);self.bitmap_reader(pid,bitmap_ref)
        capability=self.ocr.capabilities()
        if not capability['available']:raise Conflict(capability['reason'])
        ident=digest({'project_id':pid,'bitmap_ref':bitmap_ref,'component_sha256':capability['component_sha256']})
        path=self._path(pid,ident)
        if path.is_file():return self.get(pid,ident)
        result=self.ocr.recognize(pid,bitmap_ref)
        if digest({k:v for k,v in result.items() if k!='sha256'})!=result['sha256']:
            raise Conflict('OCR engine observation integrity failed')
        payload={'project_id':pid,'bitmap_ref':bitmap_ref,'recognition':result,
            'authority':'OCR_OBSERVATION_NOT_VERIFIED_DOCUMENT_OR_PROJECT_FACT','semantic_correctness':'USER_REVIEW_REQUIRED'}
        wrapper={'payload':payload,'sha256':digest(payload)}
        try:LocalWorkspaces._write(path,wrapper)
        except FileExistsError:pass
        return self.get(pid,ident)

    def resolve(self,pid,ref,bitmap_ref=None):
        value=self.get(pid,ref['id'])
        if value['observation_ref']!=ref:raise Conflict('OCR observation reference changed')
        original=value['payload']['bitmap_ref']
        if bitmap_ref is not None and original!=bitmap_ref:raise Conflict('OCR text belongs to another image/frame')
        self.bitmap_reader(pid,original)
        return value

    def quotation(self,pid,locator):
        value=self.resolve(pid,locator.observation_ref.model_dump())
        line=next((l for l in value['payload']['recognition']['lines'] if l['id']==locator.line_id),None)
        if line is None:raise InvalidInput('Selected OCR line does not exist')
        text=line['text']
        if not 0<=locator.char_start<locator.char_end<=len(text):raise InvalidInput('OCR selected range is outside the exact observation')
        return text[locator.char_start:locator.char_end]

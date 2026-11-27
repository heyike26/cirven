"""Main-process attestation and selected-file authority for installed-app windows.

This broker neither accepts renderer window handles nor adds an Agent Loop.
Volatile grants expire on controller restart; captured frame receipts persist.
"""
import copy
import hmac
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid
from packages.domain.local_workspaces import FileSource,digest,sha
from packages.domain.paths import resolve_data_path
from packages.domain.store import Conflict,InvalidInput,NotFound


class DesktopHost:
    ACTIONS={'OPEN_NATIVE','HOST_NATIVE','FOCUS_NATIVE','CAPTURE_NATIVE','DETACH_NATIVE'}

    def __init__(self,store,workspaces,data_dir,*,token=None,owner_pid=None,documents_factory=None,backend=None):
        self.store,self.workspaces,self.data_dir=store,workspaces,Path(data_dir)
        self.token=token if token is not None else os.environ.get('EEAS_DESKTOP_HOST_TOKEN','')
        self.owner_pid=int(owner_pid if owner_pid is not None else os.environ.get('EEAS_DESKTOP_HOST_PID','0'))
        self.enabled=bool(re.fullmatch(r'[0-9a-f]{64}',self.token) and self.owner_pid>0)
        self.grants,self.surfaces={},{};self.lock=threading.Lock()
        if documents_factory is None:
            from packages.adapters.desktop_documents import DesktopDocuments
            documents_factory=DesktopDocuments
        self.documents=documents_factory(selection_authority=self.selection,action_authority=self.authorize,
            surface_authority=self.surface,backend=backend)

    def check_host(self,provided):
        if not self.enabled or not isinstance(provided,str) or not hmac.compare_digest(provided,self.token):
            raise Conflict('A trusted desktop main process is required')

    def capabilities(self):
        return {**self.documents.describe(),'desktop_host_active':self.enabled,
            'surface_identity':'OWNING_MAIN_PROCESS_WINDOW_NOT_RENDERER_PID',
            'annotation_modes':['VIEW_CAPTURE_AND_ANNOTATE','EDIT_INSTALLED_APPLICATION'],
            'background_capture':'APPLICATION_AND_PLATFORM_DEPENDENT'}

    def _binding(self,pid,ref):
        value=FileSource.model_validate(ref)
        if value.project_id!=pid:raise Conflict('Selected file belongs to another project')
        binding=self.workspaces.get(pid,value.workspace_id,value.binding_revision)
        if binding['binding_sha256']!=value.binding_sha256:raise Conflict('Selected file workspace binding changed')
        return value,binding

    def selection(self,pid,ref):
        value,binding=self._binding(pid,ref)
        observed=self.workspaces.read(pid,value.workspace_id,value.path)
        # The workspace handle layer verifies roots, links, file identity and bytes.
        path=Path(binding['root']).joinpath(*value.path.split('/'))
        return {'native_path':str(path),'root':binding['root'],
            'source_ref':observed['source_ref'],'current':observed['current']}

    def selection_saved_snapshot(self,pid,ref):
        """Explicit readonly native-open resolution, never action write admission."""
        value,binding=self._binding(pid,ref)
        observed=self.workspaces.read_saved_snapshot(pid,value.workspace_id,value.path)
        path=Path(binding['root']).joinpath(*value.path.split('/'))
        return {'native_path':str(path),'root':binding['root'],
            'source_ref':observed['source_ref'],'current':observed['current']}

    def grant(self,pid,intent,source):
        if not self.enabled:raise Conflict('Desktop main process is unavailable')
        if intent not in self.ACTIONS:raise InvalidInput('Unknown installed-application action')
        value,_=self._binding(pid,source);nonce=uuid.uuid4().hex
        with self.lock:
            current=time.monotonic();self.grants={k:v for k,v in self.grants.items() if v['expires']>current}
            if len(self.grants)>=512:raise Conflict('Too many pending desktop actions')
            self.grants[nonce]={'project_id':pid,'intent':intent,'source':value.model_dump(),'expires':current+45}
        return {'user_action_ref':nonce,'expires_in_seconds':45,'authority':'OWNING_MAIN_FRAME_IPC_REQUEST'}

    def authorize(self,pid,intent,source,nonce):
        with self.lock:value=self.grants.pop(nonce,None)
        return bool(value and value['expires']>time.monotonic() and value['project_id']==pid and
            value['intent']==intent and value['source']==source)

    def register_surface(self,pid,hwnd_decimal,owner_pid,css_bounds,zoom_factor):
        self.store.project(pid)
        if not self.enabled or owner_pid!=self.owner_pid:
            raise Conflict('Native surface is outside the owning desktop process')
        if not re.fullmatch(r'[1-9][0-9]{0,19}',hwnd_decimal):raise InvalidInput('Native surface identity is invalid')
        hwnd=int(hwnd_decimal)
        if hwnd>2**64-1:raise InvalidInput('Native surface identity is invalid')
        backend=self.documents.backend
        if backend is None:raise Conflict('Installed-application hosting is unavailable on this platform')
        api=backend.api;window=api.window(hwnd)
        if window['pid']!=owner_pid:raise Conflict('Native surface window owner changed')
        handle=api.require(api.kernel.OpenProcess(0x1000,False,owner_pid),'NATIVE_SURFACE_PROCESS_UNAVAILABLE')
        try:process=api.process(handle)
        finally:api.kernel.CloseHandle(handle)
        if process['exit_code']!=259 or process['session']==0:raise Conflict('Native surface owner is no longer active')
        if set(css_bounds)!={'x','y','width','height'} or any(type(v) not in (int,float) or not math.isfinite(v) for v in css_bounds.values()):
            raise InvalidInput('Workbench bounds are invalid')
        if not math.isfinite(zoom_factor) or not .1<=zoom_factor<=10:raise InvalidInput('Workbench zoom is invalid')
        scale=window['dpi']/96*zoom_factor
        physical={key:round(value*scale) for key,value in css_bounds.items()}
        from packages.adapters.windows_native_window_host import physical_bounds
        physical=physical_bounds(physical)
        if physical['x']<0 or physical['y']<0:raise InvalidInput('Workbench surface must be inside the application')
        ref=uuid.uuid4().hex
        with self.lock:
            # One main window/project needs only its current rectangle, avoiding unbounded resize history.
            self.surfaces={key:value for key,value in self.surfaces.items() if value['project_id']!=pid}
            self.surfaces[ref]={'project_id':pid,'hwnd':hwnd,'pid':owner_pid,'creation':process['creation'],'bounds':physical}
        return {'surface_ref':ref,'coordinate_unit':'PHYSICAL_PARENT_CLIENT_PIXELS','scale':scale}

    def surface(self,pid,ref):
        with self.lock:value=copy.deepcopy(self.surfaces.get(ref))
        if value is None or value.pop('project_id')!=pid:raise NotFound('Desktop surface is outside this project')
        return value

    def _capture_path(self,pid,view_ref,revision):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',pid) or not re.fullmatch(r'[0-9a-f]{32}',view_ref):
            raise InvalidInput('Capture identity is invalid')
        return resolve_data_path(f'native-captures/{pid}/{view_ref}/{revision:010d}.json',self.data_dir)

    def capture(self,pid,view_ref,action):
        from packages.domain.local_workspaces import canonical
        captured=self.documents.snapshot(pid,view_ref,action)
        frame=captured['frame_ref'];raw=captured['png']
        if sha(raw)!=frame['sha256']:raise Conflict('Native frame bytes changed')
        document=self.store.save_document(pid,f'window-{frame["frame_revision"]}.png',raw,
            'native-window-observation:'+frame['binding_sha256'])
        image={'document_id':document['id'],'revision':document['revision'],'sha256':document['sha256'],
            'url':f'/api/projects/{pid}/native-captures/{view_ref}/{frame["frame_revision"]}/image','media_type':'image/png'}
        result={'frame_ref':frame,'image':image,'ocr':{'status':'NOT_REQUESTED'},
            'observation_authority':'SELECTED_WINDOW_PIXELS_NOT_VERIFIED_ACTIVE_DOCUMENT_CONTENT'}
        target=self._capture_path(pid,view_ref,frame['frame_revision']);target.parent.mkdir(parents=True,exist_ok=True)
        self.workspaces._write(target,{'payload':result,'sha256':digest(result)})
        return result

    def image(self,pid,view_ref,revision):
        wrapper=self.workspaces._json(self._capture_path(pid,view_ref,revision));value=wrapper['payload']
        if digest(value)!=wrapper['sha256']:raise Conflict('Native capture receipt changed')
        ref=value['image'];doc=self.store.document(pid,ref['document_id'],ref['revision'])
        if doc['sha256']!=ref['sha256'] or sha(doc['raw_bytes'])!=ref['sha256']:raise Conflict('Native capture image changed')
        return doc['raw_bytes'],value

    def bitmap(self,pid,ref):
        if isinstance(ref,dict) and set(ref)=={'kind','file_source_ref','sha256'} and ref['kind']=='LOCAL_FILE':
            source,binding=self._binding(pid,ref['file_source_ref'])
            # Admission is read-only: feedback already owns the controller write
            # transaction. Observing a new workspace version here would open a
            # second writer and deadlock, even for unchanged image bytes.
            version=self.workspaces._version(binding,source.path,source.revision)
            if version is None or self.workspaces._ref(version)!=source.model_dump() or source.sha256!=ref['sha256']:
                raise Conflict('Local bitmap file source changed')
            raw=self.workspaces._raw(binding,source.path)
            if sha(raw)!=ref['sha256']:raise Conflict('Local bitmap source bytes changed')
            from packages.domain.document_import import image_metadata
            info=image_metadata(raw)
            return {'bytes':raw,'sha256':info['sha256'],'media_type':info['media_type'],
                'width':info['width'],'height':info['height'],'source_ref':source.model_dump(),
                'observation_authority':'SELECTED_LOCAL_BITMAP_NOT_VERIFIED_PROJECT_FACT'}
        if not isinstance(ref,dict) or set(ref)!={'kind','view_ref','frame_revision','sha256','binding_sha256'} or ref['kind']!='NATIVE_CAPTURE':
            raise InvalidInput('An exact native capture bitmap reference is required')
        if type(ref['frame_revision']) is not int or ref['frame_revision']<1:raise InvalidInput('Capture revision is invalid')
        raw,receipt=self.image(pid,ref['view_ref'],ref['frame_revision']);frame=receipt['frame_ref']
        if (frame['sha256'],frame['binding_sha256'])!=(ref['sha256'],ref['binding_sha256']):
            raise Conflict('Native bitmap frame reference changed')
        return {'bytes':raw,'sha256':frame['sha256'],'media_type':'image/png',
            'width':frame['width'],'height':frame['height'],
            'source_ref':{k:receipt['image'][k] for k in ['document_id','revision','sha256']},
            'observation_authority':receipt['observation_authority']}

    def close(self):
        with self.lock:self.grants.clear();self.surfaces.clear()
        return self.documents.close()

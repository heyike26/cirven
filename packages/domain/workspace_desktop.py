"""Trusted Electron-main desktop authority; stock native probes own OS details.

No business project, guessed desktop path, second Agent loop, user-window kill,
registry change or renderer HWND authority is introduced here.
"""
from __future__ import annotations
import copy
from contextlib import contextmanager
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import threading
import uuid
from typing import Annotated

from pydantic import Field, model_validator
from packages.domain.questions import RequestModel
from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import canonical, digest, now
from packages.domain.paths import resolve_data_path
from packages.domain.store import Conflict, InvalidInput

EDGES = ('LEFT', 'RIGHT', 'TOP', 'BOTTOM')
PRESENTATIONS = ('DESKTOP', 'MODELESS_POPOVER', 'FULL_WORKSPACE', 'DOCUMENT', 'APPLICATION')


class Rect(RequestModel):
    x: float = Field(strict=True, allow_inf_nan=False)
    y: float = Field(strict=True, allow_inf_nan=False)
    width: float = Field(gt=0, le=65536, strict=True, allow_inf_nan=False)
    height: float = Field(gt=0, le=65536, strict=True, allow_inf_nan=False)

    @model_validator(mode='after')
    def coordinates(self):
        if abs(self.x) > 65536 or abs(self.y) > 65536:
            raise ValueError('Coordinates exceed the supported screen range')
        return self


class WindowIdentity(RequestModel):
    hwnd_decimal: str = Field(pattern=r'^[1-9][0-9]{0,19}$')
    owner_pid: int = Field(strict=True, ge=1, le=2147483647)

    @model_validator(mode='after')
    def pointer(self):
        if int(self.hwnd_decimal) >= 1 << 64:
            raise ValueError('Native window identity exceeds pointer range')
        return self


class ContentSizeDIP(RequestModel):
    width: float = Field(strict=True,gt=0,le=65536,allow_inf_nan=False)
    height: float = Field(strict=True,gt=0,le=65536,allow_inf_nan=False)


class Layout(RequestModel):
    css_bounds: Rect
    zoom_factor: float = Field(strict=True, ge=.1, le=10, allow_inf_nan=False)
    electron_scale_factor: float | None = Field(default=None,strict=True,ge=.5,le=8,allow_inf_nan=False)
    content_size_dip: ContentSizeDIP | None = None
    shape_rectangles_dip: list[Rect] | None = Field(default=None,min_length=1, max_length=256)
    workarea_insets_dip: dict[str, Annotated[float, Field(strict=True, allow_inf_nan=False)]] | None = None

    @model_validator(mode='after')
    def insets(self):
        value = self.workarea_insets_dip
        if value is not None and (set(value) != set(EDGES) or any(type(v) not in (int, float) or not math.isfinite(v)
                or not 0 <= v <= 8192 for v in value.values()) or self.css_bounds.x < 0 or self.css_bounds.y < 0):
            raise ValueError('Four finite own-window workarea insets are required')
        if self.css_bounds.x<0 or self.css_bounds.y<0:raise ValueError('CSS workarea origin must lie inside the owning page')
        if any(r.x < 0 or r.y < 0 for r in self.shape_rectangles_dip or []):
            raise ValueError('Shape rectangles must lie inside the main window')
        if (self.electron_scale_factor is None)!=(self.content_size_dip is None):
            raise ValueError('Trusted Electron scale and content size must be supplied together')
        if self.electron_scale_factor is None and (self.shape_rectangles_dip is None or self.workarea_insets_dip is None):
            raise ValueError('Native raw geometry or a complete historical geometry receipt is required')
        return self


class MonitorScreen(RequestModel):
    bounds: Rect
    work_area: Rect
    scale_factor: float = Field(strict=True, ge=.5, le=8, allow_inf_nan=False)


class DesktopBindInput(Layout):
    main_window: WindowIdentity
    helper_windows: dict[str, WindowIdentity]
    presentation: str = Field(pattern=r'^DESKTOP$')
    screen: MonitorScreen
    popover_bounds: list[Rect] = Field(default_factory=list,max_length=16)

    @model_validator(mode='after')
    def helpers(self):
        values = [self.main_window, *self.helper_windows.values()]
        if set(self.helper_windows) != set(EDGES) or len({w.hwnd_decimal for w in values}) != 5:
            raise ValueError('Four distinct helper windows and one main window are required')
        if any(w.owner_pid != self.main_window.owner_pid for w in values):
            raise ValueError('All helper windows must belong to the same main process')
        return self


class DesktopUpdateInput(Layout):
    surface_ref: RevisionRef
    expected_input_epoch: int = Field(strict=True, ge=0)
    presentation: str
    popover_bounds: list[Rect] = Field(default_factory=list, max_length=16)

    @model_validator(mode='after')
    def mode(self):
        if self.presentation not in PRESENTATIONS:
            raise ValueError('Unknown desktop presentation')
        if self.presentation not in {'DESKTOP','MODELESS_POPOVER'} and self.popover_bounds:
            raise ValueError('Only desktop edge controls or a modeless popover may supply popup rectangles')
        return self



class DesktopPrepareInput(RequestModel):
    surface_ref: RevisionRef
    expected_input_epoch: int = Field(strict=True, ge=0)
    presentation: str

    @model_validator(mode='after')
    def mode(self):
        if self.presentation not in PRESENTATIONS:
            raise ValueError('Unknown desktop presentation')
        return self

def _cover(rect, scale):
    left, top = math.floor(rect['x'] * scale), math.floor(rect['y'] * scale)
    right = math.ceil((rect['x'] + rect['width']) * scale)
    bottom = math.ceil((rect['y'] + rect['height']) * scale)
    return {'x': left, 'y': top, 'width': right - left, 'height': bottom - top}


def _historical_physical_layout(value, metadata):
    """DIP region is window-relative; center is derived from actual screen HWND.

    Do not multiply multi-monitor global DIP origins by one monitor scale.
    Native EqualRgn is the final authority for rounding/DPI differences.
    """
    window, scale = metadata['window_bounds'], metadata['dpi'] / 96
    css, zoom = value['css_bounds'], value['zoom_factor']
    left, top = math.floor(css['x'] * zoom), math.floor(css['y'] * zoom)
    right, bottom = math.ceil((css['x'] + css['width']) * zoom), math.ceil((css['y'] + css['height']) * zoom)
    hole = _cover({'x': left, 'y': top, 'width': right-left, 'height': bottom-top}, scale)
    insets = {'LEFT': hole['x'], 'RIGHT': window['width']-hole['x']-hole['width'],
              'TOP': hole['y'], 'BOTTOM': window['height']-hole['y']-hole['height']}
    if min(insets.values()) < 0 or min(hole['width'], hole['height']) < 1:
        raise InvalidInput('Workspace center exceeds the actual owning window')
    supplied = {k: round(v * scale) for k, v in value['workarea_insets_dip'].items()}
    if supplied != insets:
        raise Conflict('Host chrome insets differ from actual native window geometry')
    width_dip, height_dip = round(window['width']/scale), round(window['height']/scale)
    if round(width_dip*scale)!=window['width'] or round(height_dip*scale)!=window['height']:
        raise Conflict('Actual owning window has incompatible DIP dimensions')
    mode=value['presentation']
    if mode in {'FULL_WORKSPACE','DOCUMENT','APPLICATION'}:
        wanted=[{'x':0,'y':0,'width':width_dip,'height':height_dip}]
    else:
        wanted=[{'x':0,'y':0,'width':width_dip,'height':top},
                {'x':0,'y':bottom,'width':width_dip,'height':height_dip-bottom},
                {'x':0,'y':top,'width':left,'height':bottom-top},
                {'x':right,'y':top,'width':width_dip-right,'height':bottom-top}]
        wanted=[r for r in wanted if r['width']>0 and r['height']>0]
        if mode=='MODELESS_POPOVER':
            for raw in value.get('popover_bounds',[]):
                pl,pt=math.floor(raw['x']*zoom),math.floor(raw['y']*zoom)
                pr,pb=math.ceil((raw['x']+raw['width'])*zoom),math.ceil((raw['y']+raw['height'])*zoom)
                x,y=max(pl,left),max(pt,top);r,b=min(pr,right),min(pb,bottom)
                if r>x and b>y:wanted.append({'x':x,'y':y,'width':r-x,'height':b-y})
        if not wanted:wanted=[{'x':0,'y':0,'width':1,'height':1}]
    if wanted!=value['shape_rectangles_dip']:
        raise Conflict('Host native shape differs from the deterministic presentation geometry')
    rects = [_cover(r, scale) for r in value['shape_rectangles_dip']]
    if any(r['x']+r['width'] > window['width'] or r['y']+r['height'] > window['height'] for r in rects):
        raise Conflict('Host shape exceeds the actual owning window')
    center = {**hole, 'x': window['x']+hole['x'], 'y': window['y']+hole['y']}
    return {'scale': scale, 'rectangles_physical': rects, 'center_physical': center,
            'thickness_physical': insets, 'window_bounds': copy.deepcopy(window)}


def physical_layout(value,metadata):
    if value.get('electron_scale_factor') is None:
        return {**_historical_physical_layout(value,metadata),'mapping_authority':'HISTORICAL_WINDOWS_DPI_RECEIPT_NOT_NATIVE_BINDING'}
    dsf,zoom=value['electron_scale_factor'],value['zoom_factor']
    window=metadata['window_bounds'];client=metadata.get('client_bounds_window_physical')
    if not client:raise Conflict('Actual native client viewport is required for Electron pixel mapping')
    scale=dsf*zoom;css=value['css_bounds']
    left=max(0,min(client['width'],math.floor(css['x']*scale)))
    top=max(0,min(client['height'],math.floor(css['y']*scale)))
    right=max(0,min(client['width'],math.ceil((css['x']+css['width'])*scale)))
    bottom=max(0,min(client['height'],math.ceil((css['y']+css['height'])*scale)))
    if right<=left or bottom<=top:raise InvalidInput('Requested workspace has no visible intersection with actual native client pixels')
    l,t=client['x']+left,client['y']+top;r,b=client['x']+right,client['y']+bottom
    hole={'x':l,'y':t,'width':r-l,'height':b-t}
    thickness={'LEFT':l,'RIGHT':window['width']-r,'TOP':t,'BOTTOM':window['height']-b}
    if min(thickness.values())<0:raise Conflict('Visible client workspace exceeds actual owning window')
    if value['presentation'] in {'FULL_WORKSPACE','DOCUMENT','APPLICATION'}:
        rects=[{'x':0,'y':0,'width':window['width'],'height':window['height']}]
    else:
        rects=[{'x':0,'y':0,'width':window['width'],'height':t},
               {'x':0,'y':b,'width':window['width'],'height':window['height']-b},
               {'x':0,'y':t,'width':l,'height':b-t},
               {'x':r,'y':t,'width':window['width']-r,'height':b-t}]
        rects=[v for v in rects if min(v['width'],v['height'])>0]
        if value['presentation'] in {'DESKTOP','MODELESS_POPOVER'}:
            for popup in value.get('popover_bounds',[]):
                pl=max(left,math.floor(popup['x']*scale));pt=max(top,math.floor(popup['y']*scale))
                pr=min(right,math.ceil((popup['x']+popup['width'])*scale));pb=min(bottom,math.ceil((popup['y']+popup['height'])*scale))
                if pr>pl and pb>pt:rects.append({'x':client['x']+pl,'y':client['y']+pt,'width':pr-pl,'height':pb-pt})
        if not rects:rects=[{'x':0,'y':0,'width':1,'height':1}]
    return {'scale':dsf,'electron_scale_factor':dsf,'zoom_factor':zoom,'native_dpi':metadata['dpi'],
        'mapping_authority':'TRUSTED_ELECTRON_SCALE_AND_ACTUAL_NATIVE_CLIENT_CLIP',
        'rectangles_physical':rects,'center_physical':{**hole,'x':window['x']+l,'y':window['y']+t},
        'thickness_physical':thickness,'window_bounds':copy.deepcopy(window),
        'client_bounds_window_physical':copy.deepcopy(client),'requested_css_bounds':copy.deepcopy(css),
        'visible_css_bounds':{'x':left/scale,'y':top/scale,'width':(right-left)/scale,'height':(bottom-top)/scale},
        'content_size_dip':copy.deepcopy(value['content_size_dip']),
        'actual_visible_css_viewport':{'width':client['width']/scale,'height':client['height']/scale}}


class WorkspaceDesktop:
    def __init__(self, data_dir, *, token=None, owner_pid=None, native=None, region_factory=None,
                 appbar_factory=None, platform=None):
        self.data_dir = Path(data_dir)
        self.token = token if token is not None else os.environ.get('EEAS_DESKTOP_HOST_TOKEN', '')
        self.owner_pid = owner_pid if owner_pid is not None else int(os.environ.get('EEAS_DESKTOP_HOST_PID', '0'))
        self.enabled = bool(isinstance(self.token,str) and re.fullmatch('[0-9a-f]{64}',self.token)
                            and type(self.owner_pid) is int and self.owner_pid > 0)
        self.platform = os.name if platform is None else platform
        self.lock, self.current, self.refs = threading.RLock(), None, {}
        self.native, self.region, self.bars, self.owned_region = None, None, None, None
        self.native_error = None
        if self.platform == 'nt':
            try:
                from packages.adapters.windows_desktop_region import Win32RegionAPI, WindowsDesktopRegionProbe
                from packages.adapters.windows_appbar_lease import WindowsAppBarLease
                self.native = Win32RegionAPI() if native is None else native
                self.region = (region_factory or WindowsDesktopRegionProbe)(surface_authority=self._authority, native=self.native)
                self.bars = (appbar_factory or WindowsAppBarLease)(authority=self._bar_authority)
                from packages.adapters.windows_owned_region import WindowsOwnedRegion
                self.owned_region=WindowsOwnedRegion(surface_authority=self._authority,native=self.native)
            except (ImportError, OSError, ValueError) as exc:
                self.native_error = type(exc).__name__ + ':' + str(exc)
                self.native,self.region,self.bars,self.owned_region=None,None,None,None

    def check_host(self, provided):
        if not self.enabled or not isinstance(provided,str) or not hmac.compare_digest(provided,self.token):
            raise Conflict('A trusted desktop main process is required')

    def _path(self, *parts):
        return resolve_data_path('/'.join(['workspace-desktop', *parts]), self.data_dir)

    def _write(self, path, value):
        path.parent.mkdir(parents=True,exist_ok=True)
        raw = canonical(value).encode()
        wrapper = {'payload':value,'sha256':hashlib.sha256(raw).hexdigest(),
                   'host_token_sha256':hashlib.sha256(self.token.encode()).hexdigest(),
                   'host_mac':hmac.new(self.token.encode(),raw,hashlib.sha256).hexdigest()}
        temporary = path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
        try:
            with temporary.open('xb') as f:
                f.write(canonical(wrapper).encode());f.flush();os.fsync(f.fileno())
            os.replace(temporary,path)
        finally:
            if temporary.exists():temporary.unlink()

    def _read(self, path, *, authority=False):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4*1024*1024:
            raise Conflict('Desktop workspace receipt is unavailable or unsafe')
        try:
            wrapper=json.loads(path.read_bytes());value=wrapper['payload'];raw=canonical(value).encode()
            if hashlib.sha256(raw).hexdigest()!=wrapper['sha256']:raise ValueError
            if authority and (wrapper['host_token_sha256']!=hashlib.sha256(self.token.encode()).hexdigest() or
                    not hmac.compare_digest(wrapper['host_mac'],hmac.new(self.token.encode(),raw,hashlib.sha256).hexdigest())):
                raise ValueError
            return value
        except (ValueError,KeyError,TypeError):
            raise Conflict('Desktop workspace receipt integrity failed') from None

    def _save(self, kind):
        state=self.current
        if state.get('lease'):
            try:state['cleanup_snapshot']=self.bars.export_state(state['lease']['lease_ref'])
            except (ValueError,Conflict,OSError) as exc:
                state['cleanup_snapshot_error']=type(exc).__name__+':'+str(exc)
        private={'session_id':state['session_id'],'owner_pid':self.owner_pid,'markers':state['markers'],
                 'layout':state['layout'],'surface_ref':state['surface_ref'],'epoch':state['epoch'],
                 'lease':state.get('lease'),'closed':state.get('closed',False),'fault':state.get('fault'),
                 'observation':state.get('observation'),'helper_placement':state.get('helper_placement'),'created_at':now(),'kind':kind,
                 'cleanup_snapshot':state.get('cleanup_snapshot'),'cleared_markers':state.get('cleared_markers',[]),
                 'bind_request':state.get('bind_request'),'prepare_pending':state.get('prepare_pending'),
                 'original_container':state.get('original_container'),'container_restoration':state.get('container_restoration')}
        name=uuid.uuid4().hex+'.json';path=self._path('observations',name)
        self._write(path,private);self._write(self._path('head.json'),{'observation':name,'sha256':digest(private)})

    def _prior(self, *, authority=False):
        head=self._path('head.json')
        if not head.exists():return None
        pointer=self._read(head,authority=authority)
        if not re.fullmatch('[0-9a-f]{32}\\.json',pointer.get('observation','')):raise Conflict('Invalid desktop observation reference')
        value=self._read(self._path('observations',pointer['observation']),authority=authority)
        if digest(value)!=pointer['sha256']:raise Conflict('Desktop observation changed')
        return value

    def _authority(self, ref):
        marker=self.refs.get(digest(ref))
        if marker is None or self.current is None or self.current.get('closed'):
            raise Conflict('Desktop surface is unknown or closed')
        self.native.validate(marker)
        return copy.deepcopy(marker)

    def _bar_authority(self, ref):
        self._authority(ref)
        return {'main':copy.deepcopy(self.current['markers']['main']),
                'bars':copy.deepcopy(self.current['markers']['bars']),'callback_message':0x8001}

    def _unavailable(self):
        return {'status':'WINDOWS_ONLY' if self.platform!='nt' else 'NATIVE_UNAVAILABLE','type':'DESKTOP',
                'fit_policy':'WINDOWS_WORKAREA','surface_ref':None,'region_verified':False,
                'visible_all_verified':False,'verified_input_blocked':False,
                'prepare_pending':False,'verified_agent_dispatch_blocked':True,
                'capabilities':{'native_desktop':False,'implementation':'WINDOWS_ONLY' if self.platform!='nt' else 'NATIVE_REGION_AND_OWN_APPBAR' if self.native is not None else 'NATIVE_COMPONENT_UNAVAILABLE',
                                'modeless_region':False},
                'input':{'epoch':0,'enabled':False,'input_root':'WINDOWS_DESKTOP_REGION'},
                'qualification':'NATIVE_WINDOWS_NOT_RUN' if self.platform!='nt' else 'NATIVE_NOT_AVAILABLE'}

    def _require_native(self):
        if self.native is None:raise Conflict('WINDOWS_NATIVE_REQUIRED' if self.platform!='nt' else 'WINDOWS_NATIVE_COMPONENT_UNAVAILABLE')

    def _safe(self, observation=None):
        state=self.current
        if state is None:return self._unavailable()
        obs=observation or state.get('observation') or {}
        full=state['layout']['presentation']=='FULL_WORKSPACE'
        pending=bool(state.get('prepare_pending'))
        foreground=obs.get('foreground_verified') is True
        verified=obs.get('region_verified') is True and not state.get('fault') and not state.get('closed')
        lease=state.get('lease') or {}
        usable=verified and lease.get('visible_all_verified') is True
        scale=obs.get('scale',1)
        monitor=(lease.get('original_monitor') or {}).get('monitor_bounds') or {'x':0,'y':0}
        dip_screen=((state.get('bind_request') or {}).get('screen') or {}).get('bounds') or {'x':0,'y':0}
        accepted={k:{'x':round(dip_screen['x']+(b['x']-monitor['x'])/scale),
                     'y':round(dip_screen['y']+(b['y']-monitor['y'])/scale),
                     'width':round(b['width']/scale),'height':round(b['height']/scale)}
                  for k,b in lease.get('accepted_bar_bounds',{}).items()}
        return {'status':'CLOSED' if state.get('closed') else 'FAULTED' if state.get('fault') or not usable else 'ACTIVE',
                'type':'DESKTOP','fit_policy':'WINDOWS_WORKAREA','surface_ref':copy.deepcopy(state['surface_ref']),
                'session_ref':{'id':state['session_id'],'revision':1,'sha256':digest({'id':state['session_id']})},
                'presentation':state['layout']['presentation'],'bounds':copy.deepcopy(state['layout']['css_bounds']),
                'region_verified':verified,'visible_all_verified':verified and lease.get('visible_all_verified') is True,
                'verified_input_blocked':full and usable and foreground,
                'verified_agent_dispatch_blocked':pending or full or bool(state.get('fault')) or bool(state.get('closed')),
                'prepare_pending':pending,'pending_presentation':(state.get('prepare_pending') or {}).get('presentation'),
                'foreground_verified':foreground,'input':{'epoch':state['epoch'],'enabled':not pending and not full and usable,
                    'input_root':'WINDOWS_DESKTOP_REGION'},'accepted_bar_bounds_dip':accepted,
                'helper_bounds_applied_native':bool((state.get('helper_placement') or {}).get('helper_bounds_applied_native')),
                'helpers_verified':bool((obs.get('helper_placement') or state.get('helper_placement') or {}).get('helpers_verified')),
                'accepted_bar_bounds_physical':copy.deepcopy(lease.get('accepted_bar_bounds',{})),
                'geometry':{k:copy.deepcopy(obs.get(k)) for k in ['electron_scale_factor','zoom_factor','native_dpi','mapping_authority','requested_css_bounds','visible_css_bounds','actual_visible_css_viewport','content_size_dip']},
                'actual_work_area_physical':copy.deepcopy((lease.get('actual_monitor') or {}).get('work_area')),
                'capabilities':{'native_desktop':usable,'implementation':'NATIVE_REGION_AND_OWN_APPBAR', 'modeless_region':True},
                'qualification':'NATIVE_REGION_AND_WORKAREA_QUERY_ONLY_USER_APPLICATION_REFLOW_REQUIRES_HOST_TEST',
                'limitations':['WORKAREA_RESERVATION_DOES_NOT_PROVE_ARBITRARY_EXISTING_WINDOW_REFLOW','NO_GLOBAL_USER_WINDOW_DISABLE'],
                'error':state.get('fault')}

    def _observe(self):
        state=self.current;ref=state['surface_ref'];metadata=self.owned_region.metadata(ref)
        geometry=physical_layout(state['layout'],metadata)
        proof=self.region.verify_region(ref,geometry['rectangles_physical'])
        lease=self.bars.verify_center(state['lease']['lease_ref'],geometry['center_physical'])
        helper_proof=self.owned_region.verify_helpers(state['markers']['bars'],lease.get('accepted_bar_bounds',{}))
        foreground=False
        if state['layout']['presentation']=='FULL_WORKSPACE':
            method=getattr(self.region,'verify_foreground',None)
            if callable(method):foreground=method(ref).get('verified_keyboard_owned') is True
        return {**geometry,'region_verified':proof.get('verified_region') is True,'foreground_verified':foreground,
                'region_proof':proof,'lease':lease,'helper_placement':helper_proof}

    def get(self):
        with self.lock:
            if self.native is None:return self._unavailable()
            if self.current is None:
                prior=self._prior()
                return {**self._unavailable(),'status':'NEEDS_REBIND' if prior and not prior['closed'] else 'UNBOUND',
                        'qualification':'PRIOR_OS_RESOURCES_NOT_ADOPTED' if prior and not prior['closed'] else 'NOT_BOUND'}
            if self.current.get('closed'):return self._safe()
            try:
                observation=self._observe()
                # Read-only RPC does not update receipts, epochs or native bars.
                state=copy.copy(self.current);state['lease']=observation['lease']
                previous=self.current;self.current=state
                try:return self._safe(observation)
                finally:self.current=previous
            except (ValueError,Conflict,InvalidInput,OSError) as exc:
                return {**self._safe({'region_verified':False}),'status':'FAULTED','region_verified':False,
                        'visible_all_verified':False,'verified_input_blocked':False,
                        'verified_agent_dispatch_blocked':True,'input':{'epoch':self.current['epoch'],'enabled':False,'input_root':'WINDOWS_DESKTOP_REGION'},
                        'error':{'type':type(exc).__name__,'message':str(exc)}}

    def _ref(self, layout, revision):
        identifier=self.current['surface_ref']['id'] if self.current else uuid.uuid4().hex+uuid.uuid4().hex
        return {'id':identifier,'revision':revision,'sha256':digest({'id':identifier,'revision':revision,
                'layout':layout,'identity':digest(self.current['markers'] if self.current else {})})}

    def bind(self, request):
        body=DesktopBindInput.model_validate(request).model_dump()
        with self.lock:
            self._require_native()
            if body['electron_scale_factor'] is None or body['content_size_dip'] is None:
                raise Conflict('Trusted Electron scale and actual main content size are required for native binding')
            if body['main_window']['owner_pid']!=self.owner_pid:raise Conflict('Desktop main process identity differs from controller launch authority')
            if self.current and not self.current.get('closed'):
                if self.current['bind_request']==body:return self.get()
                raise Conflict('Desktop is already bound; use the exact versioned update')
            prior=self._prior()
            if prior and not prior['closed']:
                prior=self._prior(authority=True)
                incoming={'main':int(body['main_window']['hwnd_decimal']),
                          'bars':{k:int(v['hwnd_decimal']) for k,v in body['helper_windows'].items()}}
                if incoming!={'main':prior['markers']['main']['hwnd'],
                              'bars':{k:v['hwnd'] for k,v in prior['markers']['bars'].items()}}:
                    raise Conflict('Prior desktop resources belong to different window instances')
                self._recover_close(prior)
            marked=[]
            try:
                for w in [body['main_window'],*[body['helper_windows'][k] for k in EDGES]]:
                    marked.append(self.native.mark_window(int(w['hwnd_decimal']),w['owner_pid']))
                if any(m['creation']!=marked[0]['creation'] for m in marked):raise Conflict('Native helpers are outside the exact main process lifetime')
                self.current={'session_id':uuid.uuid4().hex+uuid.uuid4().hex,'markers':{'main':marked[0],'bars':dict(zip(EDGES,marked[1:]))},
                              'layout':{k:body[k] for k in ['css_bounds','zoom_factor','electron_scale_factor','content_size_dip','shape_rectangles_dip','workarea_insets_dip','presentation','popover_bounds']},
                              'bind_request':body,'surface_ref':{'id':uuid.uuid4().hex+uuid.uuid4().hex},'epoch':1,'fault':None,'closed':False}
                ref=self._ref(self.current['layout'],1);self.current['surface_ref']=ref;self.refs[digest(ref)]=marked[0]
                metadata=self.owned_region.metadata(ref);geometry=physical_layout(self.current['layout'],metadata)
                self.current['original_container']=copy.deepcopy(metadata)
                scale=body['electron_scale_factor']
                if abs(scale-body['screen']['scale_factor'])>.001:raise Conflict('Trusted Electron scale differs from its actual main display receipt')
                monitor=self.bars.native.monitor(marked[0])
                if metadata['window_bounds']!=monitor['work_area']:
                    raise Conflict('Owning main window must exactly cover the actual pre-reservation Windows workarea')
                self.current['lease']=self.bars.reserve(ref,geometry['thickness_physical'],expected_center_physical=geometry['center_physical'])
                lease=self.current['lease']
                self.current['container_restoration']=self.owned_region.restore_container(ref,self.current['original_container'])
                self.current['helper_placement']=self.owned_region.place_helpers(self.current['markers']['bars'],lease.get('accepted_bar_bounds',{}))
                self.owned_region.apply(ref,geometry['rectangles_physical'])
                proof=self.region.verify_region(ref,geometry['rectangles_physical'])
                if lease.get('visible_all_verified') is not True:raise Conflict('Actual Windows rcWork differs from the expected center')
                self.current['observation']={**geometry,'region_verified':proof.get('verified_region') is True,'foreground_verified':False}
                self._save('BOUND');return self._safe()
            except Exception as exc:
                if self.current and self.current.get('surface_ref') and self.refs:
                    try:self.owned_region.fail_closed(self.current['surface_ref'])
                    except Exception as fail_closed:self.current['region_failure']=str(fail_closed)
                if self.current and self.current.get('lease'):
                    self.current['fault']={'type':type(exc).__name__,'message':str(exc)};self._save('BIND_FAILED_OWN_RESOURCES_RETAINED')
                else:
                    failures=[]
                    for m in reversed(marked):
                        try:self.native.clear_marker(m)
                        except Exception as cleanup:failures.append(str(cleanup))
                    self.current=None;self.refs.clear()
                    if failures:raise Conflict('Native identity cleanup was incomplete: '+','.join(failures)) from exc
                raise

    def prepare(self, request):
        body=DesktopPrepareInput.model_validate(request).model_dump()
        # This lock drains synchronous actions before main may setShape. It
        # changes only our Agent authority; human user windows stay untouched.
        with self.lock:
            self._require_native()
            if not self.current or self.current.get('closed') or self.current.get('fault'):
                raise Conflict('Desktop surface is not active for layout preparation')
            if body['surface_ref']!=self.current['surface_ref'] or body['expected_input_epoch']!=self.current['epoch']:
                raise Conflict('Stale desktop layout preparation')
            pending=self.current.get('prepare_pending')
            if pending:
                if pending['request']!=body:raise Conflict('Another exact desktop layout preparation is pending')
                return {**self.get(),'prepared':True,'replayed':True}
            observation=self._observe()
            if observation['region_verified'] is not True or observation['lease'].get('visible_all_verified') is not True:
                raise Conflict('Current native region or workarea is not verified for preparation')
            self.current['prepare_pending']={'request':body,'presentation':body['presentation'],'created_at':now()}
            self._save('PREPARED_AGENT_FENCED')
            return {**self._safe(observation),'prepared':True,'replayed':False}

    def update(self, request):
        body=DesktopUpdateInput.model_validate(request).model_dump()
        with self.lock:
            self._require_native()
            if not self.current or self.current.get('closed'):raise Conflict('Desktop surface is not active')
            if body['surface_ref']!=self.current['surface_ref'] or body['expected_input_epoch']!=self.current['epoch']:
                raise Conflict('Stale desktop surface or input epoch')
            if self.current.get('fault'):raise Conflict('Faulted desktop resources require trusted close and rebind')
            if body['electron_scale_factor'] is None or body['content_size_dip'] is None:
                raise Conflict('Trusted Electron scale and actual main content size are required for native updates')
            pending=self.current.get('prepare_pending')
            expected={'surface_ref':body['surface_ref'],'expected_input_epoch':body['expected_input_epoch'],
                      'presentation':body['presentation']}
            if not pending or pending['request']!=expected:
                raise Conflict('Exact trusted-main layout preparation is required before setting shape')
            old=self.current
            layout={k:body[k] for k in ['css_bounds','zoom_factor','electron_scale_factor','content_size_dip','shape_rectangles_dip','workarea_insets_dip','presentation','popover_bounds']}
            ref=self._ref(layout,old['surface_ref']['revision']+1);self.refs[digest(ref)]=old['markers']['main']
            self.current={**old,'layout':layout,'surface_ref':ref,'epoch':old['epoch']+1,'fault':None}
            try:
                self.current['container_restoration']=self.owned_region.restore_container(ref,old['original_container'])
                metadata=self.owned_region.metadata(ref);geometry=physical_layout(layout,metadata)
                lease=old['lease']
                if lease['thickness_physical']!=geometry['thickness_physical']:
                    lease=self.bars.update(lease['lease_ref'],geometry['thickness_physical'],lease['revision'],expected_center_physical=geometry['center_physical'])
                else:lease=self.bars.verify_center(lease['lease_ref'],geometry['center_physical'])
                self.current['lease']=lease
                self.current['container_restoration']=self.owned_region.restore_container(ref,old['original_container'])
                self.current['helper_placement']=self.owned_region.place_helpers(self.current['markers']['bars'],lease.get('accepted_bar_bounds',{}))
                self.owned_region.apply(ref,geometry['rectangles_physical'])
                proof=self.region.verify_region(ref,geometry['rectangles_physical'])
                if lease.get('visible_all_verified') is not True:raise Conflict('Actual Windows rcWork differs from the expected center')
                foreground=False
                if layout['presentation']=='FULL_WORKSPACE':
                    method=getattr(self.region,'verify_foreground',None)
                    if callable(method):foreground=method(ref).get('verified_keyboard_owned') is True
                self.current['observation']={**geometry,'region_verified':proof.get('verified_region') is True,'foreground_verified':foreground}
                self.current['prepare_pending']=None
                self._save('UPDATED');return self._safe()
            except Exception as exc:
                try:self.owned_region.fail_closed(ref)
                except Exception as fail_closed:self.current['region_failure']=str(fail_closed)
                self.current['fault']={'type':type(exc).__name__,'message':str(exc)}
                self._save('UPDATE_FAILED_AGENT_FENCED');raise

    @contextmanager
    def dispatch_guard(self, surface_ref, expected_input_epoch):
        """Root native Agent dispatch must execute inside this synchronous guard."""
        with self.lock:
            self._require_native()
            if not self.current or self.current.get('closed') or self.current.get('fault'):
                raise Conflict('Desktop Agent dispatch is not active')
            if surface_ref!=self.current['surface_ref'] or type(expected_input_epoch) is not int or expected_input_epoch!=self.current['epoch']:
                raise Conflict('Stale desktop Agent surface or input epoch')
            if self.current.get('prepare_pending'):raise Conflict('Agent desktop input is fenced by a pending native layout preparation')
            if self.current['layout']['presentation']=='FULL_WORKSPACE':raise Conflict('Agent desktop input is fenced by the full workspace panel')
            observation=self._observe()
            if observation['region_verified'] is not True or observation['lease'].get('visible_all_verified') is not True:
                raise Conflict('Current native desktop region or workarea is unverified')
            yield self._safe(observation)

    def close(self):
        with self.lock:
            if self.native is None:
                if self.platform!='nt':return {'status':'CLOSED','native_resources':'NONE_WINDOWS_ONLY'}
                return {'status':'NATIVE_UNAVAILABLE','native_resources':'RELEASE_NOT_VERIFIED'}
            if self.current is None:
                prior=self._prior()
                if prior and not prior['closed']:return self._recover_close(self._prior(authority=True))
                return {'status':'CLOSED','native_resources':'NONE'}
            if self.current.get('closed'):return self._safe()
            state=self.current;state['epoch']+=1
            if state.get('lease'):
                receipt=self.bars.release(state['lease']['lease_ref']);state['lease']=receipt
                if receipt.get('status')!='RELEASED' or receipt.get('original_work_area_restored') is not True:
                    state['fault']={'type':'RESOURCE_RELEASE_INCOMPLETE','message':'Exact own AppBar removal or original rcWork restoration was not verified'}
                    self._save('CLOSE_INCOMPLETE');return self._safe()
                # Persist the empty registration set while every own marker
                # still exists. A crash during marker removal must not leave a
                # stale snapshot which tries to remove already released bars.
                self._save('OWN_APPBARS_RELEASED_BEFORE_MARKER_REMOVAL')
            self.owned_region.fail_closed(state['surface_ref'])
            failures=[]
            state.setdefault('cleared_markers',[])
            for marker in [*state['markers']['bars'].values(),state['markers']['main']]:
                if marker['marker_name'] in state['cleared_markers']:continue
                try:
                    self.native.clear_marker(marker);state['cleared_markers'].append(marker['marker_name'])
                    self._save('OWN_MARKER_RELEASED')
                except Exception as exc:failures.append(str(exc))
            if failures:
                state['fault']={'type':'MARKER_RELEASE_INCOMPLETE','message':','.join(failures)};self._save('CLOSE_INCOMPLETE');return self._safe()
            state['closed']=True;state['fault']=None;self._save('CLOSED');self.refs.clear();return self._safe()


    def _recover_close(self, prior):
        if prior['owner_pid']!=self.owner_pid or prior['markers']['main']['pid']!=self.owner_pid:
            raise Conflict('Prior resource process differs from current trusted desktop main')
        snapshot=prior.get('cleanup_snapshot')
        if not snapshot:raise Conflict('Prior native resource cleanup snapshot is unavailable')
        for marker in [prior['markers']['main'],*prior['markers']['bars'].values()]:
            if marker['marker_name'] not in prior.get('cleared_markers',[]):self.native.validate(marker)
        # This short-lived lookup is only used by stock adapter release. It
        # never enables dispatch, reinstates a surface or accepts old UI refs.
        self.current={**prior,'epoch':prior['epoch']+1,'fault':{'type':'RECOVERY_CLEANUP','message':'Agent dispatch remains fenced'},
                      'bind_request':prior.get('bind_request')}
        self.refs[digest(snapshot['payload']['surface_ref'])]=prior['markers']['main']
        try:
            released=prior.get('lease') or {}
            if released.get('status')=='RELEASED' and released.get('original_work_area_restored') is True:
                # Authenticate prior in the caller, then read actual rcWork.
                # Already cleared markers cannot authorize another ABM_REMOVE.
                expected=released.get('original_monitor')
                if not isinstance(expected,dict) or expected!=snapshot['payload']['original_monitor']:
                    raise Conflict('Released AppBar receipt differs from its exact saved monitor')
                monitor=self.bars.native.read_monitor(expected['monitor_id'])
                receipt={'status':'OWN_STATE_RELEASED','removed_edges':list(snapshot['payload']['registered_edges']),
                    'original_work_area_restored':monitor==expected,'actual_monitor':monitor,
                    'adopted_live_authority':False,'shell_removal_replayed':False}
            else:
                receipt=self.bars.release_owned_state(snapshot)
            payload=copy.deepcopy(snapshot['payload'])
            payload['registered_edges']=[e for e in payload['registered_edges'] if e not in receipt['removed_edges']]
            payload['released']=not payload['registered_edges']
            self.current['cleanup_snapshot']={'payload':payload,'sha256':digest(payload)}
            self.current['lease']=None
            self._save('EXPLICIT_RESTART_OWN_RESOURCE_CLEANUP')
            if receipt['status']!='OWN_STATE_RELEASED' or receipt['original_work_area_restored'] is not True:
                raise Conflict('Restart cleanup did not verify exact own release and original rcWork restoration')
            if prior['markers']['main']['marker_name'] not in prior.get('cleared_markers',[]):
                self.owned_region.fail_closed(snapshot['payload']['surface_ref'])
            failures=[];self.current.setdefault('cleared_markers',[])
            for marker in [*prior['markers']['bars'].values(),prior['markers']['main']]:
                if marker['marker_name'] in self.current['cleared_markers']:continue
                try:
                    self.native.clear_marker(marker);self.current['cleared_markers'].append(marker['marker_name'])
                    self._save('RESTART_OWN_MARKER_RELEASED')
                except Exception as exc:failures.append(str(exc))
            if failures:raise Conflict('Restart own marker cleanup is incomplete: '+','.join(failures))
            self.current['closed']=True;self.current['fault']=None;self._save('RESTART_OWN_RESOURCES_CLOSED')
            return {'status':'CLOSED','original_work_area_restored':True,'adopted_live_authority':False,
                    'app_processes_terminated':False}
        finally:
            self.refs.clear();self.current=None

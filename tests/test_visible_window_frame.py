"""Portable Win32-boundary policy + real Pillow/PNG/private ledger tests.

Capture_api/source are injected policy fixtures, not native Windows evidence.
No screen, GUI, Windows API, model or user file is accessed by these tests.
"""
from contextlib import contextmanager
from copy import deepcopy
from io import BytesIO
import json
import os
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw
import pytest

from packages.adapters.visible_window_frame import VisibleFrameError, VisibleWindowFrames, COORDINATES
from packages.adapters.windows_desktop_region import _sha
from packages.domain.local_workspaces import canonical, sha


def check(condition,message='visible frame policy failed'):
    if not condition:raise AssertionError(message)


def reference(name):return {'id':name,'revision':1,'sha256':sha(name.encode())}


def window(**changes):
    value={'status':'WINDOW_OBSERVED','window_id':1234,'pid':4321,'process_creation':123456789,
        'os_session':1,'window_class':'OriginalFixtureWindow','window_thread':52,'dpi':144,
        'bounds':{'x':-220,'y':-60,'width':180,'height':120},'coordinate_space':COORDINATES,
        'visible':True,'minimized':False,**changes}
    value['identity_sha256']=_sha({k:value[k] for k in ('window_id','pid','process_creation','os_session','window_class','window_thread')})
    return value


def binding(sdk=None):return {'workspace_session_ref':reference('original-workspace'),'surface_ref':reference('original-surface'),
    'input_epoch':7,'sdk_session_ref':sdk}


class Source:
    def __init__(self,current):self.current=current;self.sequence=[];self.calls=0;self.inside=lambda:True
    def sample(self):
        check(self.inside(),'sample outside own PMv2 context');self.calls+=1
        return deepcopy(self.sequence.pop(0) if self.sequence else self.current)


class CaptureApi:
    def __init__(self,desktop=None,dwm=None):
        self.desktop=desktop or {'x':-300,'y':-100,'width':500,'height':320};self.dwm=dwm
        self.desktop_sequence=[];self.dwm_sequence=[];self.active=False;self.calls=[];self.restore_error=False;self.blank=False;self.wrong_size=False
    @contextmanager
    def physical_context(self):
        check(not self.active);self.active=True;self.calls.append('pmv2-enter')
        try:yield
        finally:
            self.active=False;self.calls.append('restore-original-thread-dpi')
            if self.restore_error:raise VisibleFrameError('INJECTED_RESTORE_FAILURE')
    def desktop_bounds(self):
        check(self.active);self.calls.append('virtual-desktop-metrics')
        return deepcopy(self.desktop_sequence.pop(0) if self.desktop_sequence else self.desktop)
    def visible_bounds(self,current):
        check(self.active);self.calls.append('dwm-visible-bounds')
        return deepcopy(self.dwm_sequence.pop(0) if self.dwm_sequence else self.dwm)
    def image(self):
        image=Image.new('RGB',(self.desktop['width']+(1 if self.wrong_size else 0),self.desktop['height']),(0,0,0))
        if not self.blank:
            draw=ImageDraw.Draw(image)
            for y in range(0,image.height,12):
                for x in range(0,image.width,13):draw.rectangle((x,y,x+8,y+7),fill=((x*3)%256,(y*5)%256,(x+y)%256))
        return image
    def grab(self):check(self.active);self.calls.append('actual-image-fixture-copy');return self.image()


@pytest.fixture
def scene(tmp_path,request):
    current=window();source=Source(current);api=CaptureApi();source.inside=lambda:api.active
    frames=VisibleWindowFrames(tmp_path/'private',window_source=source,capture_api=api,platform='portable-policy-test',clock=lambda:1234567.25)
    return {'current':current,'source':source,'api':api,'frames':frames,'tmp':tmp_path,'target':reference('actual-target'),'binding':binding()}


def capture(s):return s['frames'].capture(s['current'],s['target'],s['binding'])


def empty_ledger(s):check(list(s['frames'].data.iterdir())==[],'failed capture published a successful/partial frame')


def test_real_png_crop_negative_screen_origin_exact_ledger_and_null_sdk_scope(scene):
    s=scene;frame=capture(s);check(s['api'].calls[-1]=='restore-original-thread-dpi' and s['source'].calls==2)
    check(frame['session_ref'] is None and frame['pixel_source']=='VISIBLE_TARGET_RECTANGLE')
    check(frame['provider_kind']=='INJECTED_TEST_PROVIDER_NOT_WINDOWS_EVIDENCE')
    check(not frame['visual_coordinate_input_authorized'] and not frame['automatic_input_authorized'])
    check(not frame['coverage']['whole_page_seen'] and not frame['coverage']['occlusion_checked'])
    proof=frame['pixel_source_proof'];check(proof['target_bounds']==s['current']['bounds'] and proof['capture_bounds']==s['current']['bounds'])
    check(proof['self_exclusion_verified'] is False and proof['capture_method']=='PILLOW_ACTUAL_DESKTOP_CROP')
    check(s['frames'].has(frame['frame_ref']) and s['frames'].describe(frame['frame_ref'])==frame)
    frozen=s['frames'].lookup(frame['frame_ref'],target_ref=s['target'],binding=s['binding'])
    with Image.open(BytesIO(frozen['bytes'])) as actual:
        original=s['api'].image();expected=original.crop((80,40,260,160))
        check(actual.size==(180,120) and ImageChops.difference(actual,expected).getbbox() is None)
        expected.close();original.close()
    check(sha(frozen['bytes'])==frame['image_ref']['sha256'] and frozen['sha256']==proof['image_sha256'])
    check(max(frame['pixel_statistics']['channel_stddev'])>.5)
    # A fresh reader can inspect immutable image bytes without a live SDK session.
    fresh=VisibleWindowFrames(s['tmp']/'private',window_source=s['source'],capture_api=s['api'],platform='test')
    check(fresh.describe(frame['frame_ref'])==frame and fresh.lookup(frame['frame_ref'])==frozen)
    frame['pixel_source']='CALLER_MUTATION';check(fresh.describe(frame['frame_ref'])['pixel_source']=='VISIBLE_TARGET_RECTANGLE')
    evidence=Path(os.environ.get('EEAS_VISIBLE_FRAME_EVIDENCE',s['tmp']/'evidence'));evidence.mkdir(parents=True,exist_ok=True)
    (evidence/'portable-negative-origin.json').write_text(json.dumps({'scope':'INJECTED_API_POLICY_WITH_REAL_PILLOW_AND_LEDGER_NOT_WINDOWS_CAPTURE',
        'frame':fresh.describe(frame['frame_ref']),'png_sha256':frozen['sha256'],'calls':s['api'].calls},ensure_ascii=False,indent=2))


def test_maximized_invisible_border_uses_dwm_and_actual_png_matches_capture_bounds(tmp_path):
    current=window(bounds={'x':-8,'y':-8,'width':216,'height':136},dpi=192)
    source=Source(current);api=CaptureApi({'x':0,'y':0,'width':200,'height':120},{'x':0,'y':0,'width':200,'height':120})
    source.inside=lambda:api.active;frames=VisibleWindowFrames(tmp_path,window_source=source,capture_api=api)
    frame=frames.capture(current,reference('maximized-target'),binding(reference('readonly-sdk')))
    check((frame['width'],frame['height'])==(200,120) and frame['session_ref']==reference('readonly-sdk'))
    check(frame['pixel_source_proof']['target_bounds']==current['bounds'])
    check(frame['pixel_source_proof']['capture_bounds']==api.dwm)
    record=json.loads((frames.data/frame['frame_ref']['id']/'proof.json').read_text())
    check(record['bounds_source']=='DWM_EXTENDED_FRAME_BOUNDS' and record['before']==record['after'])
    check(frames.lookup(frame['frame_ref'])['width']==200)


@pytest.mark.parametrize('phase,change',[
    ('before',{'pid':9999}),('after',{'process_creation':123456790}),('after',{'window_id':5678}),
    ('after',{'window_thread':53}),('after',{'dpi':192}),
    ('after',{'bounds':{'x':-219,'y':-60,'width':180,'height':120}}),
    ('after',{'minimized':True}),('after',{'visible':False}),
    ('after',{'coordinate_space':'CSS_OR_UNKNOWN'}),
    ('before',{'status':'EXCLUDED_EEAS_WINDOW'}),
])
def test_foreground_lifetime_dpi_geometry_exclusion_and_visibility_fail_closed(scene,phase,change):
    s=scene;changed=window(**change)
    s['source'].sequence=[changed] if phase=='before' else [s['current'],changed]
    with pytest.raises(ValueError):capture(s)
    check(s['api'].active is False and s['api'].calls[-1]=='restore-original-thread-dpi');empty_ledger(s)
    if phase=='before':check('actual-image-fixture-copy' not in s['api'].calls)


def test_dwm_change_display_change_and_pmv2_restore_failure_do_not_publish_frames(scene):
    s=scene;s['api'].dwm_sequence=[None,{'x':-220,'y':-60,'width':179,'height':120}]
    with pytest.raises(ValueError):capture(s)
    empty_ledger(s)
    s['api'].dwm_sequence=[];s['api'].desktop_sequence=[s['api'].desktop,{**s['api'].desktop,'width':501}]
    with pytest.raises(ValueError):capture(s)
    empty_ledger(s)
    s['api'].desktop_sequence=[];s['api'].restore_error=True
    with pytest.raises(VisibleFrameError,match='RESTORE'):capture(s)
    empty_ledger(s)


@pytest.mark.parametrize('invalid', ['offscreen-without-dwm','invalid-dwm','wrong-desktop-image-size','blank-frame','forged-identity','wrong-scope'])
def test_no_clamp_no_rescale_no_blank_or_unattested_frame(scene,invalid):
    s=scene
    if invalid=='offscreen-without-dwm':s['current']=window(bounds={'x':-310,'y':-60,'width':180,'height':120});s['source'].current=s['current']
    elif invalid=='invalid-dwm':s['api'].dwm={'x':-220,'y':-60,'width':0,'height':120}
    elif invalid=='wrong-desktop-image-size':s['api'].wrong_size=True
    elif invalid=='blank-frame':s['api'].blank=True
    elif invalid=='forged-identity':s['current']['identity_sha256']='a'*64
    elif invalid=='wrong-scope':s['binding']['unexpected_permission']=True
    with pytest.raises(Exception):capture(s)
    empty_ledger(s);check(s['api'].active is False)


def test_private_lookup_rejects_scope_ref_png_and_manifest_tampering(scene):
    s=scene;frame=capture(s);ref=frame['frame_ref'];original=s['frames'].lookup(ref)
    with pytest.raises(ValueError):s['frames'].lookup(ref,target_ref=reference('other-target'))
    with pytest.raises(ValueError):s['frames'].lookup(ref,binding={**s['binding'],'input_epoch':8})
    check(not s['frames'].has({**ref,'sha256':'0'*64}))
    check(not s['frames'].has({**ref,'id':'../not-a-frame'}))
    image=s['frames'].data/ref['id']/'frame.png';image.write_bytes(original['bytes']+b'changed')
    with pytest.raises(ValueError):s['frames'].lookup(ref)
    image.write_bytes(original['bytes'])
    proof=s['frames'].data/ref['id']/'proof.json';proof.write_bytes(proof.read_bytes()+b' ')
    check(not s['frames'].has(ref))
    with pytest.raises(ValueError):s['frames'].describe(ref)
    check(image.read_bytes()==original['bytes'],'failed lookup deleted or rewrote evidence')


def test_symlink_roots_and_frame_paths_do_not_read_external_bytes(scene):
    s=scene;frame=capture(s);image=s['frames'].data/frame['frame_ref']['id']/'frame.png'
    external=s['tmp']/'preserved-external.png';external.write_bytes(b'user-owned marker; must remain untouched')
    image.unlink();image.symlink_to(external)
    with pytest.raises(ValueError):s['frames'].lookup(frame['frame_ref'])
    check(external.read_bytes()==b'user-owned marker; must remain untouched')
    link=s['tmp']/'linked-private';link.symlink_to(s['tmp']/'private',target_is_directory=True)
    with pytest.raises(ValueError):VisibleWindowFrames(link,window_source=s['source'],capture_api=s['api'])


def test_native_default_is_windows_only_and_creates_no_desktop_fallback(tmp_path):
    if os.name=='nt':pytest.skip('Portable default guard test is for the WSL host; real Windows qualification is separate')
    with pytest.raises(VisibleFrameError,match='WINDOWS_VISIBLE_CAPTURE_ONLY'):
        VisibleWindowFrames(tmp_path/'must-not-create',window_source=Source(window()))
    check(not (tmp_path/'must-not-create').exists())

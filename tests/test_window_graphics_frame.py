"""Real PNG/private ledgers and owned subprocesses; Win32 is an explicit seam."""
import asyncio
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest

from apps.api.native_runtime import NativeCollaborationRuntime, register_native_collaboration_routes
from apps.api.owner_shutdown import OwnerShutdown
from packages.adapters.window_graphics_frame import WindowGraphicsFrames, WindowCaptureWorker, METHOD
from packages.adapters.visible_window_frame import VisibleFrameError
from packages.domain.local_workspaces import sha
from packages.domain.native_collaboration import NativeCollaborationAuthority
from test_visible_window_frame import window, reference, binding
from test_visible_frame_runtime_integration import WindowTransport, OBSERVE, HEADERS, bind
from test_native_collaboration import TOKEN, HostLifecycleCounter


def png(size=(180,120)):
    image=Image.new('RGB',size,'white');ImageDraw.Draw(image).rectangle((9,10,60,50),fill='#456789')
    stream=BytesIO();image.save(stream,'PNG');image.close();return stream.getvalue()


class Source:
    def __init__(self):self.current=window();self.sequence=[]
    def inspect(self,hwnd):
        assert hwnd==self.current['window_id']
        return deepcopy(self.sequence.pop(0) if self.sequence else self.current)
    def excluded_pids(self):return [9000]


class Worker:
    owned_processes=()
    def __init__(self):self.calls=[];self.mutate=lambda v:v;self.closed=False
    def capture(self,current,excluded):
        assert not self.closed;self.calls.append((deepcopy(current),excluded))
        raw=png((current['bounds']['width'],current['bounds']['height']))
        return self.mutate({'before':deepcopy(current),'after':deepcopy(current),'capture_bounds':current['bounds'],
            'bytes':raw,'image_sha256':sha(raw),'method':METHOD,'package_version':'2.0.1'})
    def close(self):self.closed=True;return {'status':'CLOSED','pending_process_count':0}


def scene(tmp_path):
    source,worker=Source(),Worker();frames=WindowGraphicsFrames(tmp_path,window_source=source,worker=worker)
    return frames,source,worker


def test_exact_png_scope_and_immutable_restart_without_cua_input_or_foreground(tmp_path):
    frames,source,worker=scene(tmp_path);b=binding();target=reference('selected-target')
    frame=frames.capture(source.current,target,b);assert len(worker.calls)==1
    assert frame['pixel_source']=='EXACT_TARGET_WINDOW' and frame['coverage']['target_window_only']
    assert not frame['automatic_input_authorized'] and not frame['visual_coordinate_input_authorized']
    assert not frame['coverage']['whole_page_seen'] and frame['provider_kind']=='INJECTED_TEST_PROVIDER_NOT_WINDOWS_EVIDENCE'
    saved=frames.lookup(frame['frame_ref'],target_ref=target,binding=b);assert saved['bytes']==png()
    fresh=WindowGraphicsFrames(tmp_path,window_source=source,worker=Worker());assert fresh.describe(frame['frame_ref'])==frame
    assert fresh.lookup(frame['frame_ref'])==saved and fresh.worker.calls==[]
    with pytest.raises(ValueError):fresh.lookup(frame['frame_ref'],binding={**b,'input_epoch':8})
    with pytest.raises(ValueError):fresh.lookup(frame['frame_ref'],target_ref=reference('different'))
    path=frames.data/frame['frame_ref']['id']/'frame.png';path.write_bytes(saved['bytes']+b'changed')
    with pytest.raises(ValueError):fresh.lookup(frame['frame_ref'])


@pytest.mark.parametrize('change',[
    {'pid':999},{'process_creation':123456790},{'visible':False},{'minimized':True},
    {'dpi':192},{'window_thread':900},{'coordinate_space':'UNKNOWN'},
    {'bounds':{'x':1,'y':1,'width':180,'height':120}},
])
def test_lifetime_geometry_dpi_or_visibility_change_does_not_publish(tmp_path,change):
    frames,source,worker=scene(tmp_path);source.sequence=[source.current,window(**change)]
    with pytest.raises(ValueError):frames.capture(source.current,reference('target'),binding())
    assert not list(frames.data.glob('*/observation.json'))


def test_maximized_invisible_border_uses_explicit_matching_dwm_geometry(tmp_path):
    frames,source,worker=scene(tmp_path);source.current=window(bounds={'x':-8,'y':-8,'width':216,'height':136})
    visible={'x':0,'y':0,'width':200,'height':120}
    def dwm(v):
        raw=png((200,120));return {**v,'capture_bounds':visible,'dwm_before':visible,'dwm_after':visible,'bytes':raw,'image_sha256':sha(raw)}
    worker.mutate=dwm;frame=frames.capture(source.current,reference('maximized'),binding())
    assert frame['width']==200 and frame['pixel_source_proof']['capture_bounds']==visible
    worker.mutate=lambda v:{**dwm(v),'dwm_after':None}
    with pytest.raises(ValueError):frames.capture(source.current,reference('maximized'),binding())


@pytest.mark.parametrize('mutate',[
    lambda v:{**v,'image_sha256':'0'*64},lambda v:{**v,'method':'TITLE_OR_MONITOR'},
    lambda v:{**v,'package_version':'future'},lambda v:{**v,'bytes':b'not PNG','image_sha256':sha(b'not PNG')},
    lambda v:{**v,'after':window(pid=9876)},
])
def test_changed_bytes_proof_or_provider_is_never_published(tmp_path,mutate):
    frames,source,worker=scene(tmp_path);worker.mutate=mutate
    with pytest.raises((ValueError,OSError)):frames.capture(source.current,reference('target'),binding())
    assert not list(frames.data.glob('*/observation.json'))


def test_actual_owned_helper_timeout_is_reaped_and_no_idle_capture_remains(tmp_path):
    processes=[]
    def start(_argv,**kwargs):
        p=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(90)'],**kwargs);processes.append(p);return p
    worker=WindowCaptureWorker(tmp_path/'jobs',timeout=.1,process_factory=start)
    with pytest.raises(VisibleFrameError,match='TIMEOUT'):worker.capture(window(),[9000])
    assert len(processes)==1 and processes[0].poll() is not None and not worker.owned_processes
    assert list((tmp_path/'jobs').iterdir())==[]
    assert worker.close()['status']=='CLOSED'
    with pytest.raises(VisibleFrameError,match='OWNER_CLOSED'):worker.capture(window(),[9000])
    assert len(processes)==1


def test_unknown_cleanup_retains_same_owner_blocks_recapture_and_can_later_settle(tmp_path):
    class Pending:
        exited=False
        def poll(self):return 0 if self.exited else None
        def wait(self,timeout):
            if self.exited:return 0
            raise subprocess.TimeoutExpired('owned fixture',timeout)
        def terminate(self):pass
        def kill(self):pass
    process=Pending();calls=[]
    worker=WindowCaptureWorker(tmp_path/'jobs',process_factory=lambda *_a,**_k:(calls.append(True) or process))
    with pytest.raises(VisibleFrameError,match='CLEANUP_UNKNOWN'):worker.capture(window(),[9000])
    assert worker.owned_processes==(process,) and worker.close()['status']=='CLOSE_UNKNOWN'
    with pytest.raises(VisibleFrameError):worker.capture(window(),[9000])
    assert len(calls)==1 and list((tmp_path/'jobs').iterdir())
    process.exited=True;assert worker.close()['status']=='CLOSED' and not worker.owned_processes
    assert list((tmp_path/'jobs').iterdir())==[]


def test_original_runtime_http_proof_and_frozen_image_without_sdk_or_model(tmp_path):
    source,worker=WindowTransport(),Worker();source.excluded_pids=lambda:[9000]
    authority=NativeCollaborationAuthority(tmp_path,token=TOKEN,owner_pid=123,native=HostLifecycleCounter(),platform='POLICY')
    class Runtime(NativeCollaborationRuntime):
        def _start_context(self):
            super()._start_context();self.visible_frames=WindowGraphicsFrames(tmp_path/'frames',window_source=source,worker=worker)
            self.context.visual_capture=self.visible_frames.capture
    runtime=Runtime(tmp_path,authority=authority,source_factory=lambda **_:source,platform='POLICY')
    app=FastAPI();register_native_collaboration_routes(app,None,tmp_path,service=runtime)
    with TestClient(app) as client:
        bind(client);response=client.post('/api/desktop-host/context/observe',json=OBSERVE,headers=HEADERS)
        assert response.status_code in (200,201),response.text
        snapshot=response.json();assert snapshot['observation']['status']=='READY_OBSERVATION'
        assert snapshot['observation']['coverage']['pixel_source']=='EXACT_TARGET_WINDOW'
        assert snapshot['preview']['status']=='AVAILABLE' and runtime.cua is None
        image=client.get(snapshot['preview']['image_ref']['download_url'],headers=HEADERS)
        assert image.status_code==200 and image.content==png()
        frame=snapshot['observation']['observations']['visual'];assert runtime.exact_frame_image(snapshot['observation']['context_ref'],frame['frame_ref'])['bytes']==png()
        saved=runtime.context_rpc.frozen_lookup(snapshot['observation']['context_ref']);assert saved['current'] is False
    assert runtime.close()['status']=='CLOSED' and worker.closed


def test_shutdown_retains_actual_capture_handle_even_after_registry_is_cleared(tmp_path):
    process=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(90)'])
    class Runtime:
        owned_processes=(process,)
        cua=None
        def close(self):
            process.terminate();process.wait(timeout=2);self.owned_processes=();return {'status':'CLOSED'}
    app=SimpleNamespace(state=SimpleNamespace(native_collaboration_runtime=Runtime()))
    try:
        receipt=asyncio.run(OwnerShutdown(app,tmp_path).close())
        row=next(v for v in receipt['outcomes'] if v['owner_kind']=='RUNTIME_SDK')
        assert row['captured_process_count']==row['stopped_process_count']==1 and row['pending_process_count']==0
        assert row['status']=='CLOSED' and process.poll() is not None
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=2)


def test_runtime_and_aggregate_cannot_report_closed_while_capture_cleanup_is_unknown(tmp_path):
    process=subprocess.Popen([sys.executable,'-B','-c','import time;time.sleep(90)'])
    class Frames:
        owned_processes=(process,)
        def close(self):return {'status':'CLOSE_UNKNOWN','pending_process_count':1}
    runtime=NativeCollaborationRuntime(tmp_path,platform='POLICY')
    retained=Frames();runtime.visible_frames=retained
    app=SimpleNamespace(state=SimpleNamespace(native_collaboration_runtime=runtime))
    try:
        receipt=asyncio.run(OwnerShutdown(app,tmp_path).close())
        row=next(v for v in receipt['outcomes'] if v['owner_kind']=='RUNTIME_SDK')
        assert row['status']=='CLOSE_UNKNOWN' and row['pending_process_count']==1 and row['blocked']
        assert runtime.visible_frames is retained and runtime.owned_processes==(process,)
        assert runtime.close()['status']=='CLOSE_UNKNOWN'
    finally:
        process.terminate();process.wait(timeout=2)

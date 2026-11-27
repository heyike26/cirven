"""Portable authority/geometry/receipt tests; no fake native success.

Linux is actual WINDOWS_ONLY. Real region/AppBar positive qualification belongs
in the original native adapter and trusted Electron host acceptance.
"""
import copy
import json
from pathlib import Path
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic import ValidationError
from apps.api.workspace_desktop import register_workspace_desktop_routes
from packages.domain.local_workspaces import canonical,digest
from packages.domain.store import Conflict,InvalidInput
from packages.domain.workspace_desktop import WorkspaceDesktop,DesktopBindInput,DesktopUpdateInput,DesktopPrepareInput,physical_layout


def bind_body():
    return {'main_window':{'hwnd_decimal':'100','owner_pid':123},
            'helper_windows':{k:{'hwnd_decimal':str(i+101),'owner_pid':123} for i,k in enumerate(['LEFT','RIGHT','TOP','BOTTOM'])},
            'css_bounds':{'x':200,'y':40,'width':500,'height':700},'zoom_factor':1,
            'shape_rectangles_dip':[{'x':0,'y':0,'width':1000,'height':40},
                {'x':0,'y':740,'width':1000,'height':60},{'x':0,'y':40,'width':200,'height':700},
                {'x':700,'y':40,'width':300,'height':700}],
            'workarea_insets_dip':{'LEFT':200,'RIGHT':300,'TOP':40,'BOTTOM':60},'presentation':'DESKTOP',
            'screen':{'bounds':{'x':0,'y':0,'width':1000,'height':850},'work_area':{'x':0,'y':0,'width':1000,'height':800},'scale_factor':1}}


def host(tmp_path,token='a'*64,pid=123):
    return WorkspaceDesktop(tmp_path,token=token,owner_pid=pid,platform='posix')


def update_body(mode='FULL_WORKSPACE'):
    b=bind_body();return {k:v for k,v in b.items() if k not in ['main_window','helper_windows','screen']}|{
        'surface_ref':{'id':'f'*64,'revision':1,'sha256':'c'*64},'expected_input_epoch':1,
        'presentation':mode,'popover_bounds':[],
        'shape_rectangles_dip':[{'x':0,'y':0,'width':1000,'height':800}]}


def test_actual_linux_no_native_claim_no_os_mutation_and_no_project_needed(tmp_path):
    value=host(tmp_path);result=value.get()
    assert result['status']=='WINDOWS_ONLY' and result['surface_ref'] is None
    assert not any(result[k] for k in ['region_verified','visible_all_verified','verified_input_blocked'])
    assert result['capabilities']['native_desktop'] is False and result['input']['enabled'] is False
    assert value.close()=={'status':'CLOSED','native_resources':'NONE_WINDOWS_ONLY'}
    assert list(tmp_path.iterdir())==[]
    with pytest.raises(Conflict,match='WINDOWS_NATIVE_REQUIRED'):value.bind(bind_body())
    with pytest.raises(Conflict,match='WINDOWS_NATIVE_REQUIRED'):value.update(update_body())
    with pytest.raises(Conflict,match='WINDOWS_NATIVE_REQUIRED'):value.prepare({'surface_ref':update_body()['surface_ref'],'expected_input_epoch':1,'presentation':'FULL_WORKSPACE'})
    with pytest.raises(Conflict,match='WINDOWS_NATIVE_REQUIRED'):
        with value.dispatch_guard({'id':'f'*64,'revision':1,'sha256':'c'*64},1):pass
    assert list(tmp_path.iterdir())==[]


def test_main_secret_guard_cannot_be_replaced_by_renderer_pid_or_window(tmp_path):
    value=host(tmp_path);value.check_host('a'*64)
    for token in [None,'','a'*63,'b'*64,123]:
        with pytest.raises(Conflict):value.check_host(token)
    for token,pid in [('z'*64,123),('a'*64,0),('a'*64,True)]:
        with pytest.raises(Conflict):host(tmp_path,token,pid).check_host(token)


@pytest.mark.parametrize('mutate',[
    lambda b:b['main_window'].update(owner_pid=True),
    lambda b:b['main_window'].update(hwnd_decimal='18446744073709551616'),
    lambda b:b['helper_windows']['LEFT'].update(owner_pid=456),
    lambda b:b['helper_windows']['LEFT'].update(hwnd_decimal='100'),
    lambda b:b['helper_windows'].pop('TOP'),
    lambda b:b['main_window'].update(marker_name='EEAS-renderer-forged'),
    lambda b:b.update(project_id='unneeded-fake-project'),
    lambda b:b.update(zoom_factor=float('nan')),
    lambda b:b['workarea_insets_dip'].update(BOTTOM=True),
    lambda b:b.update(shape_rectangles_dip=[]),
])
def test_native_identity_and_exact_geometry_schema_reject_bad_input(mutate):
    body=bind_body();mutate(body)
    with pytest.raises(ValidationError):DesktopBindInput.model_validate(body)


def test_safe_update_cas_only_no_hwnd_or_markers_accepted():
    b=update_body();assert DesktopUpdateInput.model_validate(b).expected_input_epoch==1
    for patch in [{'expected_input_epoch':True},{'presentation':'FAKE_SCREENSHOT'},
                  {'main_window':{'hwnd_decimal':'100','owner_pid':123}},
                  {'popover_bounds':[{'x':1,'y':1,'width':2,'height':2}]},
                  {'surface_ref':{'id':'f'*64,'revision':True,'sha256':'c'*64}}]:
        with pytest.raises(ValidationError):DesktopUpdateInput.model_validate(b|patch)


def test_exact_physical_coordinate_conversion_at_negative_multi_monitor_origin():
    b=DesktopBindInput.model_validate(bind_body()).model_dump()
    metadata={'window_bounds':{'x':-2000,'y':120,'width':2000,'height':1600},'dpi':192}
    g=physical_layout(b,metadata)
    assert g['center_physical']=={'x':-1600,'y':200,'width':1000,'height':1400}
    assert g['thickness_physical']=={'LEFT':400,'RIGHT':600,'TOP':80,'BOTTOM':120}
    assert g['rectangles_physical'][0]=={'x':0,'y':0,'width':2000,'height':80}
    # Global monitor origin must not be blindly multiplied by DPI.
    assert g['center_physical']['x']!=-3600


def test_full_workspace_requires_whole_actual_window_shape_and_modeless_clips_only_popup():
    meta={'window_bounds':{'x':0,'y':0,'width':1000,'height':800},'dpi':96}
    full=DesktopUpdateInput.model_validate(update_body()).model_dump()
    assert physical_layout(full,meta)['rectangles_physical']==[{'x':0,'y':0,'width':1000,'height':800}]
    full['shape_rectangles_dip']=[{'x':200,'y':40,'width':500,'height':700}]
    with pytest.raises(Conflict,match='deterministic'):physical_layout(full,meta)
    b=bind_body();modeless={k:v for k,v in b.items() if k not in ['main_window','helper_windows','screen']}|{
        'presentation':'MODELESS_POPOVER','popover_bounds':[{'x':100,'y':20,'width':200,'height':120}]}
    modeless['shape_rectangles_dip']=b['shape_rectangles_dip']+[{'x':200,'y':40,'width':100,'height':100}]
    g=physical_layout(modeless,meta)
    assert g['rectangles_physical'][-1]=={'x':200,'y':40,'width':100,'height':100}
    assert len(g['rectangles_physical'])==5
    changed=copy.deepcopy(modeless);changed['workarea_insets_dip']['RIGHT']=200
    with pytest.raises(Conflict,match='insets'):physical_layout(changed,meta)


def test_zoom_geometry_exceeding_native_window_refuses_claim():
    b=bind_body();b['zoom_factor']=2
    with pytest.raises(InvalidInput,match='exceeds'):physical_layout(b,{'window_bounds':{'x':0,'y':0,'width':1000,'height':800},'dpi':96})


def test_receipts_immutable_observations_restart_integrity_and_host_mac(tmp_path):
    value=host(tmp_path);path=value._path('observations','0'*32+'.json')
    payload={'kind':'ORIGINAL_NEGATIVE_AUTHORITY_FIXTURE','closed':False,'owner_pid':123}
    value._write(path,payload)
    value._write(value._path('head.json'),{'observation':path.name,'sha256':digest(payload)})
    restarted=host(tmp_path)
    assert restarted._prior(authority=True)==payload
    # A new desktop lifetime may read old evidence, but cannot use it as authority.
    different=host(tmp_path,'b'*64)
    assert different._prior()==payload
    with pytest.raises(Conflict,match='integrity'):different._prior(authority=True)
    wrapper=json.loads(path.read_bytes());wrapper['payload']['closed']=True
    path.write_bytes(canonical(wrapper).encode())
    with pytest.raises(Conflict,match='integrity'):restarted._prior()
    wrapper['sha256']=digest(wrapper['payload']);path.write_bytes(canonical(wrapper).encode())
    with pytest.raises(Conflict,match='integrity'):restarted._prior(authority=True)


def test_stored_receipt_path_symlink_or_escape_refused_without_os_actions(tmp_path):
    value=host(tmp_path);outside=tmp_path.parent/(tmp_path.name+'-outside');outside.mkdir()
    folder=tmp_path/'workspace-desktop';folder.mkdir();(folder/'observations').symlink_to(outside,target_is_directory=True)
    with pytest.raises(InvalidInput,match='symlink|escaped'):value._path('observations','0'*32+'.json')
    with pytest.raises(InvalidInput):value._path('..','arbitrary.json')


def test_host_header_guarded_rpc_exact_body_no_project_or_handles_in_get(tmp_path):
    app=FastAPI();service=host(tmp_path);register_workspace_desktop_routes(app,tmp_path,service=service)
    @app.exception_handler(Conflict)
    async def conflict(request,exc):return JSONResponse({'error':str(exc)},status_code=409)
    with TestClient(app) as client:
        root='/api/desktop-host/workspace'
        assert client.get(root+'/get').status_code==409
        assert client.get(root+'/get',headers={'X-EEAS-Desktop-Host':'b'*64}).status_code==409
        headers={'X-EEAS-Desktop-Host':'a'*64}
        res=client.get(root+'/get',headers=headers)
        assert res.status_code==200 and res.json()['status']=='WINDOWS_ONLY'
        assert not any(w in json.dumps(res.json()).lower() for w in ['hwnd','owner_pid','marker_name'])
        assert client.post(root+'/bind',json=bind_body(),headers=headers).status_code==409
        assert client.post(root+'/update',json=update_body(),headers=headers).status_code==409
        prep={'surface_ref':update_body()['surface_ref'],'expected_input_epoch':1,'presentation':'FULL_WORKSPACE'}
        assert client.post(root+'/prepare',json=prep,headers=headers).status_code==409
        assert client.post(root+'/prepare',json=prep|{'hwnd_decimal':'100'},headers=headers).status_code==422
        assert client.post(root+'/close',json={'kill_all':True},headers=headers).status_code==422
        assert client.post(root+'/close',json={},headers=headers).json()['status']=='CLOSED'


def test_requested_windows_without_available_native_backend_never_claims_close_restored(tmp_path):
    value=WorkspaceDesktop(tmp_path,token='a'*64,owner_pid=123,platform='nt')
    # This is an actual absent/unsupported Windows component on Linux, not a
    # stand-in native adapter and not a successful native qualification.
    if value.native is not None:pytest.skip('Windows backend is actually available; host qualification owns close')
    assert value.get()['status']=='NATIVE_UNAVAILABLE'
    assert value.close()=={'status':'NATIVE_UNAVAILABLE','native_resources':'RELEASE_NOT_VERIFIED'}



def test_prepare_exact_safe_ref_epoch_and_mode_only():
    b={'surface_ref':update_body()['surface_ref'],'expected_input_epoch':1,'presentation':'MODELESS_POPOVER'}
    assert DesktopPrepareInput.model_validate(b).presentation=='MODELESS_POPOVER'
    for patch in [{'expected_input_epoch':True},{'presentation':'FAKE_PANEL'},
                  {'hwnd_decimal':'100'},{'owner_pid':123},
                  {'surface_ref':{'id':'f'*64,'revision':0,'sha256':'c'*64}}]:
        with pytest.raises(ValidationError):DesktopPrepareInput.model_validate(b|patch)

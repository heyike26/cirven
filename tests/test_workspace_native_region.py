"""Raw Electron geometry and physical pixel invariants, without fake native success."""
import copy
import pytest
from pydantic import ValidationError
from packages.domain.workspace_desktop import DesktopBindInput,DesktopUpdateInput,physical_layout,WorkspaceDesktop
from packages.domain.store import Conflict,InvalidInput
from test_workspace_desktop import bind_body,update_body


def raw_body(mode='DESKTOP',**patch):
    value=bind_body()
    value.pop('shape_rectangles_dip');value.pop('workarea_insets_dip')
    value.update(electron_scale_factor=2.24,content_size_dip={'width':1286,'height':822},
        css_bounds={'x':70.5,'y':57.33,'width':990.12,'height':770.444},popover_bounds=[])
    value['screen']['scale_factor']=2.24
    if mode!='DESKTOP':
        value={k:v for k,v in value.items() if k not in ['main_window','helper_windows','screen']}
        value.update(surface_ref={'id':'f'*64,'revision':1,'sha256':'a'*64},expected_input_epoch=1,presentation=mode)
    return value|patch


def metadata(width=2880,height=1840):
    return {'window_bounds':{'x':-2880,'y':0,'width':width,'height':height},'dpi':192,
            'client_bounds_window_physical':{'x':0,'y':0,'width':width,'height':height}}


def test_raw_payload_does_not_need_legacy_shape_or_integer_dip_insets():
    value=DesktopBindInput.model_validate(raw_body()).model_dump()
    assert value['shape_rectangles_dip'] is None and value['workarea_insets_dip'] is None
    assert value['electron_scale_factor']==2.24 and value['content_size_dip']=={'width':1286,'height':822}
    assert DesktopUpdateInput.model_validate(raw_body('FULL_WORKSPACE')).presentation=='FULL_WORKSPACE'


@pytest.mark.parametrize('patch',[
    {'electron_scale_factor':True},{'electron_scale_factor':float('nan')},{'electron_scale_factor':0},
    {'content_size_dip':None},{'content_size_dip':{'width':0,'height':822}},
    {'content_size_dip':{'width':True,'height':822}},
])
def test_main_geometry_strict_validation_rejects_missing_or_coerced_fields(patch):
    with pytest.raises(ValidationError):DesktopBindInput.model_validate(raw_body(**patch))


def test_actual_dpi192_electron224_counter_maps_raw_floats_then_clips_native_client():
    body=DesktopBindInput.model_validate(raw_body()).model_dump();mapped=physical_layout(body,metadata())
    assert mapped['native_dpi']==192 and mapped['electron_scale_factor']==2.24
    assert mapped['center_physical']=={'x':-2723,'y':128,'width':2219,'height':1712}
    assert mapped['thickness_physical']=={'LEFT':157,'RIGHT':504,'TOP':128,'BOTTOM':0}
    assert mapped['requested_css_bounds']==body['css_bounds']
    assert mapped['actual_visible_css_viewport']['height']==pytest.approx(1840/2.24)
    assert mapped['visible_css_bounds']['y']+mapped['visible_css_bounds']['height']==pytest.approx(1840/2.24)
    # The stale Electron content measurement is retained, never forced to be
    # equal to actual native physical client pixels or Windows DPI/96.
    assert mapped['content_size_dip']['height']==822
    assert mapped['mapping_authority']=='TRUSTED_ELECTRON_SCALE_AND_ACTUAL_NATIVE_CLIENT_CLIP'
    assert mapped['center_physical']['x']!=-2739  # wrong WindowsDPI=2 scale


def test_odd_actual_pixel_size_does_not_force_integer_dip_times_native_dpi_equality():
    body=DesktopBindInput.model_validate(raw_body()).model_dump();mapped=physical_layout(body,metadata(2881,1842))
    assert mapped['window_bounds']['width']==2881
    assert mapped['thickness_physical']['RIGHT']==505
    assert mapped['center_physical']['height']==1714


def test_zoom_and_electron_scale_are_independent_and_global_origins_remain_native():
    b=DesktopBindInput.model_validate(raw_body(zoom_factor=.5)).model_dump();mapped=physical_layout(b,metadata())
    assert mapped['center_physical']['x']==-2802
    assert mapped['center_physical']['y']==64
    assert mapped['scale']==2.24 and mapped['zoom_factor']==.5
    assert mapped['center_physical']['x']!=-6366


def test_desktop_edge_controls_union_like_modeless_without_covering_other_center_pixels():
    box={'x':70.5,'y':57.33,'width':990.12,'height':770.444}
    popup={'x':40,'y':40,'width':50,'height':50}
    body=DesktopBindInput.model_validate(raw_body(css_bounds=box,popover_bounds=[popup])).model_dump()
    mapped=physical_layout(body,metadata())
    assert mapped['rectangles_physical'][-1]=={'x':157,'y':128,'width':45,'height':74}
    assert len(mapped['rectangles_physical'])==4  # top,left,right + clipped popup; no bottom
    modeless=DesktopUpdateInput.model_validate(raw_body('MODELESS_POPOVER',popover_bounds=[popup])).model_dump()
    assert physical_layout(modeless,metadata())['rectangles_physical']==mapped['rectangles_physical']


@pytest.mark.parametrize('mode',['FULL_WORKSPACE','DOCUMENT','APPLICATION'])
def test_full_and_native_document_modes_use_whole_actual_window_physical_region(mode):
    value=DesktopUpdateInput.model_validate(raw_body(mode)).model_dump()
    assert physical_layout(value,metadata())['rectangles_physical']==[{'x':0,'y':0,'width':2880,'height':1840}]


def test_no_visible_native_client_intersection_or_missing_native_client_is_not_success():
    body=DesktopBindInput.model_validate(raw_body(css_bounds={'x':5000,'y':0,'width':100,'height':100})).model_dump()
    with pytest.raises(InvalidInput,match='no visible intersection'):physical_layout(body,metadata())
    body=DesktopBindInput.model_validate(raw_body()).model_dump();m=metadata();m.pop('client_bounds_window_physical')
    with pytest.raises(Conflict,match='client viewport'):physical_layout(body,m)


def test_actual_linux_with_raw_new_geometry_stays_windows_only_and_creates_no_native_fake_receipt(tmp_path):
    value=WorkspaceDesktop(tmp_path,token='a'*64,owner_pid=123,platform='posix')
    with pytest.raises(Conflict,match='WINDOWS_NATIVE_REQUIRED'):value.bind(raw_body())
    assert value.get()['status']=='WINDOWS_ONLY' and not value.get()['region_verified']
    assert not list(tmp_path.iterdir())

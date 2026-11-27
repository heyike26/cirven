"""Real adapter method with failure-capable stock Win32 call seams, no input."""
import ctypes as ct
from types import SimpleNamespace
import pytest

from packages.adapters.desktop_current_context import Win32CurrentWindowSource, CurrentContextError, _LASTINPUTINFO, _CURSORPOINT


def source(*, ticks=(90, 90), foreground=(100, 100), window_changed=False):
    values=iter(ticks);windows=iter(foreground);closed=[];calls=[];window_reads=0
    def read(pointer):
        info=ct.cast(pointer, ct.POINTER(_LASTINPUTINFO)).contents
        assert info.cbSize==8
        info.dwTime=next(values);calls.append('input');return 1
    def bind(_dll,name,_result,_args):
        assert name=='GetLastInputInfo';return read
    def window(_hwnd):
        nonlocal window_reads
        window_reads+=1
        return {'pid':12,'class':'OwnOrForeign','thread':3+(window_changed and window_reads==2)}
    def require(ok, code):
        if not ok:raise CurrentContextError(code)
    api=SimpleNamespace(_bind=bind,user=object(),window=window,require=require,
        kernel=SimpleNamespace(OpenProcess=lambda *_:45,CloseHandle=lambda handle:closed.append(handle)),
        process=lambda _:{'pid':12,'exit_code':259,'creation':99,'session':1})
    value=object.__new__(Win32CurrentWindowSource)
    value.api=api;value.foreground=lambda:next(windows);value.ancestor=lambda hwnd,_:hwnd
    value.excluded_pid_reader=lambda:[12]
    return value,closed,calls


def test_focus_state_includes_own_foreground_but_page_observation_still_excludes_it():
    value,closed,calls=source()
    assert value._inspect(100)=={'status':'EXCLUDED_EEAS_WINDOW'}
    result=value.activation_state()
    assert set(result)=={'status','foreground_identity_sha256','last_input_tick'}
    assert result['status']=='OBSERVED' and len(result['foreground_identity_sha256'])==64
    assert result['last_input_tick']==90 and closed==[45] and calls==['input','input']
    assert ct.sizeof(_LASTINPUTINFO)==8


@pytest.mark.parametrize('settings',[{'ticks':(90,91)},{'ticks':(0xffffffff,0)},
    {'foreground':(100,101)},{'window_changed':True}])
def test_input_or_window_change_fails_closed_and_closes_only_own_process_handle(settings):
    value,closed,_=source(**settings)
    with pytest.raises(CurrentContextError,match='CHANGED_DURING_SAMPLE'):value.activation_state()
    assert closed==[45]


def trigger_source(frames):
    value,closed,_=source()
    index=0;now=0.0
    frame=lambda:frames[min(index,len(frames)-1)]
    def read(pointer):
        ct.cast(pointer,ct.POINTER(_LASTINPUTINFO)).contents.dwTime=frame()['tick']
        return 1
    def position(pointer):
        point=ct.cast(pointer,ct.POINTER(_CURSORPOINT)).contents
        point.x,point.y=frame().get('cursor',(10,20))
        return 1
    def key(code):
        return (0x8000 if code in frame()['keys'] else 0)|1
    def bind(_dll,name,result,args):
        if name=='GetAsyncKeyState':assert result==ct.c_int16 and args==[ct.c_int32]
        return {'GetLastInputInfo':read,'GetAsyncKeyState':key,'GetCursorPos':position}[name]
    def advance(duration):
        nonlocal index,now
        index+=1;now+=duration
    value.api._bind=bind
    value.foreground=lambda:frame().get('hwnd',100)
    native_settle=value._settle_activation_trigger
    value._settle_activation_trigger=lambda *args:native_settle(*args,clock=lambda:now,sleep=advance)
    return value,closed


def test_only_invoking_keys_may_release_before_initial_baseline_then_final_sample_is_strict():
    value,closed=trigger_source([
        {'keys':{0x11,0x10,0x20},'tick':0xffffffff},
        {'keys':{0x11,0x10},'tick':0},
        {'keys':{0x11},'tick':1},
        {'keys':set(),'tick':2},
    ])
    assert value.activation_state(settle_trigger=True)['last_input_tick']==2
    assert closed==[45] and ct.sizeof(_CURSORPOINT)==8


@pytest.mark.parametrize('next_frame',[
    {'keys':{0x11,0x10,0x20},'tick':91},
    {'keys':{0x11,0x10},'tick':91,'cursor':(11,20)},
    {'keys':{0x11,0x10},'tick':91,'hwnd':101},
    {'keys':{0x11,0x10,0x01},'tick':91},
])
def test_settling_does_not_absorb_new_input_mouse_or_window_changes(next_frame):
    value,closed=trigger_source([{'keys':{0x11,0x10,0x20},'tick':90},next_frame])
    with pytest.raises(CurrentContextError,match='CHANGED_DURING_TRIGGER'):
        value.activation_state(settle_trigger=True)
    assert closed==[45]


def test_repress_or_unreleased_trigger_has_bounded_failure_and_no_process_handle_leak():
    for frames in [
        [{'keys':{0x11,0x10,0x20},'tick':90},{'keys':{0x11,0x10},'tick':91},
            {'keys':{0x11,0x10,0x20},'tick':92}],
        [{'keys':{0x11,0x10,0x20},'tick':90}],
    ]:
        value,closed=trigger_source(frames)
        with pytest.raises(CurrentContextError):value.activation_state(settle_trigger=True)
        assert closed==[45]

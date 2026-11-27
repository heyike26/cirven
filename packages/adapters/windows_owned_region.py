"""Apply stock GDI regions only to exact private-marked own main windows.

Electron and Windows may report different scales. All mutations here use actual
physical coordinates and existing PID/creation/lifetime-marker authority. No GUI
window is created, and no user application is moved, disabled or terminated.
"""
import ctypes as ct
import time
from packages.adapters.windows_desktop_region import DesktopRegionError,_RECT,_POINT,rectangles,surface_reference


class WindowsOwnedRegion:
    def __init__(self,*,surface_authority,native):
        self.authority,self.native=surface_authority,native
        user=native.user
        for name,ret,args in [
            ('SetWindowRgn',ct.c_int32,[ct.c_void_p,ct.c_void_p,ct.c_int32]),
            ('GetClientRect',ct.c_int32,[ct.c_void_p,ct.POINTER(_RECT)]),
            ('ClientToScreen',ct.c_int32,[ct.c_void_p,ct.POINTER(_POINT)]),
            ('SetWindowPos',ct.c_int32,[ct.c_void_p,ct.c_void_p,ct.c_int32,ct.c_int32,ct.c_int32,ct.c_int32,ct.c_uint32]),
        ]:
            function=getattr(user,name);function.restype=ret;function.argtypes=args

    def _marker(self,ref):
        ref=surface_reference(ref);marker=self.authority(ref)
        self.native.validate(marker);return marker

    def metadata(self,ref):
        marker=self._marker(ref);before=self.native.validate(marker)
        client,origin=_RECT(),_POINT(0,0)
        with self.native.physical_context(marker):
            if not self.native.user.GetClientRect(marker['hwnd'],ct.byref(client)) or not self.native.user.ClientToScreen(marker['hwnd'],ct.byref(origin)):
                raise DesktopRegionError('OWN_CLIENT_PHYSICAL_GEOMETRY_UNAVAILABLE')
        after=self.native.validate(marker)
        if before!=after:raise DesktopRegionError('OWN_WINDOW_CHANGED_DURING_CLIENT_QUERY')
        window=after['window_bounds']
        bounds={'x':origin.x-window['x'],'y':origin.y-window['y'],
                'width':client.right-client.left,'height':client.bottom-client.top}
        if (min(bounds['x'],bounds['y'])<0 or min(bounds['width'],bounds['height'])<1 or
                bounds['x']+bounds['width']>window['width'] or bounds['y']+bounds['height']>window['height']):
            raise DesktopRegionError('OWN_CLIENT_EXCEEDS_ACTUAL_WINDOW')
        return {**after,'client_bounds_window_physical':bounds}

    def apply(self,ref,physical_rectangles):
        marker=self._marker(ref);rects=rectangles(physical_rectangles)
        before=self.native.validate(marker)
        union=self.native._require(self.native.gdi.CreateRectRgn(0,0,0,0),'OWN_REGION_ALLOC_FAILED')
        transferred=False
        try:
            for rect in rects:
                if rect['x']+rect['width']>before['window_bounds']['width'] or rect['y']+rect['height']>before['window_bounds']['height']:
                    raise DesktopRegionError('OWN_REGION_EXCEEDS_PHYSICAL_WINDOW')
                part=self.native._require(self.native.gdi.CreateRectRgn(rect['x'],rect['y'],rect['x']+rect['width'],rect['y']+rect['height']),'OWN_REGION_ALLOC_FAILED')
                try:self.native._require(self.native.gdi.CombineRgn(union,union,part,2),'OWN_REGION_UNION_FAILED')
                finally:self.native.gdi.DeleteObject(part)
            with self.native.physical_context(marker):
                self.native._require(self.native.user.SetWindowRgn(marker['hwnd'],union,True),'OWN_NATIVE_SET_REGION_FAILED')
                transferred=True
            # SetWindowRgn transfers the HRGN to Windows on success. Do not use
            # or delete that handle again. Verification obtains a fresh copy.
            transferred=True
            if self.native.validate(marker)!=before:raise DesktopRegionError('OWN_WINDOW_CHANGED_DURING_REGION_APPLY')
            verified=self.native.equal_region(marker,rects)
            if verified.get('matches') is not True:raise DesktopRegionError('OWN_APPLIED_REGION_NOT_EQUAL')
            return {'status':'OWN_NATIVE_REGION_APPLIED','rectangles_physical':rects,'region_verified':True,
                    'region_handle_transferred_to_windows':True,'user_windows_mutated':False}
        finally:
            if not transferred:self.native.gdi.DeleteObject(union)

    def fail_closed(self,ref):
        metadata=self.metadata(ref);w=metadata['window_bounds']
        return self.apply(ref,[{'x':0,'y':0,'width':w['width'],'height':w['height']}])

    def restore_container(self,ref,original_metadata):
        """Restore only our marked shell after the AppBar workarea broadcast.

        The target comes from the native observation before reservation, never
        from renderer geometry. Windows can resize even a normal top-level
        window when rcWork changes; WM_SIZE also invalidates its custom region.
        The caller must therefore apply and verify the region after this call.
        """
        from packages.adapters.windows_appbar_lease import _rect
        if (not isinstance(original_metadata,dict) or set(original_metadata)!=
                {'window_bounds','client_bounds_window_physical','dpi'}):
            raise DesktopRegionError('OWN_ORIGINAL_CONTAINER_OBSERVATION_REQUIRED')
        target=_rect(original_metadata.get('window_bounds'))
        client=_rect(original_metadata.get('client_bounds_window_physical'))
        dpi=original_metadata.get('dpi')
        if (type(dpi) is not int or not 0<dpi<1<<32 or client!=
                {'x':0,'y':0,'width':target['width'],'height':target['height']}):
            raise DesktopRegionError('OWN_ORIGINAL_FRAMELESS_CLIENT_REQUIRED')
        marker=self._marker(ref);before=self.metadata(ref)
        if before['dpi']!=dpi:
            raise DesktopRegionError('OWN_CONTAINER_DPI_CHANGED_REBIND_REQUIRED')
        observations=[]
        # Shell delivers multiple workarea notifications while the four own
        # AppBars settle. Each attempt checks the same marked lifetime; stop
        # after a bounded interval rather than accepting a transient match.
        for attempt in range(6):
            actual=self.metadata(ref)
            if actual['dpi']!=dpi:
                raise DesktopRegionError('OWN_CONTAINER_DPI_CHANGED_REBIND_REQUIRED')
            if actual!=original_metadata:
                with self.native.physical_context(marker):
                    self.native._require(self.native.user.SetWindowPos(marker['hwnd'],None,
                        target['x'],target['y'],target['width'],target['height'],
                        0x0004|0x0010|0x0200),'OWN_CONTAINER_NATIVE_RESTORE_FAILED')
            time.sleep(.04)
            actual=self.metadata(ref);observations.append(actual)
            if actual==original_metadata:
                time.sleep(.04)
                settled=self.metadata(ref);observations.append(settled)
                if settled==original_metadata:break
        else:
            raise DesktopRegionError('OWN_ORIGINAL_CONTAINER_NOT_RESTORED')
        return {'status':'OWN_ORIGINAL_CONTAINER_RESTORED','before':before,
                'actual':settled,'settlement_observations':observations,
                'user_windows_mutated':False}

    def place_helpers(self,markers,accepted_bounds):
        result={}
        for edge,bounds in accepted_bounds.items():
            marker=markers[edge];before=self.native.validate(marker)
            if any(type(v) is not int for v in bounds.values()) or min(bounds['width'],bounds['height'])<1:
                raise DesktopRegionError('OWN_HELPER_PHYSICAL_BOUNDS_REQUIRED')
            observations=[]
            for attempt in range(6):
                actual=self.native.validate(marker)
                if actual['dpi']!=before['dpi']:raise DesktopRegionError('OWN_HELPER_DPI_CHANGED')
                if actual['window_bounds']!=bounds:
                    with self.native.physical_context(marker):
                        self.native._require(self.native.user.SetWindowPos(marker['hwnd'],None,bounds['x'],bounds['y'],
                            bounds['width'],bounds['height'],0x0004|0x0010|0x0200),'OWN_HELPER_NATIVE_POSITION_FAILED')
                time.sleep(.04)
                actual=self.native.validate(marker);observations.append(actual)
                if actual['window_bounds']==bounds:
                    time.sleep(.04)
                    settled=self.native.validate(marker);observations.append(settled)
                    if settled==actual:break
            else:raise DesktopRegionError('OWN_HELPER_NATIVE_POSITION_NOT_VERIFIED')
            result[edge]={'physical_bounds':dict(bounds),'verified':True,'settlement_observations':observations}
        return {'helper_bounds_applied_native':True,'helpers_verified':True,'helpers':result}


    def verify_helpers(self,markers,accepted_bounds):
        proofs={}
        for edge,bounds in accepted_bounds.items():
            actual=self.native.validate(markers[edge])
            if actual['window_bounds']!=bounds:raise DesktopRegionError('OWN_HELPER_BOUNDARY_CHANGED')
            proofs[edge]={'physical_bounds':dict(bounds),'verified':True}
        return {'helper_bounds_applied_native':True,'helpers_verified':True,'helpers':proofs}

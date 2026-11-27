"""Versioned semantic fingerprint for a selected public Quick work input.

The canonical operation's historical body_sha256 remains authoritative and
unchanged. This sideband compares Python model defaults and JSON number values
with the frozen host input, without depending on float JSON spelling.
"""
import math
import struct

from packages.domain.tasks import digest


def original_work_input_fingerprint(request):
    if request.get('original_work_ref') is None or request.get('file_input') is not None:
        return None
    value=dict(request)
    for key in ('file_input','context_selection_id','original_work_ref'):
        if value.get(key) is None:value.pop(key,None)
    value.setdefault('agent','build')
    value.setdefault('original_source',None)
    value['input_attachments']=[{**item,'regions':item.get('regions',[]),
        'image_sha256':item.get('image_sha256'),'user_note':item.get('user_note')}
        for item in value.get('input_attachments',[])]

    def tagged(item):
        if item is None:return ['null']
        if isinstance(item,bool):return ['boolean',item]
        if isinstance(item,(int,float)):
            if isinstance(item,int) and abs(item)>9007199254740991:raise ValueError('Unsafe JSON integer')
            number=float(item)
            if not math.isfinite(number):raise ValueError('Nonfinite JSON number')
            # JSON.stringify sends negative zero as zero.
            if number==0:number=0.0
            return ['number',struct.pack('>d',number).hex()]
        if isinstance(item,str):return ['string',item]
        if isinstance(item,list):return ['array',[tagged(v) for v in item]]
        if isinstance(item,dict):return ['object',[[key,tagged(item[key])] for key in sorted(item)]]
        raise ValueError('Unsupported canonical input')
    try:return digest(['cirven.original-work-input-fingerprint.v1',tagged(value)])
    except (ValueError,TypeError,OverflowError,UnicodeError):return None

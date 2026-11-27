"""One bounded original native GET under the existing task resource authority.

Counts returned decompressed body bytes, not socket traffic, OS/SDK tool I/O or
commercial usage. No native mutation, fallback transport or automatic retry.
"""
import hashlib
from packages.domain.context_read_budget import context_read_scope,ContextReadLimit
from packages.domain.task_resource_budget import current_task_resource_budget
from packages.domain.native_capabilities import _json
from packages.domain.store import Conflict

MAX_NATIVE_SOURCE_BYTES=4*1024*1024


async def read_native_json(context,path,*,expected,params,options):
    budget=current_task_resource_budget()
    if budget is None or budget.authority['project_id']!=context.get('project_id'):
        raise Conflict('Original native source read resource project differs')
    status=None
    complete=None
    async def read(limit):
        nonlocal status,complete
        chunks=[];count=0
        async with context['client'].stream('GET',path,json=None,params=params,**options) as response:
            status=response.status_code
            async for chunk in response.aiter_bytes(chunk_size=max(1,min(65536,limit+1))):
                if not isinstance(chunk,bytes) or count+len(chunk)>limit:
                    raise ContextReadLimit('Original native source read exceeds the admitted byte limit')
                count+=len(chunk);chunks.append(chunk)
        complete=b''.join(chunks)
        return complete
    try:
        with context_read_scope() as source:
            raw=await source.guarded_async(read,MAX_NATIVE_SOURCE_BYTES)
        if status!=expected:
            raise Conflict('Original API response unavailable: GET '+path+' '+str(status))
        value=None if not raw else _json(raw)
    except BaseException:
        context['last_http_error']={'method':'GET','path':path,'http_status':status,
            'body_sha256':None if complete is None else hashlib.sha256(complete).hexdigest(),
            'body_complete':complete is not None,'task_resource_read':True}
        raise
    context.pop('last_http_error',None)
    return value

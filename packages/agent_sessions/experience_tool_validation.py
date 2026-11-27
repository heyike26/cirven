"""Explicit fixed-case MCP validation with the installed official MCP client.

One private process/connection per fixed case, no model loop or global capability
pointer changes. Working-directory separation is NOT an OS/network sandbox.
Unknown/cancelled original attempts are inspected, never automatically replayed.
"""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import os
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client, DEFAULT_INHERITED_ENV_VARS
from mcp.client.streamable_http import streamable_http_client
from mcp.types import PaginatedRequestParams, CallToolResult
from pydantic import Field

from packages.domain.questions import RequestModel, Id
from packages.domain.native_capabilities import _command_files
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, digest, now
from packages.domain.context_read_budget import context_read_scope
from .models import ConfigurationRef
from .experience_candidates import ExperienceCandidates

MAX_RESULT_BYTES=65536
MAX_VALIDATION_BYTES=1024*1024
MAX_VALIDATIONS=16


class ExperienceToolValidate(RequestModel):
    operation_id: Id
    candidate_ref: ConfigurationRef
    review_ref: ConfigurationRef
    validation_confirmed: bool = Field(strict=True)
    allowed_effects: list[str] = Field(max_length=1)
    process_boundary_acknowledged: bool = Field(strict=True)


class ExperienceToolValidation:
    def __init__(self,candidates:ExperienceCandidates):
        self.candidates=candidates
        self.sessions=candidates.sessions

    def _path(self,pid,sid,operation,*parts):
        return self.candidates._path(pid,sid,'validation',digest(operation),*parts)

    def inspect(self,pid,sid,operation,request_sha256):
        intent=self.candidates.operation(pid,sid,operation,request_sha256)
        if intent['kind']!='VALIDATION':raise Conflict('This experience operation is not a validation')
        path=self._path(pid,sid,operation,'result.json')
        if not path.exists():
            return {'kind':'EXPERIENCE_TOOL_VALIDATION','project_id':pid,'session_id':sid,'operation_id':operation,
                'request_sha256':request_sha256,'candidate_ref':intent['request']['candidate_ref'],
                'review_ref':intent['request']['review_ref'],'status':'UNKNOWN_OR_IN_PROGRESS',
                'automatic_replay':False,'qualified':False,'permission_granted':False,'enabled':False}
        result=self.candidates._read(path,MAX_VALIDATION_BYTES)
        if (result.get('request_sha256')!=request_sha256 or result.get('operation_id')!=operation
                or result.get('project_id')!=pid or result.get('session_id')!=sid
                or result.get('candidate_ref')!=intent['request']['candidate_ref']
                or result.get('review_ref')!=intent['request']['review_ref']):
            raise Conflict('Original validation result scope changed')
        return deepcopy(result)

    @asynccontextmanager
    async def _connection(self,configuration,workspace):
        if configuration['type']=='local':
            _command_files(configuration)
            workspace.mkdir(parents=True,exist_ok=False)
            home=workspace/'home';home.mkdir()
            env={k:'' for k in DEFAULT_INHERITED_ENV_VARS}
            env.update({k:os.environ[k] for k in ['SystemRoot','WINDIR','SYSTEMROOT','SYSTEMDRIVE'] if k in os.environ})
            env.update({k:str(home) for k in ['HOME','USERPROFILE','APPDATA','LOCALAPPDATA','XDG_CONFIG_HOME','XDG_DATA_HOME','TMP','TEMP','TMPDIR']})
            env.update(LOGNAME='cirven-experience-test',USER='cirven-experience-test')
            command=configuration['command']
            parameters=StdioServerParameters(command=command[0],args=command[1:],env=env,cwd=str(workspace))
            with open(os.devnull,'w') as errors:
                async with stdio_client(parameters,errlog=errors) as streams:
                    yield streams[0],streams[1]
        else:
            # An unauthenticated HTTPS declaration; never inherit API accounts.
            async with streamable_http_client(configuration['url'],max_sse_event_size=MAX_RESULT_BYTES) as streams:
                yield streams[0],streams[1]

    async def _case(self,pid,sid,operation,configuration,case,remaining,progress):
        workspace=self._path(pid,sid,operation,'cases',case['id'])
        row={'id':case['id'],'label':case['label'],'purpose':case['purpose'],'tool_name':case['tool_name'],
            'input_sha256':digest(case['input']),'expected_result_sha256':digest(case['expected_result']),
            'expected_is_error':case['expected_result']['isError'],'status':'UNKNOWN','call_reserved':False,
            'process_boundary':'PRIVATE_WORKING_DIRECTORY_NOT_OS_OR_NETWORK_SANDBOX'}
        progress.append(row)
        self.sessions._write(self._path(pid,sid,operation,'progress.json'),{'cases':progress},replace=True)
        async with self._connection(configuration,workspace) as streams:
            async with ClientSession(*streams,read_timeout_seconds=min(remaining,configuration['timeout']/1000)) as client:
                initialized=await client.initialize()
                tools={};listed=await client.list_tools()
                for _ in range(8):
                    for tool in listed.tools:
                        if (tool.name in tools or len(tools)>=256 or len(canonical(tool.model_dump(mode='json',exclude_none=True)).encode())>MAX_RESULT_BYTES):
                            raise Conflict('Experience validation tool directory exceeds its bounded unique schema')
                        tools[tool.name]=tool
                    if not listed.next_cursor:break
                    listed=await client.list_tools(params=PaginatedRequestParams(cursor=listed.next_cursor))
                else:raise Conflict('Experience validation tool listing pagination exceeded')
                if case['tool_name'] not in tools:raise Conflict('Reviewed original Tool is absent from this server')
                row.update(protocol_version=initialized.protocol_version,
                    input_schema_sha256=digest(tools[case['tool_name']].input_schema),call_reserved=True)
                self.sessions._write(self._path(pid,sid,operation,'progress.json'),{'cases':progress},replace=True)
                # One explicit original call. No sampling, elicitation, task/claim
                # continuation or model callback is provided to ClientSession.
                result=await client.call_tool(case['tool_name'],case['input'],read_timeout_seconds=remaining)
                if not isinstance(result,CallToolResult):raise Conflict('Tool returned an unsupported continuation')
                value=result.model_dump(mode='json',by_alias=True,exclude_none=True)
                if len(canonical(value).encode())>MAX_RESULT_BYTES:raise Conflict('Tool output exceeds 64KiB after protocol parsing')
                material={'content':value.get('content',[]),'structuredContent':value.get('structuredContent'),'isError':bool(result.is_error)}
                row.update(result=material,result_sha256=digest(material),is_error=bool(result.is_error))
                row['status']='PASS' if row['result_sha256']==digest(case['expected_result']) and row['is_error']==case['expected_result']['isError'] else 'FAIL'
                self.sessions._write(self._path(pid,sid,operation,'cases',case['id']+'.json'),row)
        if configuration['type']=='local':_command_files(configuration)
        return row

    async def validate(self,pid,sid,body):
        body=ExperienceToolValidate.model_validate(body);request=body.model_dump();request_hash=digest(request)
        if not body.validation_confirmed or not body.process_boundary_acknowledged:
            raise InvalidInput('Explicit test execution and non-sandbox boundary acknowledgement are required')
        c=self.candidates
        with context_read_scope(),c._guard(pid,sid):
            index=c._index(pid,sid);old=c._record(pid,sid,body.operation_id,'VALIDATION',request_hash,index)
            if old is not None:
                # Interrupted intent may be reindexed, but its process/call is
                # never started again by a repeated positive request.
                c._append(pid,sid,index,old)
                return self.inspect(pid,sid,body.operation_id,request_hash)
            c.source_policy().require(pid,sid,body.candidate_ref)
            candidate=c.candidate(pid,sid,body.candidate_ref)
            review=c.saved_record(pid,sid,body.review_ref,'REVIEW')
            if (candidate['request']['proposal']['kind']!='MCP_TOOL'
                    or review['candidate_ref']!=body.candidate_ref.model_dump()):
                raise Conflict('Tool validation requires an independently reviewed exact Tool candidate')
            plan=review['request'];configuration=deepcopy(candidate['configuration'])
            effect='PROCESS_START' if configuration['type']=='local' else 'REMOTE_MCP_CONNECT'
            if body.allowed_effects!=[effect] or plan['allowed_effects']!=[effect] or plan['max_model_calls']!=0:
                raise Conflict('Validation effects differ from the fixed reviewed plan')
            previous=[r for r in index['records'] if r['kind']=='VALIDATION']
            count=sum(c._record(pid,sid,r['id'],'VALIDATION',r['request_sha256'],index)['request']['candidate_ref']==body.candidate_ref.model_dump() for r in previous)
            if count>=MAX_VALIDATIONS:raise Conflict('Validation history is full; no earlier attempt was removed')
            intent={'schema':'eeas.task-experience-validation-intent.v1','kind':'VALIDATION','project_id':pid,
                'session_id':sid,'operation_id':body.operation_id,'request':request,'request_sha256':request_hash,
                'created_at':now(),'dispatch_status':'RESERVED_NOT_REPLAYABLE','automatic_replay':False}
            c._append(pid,sid,index,intent)
        progress=[];started=time.monotonic();status='FAIL';error=None
        try:
            for case in plan['cases']:
                c.source_policy().require(pid,sid,body.candidate_ref)
                remaining=plan['timeout_seconds']-(time.monotonic()-started)
                if remaining<=0:raise TimeoutError('Shared validation deadline exhausted')
                await asyncio.wait_for(self._case(pid,sid,body.operation_id,configuration,case,remaining,progress),timeout=remaining)
            status='PASS' if all(row['status']=='PASS' for row in progress) and len(progress)==len(plan['cases']) else 'FAIL'
        except asyncio.CancelledError:
            status='CANCELLED';error='CancelledError'
            self._finish(pid,sid,body,request_hash,progress,status,error,started)
            raise
        except Exception as exc:
            error=type(exc).__name__;status='UNKNOWN' if any(row['call_reserved'] and row['status']=='UNKNOWN' for row in progress) else 'FAIL'
        return self._finish(pid,sid,body,request_hash,progress,status,error,started)

    def _finish(self,pid,sid,body,request_hash,progress,status,error,started):
        if self.candidates.source_policy().status(pid,sid,body.candidate_ref)['availability']!='ACTIVE':status='SOURCE_BLOCKED'
        value={'kind':'EXPERIENCE_TOOL_VALIDATION','version':1,'project_id':pid,'session_id':sid,
            'operation_id':body.operation_id,'request_sha256':request_hash,'candidate_ref':body.candidate_ref.model_dump(),
            'review_ref':body.review_ref.model_dump(),'status':status,'cases':deepcopy(progress),
            'error_type':error,'elapsed_seconds':round(time.monotonic()-started,3),'recorded_at':now(),
            'qualification_scope':'EXPLICIT_FIXED_MCP_CASES_ONLY_NOT_ORIGINAL_SESSION_LOAD_OR_GENERAL_SAFETY',
            'qualified':status=='PASS','loaded_in_original_session':False,'os_network_sandbox':'NOT_QUALIFIED',
            'result_size_limit_scope':'CHECKED_AFTER_OFFICIAL_PROTOCOL_PARSE','automatic_replay':False,
            'model_calls':0,'enabled':False,'permission_granted':False,'source_files_written_by_controller':0,
            'external_effects':'EXACT_REVIEWED_TOOL_MAY_ACCESS_FILES_OR_NETWORK_OUTSIDE_PRIVATE_WORKING_DIRECTORY',
            'cleanup':'OWNED_OFFICIAL_MCP_CONTEXT_EXITED_OR_UNWOUND'}
        if len(canonical(value).encode())>MAX_VALIDATION_BYTES:raise Conflict('Original validation receipt exceeds 1MiB; inspect preserved progress')
        with self.candidates._guard(pid,sid):
            path=self._path(pid,sid,body.operation_id,'result.json')
            if path.exists():raise Conflict('Original validation already has a terminal receipt')
            self.sessions._write(path,value)
        return value

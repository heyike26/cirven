"""Bounded JUnit observations from exact original-command after snapshots.

References: pytest junitxml documentation and Python 3.10 XML API/security docs.
Report assertions, integer exits and source snapshots do not certify a target.
"""
import hashlib
import re
from decimal import Decimal, InvalidOperation
from typing import Literal
import xml.etree.ElementTree as ET

from pydantic import Field
from packages.agent_sessions.models import SessionRef, WorkspaceRef
from packages.domain.local_workspaces import relative
from packages.domain.questions import RequestModel, Hash
from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest

MAX_REPORT_BYTES=262144
MAX_CASES=2000
MAX_NODES=8192
MAX_DEPTH=16
MAX_SUITES=128


class CommandTestReportInput(RequestModel):
    session_ref: SessionRef
    generation: int = Field(ge=1,strict=True)
    workspace_ref: WorkspaceRef
    command_ref: SessionRef
    command_generation: int = Field(ge=1,strict=True)
    call_id: str = Field(min_length=1,max_length=192,pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')
    report_path: str = Field(min_length=1,max_length=4096)
    report_sha256: Hash
    offset: int = Field(default=0,ge=0,le=MAX_CASES,strict=True)
    limit: int = Field(default=20,ge=1,le=20,strict=True)
    outcome: Literal['ALL','PROBLEMS','SKIPPED','PASSED'] = 'ALL'


class _Tree(ET.TreeBuilder):
    def __init__(self):
        super().__init__();self.stack=[];self.nodes=0;self.suites=0;self.cases=0

    def start(self,tag,attrs):
        self.nodes+=1
        if self.nodes>MAX_NODES or len(self.stack)>=MAX_DEPTH or len(attrs)>64:
            raise Conflict('Test report structure exceeds its read bound')
        parent=self.stack[-1] if self.stack else None
        allowed={None:{'testsuites','testsuite'},'testsuites':{'testsuite','properties'},
            'testsuite':{'testsuite','testcase','properties','system-out','system-err'},
            'testcase':{'failure','error','skipped','properties','system-out','system-err'},
            'properties':{'property'}}
        if tag not in allowed.get(parent,set()):
            raise Conflict('This XML is outside the supported JUnit report structure')
        if tag=='testsuite':
            self.suites+=1
            if self.suites>MAX_SUITES:raise Conflict('Test report has too many suites')
        if tag=='testcase':
            self.cases+=1
            if self.cases>MAX_CASES:raise Conflict('Test report has too many cases')
        self.stack.append(tag);return super().start(tag,attrs)

    def end(self,tag):
        value=super().end(tag);self.stack.pop();return value


def _text(value,limit):
    value=''.join(c for c in value if c in '\n\t' or ord(c)>=32)
    return value[:limit],len(value)>limit


def parse_junit(raw):
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_REPORT_BYTES or b'\0' in raw:
        raise Conflict('An exact bounded UTF-8 JUnit report is required')
    try:text=raw.decode('utf-8-sig',errors='strict')
    except UnicodeError:raise Conflict('The saved test report is not UTF-8') from None
    # Restrict the accepted format before invoking Expat; no DTD/entity loader,
    # XInclude expansion, URI fetch, stylesheet evaluation or file resolution.
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b',text,re.I):
        raise Conflict('DTD and custom entities are not supported in test reports')
    encoding=re.match(r'\s*<\?xml\s+[^?]*encoding\s*=\s*[\'\"]([^\'\"]+)',text,re.I)
    if encoding and encoding.group(1).casefold() not in {'utf-8','utf8'}:
        raise Conflict('The saved report declares a different encoding')
    try:root=ET.fromstring(text,parser=ET.XMLParser(target=_Tree()))
    except (ET.ParseError,ValueError,RecursionError):
        raise Conflict('The original JUnit report is malformed; no result was inferred') from None
    cases=[];integrity=set();metadata=set();durations=[]
    case_suites={id(case):suite.attrib.get('name','') for suite in root.iter('testsuite') for case in suite if case.tag=='testcase'}
    for index,case in enumerate(root.iter('testcase')):
        markers=[child for child in case if child.tag in {'failure','error','skipped'}]
        status='PASSED' if not markers else {'failure':'FAILED','error':'ERROR','skipped':'SKIPPED'}[markers[0].tag] if len(markers)==1 else 'UNKNOWN'
        if len(markers)>1:integrity.add('MULTIPLE_CASE_RESULT_MARKERS')
        duration=None
        if 'time' in case.attrib:
            try:
                if len(case.attrib['time'])>48:raise InvalidOperation()
                value=Decimal(case.attrib['time'])
                if not value.is_finite() or not -12<=value.as_tuple().exponent<=12 or not 0<=value<=Decimal('1000000000000'):raise InvalidOperation()
                value=abs(value) if value.is_zero() else value
                duration=str(value);durations.append(value)
            except InvalidOperation:metadata.add('INVALID_CASE_DURATION')
        suite_name,suite_cut=_text(case_suites.get(id(case),''),160)
        labels={'suite':suite_name};truncated=suite_cut
        for name in ['name','classname','file']:
            labels[name],cut=_text(case.attrib.get(name,''),160);truncated|=cut
        marker=markers[0] if markers else None
        message,cut=_text(marker.attrib.get('message','') if marker is not None else '',256);truncated|=cut
        detail,cut=_text(''.join(marker.itertext()) if marker is not None else '',512);truncated|=cut
        kind,cut=_text(marker.attrib.get('type','') if marker is not None else '',80);truncated|=cut
        line=case.attrib.get('line');line=line if line is not None and re.fullmatch(r'\d{1,10}',line) else None
        cases.append({'ordinal':index,**labels,'line':line,'status':status,'duration_seconds':duration,
            'message':message,'detail':detail,'type':kind,'text_truncated':truncated})
    counts={key:sum(row['status']==key for row in cases) for key in ['PASSED','FAILED','ERROR','SKIPPED','UNKNOWN']}
    # Compare declared counters with actual testcase entries, including nested
    # suite rollups; duplicate names remain separate observed entries.
    for suite in root.iter():
        if suite.tag not in {'testsuites','testsuite'}:continue
        nested=list(suite.iter('testcase'))
        actual={'tests':len(nested),'failures':0,'errors':0,'skipped':0}
        for case in nested:
            for tag,key in [('failure','failures'),('error','errors'),('skipped','skipped')]:
                actual[key]+=int(any(child.tag==tag for child in case))
        for key,count in actual.items():
            declared=suite.attrib.get(key)
            if declared is not None and (not re.fullmatch(r'\d{1,10}',declared) or int(declared)!=count):
                integrity.add('DECLARED_CASE_COUNTS_DIFFER')
    outcome=('INCONSISTENT' if integrity else 'NO_TESTS' if not cases else
        'FAILED' if counts['FAILED'] or counts['ERROR'] else 'SKIPPED' if counts['SKIPPED']==len(cases) else 'PASSED_WITH_SKIPS' if counts['SKIPPED'] else 'PASSED')
    summary={'outcome':outcome,'total':len(cases),'counts':counts,'integrity_issues':sorted(integrity),
        'metadata_warnings':sorted(metadata),'duration_seconds':str(sum(durations)) if len(durations)==len(cases) and cases else None}
    return {'summary':summary,'cases':cases}


def _load_command_report(sessions,pid,sid,body,*,include_raw=False):
    from packages.adapters.opencode_command_observation import historical_admissions,read_after_source
    body=CommandTestReportInput.model_validate(body);path=relative(body.report_path)
    if body.session_ref.id!=sid or body.command_generation>body.generation:
        raise Conflict('Test report belongs to another original session generation')
    catalog=sessions.command_observations(pid,sid,session_sha256=body.session_ref.sha256,
        generation=body.generation,workspace_ref=body.workspace_ref.model_dump(),
        _command={'generation':body.command_generation,'call_id':body.call_id})
    matches=[row for row in catalog['commands'] if row['record_ref']==body.command_ref.model_dump()
        and row['source_generation']==body.command_generation and row['call_id']==body.call_id]
    if len(matches)!=1:raise Conflict('The original command report association could not be verified')
    command=matches[0];state=sessions._state(pid,sid)
    observed={**state,**catalog['scope']}
    admissions=[a for a in historical_admissions(sessions._path(pid,sid,'runtime'),observed)
        if a['scope']['generation']==body.command_generation]
    if len(admissions)!=1:raise Conflict('The original report admission is not uniquely available')
    original={**observed,'generation':body.command_generation}
    raw,source=read_after_source(admissions[0],original,command,path,body.report_sha256,data_dir=sessions.data_dir)
    parsed=parse_junit(raw);before=source.get('before')
    association=('BEFORE_UNAVAILABLE' if before is None else 'NEW_SNAPSHOT' if before['state']=='ABSENT' else
        'UNCHANGED_BYTES' if before['sha256']==body.report_sha256 else 'CHANGED_BYTES')
    reasons=[]
    if association in {'BEFORE_UNAVAILABLE','UNCHANGED_BYTES'}:reasons.append('NEW_REPORT_GENERATION_NOT_PROVEN')
    if command['status']!='EXITED':reasons.append('COMMAND_OUTCOME_UNKNOWN')
    if command['exit_code'] not in {0,None} and parsed['summary']['outcome'] in {'PASSED','PASSED_WITH_SKIPS'}:reasons.append('COMMAND_NONZERO_WITH_REPORT_PASS')
    basis={'schema':'eeas.command-test-report.v1','scope':command['source_scope'],'command_ref':command['record_ref'],
        'command_generation':body.command_generation,'call_id':body.call_id,'operation_id':command['operation_id'],
        'root_operation_id':command['root_operation_id'],'report_path':path,'report_sha256':body.report_sha256}
    report_ref={'id':digest(basis),'revision':1,'sha256':digest({'basis':basis,'parsed':parsed})}
    latest=sessions._state(pid,sid)
    if latest['generation']!=body.generation or latest.get('original_session_id')!=observed.get('original_session_id'):
        raise Conflict('Original owner changed while the report was read')
    return {'parsed':parsed,'basis':basis,'report_ref':report_ref,'report_bytes':len(raw),
        'scope':catalog['scope'],'command':command,'association':association,'reasons':reasons,
        'native_session_id':observed.get('original_session_id'),**({'raw_bytes':raw} if include_raw else {})}


def command_test_report(sessions,pid,sid,body):
    body=CommandTestReportInput.model_validate(body);request=body.model_dump()
    loaded=_load_command_report(sessions,pid,sid,body)
    parsed=loaded['parsed'];basis=loaded['basis'];report_ref=loaded['report_ref']
    command=loaded['command'];association=loaded['association'];reasons=loaded['reasons']
    rows=[row for row in parsed['cases'] if body.outcome=='ALL' or body.outcome=='PROBLEMS' and row['status'] in {'FAILED','ERROR','UNKNOWN'} or row['status']==body.outcome]
    page=rows[body.offset:body.offset+body.limit]
    value={'kind':'ORIGINAL_COMMAND_TEST_REPORT','version':1,'request_sha256':digest(request),
        'basis':basis,'observation_scope':loaded['scope'],'report_ref':report_ref,'report_bytes':loaded['report_bytes'],'summary':parsed['summary'],
        'command_status':command['status'],'command_exit_code':command['exit_code'],
        'association':association,'reason_codes':reasons,'cases':page,
        'page':{'offset':body.offset,'limit':body.limit,'outcome':body.outcome,'matching':len(rows),
            'next_offset':body.offset+len(page) if body.offset+len(page)<len(rows) else None},
        'source_scope':'IMMUTABLE_AFTER_COMMAND_SNAPSHOT','writer_attribution':'NOT_PROVEN',
        'all_dependencies_captured':False,'current_workspace_verified':False,'target_environment_verified':False,
        'whole_task_acceptance':'NOT_ASSESSED','read_only':True,'model_calls':0,'commands_started':0,
        'source_files_written':0,'automatic_replay':False}
    latest=sessions._state(pid,sid)
    if latest['generation']!=body.generation or latest.get('original_session_id')!=loaded['native_session_id']:
        raise Conflict('Original owner changed while the report was read')
    if len(canonical(value).encode())>131072:raise Conflict('The selected report page exceeds its output bound')
    return value

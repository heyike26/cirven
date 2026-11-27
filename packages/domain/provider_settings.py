"""Application-owned provider profiles and private credentials, no ambient keys."""
import hashlib
import hmac
import io
import json
import os
import threading
from pathlib import Path
import tarfile
from urllib.parse import urlsplit
import uuid
import zipfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION, binary_hash
from packages.adapters.provider_transport_policy import (
    CABytes, CheckedCAFile, EnterpriseTransportPolicy, HeaderSpec, MaterialRef,
    MAX_CA_BYTES, ProxySpec, TransportPolicyError,
)
from packages.adapters.opencode_provider import declared_reasoning_efforts, transport_summary
from packages.domain.intake import canonical, digest
from packages.domain.local_workspaces import FileSource, LocalWorkspaces
from packages.domain.paths import resolve_data_path
from packages.domain.questions import Id, RequestModel
from packages.domain.store import Conflict, InvalidInput, NotFound


class ProviderHeaderInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=128)
    value: str | None = Field(default=None,max_length=8192)
    secret_value: str | None = Field(default=None,max_length=8192,repr=False)

    @model_validator(mode='after')
    def reviewed(self):
        if (self.value is None)==(self.secret_value is None):
            raise ValueError('HEADER_REQUIRES_ONE_VALUE_SOURCE')
        try:
            HeaderSpec(self.name,public_value=self.value,
                secret_ref=MaterialRef('request-private-header',1,'0'*64) if self.secret_value is not None else None)
        except TransportPolicyError as error:raise ValueError(error.code) from None
        value=self.secret_value if self.secret_value is not None else self.value
        if not value or value!=value.strip(' ') or any(not 32<=ord(c)<=126 for c in value):
            raise ValueError('INVALID_HEADER_VALUE')
        return self


class ProviderProxyInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    url: str = Field(min_length=1,max_length=2048)
    username: str | None = Field(default=None,min_length=1,max_length=8192,repr=False)
    password: str | None = Field(default=None,min_length=1,max_length=8192,repr=False)

    @model_validator(mode='after')
    def reviewed(self):
        try:ProxySpec(self.url)
        except TransportPolicyError as error:raise ValueError(error.code) from None
        if (self.username is None)!=(self.password is None):raise ValueError('PROXY_AUTH_REQUIRES_BOTH_VALUES')
        for value in (self.username,self.password):
            if value is not None and (value!=value.strip(' ') or any(not 32<=ord(c)<=126 for c in value)):
                raise ValueError('INVALID_PROXY_AUTH')
        if self.username is not None and ':' in self.username:raise ValueError('INVALID_PROXY_AUTH')
        return self


class ProviderTransportInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    timeout_seconds: float = Field(default=60,ge=0.1,le=120,allow_inf_nan=False,strict=True)
    ca_pem: str | None = Field(default=None,min_length=1,max_length=MAX_CA_BYTES,repr=False)
    ca_file_source_ref: FileSource | None = None
    proxy: ProviderProxyInput | None = None
    custom_headers: list[ProviderHeaderInput] = Field(default_factory=list,max_length=32)

    @model_validator(mode='after')
    def reviewed(self):
        if self.ca_pem is not None and self.ca_file_source_ref is not None:
            raise ValueError('CA_REQUIRES_ONE_SOURCE')
        if self.ca_pem is not None:
            try:
                raw=self.ca_pem.encode('ascii')
                CABytes(raw,MaterialRef('request-ca',1,hashlib.sha256(raw).hexdigest()))
            except (UnicodeError,TransportPolicyError):raise ValueError('INVALID_CA_PEM') from None
        names=[header.name.lower() for header in self.custom_headers]
        if len(names)!=len(set(names)):raise ValueError('DUPLICATE_HEADER')
        return self


class ProviderInput(RequestModel):
    operation_id: Id
    id: Id
    expected_revision: int = Field(default=0,ge=0,strict=True)
    provider_url: str = Field(min_length=1,max_length=2000)
    model_id: str = Field(pattern=r'^[A-Za-z0-9_.:/-]{1,200}$')
    api_key: str | None = Field(default=None,min_length=1,max_length=8192,repr=False)
    auth_mode: Literal['API_KEY','NONE'] = 'API_KEY'
    transport: ProviderTransportInput | None = None
    supports_images: bool = Field(default=False,strict=True)
    reasoning_efforts: list[Literal['none','minimal','low','medium','high','xhigh']] = Field(default_factory=list,max_length=6,strict=True)

    @field_validator('reasoning_efforts')
    @classmethod
    def explicit_reasoning(cls,value):
        try: return list(declared_reasoning_efforts(value))
        except Conflict: raise ValueError('Reasoning efforts must be unique declared values') from None

    @field_validator('provider_url')
    @classmethod
    def endpoint(cls,value):
        parsed=urlsplit(value)
        if (parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.username or parsed.password or
                parsed.query or parsed.fragment or not parsed.path.endswith('/v1') or any(ord(c)<=32 for c in value)):
            raise ValueError('Choose an explicit compatible API endpoint ending in /v1 without credentials in its URL')
        return value

    @field_validator('api_key')
    @classmethod
    def key(cls,value):
        if value is not None and any(ord(c)<32 for c in value):raise ValueError('Credential must not contain control characters')
        return value

    @model_validator(mode='after')
    def explicit_auth(self):
        if (self.auth_mode=='API_KEY')!=(self.api_key is not None):raise ValueError('AUTH_MODE_KEY_MISMATCH')
        if self.auth_mode=='API_KEY' and self.transport and any(h.name.lower()=='authorization' for h in self.transport.custom_headers):
            raise ValueError('AUTHORIZATION_COLLISION')
        return self


_CLI_PUBLICATION_LOCK = threading.RLock()


class ProviderSettings:
    def __init__(self,store,data_dir,source_root):
        self.store,self.data_dir,self.source_root=store,Path(data_dir),Path(source_root)

    def path(self,*parts):
        return resolve_data_path('/'.join(['provider-settings',*parts]),self.data_dir)

    @staticmethod
    def _write(path,raw):
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True);raise

    def _cli(self):
        # Shared by provider saves and account-owner preparation in this process.
        with _CLI_PUBLICATION_LOCK:
            name='opencode.exe' if os.name=='nt' else 'opencode'
            target=self.path('runtime',VERSION,name)
            if target.exists():
                if target.is_symlink() or binary_hash(target)!=CLI_SHA[os.name]:raise Conflict('Installed OpenCode bytes changed')
                return target.absolute()
            component=self.source_root/'toolchain/components/opencode-v1.19.0'
            manifest=json.loads((component/'manifest.json').read_bytes())
            role='windows-x64-cli' if os.name=='nt' else 'linux-x64-cli'
            ref=next(f for f in manifest['files'] if f['role']==role)
            archive=component/ref['path']; raw=archive.read_bytes()
            if len(raw)!=ref['byte_length'] or hashlib.sha256(raw).hexdigest()!=ref['sha256']:
                raise Conflict('Bundled original OpenCode archive failed integrity checking')
            if os.name=='nt':
                with zipfile.ZipFile(io.BytesIO(raw)) as package:
                    entries=[e for e in package.infolist() if Path(e.filename).name==name and not e.is_dir()]
                    if len(entries)!=1:raise Conflict('Original OpenCode archive has ambiguous binary')
                    binary=package.read(entries[0])
            else:
                with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as package:
                    entries=[e for e in package.getmembers() if Path(e.name).name==name and e.isfile()]
                    if len(entries)!=1:raise Conflict('Original OpenCode archive has ambiguous binary')
                    with package.extractfile(entries[0]) as stream:binary=stream.read()
            if hashlib.sha256(binary).hexdigest()!=CLI_SHA[os.name]:raise Conflict('Original OpenCode binary failed integrity checking')
            try:
                self._write(target,binary)
            except FileExistsError:
                # Another process can publish this same checked cache.
                # Reuse a complete original binary; never replace or wait on it.
                if (target.is_symlink() or not target.is_file()
                        or target.stat().st_size!=len(binary)
                        or binary_hash(target)!=CLI_SHA[os.name]):
                    raise Conflict('Concurrent original OpenCode publication is incomplete or changed') from None
            if os.name!='nt':target.chmod(0o700)
            return target.absolute()

    def resolve_runtime_path(self, stored):
        # Profiles are immutable. Execution uses only this installation's data,
        # with explicit historical mappings supplied by the restore operation.
        if (self.data_dir / 'restore-provenance.json').exists():
            return resolve_data_path(stored, self.data_dir)
        return Path(stored)

    def configuration(self,ident,revision):
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',ident) or type(revision)!=int or revision<1:
            raise InvalidInput('Provider identity/revision invalid')
        path=self.path('profiles',ident,f'{revision:010d}.json')
        if not path.exists():raise NotFound('Provider profile revision does not exist')
        record=LocalWorkspaces._json(path)
        if digest(record['configuration'])!=record['sha256']:raise Conflict('Provider profile integrity failed')
        return record['configuration']

    def secret(self,reference):
        import re
        if not re.fullmatch(r'secret:[a-z0-9-]{1,100}',reference):raise InvalidInput('Provider secret identity invalid')
        path=self.path('secrets',reference[7:])
        if path.is_symlink() or not path.is_file() or path.stat().st_size>MAX_CA_BYTES:raise Conflict('Selected provider private material unavailable')
        return path.read_text('utf-8')

    def get(self,ident,revision=None):
        folder=self.path('profiles',ident)
        revisions=sorted(folder.glob('*.json')) if folder.is_dir() else []
        if not revisions:raise NotFound('Provider profile does not exist')
        if revision is None:revision=int(revisions[-1].stem)
        config=self.configuration(ident,revision)
        if config['schema']=='eeas.opencode-subscription-provider.v1':
            from packages.adapters.opencode_account import resolve_subscription_profile
            ref={'id':ident,'revision':revision,'sha256':digest(config)}
            result=resolve_subscription_profile(config,ref,self.subscription_binding,verify_executable=False,
                require_auth=False,runtime_path_resolver=self.resolve_runtime_path).public()
            result.update(provider_url='https://chatgpt.com/backend-api/codex',pricing='UNKNOWN')
            return result
        result={'profile_ref':{'id':ident,'revision':revision,'sha256':digest(config)},
            'provider_url':config['provider_url'],'model_id':config['model_id'],
            'provider_id':config['provider_id'],'auth_kind':config.get('auth_mode','API_KEY'),
            'credential_present':bool(config['secret_ref']),
            'supports_images':config.get('supports_images',False),
            'transport_kind':config['transport_kind'],'model_access':'NOT_VALIDATED',
            'pricing':'UNKNOWN','version':VERSION,'source_commit':COMMIT}
        efforts = declared_reasoning_efforts(config.get('reasoning_efforts', []))
        result.update(reasoning_efforts=list(efforts),
            reasoning_qualification='DECLARED_NOT_OBSERVED' if efforts else 'NOT_DECLARED')
        if config['schema']=='eeas.opencode-provider.v2':
            result['transport']=transport_summary(config['transport_policy'],config.get('ca_origin'))
        return result

    def list(self):
        root=self.path('profiles')
        return [self.get(folder.name) for folder in sorted(root.iterdir()) if folder.is_dir()] if root.exists() else []

    def subscription_binding(self,reference):
        from packages.adapters.opencode_account import read_account_binding
        return read_account_binding(self.data_dir,reference)

    def save_subscription(self,operation_id,ident,model_id,account_ref,supports_images):
        """Account service supplies the actual native catalog selection; no API secrets."""
        from packages.adapters.opencode_account import SCHEMA,AUTH_MODE,PROTOCOL
        binding=self.subscription_binding(account_ref)
        operation=self.path('subscription-operations',digest(operation_id)+'.json')
        request={'id':ident,'model_id':model_id,'account_ref':account_ref,'supports_images':supports_images}
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if operation.exists():
                recorded=LocalWorkspaces._json(operation)
                if recorded['request_sha256']!=digest(request):raise Conflict('Subscription profile operation input changed')
                result=self.subscription_operation(operation_id)
                if result['status']!='COMPLETED':raise Conflict('Subscription profile operation is incomplete; do not replay')
                return result['result']
            try:current=self.get(ident)['profile_ref']['revision']
            except NotFound:current=0
            config={'schema':SCHEMA,'id':ident,'revision':current+1,'version':VERSION,'source_commit':COMMIT,
                'executable':str(self._cli()),'executable_sha256':CLI_SHA[os.name],'provider_id':'openai',
                'protocol':PROTOCOL,'auth_mode':AUTH_MODE,'model_id':model_id,'account_ref':account_ref,
                'account_data_home':binding['account_data_home'],'supports_images':supports_images}
            self._write(operation,(canonical({'operation_id':operation_id,'request_sha256':digest(request),
                'public_request':{'id':ident,'model_id':model_id},
                'profile_ref':{'id':ident,'revision':current+1,'sha256':digest(config)}})+'\n').encode())
            self._write(self.path('profiles',ident,f'{current+1:010d}.json'),
                (canonical({'configuration':config,'sha256':digest(config)})+'\n').encode())
            return self.get(ident,current+1)

    def subscription_operation(self,operation_id):
        path=self.path('subscription-operations',digest(operation_id)+'.json')
        if not path.exists():return {'operation_id':operation_id,'status':'NO_RECORD','result':None}
        recorded=LocalWorkspaces._json(path);ref=recorded['profile_ref']
        if recorded['operation_id']!=operation_id:raise Conflict('Subscription operation identity changed')
        try:configuration=self.configuration(ref['id'],ref['revision'])
        except NotFound:return {'operation_id':operation_id,'status':'INCOMPLETE','result':None}
        if digest(configuration)!=ref['sha256']:raise Conflict('Subscription operation profile binding changed')
        return {'operation_id':operation_id,'status':'COMPLETED','result':self.get(ref['id'],ref['revision']),
            'public_request':recorded['public_request']}

    def _requests(self,body):
        public={'id':body.id,'expected_revision':body.expected_revision,'provider_url':body.provider_url,
            'model_id':body.model_id,'auth_mode':body.auth_mode,'supports_images':body.supports_images,
            'profile_schema':'eeas.opencode-provider.v2' if {'auth_mode','transport'} & body.model_fields_set else 'eeas.opencode-provider.v1'}
        if body.reasoning_efforts: public['reasoning_efforts'] = list(body.reasoning_efforts)
        private={'api_key':body.api_key,'headers':[],'proxy_auth':None,'ca_pem':None}
        selected=body.transport or ProviderTransportInput()
        public['transport']={'timeout_seconds':selected.timeout_seconds,
            'ca_pem_sha256':hashlib.sha256(selected.ca_pem.encode('ascii')).hexdigest() if selected.ca_pem else None,
            'ca_file_source_ref':selected.ca_file_source_ref.model_dump() if selected.ca_file_source_ref else None,
            'proxy':{'url':selected.proxy.url,'auth_configured':selected.proxy.username is not None} if selected.proxy else None,
            'custom_headers':[{'name':h.name.lower(),'value':h.value,'private':h.secret_value is not None} for h in selected.custom_headers]}
        private['headers']=[{'name':h.name.lower(),'secret_value':h.secret_value} for h in selected.custom_headers if h.secret_value is not None]
        private['ca_pem']=selected.ca_pem
        if selected.proxy and selected.proxy.username is not None:
            private['proxy_auth']={'username':selected.proxy.username,'password':selected.proxy.password}
        return public,canonical(private).encode('utf-8')

    @staticmethod
    def _legacy_request_hash(body):
        request=body.model_dump(exclude={'operation_id','auth_mode','transport'})
        if not body.supports_images:request.pop('supports_images',None)
        if not body.reasoning_efforts:request.pop('reasoning_efforts',None)
        return digest(request)

    def _operation_dir(self,operation_id):
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',operation_id):raise InvalidInput('Provider operation identity invalid')
        return self.path('operations-v2',digest(operation_id))

    def operation(self,operation_id):
        directory=self._operation_dir(operation_id)
        if (directory/'request.json').exists():
            claim=LocalWorkspaces._json(directory/'request.json')
            result_path=directory/'result.json';failure_path=directory/'failure.json'
            if result_path.is_file():
                result=self._operation_result(result_path);status='COMPLETED'
            else:result=None;status='FAILED_OR_PARTIAL' if failure_path.exists() else 'INCOMPLETE'
            return {'operation_id':operation_id,'status':status,'request_sha256':claim['request_sha256'],
                'result':result,'failure':LocalWorkspaces._json(failure_path) if failure_path.exists() else None}
        legacy=self.path('operations',digest(operation_id)+'.json')
        if legacy.exists():
            saved=LocalWorkspaces._json(legacy)
            return {'operation_id':operation_id,'status':'COMPLETED','result':saved['result'],
                'request_sha256':None,'failure':None,'history_format':'LEGACY_IMMUTABLE'}
        raise NotFound('NO_RECORD: Provider operation has no recorded outcome; absence does not prove no effect')

    def _operation_result(self,path):
        record=LocalWorkspaces._json(path);result=record['result']
        if digest(result)!=record.get('sha256'):raise Conflict('Provider operation result integrity failed')
        # Old immutable receipts predate this optional display metadata. Verify
        # their original checksum first, then compare an in-memory default only.
        result = dict(result)
        result.setdefault('reasoning_efforts', [])
        result.setdefault('reasoning_qualification', 'DECLARED_NOT_OBSERVED' if result['reasoning_efforts'] else 'NOT_DECLARED')
        ref=result['profile_ref'];actual=self.get(ref['id'],ref['revision'])
        if actual!=result:raise Conflict('Provider operation profile changed')
        return result

    def _existing(self,body,request_hash,private):
        directory=self._operation_dir(body.operation_id)
        if (directory/'request.json').exists():
            saved=LocalWorkspaces._json(directory/'request.json')
            ident=saved['private_request_id']
            try:
                if str(uuid.UUID(ident))!=ident:raise ValueError
                path=self.path('private-requests',ident)
                if path.is_symlink() or path.stat().st_size>1024*1024:raise ValueError
                expected=path.read_bytes()
            except (OSError,ValueError):raise Conflict('Provider private operation binding unavailable') from None
            if saved['request_sha256']!=request_hash or not hmac.compare_digest(expected,private):
                raise Conflict('IDEMPOTENCY_CONFLICT: provider operation binds another input')
            result=directory/'result.json'
            if result.is_file():return self._operation_result(result)
            raise Conflict('OPERATION_INCOMPLETE: inspect the original provider operation; do not replay')
        legacy=self.path('operations',digest(body.operation_id)+'.json')
        if legacy.exists():
            saved=LocalWorkspaces._json(legacy)
            if body.auth_mode!='API_KEY' or {'auth_mode','transport'} & body.model_fields_set or saved['request_sha256']!=self._legacy_request_hash(body):
                raise Conflict('IDEMPOTENCY_CONFLICT: historical provider operation binds another input')
            return saved['result']
        return None

    def _selected_ca(self,selected):
        ref=selected.ca_file_source_ref
        if ref is None:return None,None,None
        workspaces=LocalWorkspaces(self.store,self.data_dir)
        versions=workspaces.history(ref.project_id,ref.workspace_id,ref.path)
        version=next((v for v in versions if v['revision']==ref.revision and v['sha256']==ref.sha256),None)
        if version is None or not 0<version['byte_length']<=MAX_CA_BYTES:
            raise InvalidInput('INVALID_CA_SIZE_OR_VERSION: select a checked CA within 128KiB')
        status=workspaces.check_sources(ref.project_id,[ref])[0]
        if status['status']!='CURRENT':raise Conflict('CA_SOURCE_DRIFT: choose the exact current CA file')
        bound=workspaces.get(ref.project_id,ref.workspace_id,ref.binding_revision)
        actual=workspaces.read(ref.project_id,ref.workspace_id,ref.path,ref.revision)
        if actual['source_ref']!=ref.model_dump() or not actual['current'] or actual['encoding']!='utf-8':
            raise Conflict('CA_SOURCE_DRIFT: selected CA version is unavailable or changed')
        raw=actual['content'].encode('utf-8')
        source=CheckedCAFile(Path(bound['root']),ref.path,
            MaterialRef('selected-ca-'+digest(ref.model_dump())[:64],ref.revision,ref.sha256))
        try:
            if source.read()!=raw:raise Conflict('CA_SOURCE_DRIFT: live CA differs from immutable selected version')
        except TransportPolicyError as error:raise InvalidInput(error.code) from None
        return source,raw,ref.model_dump()

    def check_ca_origin(self,origin,ca_configuration):
        ref=FileSource.model_validate(origin);workspaces=LocalWorkspaces(self.store,self.data_dir)
        rows=workspaces.check_sources(ref.project_id,[ref])
        bound=workspaces.get(ref.project_id,ref.workspace_id,ref.binding_revision)
        if (rows[0]['status']!='CURRENT' or ca_configuration.get('kind')!='PEM_FILE' or
                ca_configuration.get('root')!=bound['root'] or ca_configuration.get('relative_path')!=ref.path or
                ca_configuration.get('source_ref',{}).get('sha256')!=ref.sha256):
            raise Conflict('CA_SOURCE_DRIFT: selected binding/version/root no longer matches the approved CA')
        return True

    def save(self,body):
        request,private=self._requests(body);request_hash=digest(request)
        # Inspect once before selected workspace IO, which owns its own SQLite lock.
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing=self._existing(body,request_hash,private)
            if existing is not None:return existing
        selected=body.transport or ProviderTransportInput()
        ca_file,ca_raw,ca_origin=self._selected_ca(selected)
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing=self._existing(body,request_hash,private)
            if existing is not None:return existing
            try:current=self.get(body.id)['profile_ref']['revision']
            except NotFound:current=0
            if current!=body.expected_revision:raise Conflict('Provider profile changed; reload current revision')
            revision=current+1;directory=self._operation_dir(body.operation_id);private_id=str(uuid.uuid4())
            self._write(self.path('private-requests',private_id),private)
            self._write(directory/'request.json',(canonical({'operation_id':body.operation_id,
                'request_sha256':request_hash,'private_request_id':private_id})+'\n').encode())
            phase='CLI_INTEGRITY'
            try:
                executable=self._cli()
                def material(raw):
                    ident=str(uuid.uuid4());self._write(self.path('secrets',ident),raw)
                    return MaterialRef('secret:'+ident,1,hashlib.sha256(raw).hexdigest())
                phase='PRIVATE_MATERIAL'
                key_ref=material(body.api_key.encode('utf-8')) if body.api_key is not None else None
                config={'schema':'eeas.opencode-provider.v1','id':body.id,'revision':revision,
                    'version':VERSION,'source_commit':COMMIT,'executable':str(executable),
                    'executable_sha256':CLI_SHA[os.name],'provider_id':'eeas-'+body.id[:80],
                    'protocol':'OPENAI_COMPATIBLE_CHAT_COMPLETIONS','provider_url':body.provider_url,
                    'model_id':body.model_id,'secret_ref':key_ref.id if key_ref else None,'transport_kind':'COMPANY_CONFIGURED'}
                if body.supports_images:config['supports_images']=True
                if body.reasoning_efforts:config['reasoning_efforts']=list(body.reasoning_efforts)
                modern=bool({'auth_mode','transport'} & body.model_fields_set)
                if modern:
                    phase='TRANSPORT_ADMISSION';ca=ca_file
                    if selected.ca_pem is not None:
                        ca_raw=selected.ca_pem.encode('ascii');ca=CABytes(ca_raw,material(ca_raw))
                    headers=tuple(HeaderSpec(h.name,public_value=h.value,
                        secret_ref=material(h.secret_value.encode('utf-8')) if h.secret_value is not None else None)
                        for h in selected.custom_headers)
                    proxy=None
                    if selected.proxy:
                        auth_ref=material(canonical({'username':selected.proxy.username,'password':selected.proxy.password}).encode()) if selected.proxy.username is not None else None
                        proxy=ProxySpec(selected.proxy.url,auth_ref)
                    policy=EnterpriseTransportPolicy.admit(policy_id='provider-'+body.id,revision=revision,
                        auth_mode=body.auth_mode,api_key_ref=key_ref,headers=headers,ca=ca,proxy=proxy,
                        timeout_seconds=selected.timeout_seconds,read_secret=lambda ref:self.secret(ref.id).encode('utf-8'))
                    config.update(schema='eeas.opencode-provider.v2',auth_mode=body.auth_mode,
                        transport_policy=policy.public_configuration(),transport_policy_ref=policy.reference.public(),ca_origin=ca_origin)
                phase='PROFILE_COMMIT'
                self._write(self.path('profiles',body.id,f'{revision:010d}.json'),(canonical({'configuration':config,'sha256':digest(config)})+'\n').encode())
                result=self.get(body.id,revision)
                phase='OPERATION_RECEIPT'
                self._write(directory/'result.json',(canonical({'result':result,'sha256':digest(result)})+'\n').encode())
                return result
            except Exception as error:
                code=error.code if isinstance(error,TransportPolicyError) else type(error).__name__.upper()
                self._write(directory/'failure.json',(canonical({'phase':phase,'code':code})+'\n').encode())
                if isinstance(error,TransportPolicyError):raise InvalidInput(error.code) from None
                if isinstance(error,(Conflict,InvalidInput,NotFound)):raise
                raise Conflict('PROVIDER_SAVE_FAILED_OR_PARTIAL: inspect the exact original provider operation') from None

"""Actual localhost API/storage/restart transport admission, no model or GUI."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest

from conftest import LiveServer
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION, binary_hash
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.adapters.provider_transport_policy import TransportPolicyError
from packages.domain.intake import canonical, digest
from packages.domain.provider_settings import ProviderInput, ProviderSettings
from packages.domain.store import Conflict, Store
from test_provider_transport_policy import certificates, check, evidence, lower_headers, running_server, ForwardProxy

ROOT=Path(__file__).resolve().parents[1]
CLI=Path(os.environ.get('EEAS_TEST_APPROVED_CLI_CACHE',str(ROOT/'var/opencode-ui-offline-guard/opencode')))


class ProviderLiveServer(LiveServer):
    def start(self):
        # Full current app may cold-verify bundled capabilities longer than the
        # old generic 7.5-second fixture. Preserve real output and exact owned PID.
        self.output=(self.data_dir/'provider-api-server.log').open('ab')
        try:
            self.process=subprocess.Popen([sys.executable]+(['-O'] if not __debug__ else [])+
                ['-m','apps.api','serve'],cwd=ROOT,env=self.env,stdout=self.output,stderr=self.output)
            started=time.monotonic()
            with httpx.Client(trust_env=False) as client:
                while time.monotonic()-started<30:
                    if self.process.poll() is not None:raise RuntimeError('real provider API exited; see preserved log')
                    try:
                        if client.get(self.origin+'/health',timeout=0.25).status_code==200:return self
                    except httpx.HTTPError:pass
                    time.sleep(0.05)
            raise RuntimeError('real provider API cold startup exceeded 30-second fixture bound')
        except BaseException:
            self.stop();raise

    def stop(self):
        try:super().stop()
        finally:
            output=getattr(self,'output',None)
            if output:output.close()


@contextmanager
def public_client(server):
    with httpx.Client(base_url=server.origin,trust_env=False,timeout=15) as client:
        response=client.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'})
        check(response.status_code==200)
        client.headers.update({'Origin':server.origin,'X-EEAS-CSRF':response.json()['csrf_token']})
        yield client


@pytest.fixture
def scene(tmp_path):
    data=tmp_path/'data';data.mkdir()
    actual=data/'provider-settings/runtime'/VERSION/'opencode'
    actual.parent.mkdir(parents=True)
    # Real approved bytes, shared immutable inode to avoid 178MiB per test copy.
    check(binary_hash(CLI)==CLI_SHA['posix'])
    actual.hardlink_to(CLI)
    server=ProviderLiveServer(data)
    server.env['EEAS_WORKER']='0'
    try:
        server.start()
        settings=ProviderSettings(Store(data/'eeas.sqlite3'),data,ROOT)
        with public_client(server) as client:
            yield {'server':server,'client':client,'settings':settings,'data':data,'tmp':tmp_path}
    finally:
        server.stop()


def body(name='new-none',**changes):
    result={'operation_id':name+'-save','id':name,'expected_revision':0,
            'provider_url':'http://127.0.0.1:12345/v1','model_id':'original-no-model-called',
            'auth_mode':'NONE','transport':{'timeout_seconds':60,'custom_headers':[]}}
    result.update(changes)
    return result


def post(client,payload,expected=201):
    result=client.post('/api/planning/providers',json=payload)
    check(result.status_code==expected,f'provider status {result.status_code}, expected {expected}')
    return result


def registry(scene):
    settings=scene['settings']
    return OpenCodeProviderRegistry(settings.configuration,settings.secret)


def no_private_in_public(scene,values):
    settings=scene['settings']
    for directory in ('profiles','operations','operations-v2'):
        for path in settings.path(directory).rglob('*.json') if settings.path(directory).exists() else []:
            content=path.read_text()
            check(not any(value in content for value in values),'private value in public persistent record')
    for path in settings.path('secrets').glob('*') if settings.path('secrets').exists() else []:
        check(path.stat().st_mode & 0o777==0o600,'private material permissions')
    for path in settings.path('private-requests').glob('*'):
        check(path.stat().st_mode & 0o777==0o600,'private replay binding permissions')


def test_actual_api_none_restart_operation_and_no_ambient_key(scene,monkeypatch):
    client=scene['client'];server=scene['server']
    monkeypatch.setenv('OPENAI_API_KEY','must-never-be-used')
    payload=body()
    result=post(client,payload).json();selected=result['profile_ref']
    check(result['auth_kind']=='NONE' and not result['credential_present'])
    check(result['model_access']=='NOT_VALIDATED' and result['transport']['tls_verification']=='SYSTEM_TRUST_PLUS_EXPLICIT_CA')
    loaded=registry(scene).resolve(selected)
    check(loaded.api_key is None and loaded.secret_ref is None and loaded.credential_sha256 is None)
    check(loaded.transport_policy.auth_mode=='NONE' and loaded.transport_secrets==())
    check(registry(scene).current_fingerprint(selected)==loaded.fingerprint)
    with running_server() as gateway,loaded.transport_policy.open_client(loaded.transport_policy.reference) as transport:
        check(transport.request('GET',gateway.url+'/no-key').status_code==200)
        check('authorization' not in lower_headers(gateway.observed[0]))
    operation=client.get('/api/planning/providers/operations/'+payload['operation_id']).json()
    check(operation['status']=='COMPLETED' and operation['result']==result)
    before={str(p.relative_to(scene['data'])):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in scene['settings'].path('profiles').rglob('*.json')}
    server.stop();server.start()
    with public_client(server) as restarted:
        check(restarted.get('/api/planning/providers/'+payload['id']).json()==result)
        check(restarted.get('/api/planning/providers/operations/'+payload['operation_id']).json()==operation)
        check(post(restarted,payload).json()==result)
    after={str(p.relative_to(scene['data'])):hashlib.sha256(p.read_bytes()).hexdigest()
           for p in scene['settings'].path('profiles').rglob('*.json')}
    check(before==after)
    evidence('provider-api-none-restart',auth_mode='NONE',fake_key=False,profile_ref=selected,
             same_operation=True,history_unchanged=True,model_calls=0)


def test_actual_api_private_headers_ca_proxy_and_budget_cap(scene,certificates):
    auth='Basic b3JpZ2luYWwtcWE6cHJpdmF0ZQ=='
    proxy_user='provider-qa-proxy-user';proxy_password='provider-qa-proxy-password'
    with running_server(tls=certificates) as gateway,running_server(ForwardProxy) as proxy:
        payload=body('private-transport',provider_url=gateway.url+'/v1',transport={
            'timeout_seconds':30,'ca_pem':certificates['root'].decode(),
            'proxy':{'url':proxy.url,'username':proxy_user,'password':proxy_password},
            'custom_headers':[{'name':'Authorization','secret_value':auth},{'name':'X-Tenant','value':'original-provider-qa'}]})
        result=post(scene['client'],payload).json();summary=result['transport']
        check(summary['ca']['sha256']==hashlib.sha256(certificates['root']).hexdigest())
        check(summary['proxy']=={'url':proxy.url,'auth_configured':True})
        check(summary['custom_headers']==[{'name':'authorization','private':True},{'name':'x-tenant','private':False}])
        loaded=registry(scene).resolve(result['profile_ref'])
        check(loaded.api_key is None and loaded.auth_mode=='NONE')
        check(all(value in loaded.transport_secrets for value in (auth,proxy_user,proxy_password)))
        check(any('"username"' in value and '"password"' in value for value in loaded.transport_secrets))
        safe=json.dumps(result)+repr(loaded)+json.dumps(loaded.public())
        check(not any(value in safe for value in (auth,proxy_user,proxy_password)))
        check('BEGIN CERTIFICATE' not in safe)
        with loaded.transport_policy.open_client(loaded.transport_policy.reference,timeout_cap_seconds=2) as transport:
            check(transport.effective_timeout_seconds==2)
            check(transport.request('POST',gateway.url+'/v1/chat/completions',json={'fixture':True}).status_code==200)
        check(lower_headers(gateway.observed[0])['authorization']==auth)
        check(len(proxy.observed)==1 and proxy.observed[0]['method']=='CONNECT')
        operation=scene['client'].get('/api/planning/providers/operations/'+payload['operation_id']).json()
        check(operation['status']=='COMPLETED')
        no_private_in_public(scene,[auth,proxy_user,proxy_password,certificates['root'].decode()])
        changed=json.loads(json.dumps(payload));changed['transport']['custom_headers'][0]['secret_value']='Basic changed-private-fixture'
        post(scene['client'],changed,409)
        # Same public metadata hash; private bytes still bind the exact operation.
        check(scene['settings']._requests(ProviderInput(**payload))[0]==scene['settings']._requests(ProviderInput(**changed))[0])
        evidence('provider-api-ca-proxy-private-header',gateway_requests=len(gateway.observed),
                 proxy_requests=len(proxy.observed),private_public_separated=True,
                 private_idempotency_conflict=True,budget_timeout_seconds=2,model_calls=0)


def test_existing_legacy_profile_digest_and_operation_remain_immutable(scene):
    client=scene['client'];settings=scene['settings']
    payload={'operation_id':'legacy-save','id':'legacy-provider','provider_url':'http://127.0.0.1:12345/v1',
             'model_id':'legacy-original-model','api_key':'legacy-original-private-fixture'}
    first=post(client,payload).json();configuration=settings.configuration(payload['id'],1)
    check(configuration['schema']=='eeas.opencode-provider.v1' and 'transport_policy' not in configuration)
    old_profile_path=settings.path('profiles',payload['id'],'0000000001.json')
    old_profile_bytes=old_profile_path.read_bytes()
    historical_payload={**payload,'operation_id':'legacy-historical-replay'}
    historical_hash=settings._legacy_request_hash(ProviderInput(**historical_payload))
    old_operation=settings.path('operations',digest(historical_payload['operation_id'])+'.json')
    old_record=(canonical({'request_sha256':historical_hash,'result':first})+'\n').encode()
    settings._write(old_operation,old_record)
    check(post(client,historical_payload).json()==first)
    historical=client.get('/api/planning/providers/operations/'+historical_payload['operation_id']).json()
    check(historical['history_format']=='LEGACY_IMMUTABLE' and historical['request_sha256'] is None)
    loaded=registry(scene).resolve(first['profile_ref'])
    check(loaded.transport_policy is None and loaded.api_key==payload['api_key'])
    check(loaded.fingerprint==digest({'profile_sha256':first['profile_ref']['sha256'],'credential_sha256':digest(payload['api_key'])}))
    check(registry(scene).current_fingerprint(first['profile_ref'])==loaded.fingerprint)
    next_body=body(payload['id'],operation_id='explicit-none-v2',expected_revision=1)
    second=post(client,next_body).json()
    check(second['profile_ref']['revision']==2 and second['auth_kind']=='NONE')
    check(old_profile_path.read_bytes()==old_profile_bytes and old_operation.read_bytes()==old_record)
    check(client.get('/api/planning/providers/'+payload['id'],params={'revision':1}).json()==first)
    evidence('legacy-profile-operation-history',old_profile_ref=first['profile_ref'],new_profile_ref=second['profile_ref'],
             legacy_bytes_unchanged=True,legacy_fingerprint_unchanged=True,legacy_operation_not_rewritten=True)


def test_current_fingerprint_rechecks_private_source_without_cli_reread(scene):
    secret='exact-api-key-private-fixture'
    result=post(scene['client'],body('exact-key',auth_mode='API_KEY',api_key=secret)).json()
    selected=result['profile_ref'];reg=registry(scene);loaded=reg.resolve(selected)
    expected=loaded.fingerprint
    executable=loaded.executable
    # Replace only this test's hardlink, never modify the shared original bytes.
    executable.unlink();executable.write_bytes(b'own-cache-corruption')
    check(reg.current_fingerprint(selected)==expected)
    with pytest.raises(Conflict,match='Original CLI path or bytes'):
        reg.resolve(selected)
    executable.unlink();executable.hardlink_to(CLI)
    private_path=scene['settings'].path('secrets',loaded.secret_ref[7:])
    private_path.write_text('changed-api-key-private-fixture')
    with pytest.raises(Conflict,match='SECRET_SOURCE_DRIFT'):
        reg.current_fingerprint(selected)
    check(private_path.read_text()=='changed-api-key-private-fixture')
    no_private_in_public(scene,[secret,'changed-api-key-private-fixture'])
    evidence('provider-current-fingerprint',startup_cli_hash_separate=True,fast_fingerprint_same=True,
             current_private_drift_rejected=True,private_changed_bytes_preserved=True)


def test_actual_file_source_ca_origin_is_bound_and_drift_rejected(scene,certificates):
    client=scene['client'];actual=scene['tmp']/'original-selected-ca';actual.mkdir()
    ca=actual/'公司根 CA.pem';ca.write_bytes(certificates['root'])
    project=client.post('/api/projects',json={'name':'原创CA资料资格','description':''})
    check(project.status_code==201);pid=project.json()['id']
    bound=client.post(f'/api/projects/{pid}/local-workspaces',json={'idempotency_key':'ca-only','root':str(actual),'allowed_write_paths':[]})
    check(bound.status_code==201);wid=bound.json()['id']
    source=client.get(f'/api/projects/{pid}/local-workspaces/{wid}/file',params={'path':ca.name}).json()['source_ref']
    result=post(client,body('source-ca',transport={'ca_file_source_ref':source})).json()
    check(result['transport']['ca']['file_source_ref']==source)
    reg=registry(scene);loaded=reg.resolve(result['profile_ref'])
    check(loaded.ca_origin==source and loaded.transport_policy.ca.source_ref.sha256==source['sha256'])
    check(reg.current_fingerprint(result['profile_ref'])==loaded.fingerprint)
    with running_server() as gateway,loaded.transport_policy.open_client(loaded.transport_policy.reference) as transport:
        moved=scene['tmp']/'original-selected-ca-preserved';actual.rename(moved)
        actual.mkdir();ca.write_bytes(certificates['root'])
        try:
            with pytest.raises(TransportPolicyError,match='CA_SOURCE_DRIFT'):
                transport.request('GET',gateway.url+'/same-bytes-different-root')
            check(len(gateway.observed)==0)
        finally:
            ca.unlink();actual.rmdir();moved.rename(actual)
    check(reg.current_fingerprint(result['profile_ref'])==loaded.fingerprint)
    changed=certificates['root']+b'\n';ca.write_bytes(changed)
    with pytest.raises(Conflict,match='CA_SOURCE_DRIFT'):
        reg.current_fingerprint(result['profile_ref'])
    post(client,body('stale-source-ca',transport={'ca_file_source_ref':source}),409)
    historic=client.get(f'/api/projects/{pid}/local-workspaces/{wid}/file',params={'path':ca.name,'revision':source['revision']}).json()
    check(historic['source_ref']==source and historic['content'].encode()==certificates['root'] and not historic['current'])
    check(ca.read_bytes()==changed)
    check(client.get('/api/planning/providers/source-ca').json()==result)
    evidence('provider-ca-exact-file-source',source_ref=source,current_origin_drift_rejected=True,
             original_snapshot_retained=True,current_user_file_preserved=True,provider_history_readable=True,
             same_bytes_different_root_rejected_before_dispatch=True)


def test_actual_api_partial_commit_failure_is_durable_and_never_replayed(scene):
    payload=body('partial-failure')
    blocked=scene['settings']._operation_dir(payload['operation_id'])/'result.json'
    blocked.mkdir(parents=True)
    response=post(scene['client'],payload,409)
    check('PROVIDER_SAVE_FAILED_OR_PARTIAL' in response.text)
    operation=scene['client'].get('/api/planning/providers/operations/'+payload['operation_id']).json()
    check(operation['status']=='FAILED_OR_PARTIAL' and operation['result'] is None)
    check(operation['failure']=={'phase':'OPERATION_RECEIPT','code':'FILEEXISTSERROR'})
    current=scene['client'].get('/api/planning/providers/'+payload['id'])
    check(current.status_code==200 and current.json()['profile_ref']['revision']==1)
    post(scene['client'],payload,409)
    check(scene['client'].get('/api/planning/providers/'+payload['id']).json()==current.json())
    server=scene['server'];server.stop();server.start()
    with public_client(server) as restarted:
        check(restarted.get('/api/planning/providers/operations/'+payload['operation_id']).json()==operation)
        check(restarted.get('/api/planning/providers/operations/no-record-operation').status_code==404)
    evidence('provider-real-partial-write',first_status=409,record_status=operation['status'],
             committed_profile_revision=1,replayed=False,restart_preserved=True)


def test_actual_api_validation_redacts_private_input_and_rejects_unsafe_overrides(scene):
    private='sensitive-fixture-not-in-error-body'
    invalid=[body('none-key',api_key=private),body('missing-key',auth_mode='API_KEY'),
        body('private-proxy',transport={'proxy':{'url':'http://'+private+':password@localhost:1234'}}),
        body('header-framing',transport={'custom_headers':[{'name':'Host','secret_value':private}]}),
        body('literal-auth',transport={'custom_headers':[{'name':'Authorization','value':private}]}),
        body('header-crlf',transport={'custom_headers':[{'name':'X-Auth','secret_value':private+'\r\nHost:evil'}]}),
        body('bad-ca',transport={'ca_pem':private}),body('bad-timeout',transport={'timeout_seconds':True}),
        body('tls-disable',transport={'verify':False}),body('ambient-secret',transport={'api_key_env':'OPENAI_API_KEY'})]
    for payload in invalid:
        response=post(scene['client'],payload,422)
        check(private not in response.text and 'password@' not in response.text)
    check(scene['client'].get('/api/planning/providers').json()['providers']==[])
    evidence('provider-api-input-safety',invalid_cases=len(invalid),private_errors_redacted=True,
             profiles_created=0,model_calls=0)


def test_actual_api_ca_file_and_proxy_restore_validate_distinct_exact_descriptors(scene,certificates):
    client=scene['client'];actual=scene['tmp']/'combined-ca-source';actual.mkdir()
    ca_path=actual/'combined-root.pem';ca_path.write_bytes(certificates['root'])
    project=client.post('/api/projects',json={'name':'原创CA文件及代理组合','description':''})
    check(project.status_code==201);pid=project.json()['id']
    bound=client.post(f'/api/projects/{pid}/local-workspaces',json={
        'idempotency_key':'combined-readonly-ca','root':str(actual),'allowed_write_paths':[]})
    check(bound.status_code==201);wid=bound.json()['id']
    selected=client.get(f'/api/projects/{pid}/local-workspaces/{wid}/file',params={'path':ca_path.name}).json()['source_ref']
    with running_server(tls=certificates) as gateway,running_server(ForwardProxy) as proxy:
        payload=body('combined-ca-proxy',provider_url=gateway.url+'/v1',transport={
            'ca_file_source_ref':selected,'proxy':{'url':proxy.url,'username':'combined-qa-user','password':'combined-qa-pass'},
            'custom_headers':[{'name':'X-Auth','secret_value':'combined-original-private-header'}]})
        saved=post(client,payload).json()
        loaded=registry(scene).resolve(saved['profile_ref'])
        check(registry(scene).current_fingerprint(saved['profile_ref'])==loaded.fingerprint)
        check(loaded.transport_policy.ca.public()['kind']=='PEM_FILE' and loaded.transport_policy.proxy.url==proxy.url)
        with loaded.transport_policy.open_client(loaded.transport_policy.reference,timeout_cap_seconds=2) as transport:
            check(transport.request('POST',gateway.url+'/v1/chat/completions',json={'external_gateway_fixture':True}).status_code==200)
        check(len(gateway.observed)==len(proxy.observed)==1)
        check(lower_headers(gateway.observed[0])['x-auth']=='combined-original-private-header')
        scene['server'].stop();scene['server'].start()
        with public_client(scene['server']) as restarted:
            check(restarted.get('/api/planning/providers/'+payload['id']).json()==saved)
            operation=restarted.get('/api/planning/providers/operations/'+payload['operation_id']).json()
            check(operation['status']=='COMPLETED' and operation['result']==saved)
        reloaded=registry(scene).resolve(saved['profile_ref'])
        check(reloaded.transport_policy.ca.source_ref.sha256==selected['sha256'])
        evidence('ca-file-proxy-independent-restore',gateway_requests=1,proxy_connect_requests=1,
                 exact_ca_source_ref=selected,distinct_descriptors=True,restart_restored=True,model_calls=0)

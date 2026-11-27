"""Real provider API/storage/registry; declarations are not gateway qualification."""
from copy import deepcopy
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from apps.api.config import Settings
from apps.api.main import create_app
from packages.adapters.opencode_profile import CLI_SHA, VERSION, binary_hash
from packages.adapters.opencode_provider import OpenCodeProviderRegistry, REASONING_EFFORTS
from packages.domain.intake import canonical, digest
from packages.domain.provider_settings import ProviderInput
from packages.domain.store import Conflict

ROOT=Path(__file__).resolve().parents[1]
CLI=Path(os.environ.get('EEAS_TEST_APPROVED_CLI_CACHE',str(ROOT/'var/opencode-ui-offline-guard/opencode')))


def check(value,message='provider reasoning acceptance failed'):
    if not value: raise AssertionError(message)


def body(**changes):
    value={'operation_id':'declare-first','id':'declared-model','provider_url':'http://127.0.0.1:1/v1',
           'model_id':'no-name-based-inference','auth_mode':'NONE',
           'transport':{'timeout_seconds':17,'custom_headers':[{'name':'X-Auth-Token','secret_value':'fixture-header-private'}]}}
    value.update(changes)
    return value


def application(tmp_path):
    settings=Settings(data_dir=tmp_path/'app',port=8787,worker_enabled=False)
    target=settings.data_dir/'provider-settings/runtime'/VERSION/'opencode'
    target.parent.mkdir(parents=True)
    check(binary_hash(CLI)==CLI_SHA[os.name])
    target.hardlink_to(CLI)
    return create_app(settings),settings


def connect(client):
    bootstrap=client.get('/api/bootstrap',headers={'X-EEAS-Bootstrap':'1'})
    check(bootstrap.status_code==200)
    client.headers.update({'Origin':'http://127.0.0.1:8787','X-EEAS-CSRF':bootstrap.json()['csrf_token']})


def test_reasoning_input_is_explicit_unique_finite_literal_list_and_normalized():
    check(ProviderInput(**body()).reasoning_efforts==[])
    check(ProviderInput(**body(reasoning_efforts=list(reversed(REASONING_EFFORTS)))).reasoning_efforts==list(REASONING_EFFORTS))
    for item in REASONING_EFFORTS:
        check(ProviderInput(**body(reasoning_efforts=[item])).reasoning_efforts==[item])
    for bad in [None,'high',True,{},['HIGH'],['max'],['auto'],[1],['low','low'],list(REASONING_EFFORTS)+['low']]:
        with pytest.raises(ValidationError): ProviderInput(**body(reasoning_efforts=bad))
    with pytest.raises(ValidationError): ProviderInput(**body(reasoning_qualification='OBSERVED'))


def test_real_api_save_version_reopen_receipt_and_registry_keep_declared_capabilities_and_private_transport(tmp_path):
    app,settings=application(tmp_path)
    declared=body(reasoning_efforts=['high','low'])
    with TestClient(app,base_url=settings.origin) as client:
        connect(client)
        response=client.post('/api/planning/providers',json=declared)
        check(response.status_code==201,response.text)
        saved=response.json();ref=saved['profile_ref']
        check(saved['reasoning_efforts']==['low','high'] and saved['reasoning_qualification']=='DECLARED_NOT_OBSERVED')
        check(saved['model_access']=='NOT_VALIDATED' and saved['credential_present'] is False)
        check(saved['transport']['timeout_seconds']==17 and saved['transport']['custom_headers']==[{'name':'x-auth-token','private':True}])
        check('fixture-header-private' not in json.dumps(saved))
        config=app.state.provider_settings.configuration(ref['id'],ref['revision'])
        check(config['reasoning_efforts']==['low','high'])
        registry=OpenCodeProviderRegistry(app.state.provider_settings.configuration,app.state.provider_settings.secret)
        loaded=registry.resolve(ref)
        check(loaded.reasoning_efforts==('low','high') and loaded.public()['reasoning_qualification']=='DECLARED_NOT_OBSERVED')
        check('fixture-header-private' in loaded.transport_secrets)
        original=app.state.provider_settings.path('profiles',ref['id'],'0000000001.json').read_bytes()
        check(client.post('/api/planning/providers',json=declared).json()==saved)
        conflicting={**declared,'reasoning_efforts':['medium']}
        check(client.post('/api/planning/providers',json=conflicting).status_code==409)
        second=client.post('/api/planning/providers',json={**declared,'operation_id':'declare-second','expected_revision':1,'reasoning_efforts':['medium']})
        check(second.status_code==201 and second.json()['profile_ref']['revision']==2)
        check(app.state.provider_settings.path('profiles',ref['id'],'0000000001.json').read_bytes()==original)
    reopened=create_app(settings)
    with TestClient(reopened,base_url=settings.origin) as client:
        connect(client)
        check(client.get('/api/planning/providers/'+ref['id']).json()['reasoning_efforts']==['medium'])
        first=client.get('/api/planning/providers/operations/declare-first').json()
        check(first['status']=='COMPLETED' and first['result']==saved)
        check(client.get('/api/planning/providers/'+ref['id']+'?revision=1').json()==saved)
        check(client.get('/api/planning/providers').json()['providers'][0]['reasoning_qualification']=='DECLARED_NOT_OBSERVED')


def test_legacy_empty_config_and_original_receipt_bytes_remain_readable_without_rewrite(tmp_path):
    app,settings=application(tmp_path);service=app.state.provider_settings
    payload=body()
    saved=service.save(ProviderInput(**payload));ref=saved['profile_ref']
    config=service.configuration(ref['id'],1)
    check('reasoning_efforts' not in config and saved['reasoning_efforts']==[])
    registry=OpenCodeProviderRegistry(service.configuration,service.secret)
    check(registry.resolve(ref).reasoning_efforts==())
    result={key:value for key,value in saved.items() if key not in {'reasoning_efforts','reasoning_qualification'}}
    receipt=service._operation_dir(payload['operation_id'])/'result.json'
    receipt.write_text(canonical({'result':result,'sha256':digest(result)})+'\n')
    before=receipt.read_bytes()
    actual=service.operation(payload['operation_id'])
    check(actual['status']=='COMPLETED' and actual['result']==saved and receipt.read_bytes()==before)
    check(service.save(ProviderInput(**payload))==saved and receipt.read_bytes()==before)
    # The former v1 request hash omitted the absent optional field.
    old_body=ProviderInput(**{'operation_id':'old-hash','id':'old','provider_url':'http://127.0.0.1:1/v1','model_id':'old','api_key':'fixture-only'})
    old_request=old_body.model_dump(exclude={'operation_id','auth_mode','transport','reasoning_efforts','supports_images'})
    check(service._legacy_request_hash(old_body)==digest(old_request))


def test_registry_rejects_declared_fields_in_corrupt_real_profiles_even_with_recomputed_hash(tmp_path):
    app,_settings=application(tmp_path);service=app.state.provider_settings
    saved=service.save(ProviderInput(**body(reasoning_efforts=['low'])))
    config=service.configuration(saved['profile_ref']['id'],1)
    path=service.path('profiles',config['id'],'0000000001.json')
    registry=OpenCodeProviderRegistry(service.configuration,service.secret)
    for bad in [None,'low',{},True,['low','low'],['max'],[False],list(REASONING_EFFORTS)+['low']]:
        changed=deepcopy(config);changed['reasoning_efforts']=bad
        path.write_text(canonical({'configuration':changed,'sha256':digest(changed)})+'\n')
        ref={'id':changed['id'],'revision':1,'sha256':digest(changed)}
        with pytest.raises(Conflict): registry.resolve(ref)
        with pytest.raises(Conflict): service.get(changed['id'],1)


def test_actual_api_invalid_declarations_are_rejected_before_writes_and_leave_no_operation(tmp_path):
    app,settings=application(tmp_path)
    with TestClient(app,base_url=settings.origin) as client:
        connect(client)
        for bad in [['invented-max'],['low','low'],None,True,['high']*7]:
            response=client.post('/api/planning/providers',json=body(reasoning_efforts=bad))
            check(response.status_code==422,response.text)
        check(client.get('/api/planning/providers').json()['providers']==[])
        check(client.get('/api/planning/providers/operations/declare-first').status_code==404)

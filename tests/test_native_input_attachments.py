"""Real signed records, HTTP, SQLite and original PNGs; no native/model qualification."""
import base64
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json

from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, ValidationError as SchemaError
from PIL import Image
from pydantic import ValidationError
import pytest

from apps.api.native_messages import NativeQuestionInput
from apps.api.desktop_context import register_desktop_context_routes
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.domain.document_import import image_metadata
from packages.domain.native_input_attachments import NativeInputAttachment, READ_METHOD
from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest
from test_native_messages import application, TOKEN, ROOT
from test_desktop_context_api import frozen_fixture, REF


def scene(directory, *, image_support=True):
    app, native, _ = application(directory)
    raw, image, frame, snapshot = frozen_fixture(native.context)
    # This suite's valid input source is an OS-adapter fixture. The shared
    # historical-image fixture remains truthfully UNKNOWN in its own tests.
    snapshot['observation']['status'] = 'OBSERVED'
    snapshot['observation']['coverage'].update(visual=True, pixel_source='EXACT_TARGET_WINDOW',
        qualification='CONTROLLED_PNG_ADAPTER_FIXTURE_NOT_NATIVE_CAPTURE_QUALIFICATION')
    native.ws._write(native.context._path('contexts', REF['id']+'.json'), snapshot)
    provider = {'schema':'eeas.opencode-provider.v1','id':'input-fixture','revision':1,
        'version':VERSION,'source_commit':COMMIT,
        'executable':str(ROOT/'var/opencode-ui-offline-guard/opencode'),'executable_sha256':CLI_SHA['posix'],
        'provider_id':'eeas-input-fixture','protocol':'OPENAI_COMPATIBLE_CHAT_COMPLETIONS',
        'provider_url':'http://127.0.0.1:1/v1','model_id':'input-fixture','secret_ref':'secret:fixture-only',
        'transport_kind':'MOCK_WIRE','supports_images':image_support}
    native.transport.provider_registry = OpenCodeProviderRegistry(lambda *_:deepcopy(provider),lambda *_:'fixture-only')
    profile = {'id':provider['id'],'revision':1,'sha256':digest(provider)}
    first = {'kind':'SCREENSHOT','context_ref':REF,'label':'原始白色画面','regions':[], 'image_sha256':image['sha256']}
    return app, native, raw, first, profile


def second(native):
    image = Image.new('RGB',(64,48),'#20ba50');out=BytesIO();image.save(out,'PNG');raw=out.getvalue();info=image_metadata(raw)
    reference={'id':'1'*32,'revision':1,'sha256':digest('second-context')}
    image_ref={'id':'2'*32,'revision':1,'sha256':info['sha256']}
    frame={'id':'3'*32,'revision':1,'sha256':digest('second-frame')}
    observation={'status':'PARTIAL','context_ref':reference,'automatic_input_authorized':False,
        'window':{'title':'另一个已选择窗口'},'observations':{'visual':{'frame_ref':frame,
        'image_ref':{'sha256':info['sha256'],'byte_length':len(raw)}}},'coverage':{'whole_page_seen':False,
        'pixel_source':'EXACT_TARGET_WINDOW','qualification':'CONTROLLED_PNG_ADAPTER_FIXTURE_NOT_NATIVE_CAPTURE_QUALIFICATION'}}
    native.ws._write(native.context._path('images',image_ref['id']+'.json'),
        {'image_ref':image_ref,'context_ref':reference,'frame_ref':frame,**info})
    native.context._path('images',image_ref['id']+'.png').write_bytes(raw)
    native.ws._write(native.context._path('contexts',reference['id']+'.json'),{
        'operation_id':'second-frame-fixture','observation':observation,'current':False,
        'preview':{'status':'AVAILABLE','image_ref':{**image_ref,**info}},'automatic_input_authorized':False})
    return {'kind':'SCREENSHOT','context_ref':reference,'label':'第二张绿图','regions':[
        {'x':.25,'y':.25,'width':.5,'height':.5}], 'image_sha256':info['sha256']}


def prepare(native, profile, attachments, operation='inputs-parent'):
    return native._prepare_ordinary({'operation_id':operation,'conversation_id':'same-user-discussion',
        'raw_text':'比较这两个页面，并检查第二张圈选区域。','context_ref':None,
        'provider_profile_ref':profile,'input_attachments':attachments},TOKEN)


def test_old_request_hash_and_user_text_stay_unchanged_when_new_inputs_are_absent():
    old=NativeQuestionInput.model_validate({'operation_id':'legacy','raw_text':'原话\n{"用户JSON":true}'}).request_dict()
    assert 'input_attachments' not in old
    assert NativeQuestionInput.model_validate({**old,'input_attachments':[]}).request_dict()==old
    source={'kind':'CURRENT_PAGE','context_ref':REF,'label':'用户选择的页面','regions':[]}
    new=NativeQuestionInput.model_validate({**old,'input_attachments':[source]}).request_dict()
    assert new['raw_text']==old['raw_text'] and new['context_ref'] is None
    assert new['input_attachments'][0]['context_ref']==REF


def test_two_frozen_frames_and_selected_region_reach_real_pixel_views_without_recapture(tmp_path):
    _,native,raw,first,profile=scene(tmp_path);two=second(native)
    record,request=prepare(native,profile,[first,two]);assert record['status']=='PROCESSING'
    assert record['request']['raw_text']=='比较这两个页面，并检查第二张圈选区域。'
    assert record['request']['context_ref'] is None
    assert {'list_input_attachments','read_input_attachments'}<=set(request['method_ids'])
    assert 'propose_native_append' not in request['method_ids']
    bound={'request':request};catalog=native.input_sources.catalog({},bound)
    assert len(catalog['attachments'])==2 and not catalog['automatic_input_authorized']
    assert not catalog['file_write_authorized'] and 'data:image' not in canonical(catalog)
    result=native.input_sources.read({'view':'IMAGE','items':[{'index':0},{'index':1,'region_index':0}]},bound)
    images=[item for item in result['content'] if item['type']=='image'];assert len(images)==2
    with Image.open(BytesIO(base64.b64decode(images[0]['data']))) as image:
        assert image.getpixel((1,1))==(240,243,247)
    with Image.open(BytesIO(base64.b64decode(images[1]['data']))) as image:
        assert image.size==(32,24) and image.getpixel((5,5))==(32,186,80)
    assert native.context.frozen_image({key:native.context.frozen_lookup(REF)['preview']['image_ref'][key]
        for key in ('id','revision','sha256')})==raw
    assert record['request_sha256']==digest(record['request'])


def test_only_this_operation_indices_can_be_read_and_old_host_execution_is_not_adopted(tmp_path):
    _,native,_,first,profile=scene(tmp_path);two=second(native)
    record,request=prepare(native,profile,[first]);bound={'request':request}
    for pointer in [{'index':1},{'index':-1},{'index':0,'region_index':0}]:
        with pytest.raises(Conflict):native.input_sources.read({'view':'IMAGE','items':[pointer]},bound)
    for malformed in [{'index':True},{'index':0,'context_ref':two['context_ref']}]:
        with pytest.raises(SchemaError):Draft202012Validator(READ_METHOD['input_schema']).validate({'view':'IMAGE','items':[malformed]})
    with pytest.raises(Conflict):native.input_sources.read({'view':'IMAGE','items':[{'index':0},{'index':0}]},bound)
    native.generation='new-host-generation'
    with pytest.raises(Conflict):native.input_sources.catalog({},bound)
    assert native.ws._read(native.path('inputs-parent'))['request']==record['request']


def test_unsupported_model_never_receives_a_screenshot_or_falls_back_to_text(tmp_path):
    _,native,_,first,profile=scene(tmp_path,image_support=False)
    record,request=prepare(native,profile,[first])
    assert request is None and record['status']=='UNAVAILABLE'
    assert '截图' in record['error']['message'] and not native.tasks
    assert record['request']['input_attachments'][0]['image_sha256']==first['image_sha256']


def test_schema_hash_region_and_metadata_limits_are_checked_before_any_model(tmp_path):
    _,native,_,first,profile=scene(tmp_path)
    for changes in [{'image_sha256':'0'*64},{'context_ref':{**REF,'revision':2}}]:
        with pytest.raises(Conflict):prepare(native,profile,[{**first,**changes}])
    for changes in [{'kind':'CURRENT_PAGE'},{'regions':[{'x':True,'y':0.,'width':1.,'height':1.}]},
                    {'regions':[{'x':.9,'y':0.,'width':.2,'height':1.}]},{'path':'D:\\private.png'}]:
        with pytest.raises(ValidationError):NativeInputAttachment.model_validate({**first,**changes})
    with pytest.raises(ValidationError):NativeQuestionInput.model_validate({'operation_id':'too-many','raw_text':'原话','input_attachments':[first]*21})
    assert not native.tasks and not list(native.ws._path('native-messages').glob('*.json'))


def test_frozen_http_preview_survives_secret_change_and_keeps_native_authority_denied(tmp_path):
    app,native,raw,first,_=scene(tmp_path)
    register_desktop_context_routes(app,None,tmp_path,service=native.context,workspace=native.ws)
    with TestClient(app) as client:
        assert client.post('/api/desktop-host/context/thumbnail',json={'context_ref':REF}).status_code==409
        headers={'X-EEAS-Desktop-Host':TOKEN}
        result=client.post('/api/desktop-host/context/thumbnail',json={'context_ref':REF},headers=headers)
        assert result.status_code==200,result.text
        value=result.json();assert value['image_sha256']==first['image_sha256'] and not value['automatic_input_authorized']
        with Image.open(BytesIO(base64.b64decode(value['data_url'].split(',',1)[1]))) as image:
            assert max(image.size)<=320
    # New secret cannot use the old HMAC as active desktop authority. Frozen
    # source data remains readable through its exact hash, without Cua reads.
    _,fresh,_=application(tmp_path,token='z'*64)
    with pytest.raises(Conflict):fresh.context.lookup(REF)
    saved=fresh.context.frozen_lookup(REF);assert saved['current'] is False
    assert saved['source_role']=='FROZEN_DATA_NO_CURRENT_NATIVE_AUTHORITY'
    image=saved['preview']['image_ref'];ref={key:image[key] for key in ('id','revision','sha256')}
    assert fresh.context.frozen_image(ref)==raw
    path=fresh.context._path('images',ref['id']+'.png');path.write_bytes(raw+b'changed')
    with pytest.raises(Conflict):fresh.context.frozen_image(ref)


def test_capability_parts_include_all_selected_sources_and_crops_with_no_base64_in_evidence(tmp_path):
    _,native,_,first,_=scene(tmp_path);two=second(native)
    normalized=NativeQuestionInput.model_validate({'operation_id':'parts','raw_text':'看圈选',
        'input_attachments':[first,two]}).request_dict()
    parts=native.input_sources.prompt_parts(normalized)
    images=[part for part in parts if part['type']=='file'];assert len(images)==2
    from packages.agent_sessions.capability_task import prompt_evidence
    evidence=prompt_evidence(parts)
    assert evidence['parts_sha256']==digest(parts) and 'data:image' not in canonical(evidence)
    with Image.open(BytesIO(base64.b64decode(images[1]['url'].split(',',1)[1]))) as image:
        assert image.size==(32,24) and image.getpixel((3,3))==(32,186,80)
    assert any('FROZEN_INPUT_IMAGE_DATA' in part.get('text','') for part in parts)

"""Actual pinned OpenCode receives an admitted bitmap; paid/company vision is not qualified."""
import asyncio
import base64
import copy
from pathlib import Path
from PIL import Image
import pytest
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.domain.tasks import digest
from packages.domain.store import Conflict
from test_opencode_planning import scene,transport,KEY
from discovery_native_wire import DiscoveryNativeWire


def image_request(scene,wire,*,supports=True):
    native,request,config=transport(scene,wire);config['supports_images']=supports
    native.provider_registry=OpenCodeProviderRegistry(lambda *_:copy.deepcopy(config),lambda _:KEY)
    request['provider_profile_ref']={**request['provider_profile_ref'],'sha256':digest(config)}
    Image.new('RGB',(100,80),'#abcdef').save(scene['folder']/'真实图片.png')
    value=scene['authority'].read(scene['pid'],scene['bound']['id'],'真实图片.png')
    bitmap={'kind':'LOCAL_FILE','file_source_ref':value['source_ref'],'sha256':value['source_ref']['sha256']}
    native.bitmap_authority=scene['client'].app.state.desktop_host.bitmap
    request['artifact_images']=[bitmap]
    return native,request,bitmap


def test_actual_original_cli_image_part_arrives_at_same_configured_provider(scene):
    wire=DiscoveryNativeWire().start()
    try:
        native,request,bitmap=image_request(scene,wire)
        result=asyncio.run(native.propose(request));assert result['status']=='RETURNED',result
        assert len(wire.requests)==1 and wire.requests[0]['auth_matches']
        images=[part for message in wire.requests[0]['body']['messages'] if isinstance(message.get('content'),list)
            for part in message['content'] if part.get('type')=='image_url']
        assert len(images)==1,wire.requests[0]['body']['messages']
        uri=images[0]['image_url']['url'];raw=base64.b64decode(uri.split(',',1)[1])
        from io import BytesIO
        with Image.open(BytesIO(raw)) as actual:
            assert actual.size==(100,80) and actual.convert('RGB').getpixel((10,10))==(171,205,239)
        with scene['service'].connect() as c:
            scope=c.execute('SELECT scope FROM agent_scopes WHERE execution_id=?',(result['execution_id'],)).fetchone()[0]
            assert bitmap['sha256'] in scope
            assert c.execute('SELECT count(*) FROM task_runs').fetchone()[0]==0
        assert result['usage']['rpc_attempts']==1
    finally:wire.close()


def test_text_only_or_changed_bitmap_rejected_before_model_or_native_dispatch(scene):
    wire=DiscoveryNativeWire().start()
    try:
        native,request,bitmap=image_request(scene,wire,supports=False)
        with pytest.raises(Conflict,match='image input'):asyncio.run(native.propose(request))
        assert not wire.requests and not native.active
        with scene['service'].connect() as c:assert c.execute('SELECT count(*) FROM agent_executions').fetchone()[0]==0
    finally:wire.close()

"""Pinned metadata-only model hook; reuses the owned loopback bridge transport."""
import hashlib
import json
from pathlib import Path
import re

from packages.adapters.opencode_native_review import regular, write_private
from packages.domain.store import Conflict
from packages.domain.tasks import digest

PLUGIN_PATH='toolchain/opencode-plugins/model-authority'
PLUGIN_FILES=('index.mjs','package.json','README.md','subscription-http.mjs')
PLUGIN_MANIFEST_SHA256='583cf34e062c624d0bf1bf998dc4d57f496f314b88a7bd7264cca9fb08a51a08'


def verify_bundle(source_root):
    root=Path(source_root)/PLUGIN_PATH;raw=regular(root/'manifest.json',16384)
    if hashlib.sha256(raw).hexdigest()!=PLUGIN_MANIFEST_SHA256:
        raise Conflict('MODEL_AUTHORITY_PLUGIN_INTEGRITY')
    value=json.loads(raw)
    if (value.get('id')!='eeas.model-authority' or value.get('schema')!='eeas.model-authority-plugin.v1'
            or value.get('upstream_commit')!='733b2c627e5aec39cb33cc22373bdce33053ef2c'
            or {p.name for p in root.iterdir()}!=set(PLUGIN_FILES)|{'manifest.json'}
            or [row['path'] for row in value.get('files',[])]!=list(PLUGIN_FILES)):
        raise Conflict('MODEL_AUTHORITY_PLUGIN_CLOSURE')
    for row in value['files']:
        body=regular(root/row['path'],65536)
        if len(body)!=row['bytes'] or hashlib.sha256(body).hexdigest()!=row['sha256']:
            raise Conflict('MODEL_AUTHORITY_PLUGIN_MEMBER_CHANGED')
    return {'entry':str((root/'index.mjs').resolve()),'manifest_sha256':PLUGIN_MANIFEST_SHA256,
        'files':[(root/name,PLUGIN_PATH+'/'+name) for name in (*PLUGIN_FILES,'manifest.json')]}


def prepare(source_root,runtime,*,owner_ref,generation,directory,bridge,subscription_http=None,call_protocol=None):
    bundle=verify_bundle(source_root);root=Path(runtime)/'model-authority'/f'{generation:08d}';root.mkdir(parents=True,exist_ok=True)
    scope={'schema':'eeas.model-authority-scope.v1','plugin_id':'eeas.model-authority',
        'plugin_manifest_sha256':bundle['manifest_sha256'],'owner_ref':owner_ref,'generation':generation,
        'directory':str(directory),'native_session_id':None,'broker_url':bridge.base_url,'broker_key':bridge.key}
    if subscription_http is not None:scope['subscription_http']=subscription_http
    if call_protocol is not None:
        if call_protocol!='ADAPTER_EXECUTION_V1':raise Conflict('MODEL_AUTHORITY_CALL_PROTOCOL_UNSUPPORTED')
        scope['call_protocol']=call_protocol
    value={**scope,'scope_sha256':digest(scope)};write_private(root/'scope.json',value)
    return {'entry':bundle['entry'],'config_path':str(root/'scope.json'),'ready_path':str(root/'ready.json'),
        'manifest_sha256':bundle['manifest_sha256'],'initial_scope_sha256':value['scope_sha256'],
        'scope_sha256':value['scope_sha256'],**({'subscription_http_mode':'INSTALLED_ORIGINAL_CODEX_HTTP_FALLBACK'} if subscription_http else {})}


def bind(prepared,native_session_id):
    if not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,100}',native_session_id):raise Conflict('MODEL_AUTHORITY_NATIVE_ID')
    path=Path(prepared['config_path']);value=json.loads(regular(path,16384,private=True))
    basis={k:v for k,v in value.items() if k!='scope_sha256'}
    if digest(basis)!=prepared['scope_sha256'] or value['scope_sha256']!=prepared['scope_sha256'] or value['native_session_id'] is not None:
        raise Conflict('MODEL_AUTHORITY_INITIAL_SCOPE_CHANGED')
    basis['native_session_id']=native_session_id;value={**basis,'scope_sha256':digest(basis)}
    write_private(path,value,replace=True);return {**prepared,'scope_sha256':value['scope_sha256']}


def ready(prepared):
    try:value=json.loads(regular(Path(prepared['ready_path']),4096,private=True))
    except FileNotFoundError:return False
    return value=={'schema':'eeas.model-authority-ready.v1','plugin_id':'eeas.model-authority',
        'plugin_manifest_sha256':prepared['manifest_sha256'],'initial_scope_sha256':prepared['initial_scope_sha256'],**({'subscription_http':prepared['subscription_http_mode']} if prepared.get('subscription_http_mode') else {})}

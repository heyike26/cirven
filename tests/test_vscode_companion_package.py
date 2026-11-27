"""Standard package/fixture qualification only: no VS Code or Windows launch."""
import importlib.util
import hashlib
import json
from pathlib import Path
import posixpath
import re
import shutil
import zipfile
from xml.etree import ElementTree as ET

import pytest


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "vscode-companion"
# Reviewed package scope, independent of the producer's allowlist. The added
# native-conversation client is required by both extension and controller.
EXPECTED_FILES = {
    "work-input-wire.cjs",
    "work-delivery-wire.cjs", "work-delivery-core.cjs", "work-deliveries.cjs", "native-export-wire.cjs", "native-export-core.cjs", "native-exports.cjs", "work-maintenance-wire.cjs", "work-maintenance-core.cjs", "work-maintenance.cjs",
    "package.json", "product.json", "package.nls.json", "package.nls.zh-cn.json",
    "extension.cjs", "client.cjs", "controller.cjs", "native-conversation.cjs",
    "session-diff.cjs", "session-tool.cjs", "test-report-wire.cjs", "command-test-reports.cjs", "work-requirement-wire.cjs", "work-requirements.cjs", "work-case-wire.cjs", "work-case-core.cjs", "work-cases.cjs", "work-source-wire.cjs", "work-source-core.cjs", "work-sources.cjs", "work-retest-wire.cjs", "work-retest-core.cjs", "work-retests.cjs", "session-child.cjs", "editor-change.cjs", "editor-files.cjs", "editor-observer.cjs", "editor-capture.cjs", "editor-observer-recovery.cjs", "native-editor.cjs", "context-recovery.cjs", "native-context-recovery.cjs", "canonical-conversations.cjs", "connection-configuration.cjs", "protocol.json", "README.md", "README.en.md", "LICENSE",
    "media/view.js", "media/view.css", "media/vendor/marked.js",
    "media/vendor/MARKED-LICENSE", "media/vendor/marked-provenance.json", "media/eeas.svg",
    "i18n/index.cjs", "i18n/locales.json", "i18n/common.json", "i18n/vscode.json",
    "media/brand/icon.svg", "media/brand/icon-monochrome.svg", "media/brand/icon-assets.json",
    "media/brand/icon-16.png", "media/brand/icon-24.png", "media/brand/icon-32.png",
    "media/brand/icon-256.png", "media/brand/tray-32.png", "media/brand/app-256.png",
    "media/theme/command-light.svg", "media/theme/command-dark.svg",
}
EXPECTED_MEMBERS = {"extension/" + name for name in EXPECTED_FILES} | {
    "extension.vsixmanifest", "[Content_Types].xml",
}


def packager():
    spec = importlib.util.spec_from_file_location("eeas_vsix_packager", PLUGIN / "scripts/package_vsix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_actual_vsix_standard_structure_and_source_identity(tmp_path):
    module = packager()
    out = tmp_path / "companion.vsix"
    result = module.package(PLUGIN, out)
    assert len(module.FILES) == len(set(module.FILES))
    assert set(module.FILES) == EXPECTED_FILES
    assert result["entries"] == len(EXPECTED_MEMBERS)
    assert result["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert result["validation"].endswith("NOT_VSCODE_RUNTIME")
    with zipfile.ZipFile(out) as archive:
        assert len(archive.namelist()) == len(EXPECTED_MEMBERS)
        assert set(archive.namelist()) == EXPECTED_MEMBERS
        assert archive.testzip() is None
        for name in module.FILES:
            assert archive.read("extension/" + name) == (PLUGIN / name).read_bytes()
        canonical = ROOT / 'packages/branding'
        assert archive.read('extension/product.json') == (canonical / 'product.json').read_bytes()
        assert archive.read('extension/media/eeas.svg') == (canonical / 'icon.svg').read_bytes()
        for name in module.BRAND_FILES:
            assert archive.read('extension/media/brand/' + name) == (canonical / name).read_bytes()
        manifest = json.loads(archive.read("extension/package.json"))
        assert manifest['displayName'] == json.loads((canonical / 'product.json').read_bytes())['display_name']
        assert manifest["extensionKind"] == ["ui"]
        assert manifest["main"] == "./extension.cjs"
        assert not manifest.get("dependencies")
        assert set(manifest['contributes']['viewsContainers']) == {'secondarySidebar'}
        assert manifest['contributes']['viewsContainers']['secondarySidebar'][0]['icon'] == 'media/brand/icon-monochrome.svg'
        assert manifest['icon'] == 'media/brand/app-256.png'
        assert manifest['engines']['vscode'] == '^1.106.0'
        assert manifest['contributes']['menus']['editor/title'][0]['command'] == 'eeasCompanion.showChat'
        command = next(item for item in manifest['contributes']['commands']
                       if item['command'] == 'eeasCompanion.showChat')
        assert command['icon'] == {'light': 'media/theme/command-light.svg',
                                   'dark': 'media/theme/command-dark.svg'}
        mono = ET.fromstring(archive.read('extension/media/brand/icon-monochrome.svg'))
        geometry = lambda svg: [(element.tag, {k: v for k, v in element.attrib.items() if k != 'fill'})
                                for element in svg.iter()]
        for theme, foreground in [('light', '#424242'), ('dark', '#C5C5C5')]:
            svg = ET.fromstring(archive.read('extension/' + command['icon'][theme]))
            assert geometry(svg) == geometry(mono)
            assert {element.attrib['fill'] for element in svg.iter() if 'fill' in element.attrib} == {foreground}
        assert manifest['description'] == '%extension.description%'
        default_nls = json.loads(archive.read('extension/package.nls.json'))
        chinese_nls = json.loads(archive.read('extension/package.nls.zh-cn.json'))
        metadata = ET.fromstring(archive.read('extension.vsixmanifest'))
        ns = '{' + module.NS + '}'
        assert metadata.find(ns + 'Metadata/' + ns + 'Identity').attrib == {
            'Language': 'en-US', 'Id': manifest['name'],
            'Version': manifest['version'], 'Publisher': manifest['publisher'],
        }
        assert manifest['license'] == 'MIT'
        assert metadata.find(ns + 'Metadata/' + ns + 'License').text == 'extension/LICENSE'
        assert hashlib.sha256(archive.read('extension/LICENSE')).hexdigest() == (
            '0a618215858524f1cb07d772c44320c12bbe27fa9c89affda24245f4bc2e8039')
        for asset in metadata.find(ns + 'Assets'):
            assert asset.attrib['Path'] in EXPECTED_MEMBERS
        types_ns = '{http://schemas.openxmlformats.org/package/2006/content-types}'
        types = ET.fromstring(archive.read('[Content_Types].xml'))
        overrides = {item.attrib['PartName']: item.attrib['ContentType']
                     for item in types.findall(types_ns + 'Override')}
        assert overrides['/extension/LICENSE'] == 'text/plain'
        assert overrides['/extension/media/vendor/MARKED-LICENSE'] == 'text/plain'
        for name in EXPECTED_FILES:
            if not name.endswith('.cjs'):
                continue
            requires = re.findall(r'''\brequire\(['"]([^'"]+)['"]\)''',
                                  archive.read('extension/' + name).decode('utf-8'))
            for dependency in requires:
                if dependency.startswith('.'):
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(name), dependency))
                    assert 'extension/' + target in EXPECTED_MEMBERS, (name, dependency)
                else:
                    assert dependency == 'vscode' or dependency.startswith('node:'), (name, dependency)
            if name in ('extension.cjs', 'controller.cjs'):
                assert './native-conversation.cjs' in requires
        icons = [item for item in metadata.find('{' + module.NS + '}Assets')
                 if item.attrib['Type'] == 'Microsoft.VisualStudio.Services.Icons.Default']
        assert len(icons) == 1 and icons[0].attrib['Path'] == 'extension/' + manifest['icon']
        assert metadata.find('{' + module.NS + '}Metadata/{' + module.NS + '}Description').text == default_nls['extension.description']
        assert default_nls['command.connect'] != chinese_nls['command.connect']
        for name in ('index.cjs', 'locales.json', 'common.json', 'vscode.json'):
            assert archive.read('extension/i18n/' + name) == (ROOT / 'packages/i18n' / name).read_bytes()

        assert manifest["capabilities"]["untrustedWorkspaces"]["supported"] == "limited"


def test_actual_vsix_reproducible_and_no_overwrite(tmp_path):
    module = packager()
    one, two = tmp_path / "one.vsix", tmp_path / "two.vsix"
    module.package(PLUGIN, one)
    module.package(PLUGIN, two)
    assert one.read_bytes() == two.read_bytes()
    with pytest.raises(ValueError, match="preserve"):
        module.package(PLUGIN, one)


def test_package_missing_or_symlink_input_rejected(tmp_path):
    module = packager()
    candidate = tmp_path / "source"
    shutil.copytree(PLUGIN, candidate)
    (candidate / "media/view.js").unlink()
    with pytest.raises(ValueError, match="Missing"):
        module.package(candidate, tmp_path / "missing.vsix")
    (candidate / "media/view.js").symlink_to(candidate / "package.json")
    with pytest.raises(ValueError, match="symbolic"):
        module.package(candidate, tmp_path / "symlink.vsix")


def test_protocol_locked_to_main_bridge_not_general_transport():
    protocol = json.loads((PLUGIN / "protocol.json").read_text())
    assert protocol["protocol"] == "eeas.ide-companion.v1"
    assert protocol["host"] == "127.0.0.1"
    assert protocol["rpc"]["automatic_post_replay"] is False
    assert "session.operation" in protocol["read_only_methods"]
    assert "ARBITRARY_COMMAND" in protocol["disallowed"]
    schemas = protocol['method_contracts']
    for method in ('native.conversation.get', 'native.file.read', 'native.rework.preview'):
        assert method in protocol['read_only_methods']
        assert schemas[method]['scope_required'] == ['project_id', 'conversation_id']
        assert schemas[method]['read_only'] is True
    for method in ('session.child.diff', 'session.child.diff.file'):
        assert method in protocol['read_only_methods']
        assert schemas[method]['scope_required'] == ['project_id', 'session_ref', 'generation']
        assert schemas[method]['current_disk_compared'] is False
        assert schemas[method]['automatic_apply'] is False
        assert {'child_ref', 'workspace_ref'} <= set(schemas[method]['payload_required'])
    for method in ('session.diff.prepare', 'session.diff.review.operation'):
        assert method in protocol['read_only_methods']
        assert schemas[method]['workspace_writes'] == 0
    assert 'session.diff.review' not in protocol['read_only_methods']
    assert schemas['session.diff.review']['workspace_writes'] == 0
    for method in ('session.editor.get', 'session.editor.control.operation'):
        assert method in protocol['read_only_methods']
    for method in ('session.editor.reserve', 'session.editor.control'):
        assert method not in protocol['read_only_methods']
        assert schemas[method]['editor_client_id'] == 'INJECTED_FROM_ORIGINAL_MAIN_PAIRING_CONTEXT_NOT_WEBVIEW_PAYLOAD'
        assert schemas[method]['idle_or_timeout_does_not_release'] is True
    for method in ('session.editor.file.prepare', 'session.editor.file.operation'):
        assert method in protocol['read_only_methods'] and schemas[method]['read_only'] is True
        assert schemas[method]['actor'] == 'MAIN_PAIRED_CLIENT_ID_ONLY'
    file_action = schemas['session.editor.file.command']
    assert 'session.editor.file.command' not in protocol['read_only_methods']
    assert file_action['writer'] == 'ORIGINAL_WINDOWS_EXCLUSIVE_PARENT_LEAF_HANDLES'
    assert file_action['automatic_file_replay'] is False and file_action['native_text_undo'] is False
    assert file_action['metadata_recovery_version'] == 1
    assert file_action['metadata_material'] == 'ORIGINAL_PRIVATE_ACTION_SCOPED_RECORD_NOT_WEBVIEW_INPUT'
    assert set(protocol['methods']) == set(schemas)
    assert len(protocol['methods']) == len(set(protocol['methods']))
    for method in ('native.feedback.record', 'native.rework.apply'):
        assert method not in protocol['read_only_methods']
        assert schemas[method]['scope_required'] == ['project_id', 'conversation_id']
    assert {'plan_ref', 'expected_current_sources', 'user_approved'} <= set(
        schemas['native.rework.apply']['payload_required'])
    manifest = json.loads((PLUGIN / "package.json").read_text())
    assert all(v["scope"] == "application" for v in manifest["contributes"]["configuration"]["properties"].values())


def test_cloned_package_rejects_drifted_brand_asset_without_overwriting_source(tmp_path):
    module = packager()
    candidate = tmp_path / 'copied-plugin'
    shutil.copytree(PLUGIN, candidate)
    path = candidate / 'product.json'
    original = path.read_bytes()
    name = json.loads((ROOT / 'packages' / 'branding' / 'product.json').read_text())['display_name'].encode()
    tampered = original.replace(name, b'Other product')
    assert tampered != original
    path.write_bytes(tampered)
    with pytest.raises(ValueError, match='brand asset mismatch'):
        module.package(candidate, tmp_path / 'unbound-brand.vsix')
    assert path.read_bytes() == tampered
    assert not (tmp_path / 'unbound-brand.vsix').exists()


def test_cloned_package_retains_tampered_png_as_evidence_and_rejects_it(tmp_path):
    module = packager()
    candidate = tmp_path / 'copied-plugin'
    shutil.copytree(PLUGIN, candidate)
    image = candidate / 'media/brand/app-256.png'
    tampered = image.read_bytes() + b'fixture drift'
    image.write_bytes(tampered)
    with pytest.raises(ValueError, match='brand asset mismatch'):
        module.package(candidate, tmp_path / 'bad-icon.vsix')
    assert image.read_bytes() == tampered
    assert not (tmp_path / 'bad-icon.vsix').exists()


@pytest.mark.parametrize('asset', ['marked.js', 'MARKED-LICENSE', 'marked-provenance.json'])
def test_pinned_offline_markdown_source_license_and_tamper_rejection(tmp_path, asset):
    module = packager()
    vendor = PLUGIN / 'media/vendor'
    host = ROOT / 'apps/desktop-host/vendor'
    if (vendor / 'marked.js').read_bytes() != (host / 'marked.cjs').read_bytes():
        pytest.fail('host and view must parse the same pinned Markdown AST')
    if (vendor / 'MARKED-LICENSE').read_bytes() != (host / 'MARKED-LICENSE').read_bytes():
        pytest.fail('bundled upstream license differs')
    candidate = tmp_path / 'copied-plugin'
    shutil.copytree(PLUGIN, candidate)
    target = candidate / 'media/vendor' / asset
    if asset.endswith('.json'):
        provenance = json.loads(target.read_bytes())
        provenance['license'] = 'UNREVIEWED'
        target.write_text(json.dumps(provenance), encoding='utf-8')
    else:
        target.write_bytes(b'changed pinned asset')
    with pytest.raises(ValueError, match='Pinned Markdown (asset|provenance) mismatch'):
        module.package(candidate, tmp_path / 'tampered.vsix')


@pytest.mark.parametrize('change', ['missing-client', 'extra', 'duplicate', 'identity'])
def test_inspect_rejects_member_or_identity_drift(tmp_path, change):
    module = packager()
    original, candidate = tmp_path / 'original.vsix', tmp_path / 'changed.vsix'
    module.package(PLUGIN, original)
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(candidate, 'x') as target:
        for name in source.namelist():
            if change == 'missing-client' and name == 'extension/native-conversation.cjs':
                continue
            data = source.read(name)
            if change == 'identity' and name == 'extension.vsixmanifest':
                metadata = ET.fromstring(data)
                metadata.find('{' + module.NS + '}Metadata/{' + module.NS + '}Identity').set(
                    'Version', '0.0.0-invalid')
                data = ET.tostring(metadata)
            target.writestr(name, data)
        if change == 'extra':
            target.writestr('extension/unreviewed.cjs', b'fixture')
        elif change == 'duplicate':
            with pytest.warns(UserWarning, match='Duplicate name'):
                target.writestr('extension/native-conversation.cjs', b'fixture')
    with pytest.raises(ValueError, match='identity mismatch' if change == 'identity'
                       else 'Unexpected/duplicate package entries'):
        module.inspect(candidate)


def test_inspect_rejects_corrupt_member_crc(tmp_path):
    module = packager()
    original, candidate = tmp_path / 'original.vsix', tmp_path / 'bad-crc.vsix'
    module.package(PLUGIN, original)
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(candidate, 'x') as target:
        content = source.read('extension/native-conversation.cjs')
        for name in source.namelist():
            target.writestr(name, source.read(name))
    damaged = bytearray(candidate.read_bytes())
    assert damaged.count(content) == 1
    damaged[damaged.index(content)] ^= 1
    candidate.write_bytes(damaged)
    with pytest.raises(ValueError, match='Invalid archive CRC'):
        module.inspect(candidate)


# AUTHORED_NOT_RUN. Contract tests do not establish actual native editor coverage.
def test_shared_editor_observations_are_not_permission_or_atomic_write_grants():
    protocol=json.loads((PLUGIN/'protocol.json').read_text())
    names={'session.editor.observer.prepare','session.editor.observer.command','session.editor.observer.operation','session.editor.observer.get'}
    assert names <= set(protocol['methods'])
    assert names-{'session.editor.observer.command'} <= set(protocol['read_only_methods'])
    assert 'session.editor.observer.command' not in protocol['read_only_methods']
    for name in names:
        contract=protocol['method_contracts'][name]
        assert contract['actor']=='MAIN_PAIRED_CLIENT_ID_ONLY'
        assert contract['workspace_writes']==contract['model_calls']==contract['native_posts']==0
        assert contract['atomic_editor_write_guarantee'] is False
        assert contract['editor_buffer_text']=='NEVER_TRANSMITTED'
    assert protocol['method_contracts']['session.editor.observer.command']['permission_granted'] is False
    assert protocol['method_contracts']['session.editor.observer.operation']['observation_time_scope']=='AT_ORIGINAL_COMMAND_ONLY'


# AUTHORED_NOT_RUN. Observer events keep their authoritative API receipt.
def test_observer_producer_is_packaged_without_a_second_model_or_human_journal():
    assert 'editor-observer.cjs' in EXPECTED_FILES
    protocol=json.loads((PLUGIN/'protocol.json').read_text())
    action=protocol['method_contracts']['session.editor.observer.command']
    assert action['mutation_journal']=='ORIGINAL_API_ATOMIC_SQL_RECEIPT_NOT_DUPLICATE_MAIN_HUMAN_REQUEST_ROW'
    assert action['recovery']=='ORIGINAL_EXTENSION_PROCESS_BIRTH_ENDED_BEFORE_DIFFERENT_PROCESS_RETIREMENT_METADATA_ONLY_NATIVE_HANDOFF_UNCHANGED'


# AUTHORED_NOT_RUN. A group capture is neither a model action nor a write permit.
def test_capture_cohort_contract_has_frozen_membership_and_private_notifications():
    protocol=json.loads((PLUGIN/'protocol.json').read_text())
    for method in ('session.editor.capture.request','session.editor.capture.operation','session.editor.capture.status'):
        c=protocol['method_contracts'][method]
        assert c['actor']=='MAIN_PAIRED_CLIENT_ID_ONLY' and c['permission_granted'] is False
        assert c['workspace_writes']==c['model_calls']==c['native_posts']==0
        assert c['atomic_editor_write_guarantee'] is False
    assert 'session.editor.capture.request' not in protocol['read_only_methods']
    assert {'session.editor.capture.operation','session.editor.capture.status'} <= set(protocol['read_only_methods'])
    assert protocol['method_contracts']['session.editor.capture.request']['limits']['members']==16
    assert protocol['method_contracts']['session.editor.capture.status']['status_scope']=='CURRENT_METADATA_SNAPSHOT_NOT_EXECUTION_LEASE'


# AUTHORED_NOT_RUN. The event transport cannot carry consent or document text.
def test_capture_stream_is_ticket_scoped_metadata_with_no_idle_heartbeat():
    protocol=json.loads((PLUGIN/'protocol.json').read_text())
    stream=protocol['method_contracts']['session.editor.capture.listen']
    assert stream['transport']=='AUTHENTICATED_SSE_GET' and stream['rpc'] is False
    assert stream['permission_granted'] is False and stream['idle_heartbeat'] is False
    assert stream['limits']['streams']==16 and stream['limits']['notices']==128
    assert set(stream['payload_fields'])=={'type','instance_id','window_id','observer_id','capture_ref','expires_at_ms'}
    assert stream['target_conversation_or_permission_content'] is False


# AUTHORED_NOT_RUN. The waiting channel is observation, not task execution.
def test_capture_consumer_and_event_woken_wait_are_bounded_and_packaged():
    assert 'editor-capture.cjs' in EXPECTED_FILES
    protocol=json.loads((PLUGIN/'protocol.json').read_text())
    c=protocol['method_contracts']['session.editor.capture.wait']
    assert c['read_only'] is True and c['permission_granted'] is False
    assert c['workspace_writes']==c['model_calls']==c['native_posts']==0
    assert c['limits']=={'waiters':8,'wait_ms':8000,'status_reads':32}
    assert c['wake']=='ORIGINAL_VERIFIED_OBSERVER_API_RECEIPT_NOT_IDLE_POLL'


def test_paired_observer_recovery_retains_original_unknown_and_is_distinct_from_native_file_control():
    """AUTHORED_NOT_RUN: package contract; runtime/native qualification separate."""
    import json
    base = ROOT / 'plugins/vscode-companion'
    protocol = json.loads((base / 'protocol.json').read_text())
    for suffix in ['prepare', 'command', 'operation']:
        name = 'session.editor.observer.recovery.' + suffix
        contract = protocol['method_contracts'][name]
        assert contract['model_calls'] == contract['native_posts'] == contract['workspace_writes'] == 0
        assert contract['native_file_handoff'] == 'UNCHANGED_NOT_RETIRED_BY_METADATA_RECOVERY'
        assert (name in protocol['read_only_methods']) is (suffix != 'command')
    assert 'editor-observer-recovery.cjs' in EXPECTED_FILES

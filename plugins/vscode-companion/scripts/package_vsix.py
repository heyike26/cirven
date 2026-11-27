#!/usr/bin/env python3
"""Dependency-free standard VSIX writer; never launches or installs VS Code.

Structure checked against microsoft/vscode-vsce/src/package.ts (2026-10-15),
toVsixManifest/toContentTypes. This is not a vsce/Marketplace certification.
"""
import argparse
import hashlib
import json
from pathlib import Path
from xml.sax.saxutils import escape
from xml.etree import ElementTree as ET
import zipfile

THEME_ICONS = {"media/theme/command-light.svg": "#424242", "media/theme/command-dark.svg": "#C5C5C5"}
BRAND_FILES = ('icon.svg', 'icon-monochrome.svg', 'icon-assets.json', 'icon-16.png', 'icon-24.png', 'icon-32.png', 'icon-256.png', 'tray-32.png', 'app-256.png')
FILES = ("work-input-wire.cjs", "package.json", "product.json", "package.nls.json", "package.nls.zh-cn.json", "extension.cjs", "client.cjs", "controller.cjs", "native-conversation.cjs", "session-diff.cjs", "session-tool.cjs", "test-report-wire.cjs", "command-test-reports.cjs", "work-requirement-wire.cjs", "work-requirements.cjs", "work-case-wire.cjs", "work-case-core.cjs", "work-cases.cjs", "work-source-wire.cjs", "work-source-core.cjs", "work-sources.cjs", "work-retest-wire.cjs", "work-retest-core.cjs", "work-delivery-wire.cjs", "work-delivery-core.cjs", "work-deliveries.cjs", "native-export-wire.cjs", "native-export-core.cjs", "native-exports.cjs", "work-maintenance-wire.cjs", "work-maintenance-core.cjs", "work-maintenance.cjs", "work-retests.cjs", "session-child.cjs", "editor-change.cjs", "editor-files.cjs", "editor-observer.cjs", "editor-capture.cjs", "editor-observer-recovery.cjs", "native-editor.cjs", "context-recovery.cjs", "native-context-recovery.cjs", "canonical-conversations.cjs", "connection-configuration.cjs", "protocol.json", "README.md", "README.en.md", "LICENSE", "media/view.js", "media/view.css", "media/vendor/marked.js", "media/vendor/MARKED-LICENSE", "media/vendor/marked-provenance.json", "media/eeas.svg", "i18n/index.cjs", "i18n/locales.json", "i18n/common.json", "i18n/vscode.json", *('media/brand/' + name for name in BRAND_FILES), *THEME_ICONS)
NS = "http://schemas.microsoft.com/developer/vsx-schema/2011"


def _validate_markdown(files):
    expected = {'marked.js': '7a1f8c5e7226b75ff16644bdb2c0130d2ae7371e7ea3106c2d6dac77ab0ff7b6', 'MARKED-LICENSE': '8e3a3f82f59a60958f56ca08f445647c32a4733dc7ca6c2c46f6eb898471ab9c'}
    for name, sha in expected.items():
        if hashlib.sha256(files[name]).hexdigest() != sha:
            raise ValueError('Pinned Markdown asset mismatch: ' + name)
    provenance = json.loads(files['marked-provenance.json'])
    if (provenance.get('version') != '18.0.7' or provenance.get('license') != 'MIT'
            or provenance.get('source_sha256') != expected['marked.js']
            or provenance.get('license_sha256') != expected['MARKED-LICENSE']):
        raise ValueError('Pinned Markdown provenance mismatch')


def _theme_icon_assets(monochrome):
    """Same canonical alpha/geometry; explicit command colors survive external SVG use."""
    if b'currentColor' not in monochrome:
        raise ValueError('Canonical monochrome icon must inherit foreground')
    return {name: monochrome.replace(b'currentColor', color.encode('ascii'))
            for name, color in THEME_ICONS.items()}


def _validate_theme_icons(files):
    expected = _theme_icon_assets(files['media/brand/icon-monochrome.svg'])
    if any(files.get(name) != data for name, data in expected.items()):
        raise ValueError('Canonical command theme icon mismatch')


def _sync_branding(root):
    """Compile visible metadata from canonical local sources, never IDs."""
    plugin = Path(__file__).resolve().parents[1]
    brand = plugin.parents[1] / "packages/branding"
    source = {"product.json": brand / "product.json", "media/eeas.svg": brand / "icon.svg"}
    source.update({'media/brand/' + name: brand / name for name in BRAND_FILES})
    source.update({'i18n/' + name: brand.parent / 'i18n' / name
                   for name in ('index.cjs', 'locales.json', 'common.json', 'vscode.json')})
    for name, path in source.items():
        target = root / name
        if (path.is_symlink() or not path.is_file() or path.stat().st_size > 262144
                or any(p.is_symlink() for p in (target, *target.parents))):
            raise ValueError("Missing or unsafe canonical brand asset")
        data = path.read_bytes()
        if root == plugin:
            if not target.is_file() or target.read_bytes() != data:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        elif not target.is_file() or target.read_bytes() != data:
            raise ValueError("Canonical brand asset mismatch: " + name)
    _validate_brand({name: (brand / name).read_bytes() for name in BRAND_FILES})
    for name, data in _theme_icon_assets((brand / 'icon-monochrome.svg').read_bytes()).items():
        target = root / name
        if any(p.is_symlink() for p in (target, *target.parents)):
            raise ValueError('Unsafe command theme icon')
        if root == plugin:
            if not target.is_file() or target.read_bytes() != data:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        elif not target.is_file() or target.read_bytes() != data:
            raise ValueError('Canonical command theme icon mismatch: ' + name)
    product = json.loads(source['product.json'].read_bytes())
    manifest_path = root / 'package.json'
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError('Missing or symbolic package source: package.json')
    manifest = json.loads(manifest_path.read_bytes())
    expected = json.loads(json.dumps(manifest))
    expected['displayName'] = product['display_name']
    containers = expected['contributes']['viewsContainers']
    if set(containers) != {'secondarySidebar'} or len(containers['secondarySidebar']) != 1:
        raise ValueError('The companion has one secondary-sidebar container')
    for items in containers.values():
        for item in items:
            item['title'] = product['display_name']
            item['icon'] = 'media/brand/icon-monochrome.svg'
    expected['icon'] = 'media/brand/app-256.png'
    for items in expected['contributes']['views'].values():
        for item in items:
            item['name'] = product['display_name']
    for item in expected['contributes']['commands']:
        if item['command'] == 'eeasCompanion.showChat':
            item['icon'] = {'light': 'media/theme/command-light.svg', 'dark': 'media/theme/command-dark.svg'}
        if item['title'].startswith('%') and item['title'].endswith('%'):
            item['category'] = product['display_name']
        else:
            item['title'] = product['display_name'] + ':' + item['title'].split(':', 1)[-1]
    expected['contributes']['configuration']['title'] = product['display_name']
    if manifest != expected:
        if root != plugin:
            raise ValueError('Canonical visible product metadata mismatch')
        manifest_path.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    messages = json.loads(source['i18n/vscode.json'].read_bytes())['messages']
    common = json.loads(source['i18n/common.json'].read_bytes())['messages']
    for locale, filename in [('en', 'package.nls.json'), ('zh-CN', 'package.nls.zh-cn.json')]:
        value = {key: pair[locale] for key, pair in messages.items()
                 if key.startswith(('command.', 'setting.')) or key in ('extension.description', 'language.description')}
        value.update({key: common[key][locale] for key in ('language.system', 'language.zh-CN', 'language.en')})
        target = root / filename
        if target.is_symlink():
            raise ValueError('Unsafe localization asset')
        if root == plugin:
            data = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
            if not target.is_file() or target.read_bytes() != data:
                target.write_bytes(data)
        elif not target.is_file() or json.loads(target.read_bytes()) != value:
            raise ValueError('Canonical localization asset mismatch')
    return product


def _validate_brand(files):
    """Validate the complete local asset closure, without a runtime renderer."""
    value = json.loads(files['icon-assets.json'])
    if value.get('schema_version') != 1:
        raise ValueError('Unknown brand asset schema')
    records = [value['source'], value['monochrome'], *value['assets'].values()]
    if {record['path'] for record in records} != set(BRAND_FILES) - {'icon-assets.json'}:
        raise ValueError('Incomplete brand asset closure')
    for record in records:
        data = files[record['path']]
        if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('Brand asset integrity mismatch')
    return value


def package(root, destination):
    root, destination = Path(root).resolve(), Path(destination)
    if destination.exists():
        raise ValueError("Output exists; preserve the previous package")
    _sync_branding(root)
    manifest = json.loads((root / "package.json").read_text(encoding="utf-8"))
    default_nls = json.loads((root / "package.nls.json").read_text(encoding="utf-8"))
    # As upstream vsce's patchNLS, metadata resolves the default catalog;
    # package.json retains its placeholders for the IDE's display language.
    description = manifest['description']
    if description.startswith('%') and description.endswith('%'):
        description = default_nls[description[1:-1]]
    x = lambda v: escape(str(v), {'"': '&quot;'})
    vsix = f'''<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="{NS}"><Metadata>
<Identity Language="en-US" Id="{x(manifest['name'])}" Version="{x(manifest['version'])}" Publisher="{x(manifest['publisher'])}"/>
<DisplayName>{x(manifest['displayName'])}</DisplayName><Description xml:space="preserve">{x(description)}</Description>
<Tags>eeas,companion,local</Tags><Categories>Other</Categories><GalleryFlags>Preview</GalleryFlags>
<Properties><Property Id="Microsoft.VisualStudio.Code.Engine" Value="{x(manifest['engines']['vscode'])}"/><Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="ui"/><Property Id="Microsoft.VisualStudio.Code.PreRelease" Value="true"/><Property Id="Microsoft.VisualStudio.Code.ExecutesCode" Value="true"/></Properties><License>extension/LICENSE</License></Metadata>
<Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation><Dependencies/>
<Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/><Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/README.md" Addressable="true"/><Asset Type="Microsoft.VisualStudio.Services.Content.License" Path="extension/LICENSE" Addressable="true"/><Asset Type="Microsoft.VisualStudio.Services.Icons.Default" Path="extension/{x(manifest['icon'])}" Addressable="true"/></Assets></PackageManifest>'''
    types = '''<?xml version="1.0" encoding="utf-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="json" ContentType="application/json"/><Default Extension="vsixmanifest" ContentType="text/xml"/><Default Extension="cjs" ContentType="application/javascript"/><Default Extension="js" ContentType="application/javascript"/><Default Extension="css" ContentType="text/css"/><Default Extension="svg" ContentType="image/svg+xml"/><Default Extension="png" ContentType="image/png"/><Default Extension="md" ContentType="text/markdown"/><Override PartName="/extension/LICENSE" ContentType="text/plain"/><Override PartName="/extension/media/vendor/MARKED-LICENSE" ContentType="text/plain"/></Types>'''
    entries = {"extension.vsixmanifest": vsix.encode(), "[Content_Types].xml": types.encode()}
    for relative in FILES:
        source = root / relative
        if source.is_symlink() or not source.is_file():
            raise ValueError("Missing or symbolic package source: " + relative)
        entries["extension/" + relative] = source.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 15, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return inspect(destination)


def inspect(filename):
    with zipfile.ZipFile(filename) as archive:
        if archive.testzip() is not None:
            raise ValueError("Invalid archive CRC")
        names = archive.namelist()
        expected = {"extension.vsixmanifest", "[Content_Types].xml"} | {"extension/" + n for n in FILES}
        if set(names) != expected or len(names) != len(expected):
            raise ValueError("Unexpected/duplicate package entries")
        if any(n.startswith("/") or ".." in Path(n).parts or "\\" in n for n in names):
            raise ValueError("Unsafe archive path")
        metadata = ET.fromstring(archive.read("extension.vsixmanifest"))
        ET.fromstring(archive.read("[Content_Types].xml"))
        identity = metadata.find("{" + NS + "}Metadata/{" + NS + "}Identity")
        manifest = json.loads(archive.read("extension/package.json"))
        if identity is None or identity.attrib["Id"] != manifest["name"] or identity.attrib["Version"] != manifest["version"] or identity.attrib["Publisher"] != manifest["publisher"]:
            raise ValueError("Package identity mismatch")
        if manifest.get("dependencies") or manifest["extensionKind"] != ["ui"]:
            raise ValueError("Unexpected runtime dependency or remote host")
        protocol = json.loads(archive.read("extension/protocol.json"))
        if protocol["protocol"] != "eeas.ide-companion.v1":
            raise ValueError("Unknown protocol")
        _validate_markdown({name: archive.read('extension/media/vendor/' + name) for name in ('marked.js', 'MARKED-LICENSE', 'marked-provenance.json')})
        product = json.loads(archive.read('extension/product.json'))
        _validate_brand({name: archive.read('extension/media/brand/' + name) for name in BRAND_FILES})
        _validate_theme_icons({name: archive.read('extension/' + name)
                               for name in ('media/brand/icon-monochrome.svg', *THEME_ICONS)})
        command = next(item for item in manifest['contributes']['commands']
                       if item['command'] == 'eeasCompanion.showChat')
        if command.get('icon') != {'light': 'media/theme/command-light.svg', 'dark': 'media/theme/command-dark.svg'}:
            raise ValueError('Explicit command light/dark icons required')
        containers = manifest['contributes']['viewsContainers']
        if (set(containers) != {'secondarySidebar'} or len(containers['secondarySidebar']) != 1
                or containers['secondarySidebar'][0].get('icon') != 'media/brand/icon-monochrome.svg'):
            raise ValueError('Container alpha-mask icon must remain a single SVG path')
        if manifest['displayName'] != product['display_name']:
            raise ValueError('Canonical visible brand mismatch')
        return {"filename": str(filename), "sha256": hashlib.sha256(Path(filename).read_bytes()).hexdigest(), "bytes": Path(filename).stat().st_size, "entries": len(names), "protocol_sha256": hashlib.sha256(archive.read("extension/protocol.json")).hexdigest(),
                "product_sha256": hashlib.sha256(archive.read('extension/product.json')).hexdigest(), "icon_sha256": hashlib.sha256(archive.read('extension/media/eeas.svg')).hexdigest(), "icon_assets_sha256": hashlib.sha256(archive.read('extension/media/brand/icon-assets.json')).hexdigest(),
                "validation": "STANDARD_STRUCTURE_CRC_XML_IDENTITY_ONLY_NOT_VSCODE_RUNTIME"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--out", type=Path)
    parser.add_argument("--inspect", type=Path)
    args = parser.parse_args()
    if bool(args.out) == bool(args.inspect):
        parser.error("choose --out or --inspect")
    print(json.dumps(inspect(args.inspect) if args.inspect else package(args.root, args.out), ensure_ascii=False, indent=2))

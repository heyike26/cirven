"""Actual SVG build/PNG evidence; no desktop rendering or Windows claim."""
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import hashlib
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / 'packages/branding'


def builder():
    spec = importlib.util.spec_from_file_location('cirven_build_icons', BRAND / 'build_icons.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_checked_in_assets_are_bound_to_exact_svg_and_have_same_monochrome_geometry():
    value = json.loads((BRAND / 'icon-assets.json').read_bytes())
    assert value['schema_version'] == 1
    for item in [value['source'], value['monochrome'], *value['assets'].values()]:
        data = (BRAND / item['path']).read_bytes()
        assert len(data) == item['bytes']
        assert hashlib.sha256(data).hexdigest() == item['sha256']
        if item['path'].endswith('.png'):
            assert data[:8] == b'\x89PNG\r\n\x1a\n'
            assert struct.unpack('>II', data[16:24]) == (item['width'], item['height'])
    color, mono = (ET.fromstring((BRAND / name).read_bytes()) for name in ['icon.svg', 'icon-monochrome.svg'])
    geometry = lambda tree: [(node.tag, {k: v for k, v in node.attrib.items() if k not in ('fill', 'stroke')}) for node in tree.iter() if node.tag.endswith(('path', 'circle', 'ellipse', 'rect', 'line', 'polygon', 'polyline'))]
    assert color.attrib['viewBox'] == mono.attrib['viewBox']
    assert geometry(color) == geometry(mono)
    assert 'linearGradient' not in (BRAND / 'icon-monochrome.svg').read_text()


def test_actual_renderer_accepts_non_circle_paths_and_preserves_drift_before_regeneration(tmp_path):
    pytest.importorskip('cairosvg', reason='Optional development renderer; never a desktop runtime dependency')
    source = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><path d="M5 5h22v22H5z M11 11v10h10V11z" fill="currentColor" fill-rule="evenodd"/></svg>'
    (tmp_path / 'icon.svg').write_bytes(source)
    module = builder()
    first = module.build(tmp_path)
    assert first['source']['sha256'] == hashlib.sha256(source).hexdigest()
    png = (tmp_path / 'icon-16.png').read_bytes()
    assert module.build(tmp_path) == first
    assert (tmp_path / 'icon-16.png').read_bytes() == png
    tampered = png + b'preserve fixture edit'
    (tmp_path / 'icon-16.png').write_bytes(tampered)
    with pytest.raises(ValueError, match='drift'):
        module.build(tmp_path)
    assert (tmp_path / 'icon-16.png').read_bytes() == tampered
    assert json.loads((tmp_path / 'icon-assets.json').read_bytes()) == first


def test_vector_source_refuses_network_fonts_and_scripts_before_optional_renderer_import():
    module = builder()
    for body in ['<script>alert(1)</script>', '<image href="https://example.test/pixel"/>', '<text>font</text>', '<path fill="url(https://example.test/paint)"/>']:
        data = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">' + body + '</svg>').encode()
        with pytest.raises(ValueError):
            module.source_tree(data)


def test_monochrome_preserves_geometry_while_inheriting_the_ide_foreground():
    module = builder()
    data = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" color="#000"><defs><linearGradient id="a"><stop stop-color="red"/></linearGradient></defs><path d="M0 0h8v8z" fill="url(#a)"/><path d="M9 0h8v8z"/></svg>'
    tree, _ = module.source_tree(data)
    mono = ET.fromstring(module.monochrome(tree))
    assert mono.attrib['fill'] == 'currentColor'
    assert all('color' not in node.attrib for node in mono.iter())
    assert [node.attrib['d'] for node in mono.iter() if node.tag.endswith('path')] == ['M0 0h8v8z', 'M9 0h8v8z']
    assert not any(node.tag.endswith(('linearGradient', 'stop')) for node in mono.iter())

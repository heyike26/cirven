"""Original diagrams/images assembled in memory; no enterprise files or upstream samples."""
from dataclasses import replace
import hashlib
import io
import json
import socket
import stat
import struct
import warnings
import zipfile
import zlib
from xml.sax.saxutils import escape

import pytest

from packages.domain.diagram_import import DiagramImportError, DiagramLimits, parse_diagram


def original_png():
    def chunk(kind, payload):
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00\xff")) + chunk(b"IEND", b""))


def original_jpeg():
    # Complete baseline grayscale JPEG: one neutral 1x1 MCU, original quantization
    # and one-code DC/AC Huffman tables; no third-party image bytes.
    def segment(marker, payload):
        return bytes([255, marker]) + struct.pack(">H", len(payload) + 2) + payload
    return (b"\xff\xd8" + segment(0xDB, b"\x00" + b"\x01" * 64)
            + segment(0xC0, b"\x08\x00\x01\x00\x01\x01\x01\x11\x00")
            + segment(0xC4, b"\x00\x01" + b"\x00" * 15 + b"\x00")
            + segment(0xC4, b"\x10\x01" + b"\x00" * 15 + b"\x00")
            + segment(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x3f\xff\xd9")


def archive(parts, *, compression=zipfile.ZIP_STORED):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as output:
        for name, data in parts:
            output.writestr(name, data.encode() if isinstance(data, str) else data)
    return stream.getvalue()


def xml_content(title="原创根", tail="尾部完整", *, with_images=False):
    image = '<xhtml:img xhtml:src="xap:resources/原创.png"/>' if with_images else ""
    return ('<xmap-content xmlns="urn:xmind:xmap:xmlns:content:2.0" xmlns:xhtml="http://www.w3.org/1999/xhtml">'
            '<sheet id="sheet-a"><title>同名页</title><topic id="root"><title>' + escape(title) + '</title>' + image
            + '<notes><plain>原创备注\n第二行</plain></notes><children><topics type="attached">'
            '<topic id="child"><title>嵌套内容</title><children><topics type="detached">'
            '<topic id="last"><title>' + escape(tail) + '</title></topic></topics></children></topic>'
            '</topics></children></topic><relationships><relationship id="rel-a" end1="root" end2="last">'
            '<title>原创关系</title></relationship></relationships></sheet>'
            '<sheet id="sheet-b"><title>同名页</title><topic id="root"><title>另一页不串图</title></topic></sheet></xmap-content>')


def xmind_json():
    return [{"id": "sheet-a", "title": "同名页", "rootTopic": {
        "id": "root", "title": "JSON原创根", "notes": {"plain": {"content": "完整普通备注"},
        "html": {"content": {"paragraphs": [{"spans": [{"text": "富文本末尾"}]}]}}},
        "labels": ["原创标签"], "image": {"src": "xap:resources/原创.png"},
        "href": "https://example.invalid/never-fetch", "children": {
            role: [{"id": role, "title": "原创" + role,
                    "image": {"src": "resources/原创.jpg"}}] for role in ["attached", "detached", "summary", "callout"]},
        "boundaries": [{"title": "仅原包中的边界"}]},
        "relationships": [{"id": "relation", "end1Id": "root", "end2Id": "callout", "title": "JSON关系"}]},
        {"id": "sheet-b", "title": "同名页", "rootTopic": {"id": "root", "title": "第二张图末尾"}}]


def visio_parts():
    main = 'xmlns="http://schemas.microsoft.com/office/visio/2012/main"'
    relns = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    def rels(items):
        return '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(
            '<Relationship Id="' + rid + '" Type="http://schemas.microsoft.com/visio/2010/relationships/' + kind
            + '" Target="' + target + '"/>' for rid, kind, target in items) + '</Relationships>'
    content = ('<PageContents ' + main + ' ' + relns + '><Shapes><Shape ID="1" NameU="Group"><Text>原创组</Text>'
               '<Shapes><Shape ID="2"><Text>起点<cp IX="0"/>含格式尾部</Text>'
               '<ForeignData ForeignType="Bitmap"><Rel r:id="image"/></ForeignData></Shape></Shapes></Shape>'
               '<Shape ID="3"><Text>终点</Text></Shape><Shape ID="4"><Text>连接器</Text><Cell N="BeginX" V="1" F="GUARD(1)"/></Shape>'
               '</Shapes><Connects><Connect FromSheet="4" FromCell="BeginX" ToSheet="2" ToCell="PinX"/>'
               '<Connect FromSheet="4" FromCell="EndX" ToSheet="3" ToCell="PinX"/></Connects></PageContents>')
    return [('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="png" ContentType="image/png"/></Types>'),
            ('_rels/.rels', rels([('doc', 'document', 'visio/document.xml')])),
            ('visio/document.xml', '<VisioDocument ' + main + '/>'),
            ('visio/_rels/document.xml.rels', rels([('pages', 'pages', 'pages/pages.xml')])),
            ('visio/pages/pages.xml', '<Pages ' + main + ' ' + relns + '><Page ID="0" Name="同名页面"><Rel r:id="p1"/></Page><Page ID="1" Name="同名页面"><Rel r:id="p2"/></Page></Pages>'),
            ('visio/pages/_rels/pages.xml.rels', rels([('p1', 'page', 'page1.xml'), ('p2', 'page', 'page2.xml')])),
            ('visio/pages/page1.xml', content), ('visio/pages/page2.xml', content.replace('起点', '另一页起点').replace('连接器', '另一页连接器末尾')),
            ('visio/pages/_rels/page1.xml.rels', rels([('image', 'image', '../media/原创.png')])),
            ('visio/pages/_rels/page2.xml.rels', rels([('image', 'image', '../media/原创.jpg')])),
            ('visio/media/原创.png', original_png()), ('visio/media/原创.jpg', original_jpeg())]


def test_xmind_xml_all_sheets_nested_relationships_and_long_tail_anchors():
    long_text = "任务" * 35001 + "确切的七万字尾端"
    parsed = parse_diagram("中文 空格 任务书.xmind", archive([('content.xml', xml_content(tail=long_text))], compression=zipfile.ZIP_DEFLATED))
    assert parsed.format == "xmind-xml"
    assert len(parsed.outline["sheets"]) == 2
    nodes = parsed.graph["nodes"]
    assert len(nodes) == 4 and len({node["id"] for node in nodes}) == 4
    last = next(node for node in nodes if node["source"]["node_id"] == "last")
    assert last["text"] == long_text
    lines = parsed.content.splitlines()
    excerpt = "\n".join(lines[last["line_start"] - 1:last["line_end"]])
    assert long_text in excerpt and last["source"]["part"] == "content.xml"
    relationships = [edge for edge in parsed.graph["edges"] if edge["kind"] == "relationship"]
    assert relationships[0]["source"] == nodes[0]["id"] and relationships[0]["target"] == last["id"]
    assert "另一页不串图" in parsed.content
    assert parsed.outline["sheets"][0]["topics"][0]["children"][0]["children"][0]["id"] == last["id"]


def test_xmind_json_groups_plain_rich_notes_assets_and_scope_isolation(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Diagram import must not use network")
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    raw = archive([('content.json', json.dumps(xmind_json(), ensure_ascii=False)),
                   ('resources/原创.png', original_png()), ('resources/原创.jpg', original_jpeg())])
    parsed = parse_diagram("原创.xmind", raw)
    assert parsed.format == "xmind-json" and len(parsed.graph["nodes"]) == 6
    assert {node["role"] for node in parsed.graph["nodes"]} == {"root", "attached", "detached", "summary", "callout"}
    assert "富文本末尾" in parsed.content and "完整普通备注" in parsed.content and "第二张图末尾" in parsed.content
    assert {a["media_type"] for a in parsed.assets} == {"image/png", "image/jpeg"}
    for asset in parsed.assets:
        assert hashlib.sha256(asset["raw_bytes"]).hexdigest() == asset["sha256"]
        assert asset["byte_length"] == len(asset["raw_bytes"])
        assert asset["source_anchors"]
    assert any("boundaries" in warning for warning in parsed.warnings)
    assert any("never followed" in warning for warning in parsed.warnings)


def test_xml_xhtml_img_asset_binding_is_exact_original_bytes():
    parsed = parse_diagram("原创.xmind", archive([('content.xml', xml_content(with_images=True)), ('resources/原创.png', original_png())]))
    assert parsed.assets[0]["raw_bytes"] == original_png()
    assert parsed.graph["nodes"][0]["assets"] == ['resources/原创.png']


def test_visio_real_opc_pages_nested_shapes_connectors_images_and_same_name_ids():
    parsed = parse_diagram("多页面.vsdx", archive(visio_parts()))
    assert parsed.format == "visio-vsdx" and len(parsed.outline["pages"]) == 2
    assert len(parsed.graph["nodes"]) == 8
    assert len({node["id"] for node in parsed.graph["nodes"]}) == 8
    assert "起点含格式尾部" in parsed.content and "另一页连接器末尾" in parsed.content
    connectors = [edge for edge in parsed.graph["edges"] if edge["kind"] == "connector"]
    assert len(connectors) == 2
    for edge in connectors:
        assert edge["source"].split('/')[0] == edge["target"].split('/')[0] == edge["connector_id"].split('/')[0]
        assert edge["origin"]["part"] in {'visio/pages/page1.xml', 'visio/pages/page2.xml'}
    assert len([edge for edge in parsed.graph["edges"] if edge["kind"] == "attachment"]) == 4
    assert parsed.assets[0]["source_anchors"][0]["part"] == "visio/pages/page1.xml"
    assert parsed.assets[1]["source_anchors"][0]["part"] == "visio/pages/page2.xml"
    assert parsed.assets[1]["raw_bytes"] == original_jpeg()
    assert parsed.outline["pages"][0]["shapes"][0]["children"]


def test_missing_assets_relationships_master_and_orphan_pages_warn_instead_of_silent_loss():
    parts = visio_parts()
    parts = [(name, data.replace('<Shape ID="3">', '<Shape ID="3" Master="9">').replace('ToSheet="3"', 'ToSheet="99"') if name.endswith('page1.xml') else data) for name, data in parts if name != 'visio/media/原创.png']
    parts.append(('visio/pages/orphan.xml', '<PageContents xmlns="http://schemas.microsoft.com/office/visio/2012/main"/>'))
    parsed = parse_diagram("原创.vsdx", archive(parts))
    assert any('Missing/unsupported image' in warning for warning in parsed.warnings)
    assert any('inherited master' in warning for warning in parsed.warnings)
    assert any('Unresolved connector endpoint' in warning for warning in parsed.warnings)
    assert any('Unreferenced page' in warning for warning in parsed.warnings)
    assert len(parsed.outline['pages']) == 2


@pytest.mark.parametrize("names", [["../escape"], ["C:/drive"], ["x\\y"], ["resources/CON.png"],
    ["content.xml", "content.xml"], ["resources/A", "resources/a"], ["Resources/a", "resources/b"],
    ["resources/é.png", "resources/e\u0301.png"], ["folder", "folder/file"], ["file. "]])
def test_zip_paths_duplicates_case_unicode_aliases_and_file_directory_conflicts_fail(names):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        raw = archive([(name, "original unsafe fixture") for name in names])
    with pytest.raises(DiagramImportError):
        parse_diagram("原创.xmind", raw)


def test_zip_symlink_is_rejected_without_filesystem_write(tmp_path):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as output:
        info = zipfile.ZipInfo('link')
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(info, str(tmp_path / 'outside'))
    with pytest.raises(DiagramImportError, match='Symlink'):
        parse_diagram('原创.xmind', stream.getvalue())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("encoding", ['utf-8', 'utf-16', 'utf-16-le', 'utf-16-be'])
def test_dtd_external_entity_rejected_in_all_supported_xml_encodings(encoding, monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('must not fetch external entity'))
    payload = '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY stolen SYSTEM "https://example.invalid/private">]><xmap-content>&stolen;</xmap-content>'
    with pytest.raises(DiagramImportError, match='DTD/entity'):
        parse_diagram('原创.xmind', archive([('content.xml', payload.encode(encoding))]))


def test_unused_xml_entity_also_rejected_and_standard_entities_remain_supported():
    good = xml_content(title='正常 & < > 文本')
    parsed = parse_diagram('原创.xmind', archive([('content.xml', good)]))
    assert '正常 & < > 文本' in parsed.content
    with pytest.raises(DiagramImportError, match='DTD/entity'):
        parse_diagram('原创.xmind', archive([('content.xml', good), ('unused.xml', '<!DOCTYPE x><x/>')]))


@pytest.mark.parametrize("part,payload", [('content.json', '{"sheets":[],"sheets":[]}'),
    ('content.json', '[' * 80 + '0' + ']' * 80), ('content.json', '[NaN]'),
    ('content.xml', '<x>' * 80 + '</x>' * 80), ('content.xml', '<xmap-content>')])
def test_json_duplicates_deep_json_xml_and_malformed_parts_fail(part, payload):
    with pytest.raises(DiagramImportError):
        parse_diagram('原创.xmind', archive([(part, payload)]))


def test_archive_size_expansion_entries_nodes_content_and_bomb_limits():
    raw = archive([('content.xml', xml_content())])
    for limits in [replace(DiagramLimits(), max_archive_bytes=10), replace(DiagramLimits(), max_member_bytes=20),
                   replace(DiagramLimits(), max_expanded_bytes=20), replace(DiagramLimits(), max_nodes=5),
                   replace(DiagramLimits(), max_content_bytes=100)]:
        with pytest.raises(DiagramImportError):
            parse_diagram('原创.xmind', raw, limits=limits)
    with pytest.raises(DiagramImportError, match='entry limit'):
        parse_diagram('原创.xmind', archive([('a', ''), ('b', '')]), limits=replace(DiagramLimits(), max_entries=1))
    with pytest.raises(DiagramImportError, match='compression ratio'):
        parse_diagram('原创.xmind', archive([('bomb', b'A' * (2 * 1024 * 1024))], compression=zipfile.ZIP_DEFLATED))


def test_opc_internal_target_traversal_fail_external_target_is_never_followed(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('no network'))
    parts = visio_parts()
    bad = [(name, data.replace('visio/document.xml', '../../../../private.xml') if name == '_rels/.rels' else data) for name, data in parts]
    with pytest.raises(DiagramImportError, match='escapes'):
        parse_diagram('原创.vsdx', archive(bad))
    external = [(name, data.replace('Target="../media/原创.png"', 'TargetMode="External" Target="https://example.invalid/no-fetch"') if name == 'visio/pages/_rels/page1.xml.rels' else data) for name, data in parts]
    parsed = parse_diagram('原创.vsdx', archive(external))
    assert any('External OPC' in warning for warning in parsed.warnings)


def test_missing_page_content_and_duplicate_shape_fail_not_partial_success():
    parts = visio_parts()
    with pytest.raises(DiagramImportError, match='required XML'):
        parse_diagram('原创.vsdx', archive([(name, data) for name, data in parts if name != 'visio/pages/page2.xml']))
    duplicate = [(name, data.replace('<Shape ID="3">', '<Shape ID="2">') if name == 'visio/pages/page1.xml' else data) for name, data in parts]
    with pytest.raises(DiagramImportError, match='Duplicate Visio shape'):
        parse_diagram('原创.vsdx', archive(duplicate))


@pytest.mark.parametrize('name', ['original.vsd', 'original.vsdm', 'original.vdx'])
def test_legacy_and_macro_visio_explicit_export_instruction(name):
    with pytest.raises(DiagramImportError, match=r'export.*\.vsdx'):
        parse_diagram(name, b'Original unsupported binary fixture')


def test_embedded_macro_and_invalid_image_cannot_be_reported_as_supported():
    with pytest.raises(DiagramImportError, match='Macro'):
        parse_diagram('原创.vsdx', archive([*visio_parts(), ('visio/vbaProject.bin', b'original inert fixture')]))
    with pytest.raises(DiagramImportError, match='Image signature'):
        parse_diagram('原创.xmind', archive([('content.xml', xml_content()), ('resources/bad.png', b'not an image')]))


def test_missing_actual_node_id_cannot_be_replaced_by_invented_source_anchor():
    with pytest.raises(DiagramImportError, match='anchors cannot be invented'):
        parse_diagram('原创.xmind', archive([('content.xml', xml_content().replace('id="last"', ''))]))


def test_original_asset_fixtures_are_complete_decodable_png_jpeg():
    # Pillow is the Root-owned media validation dependency, not a parser dependency.
    from PIL import Image
    with Image.open(io.BytesIO(original_png())) as png:
        png.load()
        assert png.format == 'PNG' and png.size == (1, 1) and png.getpixel((0, 0)) == (255, 0, 0, 255)
    with Image.open(io.BytesIO(original_jpeg())) as jpeg:
        jpeg.load()
        assert jpeg.format == 'JPEG' and jpeg.size == (1, 1) and jpeg.getpixel((0, 0)) == 128


def test_unicode_line_breaks_preserve_following_node_line_anchor():
    sheets = [{'id': 'sheet', 'rootTopic': {'id': 'root', 'title': '首行\u2028次行\u0085三行\r末行',
               'children': {'attached': [{'id': 'child', 'title': '后续节点精确末端'}]}}}]
    parsed = parse_diagram('原创.xmind', archive([('content.json', json.dumps(sheets, ensure_ascii=False))]))
    lines = parsed.content.splitlines()
    child = parsed.graph['nodes'][1]
    assert lines[child['line_start'] - 1].startswith('[source ')
    assert lines[child['line_end'] - 1] == '后续节点精确末端'
    assert '首行\n次行\n三行\n末行' in parsed.content

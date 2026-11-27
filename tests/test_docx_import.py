"""Original OOXML packages, complete generated images, and bounded attack fixtures."""
from dataclasses import replace
import hashlib
import socket
import zipfile
from xml.sax.saxutils import escape

import pytest

from packages.domain.diagram_import import DiagramImportError, DiagramLimits, parse_diagram
from packages.domain.docx_import import DocxImportError, parse_docx
from test_diagram_import import archive, original_png, original_jpeg, xml_content

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS = f'xmlns:w="{W}" xmlns:r="{R}" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office"'


def rels(items):
    return '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(
        f'<Relationship Id="{rid}" Type="{R}/{kind}" Target="{target}"{extra}/>'
        for rid, kind, target, extra in items) + '</Relationships>'


def paragraph(text):
    return '<w:p><w:r><w:t xml:space="preserve">' + escape(text) + '</w:t></w:r></w:p>'


def word_parts(long_text='原创长正文', *, chart=False):
    long_runs = ''.join('<w:r><w:t xml:space="preserve">' + escape(long_text[index:index + 1024]) + '</w:t></w:r>'
                        for index in range(0, len(long_text), 1024))
    picture = '<w:r><w:drawing><a:blip r:embed="pic"/></w:drawing></w:r>'
    chart_xml = '<w:r><w:drawing><c:chart r:id="chart"/></w:drawing></w:r>' if chart else ''
    first = ('<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:sectPr><w:headerReference r:id="header"/></w:sectPr></w:pPr>'
             '<w:r><w:t>原</w:t></w:r><w:r><w:t>创标题</w:t></w:r>' + picture + chart_xml + '</w:p>')
    refs = '<w:p><w:r><w:t>正文末端精确锚点</w:t><w:footnoteReference w:id="7"/><w:endnoteReference w:id="8"/></w:r><w:commentRangeStart w:id="3"/><w:r><w:commentReference w:id="3"/></w:r></w:p>'
    table = ('<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/><w:vMerge w:val="restart"/></w:tcPr>'
             + paragraph('合并单元格原创') + '</w:tc><w:tc>' + paragraph('第二列尾部') + '</w:tc></w:tr>'
             '<w:tr><w:tc>' + paragraph('第二行首格') + '</w:tc><w:tc>' + paragraph('表格精确尾端') + '</w:tc></w:tr></w:tbl>')
    main = '<w:document ' + NS + '><w:body>' + first + '<w:p>' + long_runs + '</w:p>' + table + refs + '<w:sectPr><w:headerReference r:id="header"/><w:footerReference r:id="footer"/></w:sectPr></w:body></w:document>'
    relationships = [('pic', 'image', 'media/原创.png', ''), ('header', 'header', 'headers/header.xml', ''),
        ('footer', 'footer', 'footers/footer.xml', ''), ('fn', 'footnotes', 'notes/footnotes.xml', ''),
        ('en', 'endnotes', 'notes/endnotes.xml', ''), ('cm', 'comments', 'comments.xml', '')]
    if chart:
        relationships.append(('chart', 'chart', 'charts/chart1.xml', ''))
    parts = [('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/task.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'),
        ('_rels/.rels', rels([('main', 'officeDocument', 'word/task.xml', '')])),
        ('word/task.xml', main), ('word/_rels/task.xml.rels', rels(relationships)),
        ('word/headers/header.xml', '<w:hdr ' + NS + '>' + paragraph('原创页眉末端') + '</w:hdr>'),
        ('word/footers/footer.xml', '<w:ftr ' + NS + '>' + paragraph('原创页脚末端') + '</w:ftr>'),
        ('word/notes/footnotes.xml', '<w:footnotes ' + NS + '><w:footnote w:id="-1" w:type="separator"/><w:footnote w:id="7">' + paragraph('脚注完整末端') + '<w:p><w:r><w:drawing><a:blip r:embed="noteImage"/></w:drawing></w:r></w:p></w:footnote></w:footnotes>'),
        ('word/notes/_rels/footnotes.xml.rels', rels([('noteImage', 'image', '../media/脚注.jpg', '')])),
        ('word/notes/endnotes.xml', '<w:endnotes ' + NS + '><w:endnote w:id="8">' + paragraph('尾注完整末端') + '</w:endnote></w:endnotes>'),
        ('word/comments.xml', '<w:comments ' + NS + '><w:comment w:id="3" w:author="Original Fixture">' + paragraph('批注完整末端') + '</w:comment></w:comments>'),
        ('word/media/原创.png', original_png()), ('word/media/脚注.jpg', original_jpeg())]
    if chart:
        parts.append(('word/charts/chart1.xml', '<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart><c:title><c:v>原创chart缓存标题</c:v></c:title></c:chart></c:chartSpace>'))
    return parts


def test_complete_word_70k_tail_runs_table_headers_notes_comments_asset_relationships():
    long_text = '长任务书' * 18001 + '七万中文最终尾部证明'
    raw = archive(word_parts(long_text), compression=zipfile.ZIP_DEFLATED)
    parsed = parse_docx('中文 空格 任务书.docx', raw)
    assert parsed.format == 'docx' and long_text in parsed.content
    assert '原创标题' in parsed.content
    for text in ['表格精确尾端', '正文末端精确锚点', '原创页眉末端', '原创页脚末端', '脚注完整末端', '尾注完整末端', '批注完整末端']:
        assert text in parsed.content
    nodes = parsed.graph['nodes']
    long_node = next(node for node in nodes if node['text'] == long_text)
    assert long_node['source']['part'] == 'word/task.xml'
    assert long_node['source']['node_id'] == '/document[1]/body[1]/p[2]'
    assert long_node['source']['anchor_kind'] == 'xml-local-path'
    lines = parsed.content.splitlines()
    assert long_text in '\n'.join(lines[long_node['line_start'] - 1:long_node['line_end']])
    for node in nodes:
        assert lines[node['line_start'] - 1].startswith('[source ')
    assert {node['kind'] for node in nodes} >= {'table', 'table-row', 'table-cell', 'footnote', 'endnote', 'comment'}
    merged = next(node for node in nodes if node.get('grid_span') == '2')
    assert merged['vertical_merge'] == 'restart'
    assert len([edge for edge in parsed.graph['edges'] if edge['kind'] == 'section-binding']) == 3
    assert len([edge for edge in parsed.graph['edges'] if edge['kind'] == 'note-reference']) == 3
    assert len(parsed.assets) == 2
    for asset in parsed.assets:
        assert hashlib.sha256(asset['raw_bytes']).hexdigest() == asset['sha256']
        assert asset['byte_length'] == len(asset['raw_bytes']) and asset['source_anchors']
    jpg = next(asset for asset in parsed.assets if asset['media_type'] == 'image/jpeg')
    assert jpg['raw_bytes'] == original_jpeg() and jpg['source_anchors'][0]['part'] == 'word/notes/footnotes.xml'


def test_chart_is_explicit_in_text_warning_and_source_not_faked_as_rendered_image():
    parsed = parse_docx('原创.docx', archive(word_parts(chart=True)))
    assert '[UNSUPPORTED chart: word/charts/chart1.xml]' in parsed.content
    assert any('Unsupported chart' in warning for warning in parsed.warnings)
    node = next(node for node in parsed.graph['nodes'] if node.get('unsupported_parts'))
    assert node['unsupported_parts'][0]['member_path'] == 'word/charts/chart1.xml'
    assert node['source']['part'] == 'word/task.xml'
    assert len(parsed.assets) == 2 and '原创chart缓存标题' not in parsed.content


def test_external_links_and_images_are_never_requested_and_unbound_parts_not_merged(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('no network'))
    parts = word_parts()
    parts = [(name, value.replace('Target="media/原创.png"', 'TargetMode="External" Target="https://example.invalid/no-fetch"') if name == 'word/_rels/task.xml.rels' else value) for name, value in parts]
    parts.append(('word/header-orphan.xml', '<w:hdr ' + NS + '>' + paragraph('不可冒充正文的旧页眉') + '</w:hdr>'))
    parsed = parse_docx('原创.docx', archive(parts))
    assert any('External OPC' in warning for warning in parsed.warnings)
    assert any('Missing/external/non-image' in warning for warning in parsed.warnings)
    assert any('Unreferenced text' in warning for warning in parsed.warnings)
    assert '不可冒充正文的旧页眉' not in parsed.content


def test_tracked_text_fields_tabs_breaks_text_boxes_and_body_tail_remain_explicit():
    parts = word_parts()
    special = ('<w:p><w:r><w:t>首段</w:t><w:tab/><w:t>制表后</w:t><w:br/><w:t>换行后</w:t></w:r>'
               '<w:ins w:id="1"><w:r><w:t>插入文字</w:t></w:r></w:ins><w:del w:id="2"><w:r><w:delText>删除文字</w:delText></w:r></w:del>'
               '<w:r><w:instrText>HYPERLINK SECRET_FIELD</w:instrText><w:t>字段缓存结果</w:t></w:r>'
               '<w:r><w:drawing><v:textbox><w:txbxContent>' + paragraph('框内完整末端') + '</w:txbxContent></v:textbox></w:drawing></w:r></w:p>')
    parts = [(name, value.replace('<w:body>', '<w:body>' + special) if name == 'word/task.xml' else value) for name, value in parts]
    parsed = parse_docx('原创.docx', archive(parts))
    assert '首段\t制表后\n换行后' in parsed.content
    assert '[tracked-ins]插入文字[/tracked-ins]' in parsed.content
    assert '[tracked-del]删除文字[/tracked-del]' in parsed.content
    assert 'SECRET_FIELD' not in parsed.content and '字段缓存结果' in parsed.content
    assert parsed.content.count('框内完整末端') == 1 and '正文末端精确锚点' in parsed.content
    assert any('Tracked text' in warning for warning in parsed.warnings)


@pytest.mark.parametrize('method', [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_shared_reader_rejects_nonstandard_compression_without_decompression(method):
    # Ordinary tiny archives: no malicious dictionary allocation or large fixture.
    with pytest.raises(DiagramImportError, match='compression method'):
        parse_diagram('原创.xmind', archive([('content.xml', xml_content())], compression=method))
    with pytest.raises(DocxImportError, match='compression method'):
        parse_docx('原创.docx', archive(word_parts(), compression=method))


@pytest.mark.parametrize('name', ['legacy.doc', 'macro.docm', 'template.dotx'])
def test_unsupported_word_requires_macro_free_docx_export(name):
    with pytest.raises(DocxImportError, match='export.*docx'):
        parse_docx(name, b'Original legacy fixture')


@pytest.mark.parametrize('malicious', ['../outside', 'word/CON.xml', 'word\\bad', 'word/A', 'word/a'])
def test_docx_shares_windows_path_guard(malicious):
    parts = word_parts()
    if malicious in {'word/A', 'word/a'}:
        parts.extend([('word/A', ''), ('word/a', '')])
    else:
        parts.append((malicious, 'original inert fixture'))
    with pytest.raises(DocxImportError):
        parse_docx('原创.docx', archive(parts))


def test_dtd_entities_missing_main_invalid_namespace_and_bad_xml_use_one_error_type():
    for part, value in [('word/hidden.xml', '<!DOCTYPE stolen><stolen/>'), ('word/hidden.xml', '<broken>')]:
        with pytest.raises(DocxImportError):
            parse_docx('原创.docx', archive([*word_parts(), (part, value)]))
    parts = word_parts()
    with pytest.raises(DocxImportError, match='required XML'):
        parse_docx('原创.docx', archive([(name, value) for name, value in parts if name != 'word/task.xml']))
    with pytest.raises(DocxImportError, match='namespace'):
        parse_docx('原创.docx', archive([(name, value.replace(W, 'urn:unsupported')) if name == 'word/task.xml' else (name, value) for name, value in parts]))


def test_macro_declared_and_embedded_macro_fail_even_with_docx_extension():
    parts = word_parts()
    with pytest.raises(DocxImportError, match='Macro'):
        parse_docx('原创.docx', archive([*parts, ('word/vbaProject.bin', b'original inert fixture')]))
    with pytest.raises(DocxImportError, match='Macro'):
        parse_docx('原创.docx', archive([(name, value.replace('wordprocessingml.document.main+xml', 'word.macroEnabled.main+xml')) if name == '[Content_Types].xml' else (name, value) for name, value in parts]))


def test_limits_error_instead_of_truncated_success():
    raw = archive(word_parts('长文' * 35001 + '最后完整文字'))
    for limits in [replace(DiagramLimits(), max_archive_bytes=100), replace(DiagramLimits(), max_content_bytes=100),
                   replace(DiagramLimits(), max_nodes=10), replace(DiagramLimits(), max_depth=2)]:
        with pytest.raises(DocxImportError):
            parse_docx('原创.docx', raw, limits=limits)


def test_math_simple_fields_symbols_tracked_blocks_and_unused_notes_are_explicit():
    extra = ('<w:p xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"><m:oMath><m:r><m:t>x</m:t></m:r><m:r><m:t>+1</m:t></m:r></m:oMath>'
             '<w:fldSimple w:instr="SUM(A1:A2)"><w:r><w:t>原缓存数值3</w:t></w:r></w:fldSimple><w:r><w:sym w:font="OriginalSymbols" w:char="F0AA"/></w:r></w:p>'
             '<w:ins w:id="9">' + paragraph('整段插入修订') + '</w:ins>')
    parts = [(name, value.replace('<w:body>', '<w:body>' + extra)) if name == 'word/task.xml' else (name, value)
             for name, value in word_parts()]
    parts = [(name, value.replace('</w:footnotes>', '<w:footnote w:id="99">' + paragraph('不该混入的未绑定脚注') + '</w:footnote></w:footnotes>'))
             if name == 'word/notes/footnotes.xml' else (name, value) for name, value in parts]
    parsed = parse_docx('原创.docx', archive(parts))
    assert '[UNSUPPORTED math structure; original tokens: x+1]' in parsed.content
    assert '原缓存数值3' in parsed.content and 'SUM(A1:A2)' not in parsed.content
    assert '[symbol font=OriginalSymbols code=F0AA]' in parsed.content
    assert '[tracked-block-ins]' in parsed.content and '整段插入修订' in parsed.content
    assert '不该混入的未绑定脚注' not in parsed.content
    assert any('Unreferenced note entry' in warning for warning in parsed.warnings)


def test_alternate_content_chooses_one_branch_in_blocks_and_textboxes():
    namespaces = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
    block = '<mc:AlternateContent ' + namespaces + '><mc:Choice Requires="a">' + paragraph('选择分支正文') + '</mc:Choice><mc:Fallback>' + paragraph('不能重复的fallback正文') + '</mc:Fallback></mc:AlternateContent>'
    box = '<w:p><w:r><w:drawing><mc:AlternateContent ' + namespaces + '><mc:Choice Requires="a"><v:textbox><w:txbxContent>' + paragraph('选择文本框') + '</w:txbxContent></v:textbox></mc:Choice><mc:Fallback><v:textbox><w:txbxContent>' + paragraph('不能重复的fallback文本框') + '</w:txbxContent></v:textbox></mc:Fallback></mc:AlternateContent></w:drawing></w:r></w:p>'
    parts = [(name, value.replace('<w:body>', '<w:body>' + block + box)) if name == 'word/task.xml' else (name, value)
             for name, value in word_parts()]
    parsed = parse_docx('原创.docx', archive(parts))
    assert parsed.content.count('选择分支正文') == parsed.content.count('选择文本框') == 1
    assert 'fallback正文' not in parsed.content and 'fallback文本框' not in parsed.content
    assert any('Fallback not merged' in warning for warning in parsed.warnings)

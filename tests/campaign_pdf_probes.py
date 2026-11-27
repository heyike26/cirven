"""Original-body PDF text/scan derivative through real importer; no default discovery."""
import io
import json
import struct
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject, TextStringObject

from campaign_probes import Evidence, ingest, load_binding, sha
from campaign_workflow_probes import source_lines
from campaign_format_probes import cmap_glyph, render


def stream(writer, raw):
    obj = DecodedStreamObject()
    obj.set_data(raw)
    return writer._add_object(obj)


def render_pdf_selection(selected):
    """Use actual bundled glyphs; never consult a machine or HOME font path."""
    text = ''.join(part['text'] for part in selected)
    default_raw = ImageFont.load_default(size=20).path.getvalue()
    if all(cmap_glyph(default_raw, ord(c)) for c in text):
        image, metadata = render(selected)
        return image, metadata, default_raw
    assets = Path(__file__).parent / 'fixtures/fonts/dejavu-sans-2.37'
    metadata = json.loads((assets / 'manifest.json').read_bytes())
    font_raw, legal = (assets / 'DejaVuSans.ttf').read_bytes(), (assets / 'LICENSE').read_bytes()
    assert sha(font_raw) == metadata['font_sha256'] and sha(legal) == metadata['license_sha256']
    unsupported = sorted({ord(c) for c in text if not cmap_glyph(font_raw, ord(c))})
    if unsupported:
        raise ValueError('Packaged font lacks selected Unicode glyphs; refusing silent replacement: ' +
                         ','.join('U+%04X' % point for point in unsupported))
    font = ImageFont.truetype(io.BytesIO(font_raw), size=20)
    rows = []
    for part in selected:
        row = ''
        for char in part['text']:
            if row and font.getlength(row + char) > 920:
                rows.append({'source_line': part['line_start'], 'text': row}); row = ''
            row += char
        rows.append({'source_line': part['line_start'], 'text': row})
    image = Image.new('RGB', (960, 48 + len(rows) * 30), 'white')
    draw = ImageDraw.Draw(image)
    for n, row in enumerate(rows):
        draw.text((20, 20 + n * 30), row['text'], font=font, fill='black')
    output = io.BytesIO();image.save(output, format='PNG')
    return output.getvalue(), {'font': 'Packaged unmodified DejaVu Sans 2.37', 'font_sha256': sha(font_raw),
        'font_license_sha256': sha(legal), 'font_manifest_sha256': sha((assets / 'manifest.json').read_bytes()),
        'font_cmap_coverage_checked': True, 'rows': rows, 'width': image.width, 'height': image.height,
        'transform': 'Strip physical newline; expand tabs; wrap exact characters without replacement',
        'system_font_lookup': False}, font_raw


def embedded_font(writer, text, font_raw):
    tables = {}
    for n in range(struct.unpack_from(">H", font_raw, 4)[0]):
        tag, _, offset, size = struct.unpack_from(">4sIII", font_raw, 12 + 16 * n)
        tables[tag.decode()] = font_raw[offset:offset + size]
    names = tables['name']
    name_count, name_strings = struct.unpack_from('>HH', names, 2)
    postscript = None
    for n in range(name_count):
        platform, _, _, name_id, length, offset = struct.unpack_from('>HHHHHH', names, 6 + 12 * n)
        if name_id == 6:
            postscript = names[name_strings + offset:name_strings + offset + length].decode('utf-16-be' if platform in (0, 3) else 'latin-1')
            break
    assert postscript and all(c.isascii() and c.isalnum() or c in '-_' for c in postscript)
    font_name = NameObject('/' + postscript)
    points = sorted(set(text))
    assert 0 < len(points) < 65535
    glyphs = [cmap_glyph(font_raw, ord(c)) for c in points]
    if not all(glyphs):
        raise ValueError("Bundled font lacks selected PDF glyph; no silent Unicode replacement")
    units = struct.unpack_from(">H", tables['head'], 18)[0]
    metric_count = struct.unpack_from(">H", tables['hhea'], 34)[0]
    scale = lambda value: round(value * 1000 / units)
    widths = [scale(struct.unpack_from('>H', tables['hmtx'], 4 * min(g, metric_count - 1))[0]) for g in glyphs]
    bounds = struct.unpack_from('>hhhh', tables['head'], 36)
    ascent, descent = struct.unpack_from('>hh', tables['hhea'], 4)
    descriptor = DictionaryObject({NameObject('/Type'): NameObject('/FontDescriptor'),
        NameObject('/FontName'): font_name, NameObject('/Flags'): NumberObject(32),
        NameObject('/FontBBox'): ArrayObject([NumberObject(scale(x)) for x in bounds]),
        NameObject('/ItalicAngle'): NumberObject(0), NameObject('/Ascent'): NumberObject(scale(ascent)),
        NameObject('/Descent'): NumberObject(scale(descent)), NameObject('/CapHeight'): NumberObject(scale(ascent)),
        NameObject('/StemV'): NumberObject(80), NameObject('/FontFile2'): stream(writer, font_raw)})
    cid_map = b'\0\0' + b''.join(struct.pack('>H', g) for g in glyphs)
    cid = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/CIDFontType2'),
        NameObject('/BaseFont'): font_name, NameObject('/FontDescriptor'): writer._add_object(descriptor),
        NameObject('/CIDSystemInfo'): DictionaryObject({NameObject('/Registry'): TextStringObject('Adobe'),
            NameObject('/Ordering'): TextStringObject('Identity'), NameObject('/Supplement'): NumberObject(0)}),
        NameObject('/CIDToGIDMap'): stream(writer, cid_map), NameObject('/DW'): NumberObject(1000),
        NameObject('/W'): ArrayObject([NumberObject(1), ArrayObject([NumberObject(w) for w in widths])])})
    mappings = ['<%04X> <%s>' % (n, char.encode('utf-16-be').hex()) for n, char in enumerate(points, 1)]
    cmap = ['/CIDInit /ProcSet findresource begin', '12 dict begin', 'begincmap',
            '/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def',
            '/CMapName /EEASOriginalUnicode def', '/CMapType 2 def', '1 begincodespacerange', '<0000> <FFFF>', 'endcodespacerange']
    for start in range(0, len(mappings), 100):
        part = mappings[start:start + 100]
        cmap.extend([str(len(part)) + ' beginbfchar', *part, 'endbfchar'])
    cmap.extend(['endcmap', 'CMapName currentdict /CMap defineresource pop', 'end', 'end'])
    obj = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type0'),
        NameObject('/BaseFont'): font_name, NameObject('/Encoding'): NameObject('/Identity-H'),
        NameObject('/DescendantFonts'): ArrayObject([writer._add_object(cid)]),
        NameObject('/ToUnicode'): stream(writer, ('\n'.join(cmap)).encode('ascii'))})
    return writer._add_object(obj), {char: n for n, char in enumerate(points, 1)}, sha(font_raw)


def make_pdf(selected):
    rendered, rendering, font_raw = render_pdf_selection(selected)
    image = Image.open(io.BytesIO(rendered)).convert('RGB')
    writer = PdfWriter()
    font, codes, font_sha = embedded_font(writer, ''.join(part['text'] for part in selected), font_raw)
    obj = DecodedStreamObject()
    obj.set_data(image.tobytes())
    obj.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
        NameObject('/Width'): NumberObject(image.width), NameObject('/Height'): NumberObject(image.height),
        NameObject('/ColorSpace'): NameObject('/DeviceRGB'), NameObject('/BitsPerComponent'): NumberObject(8)})
    image_ref = writer._add_object(obj)
    pages = []
    for number, part in enumerate(selected + [None], 1):
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font}),
            NameObject('/XObject'): DictionaryObject({NameObject('/SourceImage'): image_ref})})
        rows = [r['text'] for r in rendering['rows'] if part is not None and r['source_line'] == part['line_start']]
        commands = ['BT /F1 10 Tf 40 800 Td']
        for row in rows:
            commands.append('<' + ''.join('%04X' % codes[c] for c in row) + '> Tj 0 -14 Td')
        commands.append('ET')
        height = min(500, 515 * image.height / image.width)
        commands.append('q 515 0 0 %.4f 40 40 cm /SourceImage Do Q' % height)
        page[NameObject('/Contents')] = stream(writer, '\n'.join(commands).encode('ascii'))
        pages.append({'page': number, 'rows': rows, 'source': part, 'text_layer': part is not None})
    out = io.BytesIO();writer.write(out)
    return out.getvalue(), rendered, {**rendering, 'embedded_font_sha256': font_sha,
                                      'pages': pages, 'pixel_sha256': sha(image.tobytes())}


def test_source_pdf(client):
    captured = load_binding('source-pdf')
    evidence = Evidence(captured)
    pid, _, _ = ingest(client, captured, evidence)
    lines = source_lines(captured, evidence)
    selected = [{'line_start': line['line'], 'text': line['content'].expandtabs(4),
                 'raw_text': line['physical_line'], 'original_line_mapping': line} for line in lines]
    assert sum(len(p['text']) for p in selected) <= 1200, 'Explicit derivative exceeds bound; no truncation'
    raw, preview, conversion = make_pdf(selected)
    evidence.save('source-derived.pdf', raw)
    evidence.save('source-derived-preview.png', preview)
    evidence.save('actual-pdf-derivation.json', {'parent_raw_sha256': sha(captured['raw']), 'source_kind': captured['source']['source_kind'],
        'converter': 'eeas.source-pdf.v1', 'converter_sha256': sha(Path(__file__).read_bytes()), 'command': sys.argv,
        'derived_sha256': sha(raw), 'selection': selected, 'conversion': conversion,
        'scope': 'Bounded original-body selected text pages and one explicit scan page; not upstream original PDF or complete long-source conversion. Font glyph coverage checked; no OCR/vision executed.'})
    response = client.post(f'/api/projects/{pid}/documents', files={'file': ('actual-source-derived.pdf', raw)},
                           data={'source': 'derived:public-original-body:parent=' + sha(captured['raw'])})
    assert response.status_code == 201, response.text
    doc = response.json();base = f'/api/projects/{pid}/documents/{doc["id"]}'
    assert doc['sha256'] == sha(raw) and doc['format'] == 'pdf'
    assert client.get(base + '/download', params={'revision': 1}).content == raw
    parsed = client.get(base, params={'revision': 1}).json();pages = parsed['outline']['pages']
    assert len(pages) == len(selected) + 1
    for page, part in zip(pages, selected):
        text = parsed['content'][page['char_start']:page['char_end']]
        assert ''.join(text.splitlines()[1:]) == part['text']
        assert page['text_extracted'] is True
    assert pages[-1]['text_extracted'] is False
    assert 'PDF_PAGE_' + str(len(pages)) + '_NO_TEXT_LAYER_OCR_OR_VISION_REQUIRED' in parsed['warnings']
    assert 'PDF_IMAGES_DECODED_TO_PNG_OR_JPEG_WITH_PAGE_PROVENANCE' in parsed['warnings']
    assets_response = client.get(base + '/assets', params={'revision': 1}).json();assets = assets_response['assets']
    assert len(assets) == len(pages)
    for number, asset in enumerate(assets, 1):
        assert asset['member_path'].startswith(f'pages/{number}/images/')
        actual = client.get(base + '/assets/' + asset['asset_id'], params={'revision': 1})
        assert actual.status_code == 200 and sha(actual.content) == asset['sha256']
        decoded = Image.open(io.BytesIO(actual.content)).convert('RGB')
        assert sha(decoded.tobytes()) == conversion['pixel_sha256']
        evidence.save('actual-pdf-asset-' + str(number) + '.bin', actual.content)
    tail = conversion['pages'][-2]['rows'][-1][-100:]
    hits = client.get(f'/api/projects/{pid}/search', params={'keyword': tail, 'document_id': doc['id'], 'revision': 1}).json()['hits']
    assert any(pages[-2]['line_start'] < h['line_start'] <= pages[-2]['line_end'] for h in hits)
    assert all(h['revision'] == 1 and h['sha256'] == sha(raw) for h in hits)
    evidence.save('actual-pdf-document.json', parsed)
    evidence.save('actual-pdf-assets.json', assets_response)
    evidence.save('actual-pdf-search.json', hits)
    evidence.observe()

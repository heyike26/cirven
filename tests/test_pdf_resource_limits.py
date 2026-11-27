"""Original tiny PDFs: unsafe decoder calls are stopped, never allocated."""
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           NameObject, NumberObject)
import pytest

from packages.domain import pdf_import


@pytest.fixture(autouse=True)
def restore_pdf_worker_limits(monkeypatch):
    # Some cases call the child-only extractor in process with very small limits.
    from pypdf import filters
    for key in ('ZLIB_MAX_OUTPUT_LENGTH', 'LZW_MAX_OUTPUT_LENGTH', 'RUN_LENGTH_MAX_OUTPUT_LENGTH',
                'MAX_DECLARED_STREAM_LENGTH', 'MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH', 'FLATE_MAX_BUFFER_SIZE'):
        monkeypatch.setattr(filters, key, getattr(filters, key, None), raising=False)


def image_object(width=2, height=2, *, data=None, filter_name=None):
    obj = DecodedStreamObject()
    obj.set_data(bytes([48]) * width * height if data is None else data)
    obj.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Image'),
                NameObject('/Width'): NumberObject(width), NameObject('/Height'): NumberObject(height),
                NameObject('/ColorSpace'): NameObject('/DeviceGray'), NameObject('/BitsPerComponent'): NumberObject(8)})
    if filter_name:
        obj[NameObject('/Filter')] = NameObject(filter_name)
    return obj


def resources(writer, images):
    return DictionaryObject({NameObject('/XObject'): DictionaryObject({
        NameObject('/I' + str(index)): writer._add_object(image) for index, image in enumerate(images)})})


def make_pdf(kind='direct', *, size=(4001, 4000), count=1, pages=1, form_depth=1, cycle=False, filter_name=None, data=b'\x00'):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=100, height=100)
        image = image_object(*size, data=data, filter_name=filter_name)
        if kind == 'smask':
            parent = image_object(*size, data=data) if size[0] * size[1] <= 16_000_000 and len(data) == size[0] * size[1] else image_object(1, 1)
            parent[NameObject('/SMask')] = writer._add_object(image)
            image = parent
        resource = resources(writer, [image] * count)
        if kind == 'form':
            for _ in range(form_depth):
                form = DecodedStreamObject()
                form.set_data(b'/I0 Do')
                form.update({NameObject('/Type'): NameObject('/XObject'), NameObject('/Subtype'): NameObject('/Form'),
                    NameObject('/BBox'): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]),
                    NameObject('/Resources'): resource})
                ref = writer._add_object(form)
                resource = DictionaryObject({NameObject('/XObject'): DictionaryObject({NameObject('/I0'): ref})})
            if cycle:
                form[NameObject('/Resources')] = resource
        page[NameObject('/Resources')] = resource
        if kind == 'inline':
            page.pop('/Resources')
            stream = DecodedStreamObject()
            stream.set_data(f'BI /W {size[0]} /H {size[1]} /CS /G /BPC 8 ID '.encode() + data + b'\nEI\n')
            page[NameObject('/Contents')] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.mark.parametrize('kind', ['direct', 'form', 'smask', 'inline'])
def test_pixel_limit_is_checked_before_any_pypdf_image_decoder(kind):
    raw = make_pdf(kind)
    assert len(raw) < 2048  # Tiny metadata; not a huge image allocation.
    with patch('pypdf.generic._image_xobject._xobj_to_image', side_effect=AssertionError('unsafe decoder entered')) as decoder:
        with pytest.raises(ValueError, match='pixel|dimensions'):
            pdf_import._extract(raw)
    assert decoder.call_count == 0


@pytest.mark.parametrize('kind', ['direct', 'form', 'smask', 'inline'])
def test_real_legal_small_images_and_page_provenance_still_import(kind,monkeypatch):
    commands=[];actual_run=pdf_import.subprocess.run
    def observe_child(command,**kwargs):
        commands.append(command);return actual_run(command,**kwargs)
    monkeypatch.setattr(pdf_import.subprocess,'run',observe_child)
    raw = make_pdf(kind, size=(2, 2), data=b'\x01\x02\x03\x04', pages=2)
    parsed = pdf_import.parse_pdf(raw)
    assert len(parsed['assets']) == 2
    assert parsed['outline']['pages'][1]['page_number'] == 2
    assert '# PDF 第 2 页' in parsed['content']
    for number, asset in enumerate(parsed['assets'], 1):
        assert asset['member_path'].startswith(f'pages/{number}/images/')
        with Image.open(BytesIO(asset['raw_bytes'])) as actual:
            actual.load()
            assert actual.size == (2, 2)
    assert 'PDF_IMAGES_DECODED_TO_PNG_OR_JPEG_WITH_PAGE_PROVENANCE' in parsed['warnings']
    assert commands == [pdf_import.pdf_worker_command('packages.domain.pdf_import')]


def test_cyclic_form_is_rejected_before_decoder():
    with patch('pypdf.generic._image_xobject._xobj_to_image', side_effect=AssertionError('unsafe decoder entered')) as decoder:
        with pytest.raises(ValueError, match='cyclic'):
            pdf_import._extract(make_pdf('form', size=(1, 1), cycle=True))
    assert decoder.call_count == 0


def test_resource_depth_and_object_count_have_small_bounded_cases(monkeypatch):
    monkeypatch.setattr(pdf_import, 'MAX_RESOURCE_DEPTH', 4)
    with pytest.raises(ValueError, match='depth'):
        pdf_import._extract(make_pdf('form', size=(1, 1), form_depth=3))
    monkeypatch.setattr(pdf_import, 'MAX_RESOURCE_DEPTH', 32)
    monkeypatch.setattr(pdf_import, 'MAX_RESOURCE_OBJECTS', 2)
    with pytest.raises(ValueError, match='resource object'):
        pdf_import._extract(make_pdf(size=(1, 1)))


@pytest.mark.parametrize('limit_name,limit,match', [
    ('MAX_IMAGES', 2, 'image resource count'),
    ('MAX_DECODED_IMAGE_BYTES', 8, 'decoded image'),
])
def test_aliases_and_repeated_pages_are_counted_before_decode(monkeypatch, limit_name, limit, match):
    monkeypatch.setattr(pdf_import, limit_name, limit)
    with patch('pypdf.generic._image_xobject._xobj_to_image', side_effect=AssertionError('unsafe decoder entered')) as decoder:
        with pytest.raises(ValueError, match=match):
            pdf_import._extract(make_pdf(size=(1, 1), count=3))
    assert decoder.call_count == 0
    with pytest.raises(ValueError, match=match):
        pdf_import._extract(make_pdf(size=(1, 1), pages=3))


def test_decoded_stream_total_and_single_stream_limits_are_enforced(monkeypatch):
    monkeypatch.setattr(pdf_import, 'MAX_DECODED_STREAM_BYTES', 7)
    with patch('pypdf.generic._image_xobject._xobj_to_image', side_effect=AssertionError('unsafe decoder entered')) as decoder:
        with pytest.raises(ValueError, match='decoded stream total'):
            pdf_import._extract(make_pdf(size=(2, 2), data=b'1234', pages=2))
    assert decoder.call_count == 0
    monkeypatch.setattr(pdf_import, 'MAX_DECODED_STREAM_BYTES', 64 * 1024 * 1024)
    monkeypatch.setattr(pdf_import, 'MAX_STREAM_BYTES', 3)
    with pytest.raises(ValueError, match='stream'):
        pdf_import._extract(make_pdf(size=(2, 2), data=b'1234'))


def test_content_array_is_bounded_before_pypdf_concatenation(monkeypatch):
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    parts = []
    for _ in range(2):
        stream = DecodedStreamObject()
        stream.set_data(b'q Q ' * 4)
        parts.append(writer._add_object(stream))
    page[NameObject('/Contents')] = ArrayObject(parts)
    output = BytesIO()
    writer.write(output)
    monkeypatch.setattr(pdf_import, 'MAX_STREAM_BYTES', 24)
    with patch('pypdf._page.PageObject.get_contents', side_effect=AssertionError('unbounded concatenation entered')) as concatenate:
        with pytest.raises(ValueError, match='page content'):
            pdf_import._extract(output.getvalue())
    assert concatenate.call_count == 0


def test_jbig2_is_explicitly_rejected_without_external_decoder():
    raw = make_pdf(size=(1, 1), filter_name='/JBIG2Decode')
    with patch('pypdf.filters.JBIG2Decode.decode', side_effect=AssertionError('external decoder entered')) as decoder, \
            patch('subprocess.Popen', side_effect=AssertionError('external process entered')) as processes:
        with pytest.raises(ValueError, match='JBIG2'):
            pdf_import._extract(raw)
    assert decoder.call_count == processes.call_count == 0


def test_jpeg_intrinsic_header_cannot_lie_about_pdf_dimensions():
    output = BytesIO()
    Image.new('RGB', (1, 1), 'blue').save(output, format='JPEG')
    data = bytearray(output.getvalue())
    marker = data.find(b'\xff\xc0')
    assert marker > 0
    data[marker + 5:marker + 7] = (4000).to_bytes(2, 'big')
    data[marker + 7:marker + 9] = (4001).to_bytes(2, 'big')
    raw = make_pdf(size=(1, 1), filter_name='/DCTDecode', data=bytes(data))
    with patch('pypdf.generic._image_xobject._xobj_to_image', side_effect=AssertionError('unsafe decoder entered')) as decoder:
        with pytest.raises(ValueError, match='pixel|dimensions'):
            pdf_import._extract(raw)
    assert decoder.call_count == 0


def test_real_legal_jpeg_is_kept_readable():
    output = BytesIO()
    Image.new('RGB', (3, 2), 'blue').save(output, format='JPEG')
    parsed = pdf_import.parse_pdf(make_pdf(size=(3, 2), filter_name='/DCTDecode', data=output.getvalue()))
    assert len(parsed['assets']) == 1
    assert parsed['assets'][0]['media_type'] == 'image/jpeg'
    assert parsed['assets'][0]['width'] == 3 and parsed['assets'][0]['height'] == 2


def test_content_operation_limit_is_enforced_incrementally(monkeypatch):
    writer = PdfWriter()
    page = writer.add_blank_page(width=100, height=100)
    content = DecodedStreamObject()
    content.set_data(b'q Q ' * 4)
    page[NameObject('/Contents')] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    monkeypatch.setattr(pdf_import, 'MAX_CONTENT_OPERATIONS', 4)
    with pytest.raises(ValueError, match='operation count'):
        pdf_import._extract(output.getvalue())


@pytest.mark.parametrize('size', [(0, 1), (-1, 1), (1, 0)])
def test_nonpositive_dimensions_are_rejected_before_decoder(size):
    with patch('pypdf.generic._image_xobject._xobj_to_image', side_effect=AssertionError('unsafe decoder entered')) as decoder:
        with pytest.raises(ValueError, match='dimensions'):
            pdf_import._extract(make_pdf(size=size))
    assert decoder.call_count == 0


def test_runlength_expansion_uses_the_same_small_stream_limit(monkeypatch):
    # Three encoded bytes expand to 12 bytes, safely caught at our tiny limit.
    monkeypatch.setattr(pdf_import, 'MAX_STREAM_BYTES', 8)
    with patch('pypdf.generic._image_xobject._xobj_to_image', side_effect=AssertionError('unsafe decoder entered')) as decoder:
        with pytest.raises(ValueError, match='stream'):
            pdf_import._extract(make_pdf(size=(1, 1), filter_name='/RunLengthDecode', data=bytes([245, 48, 128])))
    assert decoder.call_count == 0

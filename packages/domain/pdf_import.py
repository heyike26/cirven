"""PDF parsing runs in a bounded child; document code and attachments never execute."""
from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import warnings

MAX_PAGES = 400
MAX_EXTRACTED_CHARACTERS = 2_000_000
MAX_STREAM_BYTES = 16 * 1024 * 1024
MAX_ASSET_BYTES = 32 * 1024 * 1024
MAX_IMAGES = 200
MAX_RESOURCE_DEPTH = 32
MAX_RESOURCE_OBJECTS = 4096
MAX_CONTENT_OPERATIONS = 100_000
MAX_DECODED_STREAM_BYTES = 64 * 1024 * 1024
MAX_DECODED_IMAGE_BYTES = 64 * 1024 * 1024
TIMEOUT_SECONDS = 20


class _Resources:
    """Check all pages before pypdf is allowed to create any image pixels.

    Stream bytes are counted once per actual object (pypdf caches decoded
    streams); image resources are charged per reference/page, including masks
    and aliases. Four bytes per pixel reserves decoded RGBA raster space; it is
    an input budget, not a claim about the parser process's total heap usage.
    """
    def __init__(self, reader):
        self.reader = reader
        self.objects = self.images = self.stream_bytes = self.image_bytes = self.operations = 0
        self.streams = {}
        self.notices = set()

    @staticmethod
    def resolve(obj):
        from pypdf.generic import IndirectObject
        from pypdf.errors import LimitReachedError, PdfReadError
        seen = set()
        while isinstance(obj, IndirectObject):
            key = (id(obj.pdf), obj.idnum, obj.generation)
            if key in seen or len(seen) >= MAX_RESOURCE_DEPTH:
                raise ValueError("PDF cyclic or excessively deep indirect resource")
            seen.add(key)
            try:
                obj = obj.get_object()
            except (PdfReadError, LimitReachedError) as error:
                raise ValueError("PDF resource stream could not be resolved within its limit") from error
        return obj

    def image_shape(self, obj):
        from packages.domain.document_import import MAX_IMAGE_PIXELS
        width, height = (self.resolve(obj.get(name)) for name in ('/Width', '/Height'))
        if (not isinstance(width, int) or not isinstance(height, int) or isinstance(width, bool) or
                isinstance(height, bool) or width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS):
            raise ValueError("PDF image dimensions exceed the 16 million pixel limit or are invalid")
        self.images += 1
        self.image_bytes += width * height * 4
        if self.images > MAX_IMAGES:
            raise ValueError("PDF image resource count exceeds its limit (including masks and aliases)")
        if self.image_bytes > MAX_DECODED_IMAGE_BYTES:
            raise ValueError("PDF cumulative decoded image raster budget exceeded")
        return width, height

    def stream(self, obj):
        from pypdf.generic import ArrayObject, NullObject
        filters = self.resolve(obj.get('/Filter'))
        if filters is None or isinstance(filters, NullObject):
            filters = []
        elif not isinstance(filters, ArrayObject):
            filters = [filters]
        if len(filters) > 8:
            raise ValueError("PDF stream filter chain exceeds its limit")
        allowed = {'/FlateDecode', '/Fl', '/LZWDecode', '/LZW', '/RunLengthDecode', '/RL',
                   '/ASCIIHexDecode', '/AHx', '/ASCII85Decode', '/A85', '/DCTDecode', '/DCT',
                   '/JPXDecode', '/CCITTFaxDecode', '/CCF'}
        for name in filters:
            name = str(self.resolve(name))
            if name == '/JBIG2Decode':
                raise ValueError("PDF JBIG2 images require an external decoder and are not supported; export PNG/JPEG images")
            if name not in allowed:
                raise ValueError("PDF unsupported stream filter: " + name)
        try:
            data = obj.get_data()
        except Exception as error:
            raise ValueError("PDF stream could not be decoded within its resource limit") from error
        if len(data) > MAX_STREAM_BYTES:
            raise ValueError("PDF decoded stream exceeds its 16 MiB limit")
        key = id(obj)
        if key not in self.streams:
            self.streams[key] = obj  # Retain inline objects too; prevent id reuse undercount.
            self.stream_bytes += len(data)
            if self.stream_bytes > MAX_DECODED_STREAM_BYTES:
                raise ValueError("PDF decoded stream total exceeds its limit")
        return data

    def image_header(self, data, declared):
        # Image.open reads the format header lazily. Never call load, convert,
        # save or frombytes here: a PDF dictionary can lie about JPEG dimensions.
        if not (data.startswith((b'\xff\xd8', b'\x89PNG\r\n\x1a\n', b'II*\0', b'MM\0*', b'\xffO\xffQ')) or
                data[4:12] == b'jP  \r\n\x87\n'):
            return
        from PIL import Image
        from packages.domain.document_import import MAX_IMAGE_PIXELS
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(BytesIO(data)) as header:
                    width, height = header.size
                    if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS or (width, height) != declared:
                        raise ValueError("PDF encoded image dimensions are invalid or exceed the pixel limit")
        except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
            raise ValueError("PDF encoded image dimensions exceed the pixel limit") from error

    def content(self, data, *, in_form=False):
        from pypdf.generic import ArrayObject, ContentStream, EncodedStreamObject, NameObject
        owner = self
        class BoundedOperations(list):
            def append(self, value):
                owner.operations += 1
                if owner.operations > MAX_CONTENT_OPERATIONS:
                    raise ValueError("PDF content operation count exceeds its limit")
                super().append(value)
        content = ContentStream(None, self.reader)
        content.set_data(data)
        content._operations = BoundedOperations()
        aliases = {'/W': '/Width', '/H': '/Height', '/BPC': '/BitsPerComponent', '/CS': '/ColorSpace',
                   '/F': '/Filter', '/DP': '/DecodeParms', '/D': '/Decode', '/IM': '/ImageMask', '/I': '/Interpolate'}
        for parameters, operator in content.operations:
            if operator != b'INLINE IMAGE':
                continue
            obj = EncodedStreamObject()
            obj._data = parameters['data']
            obj[NameObject('/Filter')] = ArrayObject()  # Explicit identity; pypdf encoded streams require it.
            for name, value in parameters['settings'].items():
                obj[NameObject(aliases.get(str(name), str(name)))] = value
            dimensions = self.image_shape(obj)
            self.image_header(self.stream(obj), dimensions)
            if in_form:
                self.notices.add('PDF_FORM_INLINE_IMAGES_NOT_IMPORTED')

    def walk(self, value, depth=0, active=frozenset()):
        from pypdf.generic import ArrayObject, DictionaryObject, NullObject, StreamObject
        if depth > MAX_RESOURCE_DEPTH:
            raise ValueError("PDF resource depth exceeds its limit")
        obj = self.resolve(value)
        if obj is None or isinstance(obj, NullObject) or not isinstance(obj, (ArrayObject, DictionaryObject)):
            return
        identity = id(obj)
        if identity in active:
            raise ValueError("PDF cyclic resource graph is not supported")
        self.objects += 1
        if self.objects > MAX_RESOURCE_OBJECTS or len(obj) > MAX_RESOURCE_OBJECTS:
            raise ValueError("PDF resource object count exceeds its limit")
        active = active | {identity}
        if isinstance(obj, StreamObject):
            subtype = str(self.resolve(obj.get('/Subtype')))
            dimensions = self.image_shape(obj) if subtype == '/Image' else None
            data = self.stream(obj)
            if dimensions:
                self.image_header(data, dimensions)
            elif subtype == '/Form':
                self.content(data, in_form=True)
        children = obj if isinstance(obj, ArrayObject) else obj.values()
        for child in children:
            self.walk(child, depth + 1, active)

    def page(self, page):
        from pypdf.generic import ArrayObject, NullObject, StreamObject
        self.walk(page.get('/Resources'))
        contents = self.resolve(page.get('/Contents'))
        if contents is None or isinstance(contents, NullObject):
            return
        parts = contents if isinstance(contents, ArrayObject) else [contents]
        if len(parts) > MAX_RESOURCE_OBJECTS:
            raise ValueError("PDF page content resource count exceeds its limit")
        data, length = [], 0
        for part in parts:
            part = self.resolve(part)
            if isinstance(part, NullObject):
                continue
            if not isinstance(part, StreamObject):
                raise ValueError("PDF page content is not a stream")
            value = self.stream(part)
            # ContentStream adds a newline between array parts. Account for it
            # before constructing its concatenated buffer.
            length += len(value) + int(isinstance(contents, ArrayObject) and not value.endswith(b'\n'))
            if length > MAX_STREAM_BYTES:
                raise ValueError("PDF page content stream exceeds its 16 MiB limit")
            data.append(value)
        self.content(b'\n'.join(data))


def pdf_worker_command(module, *arguments):
    # Embedded Windows Python ignores both cwd and PYTHONPATH in isolated mode.
    # The already running parent supplies its own source root, never a document path.
    if sys.flags.isolated:
        boot = ("import runpy,sys; sys.path.insert(0,sys.argv.pop(1)); "
                "runpy.run_module(sys.argv.pop(1),run_name='__main__',alter_sys=True)")
        return [sys.executable, "-B", "-c", boot, str(Path(__file__).resolve().parents[2]), module, *arguments]
    return [sys.executable, "-B", "-m", module, *arguments]


def parse_pdf(raw: bytes) -> dict:
    root = Path(__file__).resolve().parents[2]
    try:
        result = subprocess.run(pdf_worker_command("packages.domain.pdf_import"), input=raw,
                                cwd=root, capture_output=True, timeout=TIMEOUT_SECONDS, check=False)
    except subprocess.TimeoutExpired as error:
        raise ValueError("PDF parsing exceeded its 20 second limit; split the document into smaller files") from error
    if result.returncode or len(result.stdout) > 48 * 1024 * 1024:
        raise ValueError("PDF parser failed or exceeded its resource limit")
    try:
        parsed = json.loads(result.stdout)
        if "error" in parsed:
            raise ValueError(parsed["error"])
        for asset in parsed["assets"]:
            asset["raw_bytes"] = base64.b64decode(asset.pop("raw_base64"), validate=True)
        return parsed
    except (KeyError, json.JSONDecodeError) as error:
        raise ValueError("PDF parser returned an invalid result") from error


def _extract(raw: bytes) -> dict:
    from pypdf import PdfReader, filters
    from packages.domain.document_import import image_metadata
    # Apply installed pypdf limits to every supported decompression path, not
    # just Flate. The child owns these settings; no optional binary helper runs.
    for name in ('ZLIB_MAX_OUTPUT_LENGTH', 'LZW_MAX_OUTPUT_LENGTH', 'RUN_LENGTH_MAX_OUTPUT_LENGTH',
                 'MAX_DECLARED_STREAM_LENGTH', 'MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH', 'FLATE_MAX_BUFFER_SIZE'):
        setattr(filters, name, MAX_STREAM_BYTES)
    reader = PdfReader(BytesIO(raw), strict=True)
    if reader.is_encrypted:
        raise ValueError("Encrypted PDFs are not supported; export an approved unlocked copy")
    if not 1 <= len(reader.pages) <= MAX_PAGES:
        raise ValueError("PDF must contain between 1 and 400 pages")
    resources = _Resources(reader)
    for page in reader.pages:
        resources.page(page)
    content, pages, assets, notices = [], [], [], ["PDF_LAYOUT_AND_TABLE_SEMANTICS_REQUIRE_REVIEW", *sorted(resources.notices)]
    characters, line_number, asset_bytes = 0, 1, 0
    catalog = reader.trailer["/Root"]
    if any(key in catalog for key in ("/OpenAction", "/AA")):
        notices.append("PDF_ACTIVE_ACTIONS_IGNORED")
    if catalog.get("/Names", {}).get("/EmbeddedFiles"):
        notices.append("PDF_FILE_ATTACHMENTS_NOT_EXECUTED_OR_IMPORTED")
    for number, page in enumerate(reader.pages, 1):
        stream = page.get_contents()
        if stream and len(stream.get_data()) > MAX_STREAM_BYTES:
            raise ValueError("PDF page content stream exceeds 16 MiB")
        text = page.extract_text() or ""
        fragment = f"# PDF 第 {number} 页\n" + text + ("" if text.endswith("\n") else "\n")
        characters += len(fragment)
        if characters > MAX_EXTRACTED_CHARACTERS:
            raise ValueError("PDF extracted text exceeds the two million character limit")
        pages.append({"page_number": number, "line_start": line_number,
                      "line_end": line_number + len(fragment.splitlines()) - 1,
                      "char_start": characters - len(fragment), "char_end": characters,
                      "text_extracted": bool(text.strip())})
        content.append(fragment)
        line_number += len(fragment.splitlines())
        if not text.strip():
            notices.append(f"PDF_PAGE_{number}_NO_TEXT_LAYER_OCR_OR_VISION_REQUIRED")
        for index, image in enumerate(page.images):
            data = image.data
            # pypdf returns a decoded PNG/JPEG, not the original PDF image stream.
            info = image_metadata(data)
            asset_bytes += len(data)
            if len(assets) >= MAX_IMAGES or asset_bytes > MAX_ASSET_BYTES:
                raise ValueError("PDF image assets exceed the 200 image / 32 MiB limit")
            assets.append({"member_path": f"pages/{number}/images/{index}-{image.name}",
                           **info, "raw_base64": base64.b64encode(data).decode("ascii")})
    if assets:
        notices.append("PDF_IMAGES_DECODED_TO_PNG_OR_JPEG_WITH_PAGE_PROVENANCE")
    return {"content": "".join(content), "format": "pdf", "outline": {"pages": pages},
            "graph": {}, "assets": assets, "warnings": notices}


def main():
    # This is a parser resource bound, never an execution-tool OS sandbox claim.
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    except ImportError:
        pass  # Windows: byte/page/pixel/decompression/wall-clock bounds still apply.
    raw = sys.stdin.buffer.read(16 * 1024 * 1024 + 1)
    try:
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError("PDF exceeds the 16 MiB limit")
        result = _extract(raw)
    except (Exception, MemoryError) as error:
        message = str(error) if isinstance(error, ValueError) else "PDF is invalid or could not be parsed within its limits"
        result = {"error": message}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


if __name__ == "__main__":
    main()

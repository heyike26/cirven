"""Local imports keep original bytes and explicitly describe unrecognised visual text."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from io import BytesIO
from pathlib import Path
import warnings

MAX_TEXT_BYTES = 1024 * 1024
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
SUPPORTED_SUFFIXES = {".md", ".txt", ".png", ".jpg", ".jpeg", ".pdf", ".vsdx", ".xmind", ".docx", ".pptx"}


class DocumentImportError(ValueError):
    pass


@dataclass
class ParsedDocument:
    content: str
    format: str
    outline: dict = field(default_factory=dict)
    graph: dict = field(default_factory=dict)
    assets: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def image_metadata(raw: bytes) -> dict:
    from PIL import Image, UnidentifiedImageError
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(raw), formats=("PNG", "JPEG")) as image:
                width, height = image.size
                if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                    raise DocumentImportError("Image exceeds the 16 million pixel limit")
                if getattr(image, "n_frames", 1) != 1:
                    raise DocumentImportError("Animated images are not supported; export a single PNG/JPEG")
                media_type = {"PNG": "image/png", "JPEG": "image/jpeg"}[image.format]
                image.verify()
            with Image.open(BytesIO(raw), formats=("PNG", "JPEG")) as decoded:
                decoded.load()
    except DocumentImportError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as error:
        raise DocumentImportError("Image is corrupt or is not a supported PNG/JPEG") from error
    return {"media_type": media_type, "width": width, "height": height,
            "sha256": hashlib.sha256(raw).hexdigest(), "byte_length": len(raw)}


def parse_document(filename: str, raw: bytes) -> ParsedDocument:
    suffix = Path(filename).suffix.lower()
    limit = MAX_TEXT_BYTES if suffix in {".md", ".txt"} else MAX_DOCUMENT_BYTES
    if len(raw) > limit:
        raise DocumentImportError(f"Document exceeds the {limit // (1024 * 1024)} MiB limit")
    if suffix in {".md", ".txt"}:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise DocumentImportError("Document must be valid UTF-8") from error
        if "\x00" in text:
            raise DocumentImportError("Binary content is not supported as UTF-8 text")
        return ParsedDocument(text, "markdown" if suffix == ".md" else "text")
    if suffix in {".png", ".jpg", ".jpeg"}:
        info = image_metadata(raw)
        if (suffix == ".png") != (info["media_type"] == "image/png"):
            raise DocumentImportError("Image filename does not match its actual PNG/JPEG format")
        return ParsedDocument(
            f"图片资料：{filename}\n尺寸：{info['width']} × {info['height']}\n"
            "图片中的文字尚未提取；请使用已验证支持视觉输入的公司模型，或补充人工文字说明。\n",
            "image", assets=[{"member_path": filename, "raw_bytes": raw, **info}],
            warnings=["IMAGE_TEXT_NOT_EXTRACTED", "MODEL_VISION_NOT_VALIDATED"])
    if suffix in {".vsdx", ".xmind"}:
        from packages.domain.diagram_import import parse_diagram, DiagramImportError
        try:
            result = parse_diagram(filename, raw)
        except DiagramImportError as error:
            raise DocumentImportError(str(error)) from error
        return ParsedDocument(result.content, result.format, result.outline, result.graph,
                              result.assets, result.warnings)
    if suffix == ".pdf":
        from packages.domain.pdf_import import parse_pdf
        try:
            return ParsedDocument(**parse_pdf(raw))
        except ValueError as error:
            raise DocumentImportError(str(error)) from error
    if suffix == ".pptx":
        from packages.domain.pptx_import import parse_pptx, PptxImportError
        try:
            result = parse_pptx(raw, filename=filename)
        except PptxImportError as error:
            raise DocumentImportError(str(error)) from error
        return ParsedDocument(result.content, result.format, result.outline, result.graph,
                              result.assets, result.warnings)
    if suffix == ".docx":
        from packages.domain.docx_import import parse_docx, DocxImportError
        try:
            result = parse_docx(filename, raw)
        except DocxImportError as error:
            raise DocumentImportError(str(error)) from error
        return ParsedDocument(result.content, result.format, result.outline, result.graph,
                              result.assets, result.warnings)
    raise DocumentImportError("Unsupported document format")

"""Editable, text-only report decks with explicit source and template preservation.

No DB, model, rendering, filesystem writes or external relationship requests occur.
The caller authenticates source revisions; this adapter only matches its supplied set.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
from importlib.metadata import version
from io import BytesIO
import json
import re
import xml.etree.ElementTree as ET
import zipfile

from packages.domain.diagram_import import DiagramImportError, DiagramLimits, _Archive, _local
from packages.domain.pptx_import import (
    PRESENTATION_NS, PRESENTATION_TYPE, _relations, _resolve,
)

SCHEMA = "eeas.report-deck.v1"
SOURCE_SCHEMA = "eeas.pptx-export-source.v1"
BACKEND_VERSION = "1.0.2"
TOKEN = re.compile(r"\{\{EEAS_[A-Z0-9_]+\}\}")
CTYPE_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_REPORT_BYTES = 1024 * 1024
MAX_REFS = 64


class PptxExportError(ValueError):
    """Invalid source binding, unsupported payload/template, or bounded export failure."""


@dataclass(frozen=True)
class ReportSection:
    title: str
    paragraphs: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    source_refs: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class ReportDeck:
    title: str
    sections: list[ReportSection]
    subtitle: str = ""
    source_refs: list[dict] = field(default_factory=list)
    schema_version: str = SCHEMA


@dataclass(frozen=True)
class PptxExport:
    raw_bytes: bytes
    sha256: str
    source_manifest: dict
    audit: dict


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as error:
        raise PptxExportError("Report/source must be bounded UTF-8 JSON") from error


def _keys(value, allowed, required, label):
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - allowed:
        raise PptxExportError(label + " has missing/unsupported fields")


def _text(value, label, maximum, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or not empty and not value.strip():
        raise PptxExportError(label + " is missing or exceeds the character bound")
    if any(ord(c) < 32 and c not in "\t\r\n" or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise PptxExportError(label + " contains invalid text characters")
    # XML text is inert, but provenance delimiters cannot be smuggled in as template tokens.
    if "{{EEAS_" in value:
        raise PptxExportError(label + " contains a reserved template symbol")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _identifier(value, label):
    value = _text(value, label, 200)
    if any(c.isspace() for c in value):
        raise PptxExportError(label + " must be an exact, whitespace-free identifier")
    return value


def _ref(value, project_id):
    if not isinstance(value, dict) or value.get("kind") not in {"document", "artifact"}:
        raise PptxExportError("Source kind must be document or artifact")
    kind = value["kind"]
    identity = "document_id" if kind == "document" else "artifact_id"
    fields = {"kind", "project_id", identity, "revision", "sha256"}
    allowed = fields | ({"line_start", "line_end"} if kind == "document" else set())
    _keys(value, allowed, fields, "Source ref")
    if _identifier(value["project_id"], "source project_id") != project_id:
        raise PptxExportError("Source belongs to a different project")
    _identifier(value[identity], identity)
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise PptxExportError("Source revision must be a positive integer")
    if not isinstance(value["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
        raise PptxExportError("Source SHA256 must be an exact lowercase original/artifact hash")
    if ("line_start" in value) != ("line_end" in value):
        raise PptxExportError("Source line range must have both endpoints")
    if "line_start" in value and (type(value["line_start"]) is not int or
            type(value["line_end"]) is not int or
            not 1 <= value["line_start"] <= value["line_end"] <= 10_000_000):
        raise PptxExportError("Source line range is invalid")
    return deepcopy(value)


def _refs(values, project_id):
    if not isinstance(values, list) or len(values) > MAX_REFS:
        raise PptxExportError("Source refs must be a list within the 64-ref bound")
    result, seen = [], set()
    for value in values:
        value = _ref(value, project_id)
        key = _json(value)
        if key in seen:
            raise PptxExportError("Duplicate source ref")
        seen.add(key); result.append(value)
    return result


def _report(value, project_id, validated):
    if isinstance(value, ReportDeck):
        value = asdict(value)
    raw_json = _json(value)
    if len(raw_json) > MAX_REPORT_BYTES:
        raise PptxExportError("Report exceeds the 1 MiB JSON bound")
    _keys(value, {"schema_version", "title", "subtitle", "sections", "source_refs"},
          {"title", "sections"}, "ReportDeck")
    if value.get("schema_version", SCHEMA) != SCHEMA:
        raise PptxExportError("Unsupported ReportDeck schema")
    approved = {_json(item) for item in _refs(validated, project_id)}
    refs = _refs(value.get("source_refs", []), project_id)
    sections = value["sections"]
    if not isinstance(sections, list) or not 1 <= len(sections) <= 30:
        raise PptxExportError("Report must have 1..30 sections")
    normalized = {"schema_version": SCHEMA,
                  "title": _text(value["title"], "title", 300),
                  "subtitle": _text(value.get("subtitle", ""), "subtitle", 800, empty=True),
                  "source_refs": refs, "sections": []}
    all_refs = {_json(item): item for item in refs}
    for section in sections:
        _keys(section, {"title", "paragraphs", "bullets", "source_refs"}, {"title"}, "ReportSection")
        target = {"title": _text(section["title"], "section title", 300),
                  "source_refs": _refs(section.get("source_refs", []), project_id)}
        for field_name in ("paragraphs", "bullets"):
            items = section.get(field_name, [])
            if not isinstance(items, list) or len(items) > 24:
                raise PptxExportError(field_name + " must be a list within the 24-item bound")
            target[field_name] = [_text(item, field_name, 2000) for item in items]
        body = _body(target)
        if not body or len(body) > 6000 or body.count("\n") > 80:
            raise PptxExportError("Section body is missing or exceeds editable slide text bounds")
        all_refs.update({_json(item): item for item in target["source_refs"]})
        normalized["sections"].append(target)
    if not all_refs or len(all_refs) > MAX_REFS:
        raise PptxExportError("Report needs 1..64 explicitly validated document/artifact refs")
    if all_refs.keys() - approved:
        raise PptxExportError("Report source ref was not matched to the caller's validated set")
    return normalized, _sha(raw_json), [all_refs[key] for key in sorted(all_refs)]


def _body(section):
    return "\n".join(section["paragraphs"] + ["• " + value for value in section["bullets"]])


def _archive(raw):
    try:
        return _Archive(raw, DiagramLimits())
    except (DiagramImportError, zipfile.BadZipFile, ET.ParseError, UnicodeError, OSError, ValueError) as error:
        raise PptxExportError("Unsafe or malformed PPTX archive: " + str(error)) from error


def _template(raw):
    archive = _archive(raw)
    types = archive.xml.get("[Content_Types].xml")
    if types is None or _local(types.tag) != "Types":
        raise PptxExportError("Template is missing its OPC content types")
    overrides, defaults = {}, {}
    for item in types:
        kind = _local(item.tag)
        content_type = item.get("ContentType", "")
        if any(term in content_type.casefold() for term in ("macroenabled", "vba", "activex", "oleobject")):
            raise PptxExportError("Active/macro-enabled template content is unsupported")
        if kind == "Override":
            name = _resolve("", item.get("PartName", ""))
            if name not in archive.data or name in overrides:
                raise PptxExportError("Invalid template content type override")
            overrides[name] = content_type
        elif kind == "Default":
            name = item.get("Extension", "").casefold()
            if not name or name in defaults:
                raise PptxExportError("Invalid template content type default")
            defaults[name] = content_type
        else:
            raise PptxExportError("Unsupported template content type element")
    relations = _relations(archive, [])
    if any(link["external"] for links in relations.values() for link in links.values()):
        raise PptxExportError("External template relationships are unsupported and never fetched")
    main = [link for link in relations.get("", {}).values() if link["type"].endswith("/officeDocument")]
    if len(main) != 1 or overrides.get(main[0]["target"]) != PRESENTATION_TYPE:
        raise PptxExportError("Only transitional, macro-free PPTX templates are supported")
    root = archive.xml.get(main[0]["target"])
    if root is None or root.tag != "{" + PRESENTATION_NS + "}presentation":
        raise PptxExportError("Template presentation XML is unsupported")
    slide_list = next((child for child in root if _local(child.tag) == "sldIdLst"), None)
    if slide_list is None or not 1 <= len(slide_list) <= 100:
        raise PptxExportError("Template must have 1..100 slides")
    seen_ids, seen_parts = set(), set()
    for item in slide_list:
        identifier = item.get("id")
        relationship_id = item.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        link = relations.get(main[0]["target"], {}).get(relationship_id)
        if (not identifier or identifier in seen_ids or link is None or
                not link["type"].endswith("/slide") or link["target"] in seen_parts or
                archive.xml.get(link["target"]) is None or
                archive.xml[link["target"]].tag != "{" + PRESENTATION_NS + "}sld"):
            raise PptxExportError("Invalid/duplicate template slide identity")
        seen_ids.add(identifier); seen_parts.add(link["target"])
    for name, element in archive.xml.items():
        if any(_local(node.tag) in {"oleObj", "audioFile", "videoFile", "timing", "AlternateContent"}
               for node in element.iter()):
            raise PptxExportError("Template media/effects/alternate content are unsupported: " + name)
    pixel_total = 0
    for name, blob in archive.data.items():
        if name == "[Content_Types].xml":
            continue
        kind = overrides.get(name, defaults.get(name.rsplit(".", 1)[-1].casefold(), ""))
        if not kind:
            raise PptxExportError("Template part has no declared content type")
        if name.startswith(("ppt/embeddings/", "ppt/activeX/")) or kind.startswith(("audio/", "video/")):
            raise PptxExportError("Embedded/active/audio/video template parts are unsupported")
        if kind.startswith("image/"):
            if kind not in {"image/png", "image/jpeg"}:
                raise PptxExportError("Only inert PNG/JPEG template decoration is supported")
            # The exporter does not render images. Check encoded identity/dimensions before load.
            from PIL import Image
            try:
                with Image.open(BytesIO(blob), formats=("PNG", "JPEG")) as image:
                    if image.size[0] < 1 or image.size[1] < 1 or image.size[0] * image.size[1] > 16_000_000:
                        raise PptxExportError("Template image exceeds the dimension limit")
                    pixel_total += image.size[0] * image.size[1]
                    if pixel_total * 4 > archive.limits.max_expanded_bytes:
                        raise PptxExportError("Template cumulative image byte limit exceeded")
                    actual = "image/png" if image.format == "PNG" else "image/jpeg"
                    if actual != kind:
                        raise PptxExportError("Template image type does not match its bytes")
                    image.verify()
            except PptxExportError:
                raise
            except Exception as error:
                raise PptxExportError("Corrupt PNG/JPEG template decoration") from error
    return archive, relations


def _shape_bounds(shape, presentation):
    if shape.left < 0 or shape.top < 0 or shape.width <= 0 or shape.height <= 0 or (
            shape.left + shape.width > presentation.slide_width or
            shape.top + shape.height > presentation.slide_height):
        raise PptxExportError("A report template field is outside the slide canvas")


def _fill(presentation, report):
    replacements = {"{{EEAS_TITLE}}": report["title"], "{{EEAS_SUBTITLE}}": report["subtitle"]}
    required = {"{{EEAS_TITLE}}"}
    if report["subtitle"]:
        required.add("{{EEAS_SUBTITLE}}")
    for index, section in enumerate(report["sections"], 1):
        for field_name, value in (("TITLE", section["title"]), ("BODY", _body(section))):
            symbol = "{{EEAS_SECTION_" + str(index) + "_" + field_name + "}}"
            replacements[symbol] = value; required.add(symbol)
    found, edits = set(), []
    # Group transform coordinates and table-cell fills are not guessed in this first contract.
    for slide in presentation.slides:
        for shape in slide.shapes:
            if shape.shape_type == 6:
                if any("{{EEAS_" in node.text for node in shape.element.iter() if isinstance(node.text, str)):
                    raise PptxExportError("Symbols in grouped shapes are unsupported")
                continue
            if not shape.has_text_frame:
                if any("{{EEAS_" in node.text for node in shape.element.iter() if isinstance(node.text, str)):
                    raise PptxExportError("Symbols in tables/non-text shapes are unsupported")
                continue
            for paragraph in shape.text_frame.paragraphs:
                if "{{EEAS_" not in paragraph.text:
                    continue
                tokens = TOKEN.findall(paragraph.text)
                if (not tokens or any(token not in replacements for token in tokens) or
                        "{{EEAS_" in TOKEN.sub("", paragraph.text)):
                    raise PptxExportError("Unknown/invalid template symbol")
                if sum(len(TOKEN.findall(run.text)) for run in paragraph.runs) != len(tokens):
                    raise PptxExportError("Template symbols split across runs/fields are unsupported")
                _shape_bounds(shape, presentation)
                for run in paragraph.runs:
                    value = run.text
                    for token in TOKEN.findall(value):
                        found.add(token); value = value.replace(token, replacements[token])
                    if value != run.text:
                        edits.append((run, value))
    if required - found:
        raise PptxExportError("Template is missing required report symbols: " + ", ".join(sorted(required - found)))
    for run, value in edits:
        if "\n" not in value:
            run.text = value
            continue
        # Real DrawingML breaks, rather than literal newlines inside a:t. Keep the
        # original run properties on every new run and preserve the surrounding runs.
        from pptx.oxml.xmlchemy import OxmlElement
        original_run, parent = run._r, run._r.getparent()
        styled = deepcopy(original_run)
        chunks = value.split("\n")
        run.text = chunks[0]
        position = parent.index(original_run) + 1
        for chunk in chunks[1:]:
            line_break = OxmlElement("a:br")
            if styled.rPr is not None:
                line_break.append(deepcopy(styled.rPr))
            new_run = deepcopy(styled); new_run.text = chunk
            parent.insert(position, line_break); parent.insert(position + 1, new_run)
            position += 2
    return sorted(found)


def _rel_part(owner):
    folder, name = owner.rsplit("/", 1)
    return folder + "/_rels/" + name + ".rels"


def _xml_bytes(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _merge_rels(original, generated, owner):
    """Retain old relationships verbatim unless notes add a new relationship."""
    if original is None:
        return generated
    first, second = ET.fromstring(original), ET.fromstring(generated)
    existing = {item.get("Id"): item for item in first}
    added = False
    for item in second:
        identifier = item.get("Id")
        if identifier in existing:
            if existing[identifier].attrib != item.attrib:
                raise PptxExportError("Template relationship identity conflict: " + owner)
        else:
            if not item.get("Type", "").endswith(("/notesSlide", "/notesMaster")):
                raise PptxExportError("Unexpected new relationship in original template: " + owner)
            first.append(deepcopy(item)); added = True
    return _xml_bytes(first) if added else original


def _overlay(original, generated, slide_parts, notes_parts, old_relations):
    result = dict(original.data)
    mutable = set(slide_parts) | set(notes_parts)
    for name in mutable:
        if name not in generated.data:
            raise PptxExportError("Generated report part is missing")
        if name in notes_parts and name in result and not any(
                item["target"] == name and item["type"].endswith("/notesSlide")
                for links in old_relations.values() for item in links.values()):
            raise PptxExportError("New notes part collides with an original orphan template part")
        result[name] = generated.data[name]
    # Original parts not involved in text/notes are copied byte for byte. New default notes
    # masters/themes are retained as generated, with their internal relationship parts.
    for name, blob in generated.data.items():
        if name in result:
            continue
        if not name.startswith(("ppt/notesSlides/", "ppt/notesMasters/", "ppt/theme/")) and name not in {
                _rel_part(part) for part in slide_parts}:
            raise PptxExportError("Unexpected generated template part: " + name)
        result[name] = blob
    for name, blob in generated.data.items():
        if name.endswith(".rels") and name in original.data:
            result[name] = _merge_rels(original.data[name], blob, name)
    first = deepcopy(original.xml["[Content_Types].xml"])
    keys = {(item.tag, item.get("PartName", item.get("Extension"))): item for item in first}
    added = False
    for item in generated.xml["[Content_Types].xml"]:
        key = item.tag, item.get("PartName", item.get("Extension"))
        if key in keys:
            if keys[key].attrib != item.attrib:
                raise PptxExportError("Generated template content type conflict")
        else:
            first.append(deepcopy(item)); added = True
    if added:
        result["[Content_Types].xml"] = _xml_bytes(first)
    output = BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        for name, blob in sorted(result.items()):
            target.writestr(name, blob)
    return output.getvalue()


def export_report_deck(report: ReportDeck | dict, *, project_id: str,
                       validated_source_refs: list[dict], template_bytes: bytes | None = None) -> PptxExport:
    """Generate editable text; caller separately approves source and publishes the artifact.

    Template symbols must be entirely within one text run in an ungrouped text shape.
    Untouched OPC member *payloads* are retained, not original ZIP compression/timestamps.
    Notes provenance is appended to every slide and explicitly describes deck-level refs.
    """
    project_id = _identifier(project_id, "project_id")
    normalized, input_sha, refs = _report(report, project_id, validated_source_refs)
    try:
        if version("python-pptx") != BACKEND_VERSION:
            raise PptxExportError("Exporter requires the fixed python-pptx 1.0.2 offline component")
        original, relations = _template(template_bytes) if template_bytes is not None else (None, {})
        from pptx import Presentation
        from pptx.util import Pt
        presentation = Presentation(BytesIO(template_bytes)) if original is not None else Presentation()
        if not 0 < presentation.slide_width <= 100_000_000 or not 0 < presentation.slide_height <= 100_000_000:
            raise PptxExportError("Unsupported slide canvas dimensions")
        if original is not None:
            if not 1 <= len(presentation.slides) <= 100:
                raise PptxExportError("Template must have 1..100 slides")
            symbols = _fill(presentation, normalized)
        else:
            cover = presentation.slides.add_slide(presentation.slide_layouts[0])
            cover.shapes.title.text = normalized["title"]
            cover.placeholders[1].text = normalized["subtitle"]
            for section in normalized["sections"]:
                slide = presentation.slides.add_slide(presentation.slide_layouts[1])
                slide.shapes.title.text = section["title"]
                body = slide.placeholders[1].text_frame
                body.clear(); body.word_wrap = True
                texts = section["paragraphs"] + ["• " + text for text in section["bullets"]]
                for index, text in enumerate(texts):
                    paragraph = body.paragraphs[0] if index == 0 else body.add_paragraph()
                    paragraph.text = text; paragraph.font.size = Pt(18)
                    paragraph.level = 0
            symbols = []
        manifest = {"schema_version": SOURCE_SCHEMA, "report_schema_version": SCHEMA,
                    "project_id": project_id, "input_sha256": input_sha,
                    "normalized_input_sha256": _sha(_json(normalized)),
                    "template_sha256": _sha(template_bytes) if template_bytes is not None else None,
                    "source_refs": refs,
                    "source_authority": "CALLER_VALIDATED_SET_EXACT_MATCH_ONLY",
                    "source_scope": "DECK_REFS_NOT_AUTHENTICATED_SLIDE_QUOTES",
                    "backend": {"name": "python-pptx", "version": BACKEND_VERSION}}
        slides, notes, frames, source_slides = [], [], [], []
        for slide in presentation.slides:
            slide_part = str(slide.part.partname).lstrip("/")
            frame = slide.notes_slide.notes_text_frame
            if frame is None:
                raise PptxExportError("Template notes slide has no editable notes placeholder")
            note_part = str(slide.notes_slide.part.partname).lstrip("/")
            slides.append(slide_part); notes.append(note_part); frames.append(frame)
            source_slides.append({"slide_id": slide.slide_id, "part_path": slide_part,
                                  "notes_part_path": note_part})
        manifest["slides"] = source_slides
        message = "EEAS export provenance (deck-level refs; appended, not an Office qualification)\n" + _json(manifest).decode("utf-8")
        for frame in frames:
            frame.add_paragraph().text = message
        result = BytesIO(); presentation.save(result)
        raw = result.getvalue()
        if original is not None:
            raw = _overlay(original, _archive(raw), slides, notes, relations)
        checked = _archive(raw)
        _relations(checked, [])  # Includes new notes/masters; no dangling internal targets.
        preserved, changed, added = [], [], []
        for name, blob in checked.data.items():
            item = {"member_path": name, "sha256": _sha(blob), "byte_length": len(blob)}
            if original is not None and name in original.data:
                item["original_sha256"] = _sha(original.data[name])
                (preserved if blob == original.data[name] else changed).append(item)
            else:
                added.append(item)
        audit = {"schema_version": "eeas.pptx-export-audit.v1", "symbols": symbols,
                 "supported_scope": ["title", "subtitle", "paragraphs", "literal_bullet_text", "editable_text", "appended_source_notes"],
                 "preservation_scope": "UNTARGETED_OPC_MEMBER_PAYLOAD_BYTES; NOT_ZIP_OR_NATIVE_OFFICE_FIDELITY",
                 "preserved_parts": sorted(preserved, key=lambda item: item["member_path"]),
                 "changed_parts": sorted(changed, key=lambda item: item["member_path"]),
                 "added_parts": sorted(added, key=lambda item: item["member_path"]),
                 "native_office": "NOT_RUN", "native_windows": "NOT_RUN",
                 "text_fit_and_visual_rendering": "NOT_VALIDATED", "input_text_newlines": "CRLF_CR_TO_LF"}
        return PptxExport(raw, _sha(raw), manifest, audit)
    except PptxExportError:
        raise
    except Exception as error:
        raise PptxExportError("PPTX export failed without publishing an artifact: " + type(error).__name__) from error

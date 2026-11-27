"""Bounded PPTX import using original MarkItDown v0.1.8 source, without its dispatcher.

The upstream conversion is authoritative Markdown; EEAS supplies inert OPC validation,
source nodes, conservative line anchors, original image bytes and revision binding.
"""
from __future__ import annotations

import hashlib
from io import BytesIO
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from packages.domain.diagram_import import (
    DiagramImportError, DiagramLimits, ParsedDocument, _Archive, _child, _children,
    _local, _resolve,
)

UPSTREAM_COMMIT = "b8f79c57ebc0044be41323d89b2a45d3fda8460e"
VENDOR = Path(__file__).resolve().parents[1] / "adapters/office_vendor/markitdown_v018"
PRESENTATION_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


class PptxImportError(DiagramImportError):
    """Malformed, unsafe or unsupported PPTX; callers may map this to HTTP 422."""


def _attr(element, name, default=None):
    return next((v for k, v in element.attrib.items() if _local(k) == name), default)


def _paths(root):
    paths = {id(root): "/" + _local(root.tag) + "[1]"}
    stack = [root]
    while stack:
        parent = stack.pop()
        counts = {}
        for child in parent:
            name = _local(child.tag)
            counts[name] = counts.get(name, 0) + 1
            paths[id(child)] = paths[id(parent)] + f"/{name}[{counts[name]}]"
            stack.append(child)
    return paths


def _text(element):
    """Preserve DrawingML paragraph breaks, soft breaks and actual text runs."""
    paragraphs = []
    for p in element.iter():
        if p.tag != "{" + DRAWING_NS + "}p":
            continue
        chunks = []
        for item in p.iter():
            if item.tag == "{" + DRAWING_NS + "}t":
                chunks.append(item.text or "")
            elif item.tag == "{" + DRAWING_NS + "}br":
                chunks.append("\n")
        paragraphs.append("".join(chunks))
    return "\n".join(paragraphs)


def _relations(archive, warnings):
    result = {}
    for rel_part, root in archive.xml.items():
        if not rel_part.endswith(".rels"):
            continue
        if rel_part == "_rels/.rels":
            owner = ""
        elif "/_rels/" in rel_part:
            folder, name = rel_part.rsplit("/_rels/", 1)
            owner = folder + "/" + name[:-5]
        else:
            raise PptxImportError("Invalid OPC relationship part location")
        if owner and owner not in archive.data:
            raise PptxImportError("Relationship owner part is missing")
        if _local(root.tag) != "Relationships":
            raise PptxImportError("Invalid OPC relationships root")
        links = {}
        for item in root:
            if _local(item.tag) != "Relationship":
                raise PptxImportError("Unknown OPC relationship element")
            identifier, kind = item.get("Id"), item.get("Type")
            target, mode = item.get("Target"), item.get("TargetMode", "Internal")
            if not identifier or identifier in links or not kind or not target or mode not in {"Internal", "External"}:
                raise PptxImportError("Invalid/duplicate OPC relationship")
            if mode == "External":
                warnings.append("EXTERNAL_RELATIONSHIP_NOT_FETCHED: " + rel_part + "#" + identifier)
                resolved = None  # Never retain or expose URLs/tokens from external targets.
            else:
                resolved = _resolve(owner, target)
                if resolved not in archive.data:
                    raise PptxImportError("OPC relationship target part is missing")
            links[identifier] = {"type": kind, "target": resolved, "external": mode == "External"}
        result[owner] = links
    return result


def _preflight(archive):
    warnings = []
    content_types = archive.xml.get("[Content_Types].xml")
    if content_types is None or _local(content_types.tag) != "Types":
        raise PptxImportError("PPTX content type manifest is missing")
    types, defaults = {}, {}
    for item in content_types:
        name, value = _local(item.tag), item.get("ContentType", "")
        if "macroenabled" in value.casefold() or "vba" in value.casefold():
            raise PptxImportError("Macro-enabled presentations are unsupported; export macro-free .pptx")
        if name == "Override":
            part = _resolve("", item.get("PartName", ""))
            if part in types or part not in archive.data:
                raise PptxImportError("Invalid/duplicate OPC content type override")
            types[part] = value
        elif name == "Default":
            extension = item.get("Extension", "").casefold()
            if not extension or extension in defaults:
                raise PptxImportError("Invalid/duplicate OPC content type default")
            defaults[extension] = value
        else:
            raise PptxImportError("Unsupported OPC content type element")
    relations = _relations(archive, warnings)
    main = [link for link in relations.get("", {}).values() if link["type"].endswith("/officeDocument")]
    if len(main) != 1 or main[0]["external"]:
        raise PptxImportError("PPTX requires one internal officeDocument relationship")
    presentation_part = main[0]["target"]
    presentation = archive.xml.get(presentation_part)
    if types.get(presentation_part) != PRESENTATION_TYPE or presentation is None or presentation.tag != "{" + PRESENTATION_NS + "}presentation":
        raise PptxImportError("Not a supported transitional, macro-free PPTX presentation")
    slides, ids, parts = [], set(), set()
    slide_ids = _child(presentation, "sldIdLst")
    for item in (() if slide_ids is None else slide_ids):
        identifier, rid = item.get("id"), _attr(item, "id")
        # Local names collide: presentation id is unqualified, relationship id qualified.
        rid = next((v for k, v in item.attrib.items() if k.startswith("{") and _local(k) == "id"), None)
        link = relations.get(presentation_part, {}).get(rid)
        if not identifier or identifier in ids or link is None or link["external"] or not link["type"].endswith("/slide"):
            raise PptxImportError("Invalid/duplicate presentation slide reference")
        part = link["target"]
        root = archive.xml.get(part)
        if part in parts or root is None or root.tag != "{" + PRESENTATION_NS + "}sld":
            raise PptxImportError("Duplicate/invalid slide part")
        ids.add(identifier); parts.add(part)
        slides.append({"slide_id": identifier, "part_path": part, "index": len(slides) + 1})
    if not slides:
        raise PptxImportError("Presentation has no slides")
    # Bound converter output work before upstream parsing; escaped strings and per-node
    # Markdown wrappers can enlarge XML text. This is conservative, not a rendering claim.
    text_bytes = sum(len((item.text or "").encode("utf-8"))
                     for root in archive.xml.values() for item in root.iter())
    if text_bytes * 8 + archive.nodes * 128 > archive.limits.max_content_bytes:
        raise PptxImportError("PPTX conversion work/content bound exceeded before decoding")
    # No renderer, macros, embedded Office package, links, audio or video is executed.
    for part, root in archive.xml.items():
        if any(_local(item.tag) in {"oleObj", "videoFile", "audioFile", "timing", "AlternateContent"} for item in root.iter()):
            warnings.append("UNSUPPORTED_OBJECT_OR_PRESENTATION_EFFECT_NOT_EXECUTED: " + part)
    assets, pixel_total = [], 0
    pending = []
    from PIL import Image
    from packages.domain.document_import import image_metadata, MAX_IMAGE_PIXELS
    for member, raw in archive.data.items():
        content_type = types.get(member, defaults.get(member.rsplit(".", 1)[-1].casefold(), ""))
        if content_type in {"image/png", "image/jpeg"} or member.casefold().endswith((".png", ".jpg", ".jpeg")):
            try:
                with Image.open(BytesIO(raw), formats=("PNG", "JPEG")) as image:
                    width, height = image.size
                    if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                        raise PptxImportError("PPTX image exceeds the 16 million pixel limit")
                    pixel_total += width * height
                    if pixel_total * 4 > archive.limits.max_expanded_bytes:
                        raise PptxImportError("PPTX cumulative decoded image byte limit exceeded")
            except PptxImportError:
                raise
            except Exception as error:
                raise PptxImportError("PPTX contains a corrupt or unsupported raster image") from error
            pending.append((member, raw, content_type))
        elif content_type.startswith("image/"):
            warnings.append("UNSUPPORTED_IMAGE_NOT_RENDERED: " + member)
    # All dimensions/cumulative budget are checked before any image's actual decode.
    for member, raw, content_type in pending:
        info = image_metadata(raw)
        if content_type.startswith("image/") and content_type != info["media_type"]:
            raise PptxImportError("PPTX image type does not match its original bytes")
        assets.append({"member_path": member, "raw_bytes": raw, **info})
    return presentation_part, slides, relations, assets, warnings


def _converter():
    """Verify original source then import the narrow namespace, never markitdown.__init__."""
    manifest_bytes = (VENDOR / "SOURCE.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("commit") != UPSTREAM_COMMIT or manifest.get("release") != "v0.1.8":
        raise PptxImportError("PPTX converter source version is not the approved v0.1.8")
    for item in manifest["files"]:
        path = VENDOR / item["path"]
        if path.is_symlink() or not path.resolve().is_relative_to(VENDOR.resolve()):
            raise PptxImportError("PPTX converter source path is unsafe")
        raw = path.read_bytes()
        if len(raw) != item["byte_length"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            raise PptxImportError("PPTX converter original source integrity failure")
    from packages.adapters.office_vendor.markitdown_v018.converters._pptx_converter import PptxConverter
    from packages.adapters.office_vendor.markitdown_v018._stream_info import StreamInfo
    return PptxConverter(), StreamInfo, hashlib.sha256(manifest_bytes).hexdigest()


def _line_ranges(content, slides, warnings):
    matches = list(re.finditer(r"(?m)^<!-- Slide number: ([0-9]+) -->$", content))
    lines = max(1, content.count("\n") + 1)
    if len(matches) != len(slides) or [int(m[1]) for m in matches] != list(range(1, len(slides) + 1)):
        warnings.append("SLIDE_LINE_MAPPING_AMBIGUOUS: rendered input collides with upstream slide markers")
        return [(1, lines, 0, len(content), "DOCUMENT") for _ in slides]
    result = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        result.append((content.count("\n", 0, match.start()) + 1,
                       content.count("\n", 0, end - 1) + 1, match.start(), end, "SLIDE"))
    return result


def _source_nodes(archive, slides, relations, content, assets, warnings, binding):
    nodes, edges, asset_sources = [], [], []
    ranges = _line_ranges(content, slides, warnings)
    asset_members = {item["member_path"] for item in assets}
    for slide, line_range in zip(slides, ranges):
        part, slide_index = slide["part_path"], slide["index"]
        root, used = archive.xml[part], set()
        paths = _paths(root)
        start, end, offset, stop, scope = line_range
        slide.update({"line_start": start, "line_end": end, "line_anchor_scope": scope})
        sid = f"slide:{slide_index}:{part}"
        nodes.append({"node_id": sid, "kind": "slide", "text": "", "part_path": part,
                      "node_path": paths[id(root)], "line_start": start, "line_end": end,
                      "line_anchor_scope": scope, **binding})

        def add(element, kind, text, parent, source_part=part, source_paths=paths, explicit_id=None):
            path = source_paths[id(element)]
            identifier = sid + ":" + source_part + "#" + path if explicit_id is None else sid + ":shape:" + explicit_id
            node_scope, node_start, node_end = scope, start, end
            # Match only inside the verified slide segment, never claim a synthetic title.
            position = content.find(text, offset, stop) if text and scope == "SLIDE" else -1
            unique = position >= 0 and content.find(text, position + 1, stop) < 0
            if unique:
                node_start = content.count("\n", 0, position) + 1
                node_end = content.count("\n", 0, position + len(text) - 1) + 1
                node_scope = "TEXT_MATCH"
            elif text:
                warnings.append("SOURCE_TEXT_TRANSFORMED_OR_NOT_RENDERED: " + source_part + "#" + path)
            node = {"node_id": identifier, "kind": kind, "text": text,
                    "part_path": source_part, "node_path": path, "slide_index": slide_index,
                    "line_start": node_start, "line_end": node_end,
                    "line_anchor_scope": node_scope, **binding}
            nodes.append(node); edges.append({"from": parent, "to": identifier, "kind": "contains"})
            return node

        def walk(container, parent):
            for shape in container:
                name = _local(shape.tag)
                if name not in {"sp", "pic", "graphicFrame", "cxnSp", "grpSp"}:
                    continue
                metadata = next((item for item in shape.iter() if _local(item.tag) == "cNvPr"), None)
                identifier = None if metadata is None else metadata.get("id")
                if not identifier or identifier in used:
                    raise PptxImportError("Missing/duplicate source shape ID on one slide")
                used.add(identifier)
                node = add(shape, "group" if name == "grpSp" else name,
                           "" if name in {"grpSp", "graphicFrame"} else _text(shape), parent, explicit_id=identifier)
                node["shape_id"] = identifier; node["name"] = metadata.get("name", "")
                if name == "grpSp":
                    walk(shape, node["node_id"])
                    continue
                for item in shape.iter():
                    local = _local(item.tag)
                    if local == "tc":
                        add(item, "table_cell", _text(item), node["node_id"])
                    elif local in {"blip", "chart"}:
                        rid = _attr(item, "embed") or _attr(item, "link") or _attr(item, "id")
                        link = relations.get(part, {}).get(rid)
                        if link is None:
                            raise PptxImportError("Shape references a missing OPC relationship")
                        if link["external"]:
                            warnings.append("EXTERNAL_SHAPE_RESOURCE_NOT_FETCHED: " + part + "#" + paths[id(item)])
                            continue
                        target = link["target"]
                        if local == "blip":
                            if target in asset_members:
                                asset_sources.append({"asset_member_path": target, "relationship_id": rid,
                                    "part_path": part, "node_path": paths[id(item)], "node_id": node["node_id"],
                                    "line_start": node["line_start"], "line_end": node["line_end"], **binding})
                            else:
                                warnings.append("IMAGE_REFERENCE_WITHOUT_SUPPORTED_RASTER: " + target)
                        else:
                            chart = archive.xml.get(target)
                            if chart is None:
                                raise PptxImportError("Chart relationship has no XML source")
                            chart_paths = _paths(chart)
                            chart_node = add(chart, "chart", "", node["node_id"], target, chart_paths)
                            for value in chart.iter():
                                if _local(value.tag) in {"v", "t"} and value.text:
                                    add(value, "chart_cached_value", value.text, chart_node["node_id"], target, chart_paths)
                            warnings.append("CHART_CACHED_DATA_ONLY_NO_RENDER_OR_EMBEDDED_WORKBOOK_EXECUTION: " + target)
        tree = next((item for item in root.iter() if _local(item.tag) == "spTree"), None)
        if tree is None:
            raise PptxImportError("Slide shape tree is missing")
        walk(tree, sid)
        for link in relations.get(part, {}).values():
            if link["type"].endswith("/notesSlide") and not link["external"]:
                note_part = link["target"]; note = archive.xml.get(note_part)
                if note is None:
                    raise PptxImportError("Slide notes XML is missing")
                note_paths = _paths(note)
                for paragraph in note.iter():
                    if paragraph.tag == "{" + DRAWING_NS + "}p" and _text(paragraph):
                        add(paragraph, "notes_paragraph", _text(paragraph), sid, note_part, note_paths)
    linked = {item["asset_member_path"] for item in asset_sources}
    for member in sorted(asset_members - linked):
        warnings.append("RASTER_MEDIA_WITHOUT_PARSED_SLIDE_REFERENCE: " + member)
    return nodes, edges, asset_sources


def parse_pptx(raw: bytes, *, filename="document.pptx", limits: DiagramLimits | None = None,
               source_context: dict | None = None) -> ParsedDocument:
    """Return PPTX Markdown, graph, original PNG/JPEG assets and explicit warnings.

    Optional source_context has exactly document_id, revision and raw_sha256. Its
    revision/hash is copied to nodes/assets; a stale raw hash fails before conversion.
    Node line_anchor_scope TEXT_MATCH is an actual Markdown text match; SLIDE or
    DOCUMENT is a deliberately conservative region, not an exact transformed span.
    No filesystem extraction, network, model caption, plugin, OCR or Office execution.
    """
    if not isinstance(filename, str) or not filename.lower().endswith(".pptx"):
        raise PptxImportError("Only macro-free .pptx is supported; export legacy .ppt/.pptm")
    if not isinstance(raw, bytes):
        raise PptxImportError("PPTX input must be immutable bytes")
    raw_sha = hashlib.sha256(raw).hexdigest()
    binding = {"raw_sha256": raw_sha}
    if source_context is not None:
        if (not isinstance(source_context, dict) or set(source_context) != {"document_id", "revision", "raw_sha256"}
            or not isinstance(source_context["document_id"], str) or not source_context["document_id"]
            or len(source_context["document_id"]) > 256 or any(ord(c) < 32 for c in source_context["document_id"])
            or type(source_context["revision"]) is not int or source_context["revision"] < 1
            or source_context["raw_sha256"] != raw_sha):
            raise PptxImportError("Invalid or stale PPTX document source revision/hash binding")
        binding.update(source_context)
    bounds = limits or DiagramLimits()
    try:
        archive = _Archive(raw, bounds)
        presentation_part, slides, relations, assets, warnings = _preflight(archive)
        # Structural IDs/relationships also fail before loading the upstream parser.
        _source_nodes(archive, slides, relations, "", assets, [], binding)
        for asset in assets:
            asset.update(binding)
        converter, StreamInfo, source_manifest_sha = _converter()
        # Never supply llm_client, llm_model, image HTML callback or plugin options.
        result = converter.convert(BytesIO(raw), StreamInfo(extension=".pptx"))
        # Root citations use str.splitlines(); retain line breaks but normalise its
        # other separators so source/character/line metadata agree on physical lines.
        content = re.sub(r"\r\n|[\r\v\f\x1c-\x1e\x85\u2028\u2029]", "\n", result.markdown)
        if len(content.encode("utf-8")) > bounds.max_content_bytes:
            raise PptxImportError("Converted PPTX content exceeds the byte limit")
        if "[unsupported chart]" in content:
            warnings.append("CHART_UNSUPPORTED_BY_UPSTREAM_CONVERTER")
        nodes, edges, asset_sources = _source_nodes(archive, slides, relations, content, assets, warnings, binding)
        # Add the existing Store/reader schema without discarding the exact bridge
        # fields used by standalone consumers or the original source/line scopes.
        by_id = {node['node_id']: node for node in nodes}
        for node in nodes:
            node['id'] = node['node_id']
            index = node.get('slide_index')
            if index is None:
                index = next(s['index'] for s in slides if s['part_path'] == node['part_path'])
            node['source'] = {'scope': f'pptx:slide:{index}', 'part': node['part_path'],
                              'node_id': node.get('shape_id', node['node_path']),
                              'node_path': node['node_path'], 'line_anchor_scope': node['line_anchor_scope'],
                              **binding}
            node['assets'] = []
        for edge in edges:
            edge.update({'source': edge['from'], 'target': edge['to'],
                         'origin': by_id[edge['to']]['source']})
            by_id[edge['to']]['parent_id'] = edge['from']
        by_member = {asset['member_path']: asset for asset in assets}
        for asset in assets:
            asset['source_anchors'] = []
        for entry in asset_sources:
            node = by_id[entry['node_id']]
            node['assets'].append(entry['asset_member_path'])
            by_member[entry['asset_member_path']]['source_anchors'].append({**node['source'],
                'node_path': entry['node_path'], 'relationship_id': entry['relationship_id'],
                'line_start': entry['line_start'], 'line_end': entry['line_end']})
        pages = [{**slide, 'id': slide['slide_id'], 'name': f"幻灯片 {slide['index']}",
                  'scope': f"pptx:slide:{slide['index']}",
                  'source': {'part': slide['part_path'], 'node_id': slide['slide_id']}}
                 for slide in slides]
        return ParsedDocument(content, "pptx", {"slides": slides, "source": binding,
            "pages": pages,
            "presentation_part": presentation_part, "asset_sources": asset_sources,
            "converter": {"name": "MarkItDown PPTX", "release": "v0.1.8", "commit": UPSTREAM_COMMIT,
                          "source_manifest_sha256": source_manifest_sha, "overlay": "eeas.pptx-source.v1"}},
            {"nodes": nodes, "edges": edges}, assets, list(dict.fromkeys(warnings)))
    except PptxImportError:
        raise
    except (DiagramImportError, zipfile.BadZipFile, ET.ParseError, UnicodeError) as error:
        raise PptxImportError(str(error).replace(".vsdx", ".pptx")) from error
    except ImportError as error:
        raise PptxImportError("PPTX dependency closure is unavailable; install the bundled PPTX wheels offline") from error
    except Exception as error:
        raise PptxImportError("PPTX could not be parsed safely (" + type(error).__name__ + ")") from error

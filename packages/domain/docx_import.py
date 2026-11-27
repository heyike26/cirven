"""Read DOCX text/structure/assets from bounded, inert OOXML parts in memory."""
from __future__ import annotations

from pathlib import PurePosixPath
import xml.etree.ElementTree as ET
import zipfile

from packages.domain.diagram_import import (DiagramImportError, DiagramLimits, ParsedDocument,
    _Archive, _Result, _child, _children, _local, _rels, _type_target)

WORD_NAMESPACES = {"http://schemas.openxmlformats.org/wordprocessingml/2006/main",
                   "http://purl.oclc.org/ooxml/wordprocessingml/main"}
# Standard Word style tables consume XML nodes without producing semantic graph nodes.
DOCX_MAX_XML_NODES = 65536


class DocxImportError(DiagramImportError):
    pass


def _attribute(element, name, default=None):
    return next((value for key, value in element.attrib.items() if _local(key) == name), default)


def _word(element, name):
    return _local(element.tag) == name and element.tag.startswith("{") and element.tag[1:].split("}", 1)[0] in WORD_NAMESPACES


def _choice(element):
    choices = _children(element, "Choice")
    return choices[0] if choices else _child(element, "Fallback")


def _paths(root):
    """Deterministic local-name/sibling-index paths; IDs are optional in Word XML."""
    result = {id(root): "/" + _local(root.tag) + "[1]"}
    stack = [root]
    while stack:
        element = stack.pop()
        counts = {}
        for child in element:
            name = _local(child.tag)
            counts[name] = counts.get(name, 0) + 1
            result[id(child)] = result[id(element)] + "/" + name + "[" + str(counts[name]) + "]"
            stack.append(child)
    return result


class _Reader:
    def __init__(self, archive, result):
        self.archive, self.result = archive, result
        self.parts, self.parsed, self.note_requests, self.section_requests = [], {}, [], []
        self.related, self.note_seen = set(), set()

    def warning(self, part, path, message):
        self.result.warn(message + ": " + part + "#" + path)

    def _paragraph(self, element, part, paths, parent, relations):
        text, bindings, notes, unsupported, links = [], [], [], [], []
        path = paths[id(element)]

        def inline(item):
            name = _local(item.tag)
            if item is not element and _word(item, "p"):
                return  # Text-box paragraphs have independent exact source anchors.
            if _word(item, "t") or _word(item, "delText"):
                text.append(item.text or "")
                return
            if _word(item, "tab"):
                text.append("\t")
            elif _word(item, "br") or _word(item, "cr"):
                text.append("\n")
            elif _word(item, "noBreakHyphen"):
                text.append("\u2011")
            elif _word(item, "softHyphen"):
                text.append("\u00ad")
            elif _word(item, "instrText"):
                self.warning(part, path, "Field instruction is not executed or treated as ordinary text; cached result may be present")
                return
            elif _word(item, "fldSimple"):
                self.warning(part, path, "Simple field is not evaluated; only existing cached text is preserved")
            elif name in {"oMath", "oMathPara"}:
                tokens = ''.join(part.text or '' for part in item.iter() if _local(part.tag) == 't')
                text.append("[UNSUPPORTED math structure; original tokens: " + tokens + "]")
                self.warning(part, paths[id(item)], "Math structure is not rendered or evaluated; original XML tokens are explicit")
                return
            elif _word(item, "sym"):
                text.append("[symbol font=" + _attribute(item, "font", "") + " code=" + _attribute(item, "char", "") + "]")
                self.warning(part, path, "Font-dependent symbol is preserved by code, not guessed as a glyph")
            if name in {"footnoteReference", "endnoteReference", "commentReference", "commentRangeStart"} and _word(item, name):
                role = "comments" if name.startswith("comment") else "footnotes" if name.startswith("footnote") else "endnotes"
                identifier = _attribute(item, "id")
                if identifier is None:
                    raise DocxImportError("Note reference lacks its required ID")
                notes.append((role, identifier))
                text.append("[" + role + ":" + identifier + "]")
            if name in {"blip", "imagedata"}:
                for attr in ("embed", "link") if name == "blip" else ("id",):
                    identifier = _attribute(item, attr)
                    if identifier:
                        bindings.append(identifier)
            elif name in {"chart", "OLEObject", "altChunk", "relIds"}:
                role = {"chart": "chart", "OLEObject": "OLE object", "altChunk": "alternate-format content", "relIds": "SmartArt"}[name]
                ids = [value for key, value in item.attrib.items() if _local(key) in ({"dm", "lo", "qs", "cs"} if name == "relIds" else {"id"})]
                if not ids:
                    unsupported.append((role, None))
                for identifier in ids:
                    unsupported.append((role, identifier))
            if _word(item, "hyperlink"):
                links.append({"relationship_id": _attribute(item, "id"), "bookmark": _attribute(item, "anchor")})
                self.warning(part, path, "Hyperlink is preserved as data and never followed")
            tracked = name in {"ins", "del", "moveFrom", "moveTo"} and _word(item, name)
            if tracked:
                text.append("[tracked-" + name + "]")
                self.warning(part, path, "Tracked text is explicitly marked; revisions are not silently accepted")
            if name == "AlternateContent":
                # Preserve one coherent representation, never duplicate a Choice
                # and its Fallback picture/text. Any unsupported part is warned.
                chosen = _choice(item)
                self.warning(part, path, "AlternateContent first Choice used; Fallback not merged")
                if chosen is not None:
                    inline(chosen)
            else:
                for child in item:
                    inline(child)
            if tracked:
                text.append("[/tracked-" + name + "]")

        inline(element)
        for role, identifier in unsupported:
            relation = relations.get(identifier, {})
            target = relation.get("target")
            if target:
                self.related.add(target)
            text.append("[UNSUPPORTED " + role + ": " + (target or str(identifier)) + "]")
            self.warning(part, path, "Unsupported " + role + " is retained in original package, not rendered: " + (target or str(identifier)))
        node = self.result.node("docx:" + part, path, part, "".join(text), parent, "paragraph")
        node["source"]["anchor_kind"] = "xml-local-path"
        node["hyperlinks"] = links
        node["unsupported_parts"] = [{"kind": role, "relationship_id": rid,
            "member_path": relations.get(rid, {}).get("target")} for role, rid in unsupported]
        style = _child(_child(element, "pPr") or [], "pStyle")
        node["style"] = _attribute(style, "val") if style is not None else None
        for identifier in bindings:
            relation = relations.get(identifier)
            if relation and relation["target"] and relation["type"].endswith("/image"):
                self.related.add(relation["target"])
                self.result.asset_ref("/" + relation["target"], node, part)
            else:
                self.warning(part, path, "Missing/external/non-image picture relationship " + identifier)
        for role, identifier in notes:
            request = (part, node["id"], role, identifier)
            if request not in self.note_seen:
                self.note_seen.add(request)
                self.note_requests.append(request)
        return node

    def _blocks(self, element, part, paths, parent, relations):
        trees = []
        if _local(element.tag) == "AlternateContent":
            chosen = _choice(element)
            self.warning(part, paths[id(element)], "AlternateContent first Choice used; Fallback not merged")
            children = [chosen] if chosen is not None else []
        else:
            children = list(element)
        for child in children:
            path = paths[id(child)]
            name = _local(child.tag)
            if _word(child, "p"):
                node = self._paragraph(child, part, paths, parent, relations)
                tree = {"id": node["id"], "children": []}
                # A text box is separate from the containing run's paragraph.
                stack = list(reversed(list(child)))
                while stack:
                    box = stack.pop()
                    if _word(box, "txbxContent"):
                        tree["children"].extend(self._blocks(box, part, paths, node["id"], relations))
                    elif _local(box.tag) == "AlternateContent":
                        chosen = _choice(box)
                        if chosen is not None:
                            stack.append(chosen)
                    else:
                        stack.extend(reversed(list(box)))
                trees.append(tree)
            elif name in {"tbl", "tr", "tc"} and _word(child, name):
                kind = {"tbl": "table", "tr": "table-row", "tc": "table-cell"}[name]
                node = self.result.node("docx:" + part, path, part, "", parent, kind)
                node["source"]["anchor_kind"] = "xml-local-path"
                if name == "tc":
                    props = _child(child, "tcPr")
                    span = _child(props or [], "gridSpan")
                    merge = _child(props or [], "vMerge")
                    node["grid_span"] = _attribute(span, "val", "1") if span is not None else "1"
                    node["vertical_merge"] = _attribute(merge, "val", "continue") if merge is not None else None
                trees.append({"id": node["id"], "children": self._blocks(child, part, paths, node["id"], relations)})
            elif _word(child, "sectPr"):
                pass  # Main-part scan handles tail and paragraph pPr references once.
            elif name in {"altChunk", "subDoc"} and _word(child, name):
                rid = _attribute(child, "id")
                target = relations.get(rid, {}).get("target")
                self.warning(part, path, "Unsupported " + name + " content: " + (target or str(rid)))
                node = self.result.node("docx:" + part, path, part, "[UNSUPPORTED " + name + ": " + (target or str(rid)) + "]", parent, "unsupported")
                node["source"]["anchor_kind"] = "xml-local-path"
                trees.append({"id": node["id"], "children": []})
                if target:
                    self.related.add(target)
            elif name in {"ins", "del", "moveFrom", "moveTo"} and _word(child, name):
                self.warning(part, path, "Tracked block is explicitly marked; revisions are not silently accepted")
                self.result.emit("[tracked-block-" + name + "]")
                trees.extend(self._blocks(child, part, paths, parent, relations))
                self.result.emit("[/tracked-block-" + name + "]")
            elif name not in {"pPr", "rPr", "tblPr", "tblGrid", "trPr", "tcPr"}:
                # Content controls/custom XML/revision wrappers can contain real
                # paragraphs and tables. Walk them in original XML order.
                trees.extend(self._blocks(child, part, paths, parent, relations))
        return trees

    def part(self, name, role, expected):
        key = (name, role)
        if key in self.parsed:
            return self.parsed[key]
        root = self.archive.require_xml(name, expected)
        if not _word(root, expected):
            raise DocxImportError("Required Word part has an unsupported namespace")
        paths, relations = _paths(root), _rels(self.archive, name, self.result)
        container = self.result.node("docx:" + name, paths[id(root)], name, "", kind="document-part")
        container["source"]["anchor_kind"] = "xml-local-path"
        record = {"part": name, "kind": role, "id": container["id"], "nodes": []}
        self.parts.append(record)
        self.parsed[key] = {"record": record, "relations": relations, "paths": paths, "root": root, "node": container}
        self.related.add(name)
        if role == "body":
            body = _child(root, "body")
            if body is None:
                raise DocxImportError("DOCX main document lacks its body")
            record["nodes"] = self._blocks(body, name, paths, container["id"], relations)
            for binding in body.iter():
                role = "header" if _word(binding, "headerReference") else "footer" if _word(binding, "footerReference") else None
                if role:
                    self.section_requests.append((name, paths[id(binding)], role, _attribute(binding, "id")))
        elif role in {"header", "footer"}:
            record["nodes"] = self._blocks(root, name, paths, container["id"], relations)
        return self.parsed[key]

    def finish(self, document):
        main = self.part(document, "body", "document")
        for part, path, role, identifier in self.section_requests:
            relations = self.parsed[(part, "body")]["relations"]
            relation = relations.get(identifier)
            if not relation or not relation["target"] or not relation["type"].endswith("/" + role):
                self.warning(part, path, "Missing/external section " + role + " relationship")
                continue
            if relation["target"] not in self.archive.xml:
                self.warning(part, path, "Missing section part " + relation["target"])
                continue
            supplementary = self.part(relation["target"], role, "hdr" if role == "header" else "ftr")
            self.result.edges.append({"kind": "section-binding", "source": main["node"]["id"],
                "target": supplementary["node"]["id"], "origin": {"part": part, "node_id": path}})
        parsed_notes = {}
        for part, source, role, identifier in self.note_requests:
            main_relations = main["relations"]
            candidates = [r["target"] for r in main_relations.values() if r["type"].endswith("/" + role) and r["target"]]
            if len(candidates) != 1 or candidates[0] not in self.archive.xml:
                self.result.warn("Missing/ambiguous " + role + " part for " + part + " note " + identifier)
                continue
            target = candidates[0]
            section = self.part(target, role, role)
            singular = {"footnotes": "footnote", "endnotes": "endnote", "comments": "comment"}[role]
            matches = [item for item in _children(section["root"], singular) if _attribute(item, "id") == identifier]
            if len(matches) != 1:
                self.result.warn("Missing/duplicate " + role + " ID " + identifier + " in " + target)
                continue
            note = matches[0]
            if _attribute(note, "type") in {"separator", "continuationSeparator", "continuationNotice"}:
                self.result.warn("Structural note separator is not ordinary note text: " + target + "#" + identifier)
                continue
            key = (target, identifier)
            if key not in parsed_notes:
                path = section["paths"][id(note)]
                node = self.result.node("docx:" + target, path, target, "", section["node"]["id"], singular)
                node["source"]["anchor_kind"] = "xml-local-path"
                node["logical_id"] = identifier
                tree = {"id": node["id"], "children": self._blocks(note, target, section["paths"], node["id"], section["relations"])}
                section["record"]["nodes"].append(tree)
                parsed_notes[key] = node["id"]
            self.result.edges.append({"kind": "note-reference", "source": source, "target": parsed_notes[key],
                "origin": {"part": part, "note_id": identifier, "note_kind": role}})
        for part, root in self.archive.xml.items():
            if _local(root.tag) in {"document", "hdr", "ftr", "footnotes", "endnotes", "comments"} and part not in self.related:
                self.result.warn("Unreferenced text part is retained in original package, not merged: " + part)
            elif ("/charts/" in part or "/diagrams/" in part) and part not in self.related:
                self.result.warn("Unbound unsupported chart/SmartArt part retained in original package: " + part)
        for (part, role), section in self.parsed.items():
            if role not in {"footnotes", "endnotes", "comments"}:
                continue
            singular = {"footnotes": "footnote", "endnotes": "endnote", "comments": "comment"}[role]
            for note in _children(section["root"], singular):
                identifier = _attribute(note, "id")
                if (_attribute(note, "type") not in {"separator", "continuationSeparator", "continuationNotice"}
                        and (part, identifier) not in parsed_notes):
                    self.result.warn("Unreferenced note entry retained in original package, not merged: " + part + "#" + str(identifier))
        self.result.warn("DOCX layout, numbering, formulas and embedded charts/objects are not rendered; no OCR; original package must be retained")
        return self.result.finish("docx", {"parts": self.parts})


def parse_docx(filename: str, raw: bytes, *, limits: DiagramLimits | None = None) -> ParsedDocument:
    if not isinstance(filename, str) or not filename or len(filename) > 240 or any(c in filename for c in ("/", "\\", ":", "\x00")):
        raise DocxImportError("Filename must be a plain filename")
    if PurePosixPath(filename).suffix.lower() != ".docx":
        raise DocxImportError("Legacy/macro/template Word is unsupported; export a macro-free .docx")
    limits = limits or DiagramLimits()
    if not isinstance(limits, DiagramLimits) or any(type(value) is not int or value <= 0 for value in vars(limits).values()):
        raise DocxImportError("DOCX limits must be positive integers")
    if not isinstance(raw, bytes) or len(raw) > limits.max_archive_bytes:
        raise DocxImportError("DOCX archive exceeds byte limit")
    try:
        # Shared reader rejects non-Stored/Deflate before decompression: output
        # bounds alone do not bound LZMA dictionary allocations.
        archive = _Archive(raw, limits, xml_node_limit=DOCX_MAX_XML_NODES)
        types = archive.require_xml("[Content_Types].xml", "Types")
        if any("macroenabled" in node.get("ContentType", "").lower() for node in types):
            raise DocxImportError("Macro-enabled Word package is unsupported")
        result = _Result(archive)
        document = _type_target(_rels(archive, "", result), "officeDocument")
        return _Reader(archive, result).finish(document)
    except DiagramImportError as exception:
        if isinstance(exception, DocxImportError):
            raise
        raise DocxImportError(str(exception)) from exception
    except (zipfile.BadZipFile, ET.ParseError, UnicodeError, TypeError, ValueError, OSError, RecursionError,
            KeyError, RuntimeError, NotImplementedError) as exception:
        raise DocxImportError("Malformed/unsupported DOCX archive") from exception

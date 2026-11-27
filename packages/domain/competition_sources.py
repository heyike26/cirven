"""Explicit selected competition sources, immutable captures and real Store materials.

This module captures evidence, not competitor scores or verified capabilities.
It never searches, renders JavaScript, follows page links, or invokes an Agent.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import ipaddress
import json
from pathlib import Path
import re
import socket
import sqlite3
import threading
import time
from typing import Literal
from urllib.parse import urljoin, urlsplit
import uuid

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.domain.document_import import parse_document, DocumentImportError
from packages.domain.intake import Hash
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, digest

MAX_NETWORK_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
NETWORK_SECONDS = 20
MAX_REDIRECTS = 3


def sha(raw): return hashlib.sha256(raw).hexdigest()
def stamp(): return datetime.now(timezone.utc).isoformat()


class CompetitionDocumentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1, strict=True)
    sha256: Hash


class CompetitionSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    mode: Literal["OFFLINE_PACKAGE", "ONLINE_URL"]
    publisher_kind: Literal["OFFICIAL", "USER_TEST", "OTHER"]
    document_ref: CompetitionDocumentRef | None = None
    url: str | None = Field(default=None, min_length=1, max_length=4096)

    @model_validator(mode="after")
    def exact_selection(self):
        if self.mode == "OFFLINE_PACKAGE":
            if self.document_ref is None or self.url is not None:
                raise ValueError("Offline capture requires only a selected document_ref")
        elif self.url is None or self.document_ref is not None:
            raise ValueError("Online capture requires only an explicitly selected public URL")
        return self


class CaptureDenied(InvalidInput):
    """A stable error code; transport exceptions and response text are not echoed."""
    def __init__(self, code, observed=None):
        super().__init__(code)
        self.observed = observed or {}


def public_url(value):
    if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise CaptureDenied("PUBLIC_URL_INVALID")
    try:
        url = httpx.URL(value)
        parsed = urlsplit(value)
        if (url.scheme not in {"http", "https"} or not url.host or parsed.username is not None or
                parsed.password is not None or parsed.fragment or "%" in url.host):
            raise ValueError()
        port = url.port if url.port is not None else (443 if url.scheme == "https" else 80)
        if port != (443 if url.scheme == "https" else 80): raise ValueError()
    except (ValueError, httpx.InvalidURL):
        raise CaptureDenied("PUBLIC_URL_INVALID") from None
    return url


def public_address(value):
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        raise CaptureDenied("PUBLIC_ADDRESS_INVALID") from None
    mapped = getattr(ip, "ipv4_mapped", None)
    checked = mapped or ip
    if (not checked.is_global or checked.is_multicast or checked.is_reserved or
            checked.is_loopback or checked.is_link_local or checked.is_unspecified or
            getattr(ip, "sixtofour", None) or getattr(ip, "teredo", None)):
        raise CaptureDenied("PUBLIC_ADDRESS_REQUIRED")
    return str(ip)


def resolve_public(url, timeout=4):
    try:
        ipaddress.ip_address(url.host)
        addresses = [url.host]
    except ValueError:
        done, result = threading.Event(), {}
        def resolve():
            try: result["records"] = socket.getaddrinfo(url.host, url.port, type=socket.SOCK_STREAM)
            except OSError: result["failed"] = True
            finally: done.set()
        # A timed-out system resolver may finish its DNS query later; that
        # daemon can never issue an HTTP request or accept a late DNS result.
        threading.Thread(target=resolve, daemon=True).start()
        if not done.wait(timeout): raise CaptureDenied("PUBLIC_DNS_DEADLINE_EXCEEDED")
        if result.get("failed"):
            raise CaptureDenied("PUBLIC_DNS_LOOKUP_FAILED") from None
        # All DNS answers must be public. The selected checked IP is pinned
        # into the request URL, preventing a second DNS lookup/rebinding.
        addresses = [r[4][0] for r in result["records"]]
    if not addresses: raise CaptureDenied("PUBLIC_DNS_LOOKUP_FAILED")
    return sorted(set(public_address(ip) for ip in addresses), key=lambda ip: (ipaddress.ip_address(ip).version, ip))


class SelectedPublicFetcher:
    """A single selected resource, with bounded manually admitted redirects."""
    def fetch(self, selected_url):
        hops = []
        try:
            return self._fetch(selected_url, hops)
        except (CaptureDenied, httpx.HTTPError) as error:
            code = str(error) if isinstance(error, CaptureDenied) else "CAPTURE_HTTP_TRANSPORT_ERROR"
            observed = {"requested_url": selected_url, "url": selected_url,
                "final_url": hops[-1]["url"] if hops else None, "redirects": hops,
                "http_status": hops[-1]["status"] if hops else None, "captured_at": stamp(),
                "network_requests": len(hops)}
            raise CaptureDenied(code, observed) from None

    def _fetch(self, selected_url, hops):
        url = public_url(selected_url)
        deadline = time.monotonic() + NETWORK_SECONDS
        raw, status, content_type = b"", None, ""
        # Explicit public IP + Host + sni_hostname is an official HTTPX seam.
        # No ambient proxy, cookies, auth, redirects, retry, or certificate bypass.
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=NETWORK_SECONDS) as client:
            for index in range(MAX_REDIRECTS + 1):
                if time.monotonic() >= deadline: raise CaptureDenied("CAPTURE_DEADLINE_EXCEEDED")
                addresses = resolve_public(url, min(4, max(.01, deadline - time.monotonic())))
                pinned = url.copy_with(host=addresses[0])
                host_header = "[" + url.host + "]" if ":" in url.host else url.host
                remaining = max(.01, deadline - time.monotonic())
                client.cookies.clear()
                with client.stream("GET", pinned, timeout=remaining,
                        headers={"Host": host_header, "Accept-Encoding": "identity", "User-Agent": "EEAS-Selected-Source/1"},
                        extensions={"sni_hostname": url.host}) as response:
                    status = response.status_code
                    content_type = response.headers.get("content-type", "")
                    hop = {"url": str(url), "status": status, "resolved_public_addresses": addresses,
                        "connected_address": addresses[0]}
                    hops.append(hop)
                    if status in {301, 302, 303, 307, 308}:
                        if index == MAX_REDIRECTS: raise CaptureDenied("CAPTURE_REDIRECT_LIMIT")
                        location = response.headers.get("location")
                        if not location: raise CaptureDenied("CAPTURE_REDIRECT_WITHOUT_LOCATION")
                        url = public_url(urljoin(str(url), location))
                        continue
                    encoding = response.headers.get("content-encoding", "identity").lower()
                    if encoding not in {"", "identity"}: raise CaptureDenied("CAPTURE_CONTENT_ENCODING_UNSUPPORTED")
                    length = response.headers.get("content-length")
                    if length is not None:
                        try: oversized = int(length) > MAX_NETWORK_BYTES or int(length) < 0
                        except ValueError: raise CaptureDenied("CAPTURE_CONTENT_LENGTH_INVALID") from None
                        if oversized: raise CaptureDenied("CAPTURE_BODY_TOO_LARGE")
                    parts, size = [], 0
                    for part in response.iter_raw(chunk_size=65536):
                        size += len(part)
                        if size > MAX_NETWORK_BYTES: raise CaptureDenied("CAPTURE_BODY_TOO_LARGE")
                        if time.monotonic() >= deadline: raise CaptureDenied("CAPTURE_DEADLINE_EXCEEDED")
                        parts.append(part)
                    raw = b"".join(parts)
                    return {"requested_url": selected_url, "final_url": str(url), "http_status": status,
                        "content_type": content_type, "redirects": hops, "captured_at": stamp(), "raw": raw,
                        "last_modified": response.headers.get("last-modified"), "etag": response.headers.get("etag")}
        raise CaptureDenied("CAPTURE_NO_RESPONSE")


class _PageText(HTMLParser):
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
    OMIT = {"head", "script", "style", "noscript", "template"}
    BLOCK = {"p", "div", "article", "section", "main", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "br"}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.parts, self.title_parts = [], [], []
        self.script = False
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        omitted = tag in self.OMIT or "hidden" in attrs or attrs.get("aria-hidden", "").lower() == "true"
        omitted = omitted or bool(self.stack and self.stack[-1][1])
        if tag == "script": self.script = True
        if tag in self.BLOCK and not omitted: self.parts.append("\n")
        if tag not in self.VOID: self.stack.append((tag, omitted))
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID: self.handle_endtag(tag)
    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                self.stack = self.stack[:index]; break
        if tag in self.BLOCK: self.parts.append("\n")
    def handle_data(self, data):
        if any(tag == "title" for tag, _omitted in self.stack): self.title_parts.append(data)
        if not self.stack or not self.stack[-1][1]: self.parts.append(data)
    def values(self):
        text = "\n".join(" ".join(line.split()) for line in "".join(self.parts).splitlines()).strip()
        title = " ".join("".join(self.title_parts).split())
        return title, text


def parse_capture(raw, content_type, final_url):
    kind = content_type.split(";", 1)[0].strip().lower()
    if kind == "application/pdf":
        try: parsed = parse_document("selected-source.pdf", raw)
        except DocumentImportError: raise CaptureDenied("CAPTURE_PDF_PARSE_FAILED") from None
        title, plaintext = Path(urlsplit(final_url).path).name or "selected-source.pdf", parsed.content
        warnings = list(parsed.warnings)
        coverage = "PARTIAL" if warnings else "SELECTED_PDF_TEXT"
    elif kind in {"text/html", "application/xhtml+xml", "text/plain", "text/markdown"}:
        charset = re.search(r"charset\s*=\s*[\"']?([^;\s\"']+)", content_type, re.I)
        if charset is None and kind in {"text/html", "application/xhtml+xml"}:
            charset = re.search(rb"charset\s*=\s*[\"']?([^;\s\"'>]+)", raw[:8192], re.I)
        encoding = charset[1] if charset else "utf-8"
        if isinstance(encoding, bytes): encoding = encoding.decode("ascii", errors="replace")
        warnings = []
        try: text = raw.decode(encoding)
        except LookupError: raise CaptureDenied("CAPTURE_CHARSET_UNSUPPORTED") from None
        except UnicodeDecodeError:
            text = raw.decode(encoding, errors="replace"); warnings.append("CAPTURE_ENCODING_REPLACEMENTS")
        if "\x00" in text: raise CaptureDenied("CAPTURE_BINARY_TEXT_REJECTED")
        if kind in {"text/html", "application/xhtml+xml"}:
            parser = _PageText(); parser.feed(text); parser.close(); title, plaintext = parser.values()
            warnings += ["HTML_TEXT_ONLY_NO_JAVASCRIPT_CSS_IMAGES_OR_OCR", "SINGLE_PAGE_NOT_WHOLE_SITE"]
            if parser.script: warnings.append("SCRIPT_CONTENT_NOT_EXECUTED")
            if len(plaintext.strip()) < 80: warnings.append("INSUFFICIENT_PAGE_TEXT")
            coverage = "PARTIAL_HTML_TEXT"
        else:
            title = Path(urlsplit(final_url).path).name or "selected source"
            plaintext, coverage = text, "SELECTED_TEXT_RESOURCE"
    else: raise CaptureDenied("CAPTURE_CONTENT_TYPE_UNSUPPORTED")
    if len(plaintext.encode()) > MAX_TEXT_BYTES: raise CaptureDenied("CAPTURE_TEXT_TOO_LARGE")
    if not plaintext.strip(): warnings.append("NO_EXTRACTED_TEXT")
    return {"title": title, "plaintext": plaintext, "coverage": coverage, "warnings": warnings}


class CompetitionSources:
    """Separate durable capture ledger; Store remains the material authority."""
    def __init__(self, store, data_dir, *, fetcher=None):
        self.store = store
        directory = Path(data_dir) / "competition-sources"; directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.database = directory / "captures.sqlite3"
        self.fetcher = fetcher or SelectedPublicFetcher()
        self._active = set()
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS captures (
                id TEXT PRIMARY KEY, project_id TEXT NOT NULL, operation_id TEXT NOT NULL,
                request_sha256 TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL,
                result TEXT, result_sha256 TEXT, raw BLOB, raw_sha256 TEXT,
                UNIQUE(project_id,operation_id))""")
        self.database.chmod(0o600)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database, timeout=10); db.row_factory = sqlite3.Row
        try:
            with db: yield db
        finally: db.close()

    def _view(self, row, *, replayed=False):
        if row["result"] is None:
            return {"id": row["id"], "project_id": row["project_id"], "operation_id": row["operation_id"],
                "status": "PROCESSING" if row["id"] in self._active else "UNKNOWN_OUTCOME", "replayed": replayed,
                "message": "Capture owner unavailable; this operation will not fetch again automatically"}
        value = json.loads(row["result"])
        if digest(value) != row["result_sha256"] or sha(bytes(row["raw"] or b"")) != row["raw_sha256"]:
            raise Conflict("Competition capture integrity failed")
        ref = value.get("document_ref")
        if ref:
            document = self.store.document(row["project_id"], ref["document_id"], ref["revision"])
            if document["sha256"] != ref["sha256"] or sha(document["raw_bytes"]) != ref["sha256"]:
                raise Conflict("Captured material document bytes changed")
            self.store.document_receipt(row["project_id"], ref["document_id"], ref["revision"])
            if document["content"] != value["plaintext"]:
                raise Conflict("Captured material parsed text changed")
        return {**value, "sha256": row["result_sha256"], "replayed": replayed}

    def list(self, project_id):
        self.store.project(project_id)
        with self._connect() as db:
            rows = db.execute("SELECT * FROM captures WHERE project_id=? ORDER BY rowid DESC", (project_id,)).fetchall()
        return [self._view(row) for row in rows]

    def get(self, project_id, source_id):
        self.store.project(project_id)
        with self._connect() as db:
            row = db.execute("SELECT * FROM captures WHERE project_id=? AND id=?", (project_id, source_id)).fetchone()
        if row is None: raise NotFound("Competition source is outside this project")
        return self._view(row)

    def snapshot(self, project_id, source_id):
        self.get(project_id, source_id)
        with self._connect() as db:
            row = db.execute("SELECT raw,raw_sha256 FROM captures WHERE project_id=? AND id=?", (project_id, source_id)).fetchone()
        raw = bytes(row["raw"] or b"")
        if row["raw_sha256"] is None: raise Conflict("Competition capture has no completed snapshot")
        if sha(raw) != row["raw_sha256"]: raise Conflict("Competition snapshot bytes changed")
        return raw

    def material_context(self, project_id, document_ref):
        """Preserve capture qualifications alongside unchanged source/chunk bytes."""
        ref = CompetitionDocumentRef.model_validate(document_ref).model_dump()
        document = self.store.document(project_id, ref["document_id"], ref["revision"])
        if document["sha256"] != ref["sha256"] or sha(document["raw_bytes"]) != ref["sha256"]:
            raise Conflict("Competition material context bytes changed")
        with self._connect() as db:
            rows = db.execute("SELECT id,result,result_sha256 FROM captures WHERE project_id=? AND result IS NOT NULL ORDER BY rowid",
                (project_id,)).fetchall()
        captures = []
        for row in rows:
            payload = json.loads(row["result"])
            if digest(payload) != row["result_sha256"]: raise Conflict("Competition capture provenance changed")
            if payload.get("document_ref") != ref: continue
            value = self.get(project_id, row["id"])
            captures.append({key: value.get(key) for key in ("id", "sha256", "mode", "publisher_kind",
                "publisher_kind_authority", "observation_authority", "captured_at", "url", "requested_url",
                "final_url", "http_status", "coverage", "warnings", "raw_snapshot_ref")})
        if document["source"].startswith("competition-source:") and document["source"].split(":", 1)[1] not in {c["id"] for c in captures}:
            raise Conflict("Competition source marker has no exact registered capture")
        return {"document_ref": ref, "captures": captures,
            "qualification": "CAPTURE_PROVENANCE_NOT_VERIFIED_PRODUCT_CAPABILITY"}

    def capture(self, project_id, body: CompetitionSourceInput):
        self.store.project(project_id)
        request = body.model_dump(); request_hash = digest(request)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM captures WHERE project_id=? AND operation_id=?", (project_id, body.operation_id)).fetchone()
            if old:
                if old["request_sha256"] != request_hash: raise Conflict("Competition operation binds another request")
                return self._view(old, replayed=True)
            source_id = str(uuid.uuid4())
            db.execute("INSERT INTO captures VALUES (?,?,?,?,?,'PROCESSING',NULL,NULL,NULL,NULL)",
                (source_id, project_id, body.operation_id, request_hash, canonical(request)))
            self._active.add(source_id)
        raw, captured = b"", {}
        base = {"id": source_id, "project_id": project_id, "operation_id": body.operation_id,
            "mode": body.mode, "publisher_kind": body.publisher_kind,
            "publisher_kind_authority": "USER_DECLARED_NOT_VERIFIED", "observation_authority": "SOURCE_CAPTURE_ONLY",
            "captured_at": stamp(), "document_ref": None, "url": body.url, "http_status": None}
        try:
            if body.mode == "OFFLINE_PACKAGE":
                ref = body.document_ref.model_dump()
                document = self.store.document(project_id, ref["document_id"], ref["revision"])
                raw = document["raw_bytes"]
                if document["sha256"] != ref["sha256"] or sha(raw) != ref["sha256"]:
                    raise CaptureDenied("OFFLINE_DOCUMENT_SHA_MISMATCH")
                self.store.document_receipt(project_id, ref["document_id"], ref["revision"])
                if len(document["content"].encode()) > MAX_TEXT_BYTES: raise CaptureDenied("CAPTURE_TEXT_TOO_LARGE")
                result = {**base, "status": "PARTIAL" if document.get("warnings") else "SAVED",
                    "title": document["filename"], "plaintext": document["content"], "document_ref": ref,
                    "coverage": "SELECTED_DOCUMENT_PARSE", "warnings": document.get("warnings", []),
                    "source_current_at_capture": document["current_revision"] == ref["revision"],
                    "network_requests": 0, "url": None, "http_status": None}
            else:
                captured = self.fetcher.fetch(body.url); raw = captured.pop("raw")
                if len(raw) > MAX_NETWORK_BYTES: raise CaptureDenied("CAPTURE_BODY_TOO_LARGE")
                content = parse_capture(raw, captured["content_type"], captured["final_url"])
                result = {**base, **captured, **content, "url": body.url,
                    "network_requests": len(captured["redirects"]), "status": "PARTIAL" if content["warnings"] else "SAVED"}
                if not 200 <= captured["http_status"] < 300:
                    result.update(status="FAILED", reason="CAPTURE_HTTP_ERROR", coverage="INSUFFICIENT_HTTP_ERROR")
                elif not content["plaintext"].strip():
                    result.update(status="PARTIAL", coverage="INSUFFICIENT_NO_TEXT")
                else:
                    row = self.store.save_document(project_id, "competition-" + source_id + ".txt",
                        content["plaintext"].encode(), "competition-source:" + source_id)
                    result["document_ref"] = {"document_id": row["id"], "revision": row["revision"], "sha256": row["sha256"]}
            result["raw_snapshot_ref"] = {"source_id": source_id, "sha256": sha(raw), "bytes": len(raw)}
        except (CaptureDenied, NotFound, Conflict, InvalidInput, httpx.HTTPError, OSError) as error:
            result = {**base, **captured, **(error.observed if isinstance(error, CaptureDenied) else {}),
                "status": "FAILED", "reason": str(error) if isinstance(error, CaptureDenied) else type(error).__name__,
                "coverage": "INSUFFICIENT", "warnings": [], "title": "", "plaintext": "",
                "raw_snapshot_ref": {"source_id": source_id, "sha256": sha(raw), "bytes": len(raw)}}
        try:
            with self._connect() as db:
                db.execute("UPDATE captures SET status=?,result=?,result_sha256=?,raw=?,raw_sha256=? WHERE id=? AND status='PROCESSING'",
                    (result["status"], canonical(result), digest(result), raw, sha(raw), source_id))
        finally:
            self._active.discard(source_id)
        return self.get(project_id, source_id)

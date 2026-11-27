"""Discover pinned local UI dependencies and gate static/native-read requests. No network or agent loop."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit

from packages.adapters.opencode_profile import CLI_SHA, VERSION
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest

ASSET_SUFFIXES = (".js", ".mjs", ".css", ".woff", ".woff2", ".ttf", ".otf", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico", ".wasm", ".webmanifest")


def local_path(reference, parent="/"):
    """Resolve same-origin bytes only. Never normalize a traversal into an allowed path."""
    if not isinstance(reference, str) or any(ord(c) < 32 for c in reference) or "\\" in reference:
        raise ValueError("UI asset reference is invalid")
    value = reference.strip()
    if not value or value.startswith(("data:", "blob:", "#")): return None
    if urlsplit(value).scheme or value.startswith("//"):
        raise ValueError("UI asset has an external origin")
    decoded = unquote(unquote(value))
    if any(ord(c) < 32 or ord(c) == 127 for c in decoded):
        raise ValueError("UI asset path contains encoded controls")
    decoded_url = urlsplit(decoded)
    if decoded_url.scheme or decoded_url.netloc:
        raise ValueError("UI asset has an encoded external origin")
    if any(part == ".." for part in decoded_url.path.split("/")) or "\\" in decoded:
        raise ValueError("UI asset path traverses its local root")
    path = urlsplit(urljoin("http://eeas-ui.invalid" + parent, value)).path
    if not path.startswith("/") or "%" in unquote(path):
        raise ValueError("UI asset path is not canonical")
    canonical = unquote(path)
    if "//" in canonical or any(ord(c) < 32 or ord(c) == 127 for c in canonical):
        raise ValueError("UI asset path is not canonical")
    return canonical


class _HTMLAssets(HTMLParser):
    def __init__(self):
        super().__init__(); self.references = []; self.inline_scripts = []; self.inline_styles = []; self.active = None

    def handle_starttag(self, tag, pairs):
        attrs = dict(pairs)
        if tag in {"script", "style"}: self.active = tag if not attrs.get("src") else None
        if tag == "base" and attrs.get("href") not in (None, "/"):
            raise ValueError("UI changes the static asset base")
        for key in (("src",) if tag in {"script", "img", "source", "audio", "video", "iframe"} else ("href",) if tag == "link" else ()):
            if attrs.get(key): self.references.append(attrs[key])
        if attrs.get("poster"): self.references.append(attrs["poster"])
        if attrs.get("style"):
            self.references.extend(css_references(attrs["style"]))
        if attrs.get("srcset"):
            self.references.extend(item.strip().split()[0] for item in attrs["srcset"].split(",") if item.strip())

    def handle_data(self, data):
        if self.active == "script": self.inline_scripts.append(data)
        if self.active == "style": self.inline_styles.append(data)

    def handle_endtag(self, tag):
        if tag == self.active: self.active = None


def css_references(text):
    return [a or b or c for a, b, c in re.findall(r"url\(\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s)]*))\s*\)", text, re.IGNORECASE)] + re.findall(r"@import\s+[\"']([^\"']+)[\"']", text, re.IGNORECASE)


def js_references(text):
    literals = re.findall(r"[\"']([^\"'\n]+)[\"']", text)
    references = ["/" + value if value.startswith("assets/") else value for value in literals
                  if value.split("?", 1)[0].split("#", 1)[0].lower().endswith(ASSET_SUFFIXES)]
    # Module specifiers may omit an asset suffix. External literals must still be rejected.
    references.extend(re.findall(r"\bimport\s*\(\s*[\"']([^\"']+)[\"']\s*\)", text))
    references.extend(re.findall(r"\b(?:import|export)\s+(?:[^;\n]*?\s+from\s*)?[\"']([^\"']+)[\"']", text))
    dynamic = [value[:512] for value in re.findall(r"\bimport\s*\(\s*([^)]*)\)", text)
               if not re.fullmatch(r"\s*[\"'][^\"']+[\"']\s*", value)]
    return references, dynamic


def asset_references(path, media_type, body):
    """Return observed static references and separately declared unresolved dynamic references."""
    references, dynamic = [], []
    text = body.decode("utf-8") if path.endswith((".js", ".mjs", ".css", ".html", ".webmanifest")) or "text/html" in media_type else ""
    if "text/html" in media_type or path.endswith(".html"):
        parser = _HTMLAssets(); parser.feed(text); references.extend(parser.references)
        for style in parser.inline_styles: references.extend(css_references(style))
        for script in parser.inline_scripts:
            found, unresolved = js_references(script); references.extend(found); dynamic.extend(unresolved)
    elif path.endswith(".css"):
        references.extend(css_references(text))
    elif path.endswith((".js", ".mjs")):
        # Vite import chunks, mapDeps and literal emitted assets. No API URL is fetched from JavaScript.
        references, dynamic = js_references(text)
    elif path.endswith(".webmanifest"):
        manifest = json.loads(text)
        references.extend(icon["src"] for icon in manifest.get("icons", []))
    local, external = set(), []
    for ref in references:
        try:
            candidate = local_path(ref, path)
            if candidate: local.add(candidate)
        except ValueError as error:
            external.append({"reference": ref, "reason": str(error)})
    return {"local": sorted(local), "external_or_rejected": external, "unresolved_dynamic": dynamic}


@dataclass(frozen=True)
class UIAsset:
    path: str
    content_type: str
    sha256: str
    byte_length: int
    stored_path: str


class UIAssetGate:
    """A sealed static cache cannot become an unrestricted reverse proxy to native endpoints."""
    def __init__(self, root, manifest):
        selected = Path(root).absolute()
        if selected.is_symlink() or any(p.is_symlink() for p in selected.parents):
            raise ValueError("UI cache root is a symlink")
        self.root = selected.resolve()
        if manifest.get("schema") != "eeas.opencode-local-ui-assets.v1" or manifest.get("version") != VERSION:
            raise ValueError("UI manifest version is not approved")
        if manifest.get("cli_sha256") not in CLI_SHA.values(): raise ValueError("UI CLI identity is unknown")
        if manifest.get("external_or_rejected") or manifest.get("unresolved_dynamic"):
            raise ValueError("UI static graph is incomplete; external or dynamic references remain")
        self.assets = {}
        if not isinstance(manifest.get("assets"), list) or not 0 < len(manifest["assets"]) <= 1024:
            raise ValueError("UI static manifest must contain a bounded asset set")
        for item in manifest["assets"]:
            asset = UIAsset(**{k: item[k] for k in UIAsset.__dataclass_fields__})
            if local_path(asset.path) != asset.path or asset.path in self.assets: raise ValueError("UI asset path is ambiguous")
            if (not isinstance(asset.sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", asset.sha256) or
                    type(asset.byte_length) is not int or not 0 < asset.byte_length <= 32 * 1024 * 1024 or
                    not isinstance(asset.content_type, str) or any(ord(c) < 32 for c in asset.content_type)):
                raise ValueError("UI cached asset metadata is invalid")
            if asset.path.lower().endswith(ASSET_SUFFIXES) and "text/html" in asset.content_type.lower():
                raise ValueError("UI static asset is an HTML fallback")
            stored = Path(asset.stored_path)
            if stored.is_absolute() or ".." in stored.parts or "\\" in asset.stored_path: raise ValueError("UI cache path escapes")
            self.assets[asset.path] = asset
        if sum(asset.byte_length for asset in self.assets.values()) > 256 * 1024 * 1024:
            raise ValueError("UI cached asset set exceeds byte bound")
        # Check the actual bytes before making any entry available. A manifest flag is not proof of closure.
        for asset in self.assets.values():
            graph = asset_references(asset.path, asset.content_type, self._body(asset))
            if graph["external_or_rejected"] or graph["unresolved_dynamic"]:
                raise Conflict("UI cached bytes contain external or unresolved dynamic references")
            if set(graph["local"]) - self.assets.keys():
                raise Conflict("UI cached dependency is absent from the exact manifest")

    def read(self, method, path):
        if method not in {"GET", "HEAD"}: raise Conflict("Static UI never forwards native mutations")
        canonical = local_path(path)
        if canonical not in self.assets: raise NotFound("UI asset is not in the pinned local manifest")
        asset = self.assets[canonical]
        body = self._body(asset)
        return {"body": b"" if method == "HEAD" else body, "content_type": asset.content_type,
                "sha256": asset.sha256, "native_api_forwarded": False}

    def _body(self, asset):
        source = self.root / asset.stored_path
        if source.is_symlink() or any(p.is_symlink() for p in source.parents) or not source.resolve().is_relative_to(self.root):
            raise Conflict("UI cached asset path changed")
        try:
            with source.open("rb") as stream: body = stream.read(asset.byte_length + 1)
        except OSError as error: raise Conflict("UI cached asset is unavailable") from error
        if len(body) != asset.byte_length or hashlib.sha256(body).hexdigest() != asset.sha256:
            raise Conflict("UI cached asset bytes changed")
        return body


class NativeUIReadGate:
    """Integration seam: exact execution/project/session read scope. Mutations still require separate EEAS actions."""
    def __init__(self, service): self.service = service

    def authorize(self, project_id, execution_id, method, path):
        if method != "GET": raise Conflict("Native UI action requires an explicit EEAS operation; automatic permission replies are blocked")
        with self.service.connect() as connection:
            row = connection.execute("SELECT * FROM agent_executions WHERE id=? AND project_id=?", (execution_id, project_id)).fetchone()
            scope = connection.execute("SELECT * FROM agent_scopes WHERE execution_id=?", (execution_id,)).fetchone()
        if row is None: raise NotFound("Native UI execution is outside this project")
        if (row["runtime"] != "opencode-http" or row["sdk_version"] != VERSION or not scope or
                digest(json.loads(scope["scope"])) != scope["scope_hash"]):
            raise Conflict("Native UI scope/runtime changed")
        frozen = json.loads(scope["scope"])
        if (frozen.get("runtime") != "opencode-http" or frozen.get("cli_sha256") not in CLI_SHA.values() or
                digest(json.loads(row["request"])) != row["request_hash"] or frozen.get("input_hash") != row["request_hash"]):
            raise Conflict("Native UI binary/request binding changed")
        session = row["session_id"]
        if not session: raise Conflict("No actual native session has been recorded")
        allowed = {"/session/" + session, "/session/" + session + "/message"}
        if path not in allowed: raise Conflict("Native UI read is outside the exact recorded session")
        # This is a guard result, not a fabricated upstream HTTP response or permission grant.
        return {"project_id": project_id, "execution_id": execution_id, "session_id": session,
                "runtime": row["runtime"], "scope_hash": scope["scope_hash"], "native_mutation_allowed": False}

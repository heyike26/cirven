"""Versioned local capability configuration and observations of existing owners.

No model loop, ambient credential discovery, shell, or process adoption lives here.
Saved declarations, a private MCP inspection, and an actual OpenCode execution are
different scopes. In particular none of them grants desktop input authority.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from urllib.parse import urlsplit

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import PaginatedRequestParams
from pydantic import Field, field_validator
import yaml

from packages.adapters.opencode_desktop import AGENT, MCP_NAME, MCP_TOOLS
from packages.adapters.opencode_profile import COMMIT, VERSION, binary_hash
from packages.domain.paths import resolve_data_path
from packages.domain.questions import Id, RequestModel
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, digest, now

NETWORK_MODES = {"AS_CONFIGURED", "STRICT_OFFLINE"}
_SOURCE_UNSET = object()


class SkillImportInput(RequestModel):
    operation_id: Id
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    expected_revision: int = Field(default=0, ge=0, strict=True)
    content: str = Field(min_length=1, max_length=65536)
    enabled: bool = Field(default=False, strict=True)


class CapabilityEnableInput(RequestModel):
    operation_id: Id
    expected_revision: int = Field(ge=1, strict=True)
    enabled: bool = Field(strict=True)


class MCPConfigureInput(RequestModel):
    operation_id: Id
    id: Id
    expected_revision: int = Field(default=0, ge=0, strict=True)
    configuration: dict


class MCPProbeInput(RequestModel):
    config_ref: dict
    allowed_effects: list[str] = Field(default_factory=list, max_length=1)
    network_mode: str = "AS_CONFIGURED"

    @field_validator("network_mode")
    @classmethod
    def known_mode(cls, value):
        if value not in NETWORK_MODES:
            raise ValueError("Unknown network mode")
        return value


def _ref(value):
    if (not isinstance(value, dict) or set(value) != {"id", "revision", "sha256"}
            or not isinstance(value["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value["id"])
            or type(value["revision"]) is not int or value["revision"] < 1
            or not isinstance(value["sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", value["sha256"])):
        raise InvalidInput("An exact capability configuration reference is required")
    if value["id"].upper() in {"CON", "PRN", "AUX", "NUL", *("COM" + str(x) for x in range(1, 10)), *("LPT" + str(x) for x in range(1, 10))}:
        raise InvalidInput("Capability identity is reserved on Windows")
    return value


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def nonfinite(_):
        raise ValueError("Nonfinite JSON")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


class _UniqueYaml(yaml.SafeLoader):
    pass


def _yaml_mapping(loader, node):
    result = {}
    for key, value in node.value:
        name = loader.construct_object(key)
        if not isinstance(name, str) or name in result:
            raise InvalidInput("Skill metadata has duplicate or non-string keys")
        result[name] = loader.construct_object(value)
    return result


_UniqueYaml.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def _skill(content, ident):
    if len(content.encode("utf-8")) > 65536 or any(ord(c) < 32 and c not in "\n\r\t" for c in content):
        raise InvalidInput("Skill content exceeds the text-only import boundary")
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", content, re.S)
    if not match:
        raise InvalidInput("Import an original SKILL.md with name/description frontmatter")
    try:
        if any(isinstance(t, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for t in yaml.scan(match[1])):
            raise InvalidInput("Skill metadata aliases are not accepted")
        metadata = yaml.load(match[1], Loader=_UniqueYaml)
    except yaml.YAMLError:
        raise InvalidInput("Invalid skill metadata") from None
    if (not isinstance(metadata, dict) or metadata.get("name") != ident
            or not isinstance(metadata.get("description"), str)
            or not 1 <= len(metadata["description"]) <= 2000):
        raise InvalidInput("Skill name must match its identity and have a description")
    return {"name": ident, "description": metadata["description"],
        "content_sha256": hashlib.sha256(content.encode()).hexdigest(), "content": content}


def _command_files(configuration):
    command = configuration["command"]
    files = configuration["files_sha256"]
    if (not isinstance(command, list) or not 1 <= len(command) <= 32
            or not all(isinstance(s, str) and 1 <= len(s) <= 2000 and not any(ord(c) < 32 for c in s) for s in command)
            or not isinstance(files, dict) or not 1 <= len(files) <= 32):
        raise InvalidInput("Local MCP requires a bounded argv and exact local file hashes")
    if command[0] not in files:
        raise InvalidInput("Local MCP executable must be pinned by file hash")
    for item in command:
        if Path(item).is_absolute() and Path(item).is_file() and item not in files:
            raise InvalidInput("Absolute command file arguments must also be pinned")
    for name, sha in files.items():
        path = Path(name)
        if (not path.is_absolute() or ".." in path.parts or path.is_symlink()
                or any(p.is_symlink() for p in path.parents) or not path.is_file()
                or not isinstance(sha, str) or not re.fullmatch(r"[a-f0-9]{64}", sha)):
            raise InvalidInput("MCP command must refer to ordinary absolute local files")
        if binary_hash(path) != sha:
            raise Conflict("Configured MCP command file bytes changed")


def _mcp(configuration):
    if not isinstance(configuration, dict):
        raise InvalidInput("MCP configuration must be an object")
    if type(configuration.get("enabled")) is not bool or type(configuration.get("timeout")) is not int or not 100 <= configuration["timeout"] <= 120000:
        raise InvalidInput("MCP enabled and timeout must be explicit")
    if configuration.get("type") == "local":
        if set(configuration) != {"type", "command", "files_sha256", "enabled", "timeout"}:
            raise InvalidInput("First-stage local MCP accepts argv and hashes; credentials/environment are not imported")
        _command_files(configuration)
    elif configuration.get("type") == "remote":
        if set(configuration) != {"type", "url", "enabled", "timeout"} or not isinstance(configuration["url"], str):
            raise InvalidInput("First-stage remote MCP accepts an unauthenticated endpoint declaration only")
        parsed = urlsplit(configuration["url"])
        if (len(configuration["url"]) > 2000 or parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or any(ord(c) <= 32 for c in configuration["url"])):
            raise InvalidInput("Remote MCP requires an explicit HTTPS URL without credentials or query")
    else:
        raise InvalidInput("Unknown MCP transport")
    return copy.deepcopy(configuration)


class NativeCapabilities:
    def __init__(self, store, data_dir, *, provider_settings=None, transport_reader=None,
                 extension_registry=None, planning_toolkit=None):
        self.store, self.data_dir = store, Path(data_dir).resolve()
        self.providers, self.transport_reader = provider_settings, transport_reader
        self.extensions, self.toolkit = extension_registry, planning_toolkit

    def _path(self, *parts):
        return resolve_data_path("/".join(["native-capabilities", *parts]), self.data_dir)

    def _read(self, path):
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise Conflict("Capability record is missing or oversized")
        try:
            record = _json(path.read_bytes())
            if set(record) != {"value", "sha256"} or digest(record["value"]) != record["sha256"]:
                raise ValueError()
            return record["value"]
        except (ValueError, TypeError, KeyError):
            raise Conflict("Capability record integrity failed") from None

    @staticmethod
    def _write(path, value, *, replace=False):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        raw = (canonical({"value": value, "sha256": digest(value)}) + "\n").encode()
        if not replace:
            with path.open("xb") as out:
                out.write(raw); out.flush(); os.fsync(out.fileno())
            path.chmod(0o600)
            return
        fd, name = tempfile.mkstemp(prefix="pending-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as out:
                out.write(raw); out.flush(); os.fsync(out.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    def _index(self):
        path = self._path("index.json")
        value = self._read(path) if path.exists() else {"schema": "eeas.native-capabilities.v1", "skills": {}, "mcp": {}}
        if set(value) != {"schema", "skills", "mcp"} or value["schema"] != "eeas.native-capabilities.v1":
            raise Conflict("Capability index schema changed")
        for kind in ("skills", "mcp"):
            if not isinstance(value[kind], dict):
                raise Conflict("Capability index changed")
            if len({ident.casefold() for ident in value[kind]}) != len(value[kind]):
                raise Conflict("Capability index contains Windows-aliased identities")
            for ident, ref in value[kind].items():
                if _ref(ref)["id"] != ident:
                    raise Conflict("Capability index identity changed")
        return value

    def _configuration(self, kind, ref):
        _ref(ref)
        value = self._read(self._path(kind, ref["id"], f'{ref["revision"]:010d}.json'))
        if digest(value) != ref["sha256"] or value["id"] != ref["id"] or value["revision"] != ref["revision"]:
            raise Conflict("Capability snapshot changed")
        return value

    def experience_claim(self, kind, ident):
        path=self._path('experience-claims',digest([kind,ident.casefold()])+'.json')
        if not path.exists():return None
        claim=self._read(path)
        fields={'schema','state','project_id','session_id','operation_id','request_sha256','native_operation_id','native_kind','capability_id'}
        if (not isinstance(claim,dict) or not fields<=set(claim) or set(claim)-fields not in [set(),{'closed_at'}]
                or claim['schema']!='eeas.experience-capability-claim.v1' or not isinstance(claim['state'],str) or claim['state'] not in {'ACTIVE','CLOSED'}
                or claim['native_kind']!=kind or not isinstance(claim['capability_id'],str) or claim['capability_id'].casefold()!=ident.casefold()
                or any(not isinstance(claim[k],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',claim[k]) for k in ['project_id','operation_id','native_operation_id'])
                or not isinstance(claim['session_id'],str) or not re.fullmatch(r'[a-f0-9]{32}',claim['session_id'])
                or not isinstance(claim['request_sha256'],str) or not re.fullmatch(r'[a-f0-9]{64}',claim['request_sha256'])
                or claim['state']=='CLOSED' and not isinstance(claim.get('closed_at'),str)):
            raise Conflict('Experience configuration claim is corrupt; original state retained')
        return claim

    def _save(self, kind, ident, expected, operation, configuration, *, before_save=None, experience_source=_SOURCE_UNSET, paused_control=None):
        _ref({"id": ident, "revision": 1, "sha256": "0" * 64})
        operation_path = self._path("operations", digest(operation) + ".json")
        input_sha = digest({"kind": kind, "id": ident, "expected_revision": expected, "configuration": configuration})
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            index = self._index()
            claim=self.experience_claim(kind,ident)
            from .experience_controls import view as control_view
            control=control_view(self,kind,ident)
            if paused_control is not None:
                if (control!=paused_control or control is None or control['effective_state']!='PAUSED'
                        or not control['finalized'] or control['target_native_operation_id']!=operation
                        or before_save is None):
                    raise Conflict('Exact paused metadata-only admission changed; no configuration was saved')
            elif control is not None and control['effective_state']=='PAUSED' and control['target_native_operation_id']==operation:
                raise Conflict('This pending native save is paused; only exact metadata-only recovery is permitted')
            if claim is not None and claim['state']=='ACTIVE' and claim['native_operation_id']!=operation:
                raise Conflict('An original experience configuration save needs inspection before another replacement')
            if before_save is not None:before_save(connection,index)
            from .experience_provenance import read as read_provenance, write as write_provenance, status as provenance_status
            source=experience_source
            current_ref=index[kind].get(ident)
            if source is _SOURCE_UNSET:
                source=None
                if current_ref is not None:
                    previous=read_provenance(self,kind,current_ref)
                    # Ordinary edits of the same capability retain its source.
                    # A timeout/content edit must not erase a forgetting policy.
                    # Explicit reviewed promotion/rollback supplies its exact
                    # origin (including None for the original manual version).
                    if previous is not None:
                        source=previous['source']
            if source is not None and configuration['enabled'] and paused_control is None:
                reader=getattr(self,'experience_source_reader',None)
                if not callable(reader) or reader(source).get('availability')!='ACTIVE':
                    raise Conflict('EXPERIENCE_SOURCE_BLOCKED: a withdrawn source cannot be enabled')
            def preserve_source(saved_ref):
                origin=write_provenance(self,kind,saved_ref,source)
                if origin is not None and configuration['enabled'] and paused_control is None:
                    state=provenance_status(self,kind,saved_ref)
                    if state is None or state.get('availability')!='ACTIVE':
                        raise Conflict('EXPERIENCE_SOURCE_BLOCKED: candidate source no longer admits future use')
            if any(key.casefold() == ident.casefold() and key != ident for key in index[kind]):
                raise Conflict("Capability identity aliases an existing Windows path")
            if operation_path.exists():
                old = self._read(operation_path)
                if old["input_sha256"] != input_sha:
                    raise Conflict("Capability operation already binds another input")
                saved = old["result"]["config_ref"]
                self._configuration(kind, saved)
                preserve_source(saved)
                current = index[kind].get(ident)
                if (current["revision"] if current else 0) == expected:
                    # An interruption after the immutable intent but before the
                    # pointer is recoverable by replay of this exact operation.
                    index[kind][ident] = saved
                    self._write(self._path("index.json"), index, replace=True)
                elif current is None or current["revision"] < saved["revision"]:
                    raise Conflict("Capability operation pointer history changed")
                return copy.deepcopy(old["result"])
            current = index[kind].get(ident)
            if (current["revision"] if current else 0) != expected:
                raise Conflict("Capability configuration changed; reload its exact revision")
            revision = expected + 1
            value = {"id": ident, "revision": revision, "configuration": configuration, "created_at": now()}
            ref = {"id": ident, "revision": revision, "sha256": digest(value)}
            snapshot_path = self._path(kind, ident, f"{revision:010d}.json")
            if snapshot_path.exists():
                orphan = self._read(snapshot_path)
                if orphan.get("id") != ident or orphan.get("revision") != revision or orphan.get("configuration") != configuration:
                    raise Conflict("An interrupted candidate has different immutable bytes")
                value = orphan
                ref = {"id": ident, "revision": revision, "sha256": digest(value)}
            else:
                self._write(snapshot_path, value)
            # Native skill folder contains original bytes, never synthesized executable code.
            if kind == "skills":
                native_path = self._path("skill-files", ident, f"{revision:010d}", "SKILL.md")
                native_path.parent.mkdir(parents=True, exist_ok=True)
                if native_path.exists():
                    if native_path.read_bytes() != configuration["content"].encode():
                        raise Conflict("Interrupted native skill bytes changed")
                else:
                    with native_path.open("xb") as out:
                        out.write(configuration["content"].encode()); out.flush(); os.fsync(out.fileno())
                native_path.chmod(0o600)
            preserve_source(ref)
            index[kind][ident] = ref
            result = {"config_ref": ref, "saved": True, "loaded_in_original_session": False,
                "qualified": False, "applies_to": "FUTURE_EXPLICIT_OWNER_BINDING_ONLY"}
            self._write(operation_path, {"input_sha256": input_sha, "kind": kind, "result": result})
            self._write(self._path("index.json"), index, replace=True)
            return result

    def import_skill(self, body):
        body = SkillImportInput.model_validate(body)
        configuration = {**_skill(body.content, body.id), "enabled": body.enabled}
        return self._save("skills", body.id, body.expected_revision, body.operation_id, configuration)

    def configure_mcp(self, body):
        body = MCPConfigureInput.model_validate(body)
        if body.id == MCP_NAME:
            raise Conflict("The execution-owned MCP namespace cannot be replaced by user configuration")
        configuration = _mcp(body.configuration)
        return self._save("mcp", body.id, body.expected_revision, body.operation_id, configuration)

    def set_enabled(self, kind, ident, body):
        if kind not in {"skills", "mcp"}:
            raise InvalidInput("Unknown capability kind")
        body = CapabilityEnableInput.model_validate(body)
        current = self._index()[kind].get(ident)
        if not current:
            raise NotFound("Capability configuration does not exist")
        value = self._configuration(kind, current)
        configuration = {**value["configuration"], "enabled": body.enabled}
        return self._save(kind, ident, body.expected_revision, body.operation_id, configuration)

    def _current_mcp(self, ref):
        from .experience_controls import blocked
        if blocked(self,'mcp',_ref(ref)['id']):raise Conflict('EXPERIENCE_PAUSED: Tool inspection and start are blocked')
        from .experience_provenance import status
        source=status(self,'mcp',ref)
        if source is not None and source.get('availability')!='ACTIVE':
            raise Conflict('EXPERIENCE_SOURCE_BLOCKED: withdrawn Tool code cannot be inspected or started')
        if self._index()["mcp"].get(_ref(ref)["id"]) != ref:
            raise Conflict("MCP probe requires the current exact configuration")
        return self._configuration("mcp", ref)["configuration"]

    def mcp_content(self, ref):
        from .native_tools import mcp_content
        return mcp_content(self, ref)

    def tool_catalog(self):
        from .native_tools import tool_catalog
        return tool_catalog(self)

    def validate_tool_selection(self, selected, allowlist):
        from .native_tools import validate_selection
        return validate_selection(self, selected, allowlist)

    def operation(self, operation_id):
        if not isinstance(operation_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id):
            raise InvalidInput('An exact capability operation identity is required')
        path = self._path('operations', digest(operation_id) + '.json')
        if not path.exists():
            raise NotFound('Capability operation has no saved receipt; its outcome remains unconfirmed')
        value = self._read(path)
        ref = _ref(value['result']['config_ref'])
        for kind in ([value['kind']] if value.get('kind') in {'skills', 'mcp'} else ['skills', 'mcp']):
            snapshot_path = self._path(kind, ref['id'], f"{ref['revision']:010d}.json")
            if not snapshot_path.exists():
                continue
            snapshot = self._read(snapshot_path)
            if digest(snapshot) != ref['sha256']:
                continue
            configuration = snapshot['configuration']
            if digest({'kind': kind, 'id': ref['id'], 'expected_revision': ref['revision'] - 1,
                       'configuration': configuration}) != value['input_sha256']:
                raise Conflict('Capability operation input evidence differs')
            result = {'operation_id': operation_id, 'input_sha256': value['input_sha256'],
                      'receipt': copy.deepcopy(value['result']), 'kind': kind,
                      'configuration': copy.deepcopy(configuration)}
            if ref['revision'] > 1:
                previous = self._read(self._path(kind, ref['id'], f"{ref['revision']-1:010d}.json"))
                result.update(previous_config_ref={'id': ref['id'], 'revision': ref['revision']-1, 'sha256': digest(previous)},
                              previous_configuration=copy.deepcopy(previous['configuration']))
            return result
        raise Conflict('Capability operation saved configuration is unavailable')

    async def probe_mcp(self, ident, body):
        body = MCPProbeInput.model_validate(body)
        if _ref(body.config_ref)["id"] != ident:
            raise Conflict("MCP probe identity differs")
        configuration = self._current_mcp(body.config_ref)
        if not configuration["enabled"]:
            raise Conflict("Disabled MCP configurations cannot start a process")
        if body.network_mode == "STRICT_OFFLINE":
            raise Conflict("Local process launch does not prove an offline network sandbox")
        if configuration["type"] != "local":
            raise Conflict("Remote MCP authentication/connection is not adapted in this first stage")
        if body.allowed_effects != ["PROCESS_START"]:
            raise Conflict("Explicit owned MCP process-start permission is required")
        _command_files(configuration)

        async def inspect_server():
            # No inherited developer credentials and no shell. Protocol inspection only.
            command = configuration["command"]
            workspace = self._path("probe-workspace", uuid.uuid4().hex)
            workspace.mkdir(parents=True, exist_ok=False)
            home = workspace / "home"; home.mkdir()
            env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
            # stdio_client adds a documented small inherited environment; its
            # HOME must also point at this private empty directory.
            env.update({"HOME": str(home), "USERPROFILE": str(home), "APPDATA": str(home),
                "LOCALAPPDATA": str(home), "XDG_CONFIG_HOME": str(home), "XDG_DATA_HOME": str(home),
                "LOGNAME": "eeas-mcp-inspection", "USER": "eeas-mcp-inspection"})
            parameters = StdioServerParameters(command=command[0], args=command[1:], env=env,
                cwd=str(workspace))
            with open(os.devnull, "w") as errors:
                async with stdio_client(parameters, errlog=errors) as (reader, writer):
                    async with ClientSession(reader, writer, read_timeout_seconds=configuration["timeout"] / 1000) as client:
                        initialized = await client.initialize()
                        listed, tools, seen = await client.list_tools(), [], set()
                        for _ in range(20):
                            for tool in listed.tools:
                                item = tool.model_dump(mode="json", exclude_none=True)
                                if (tool.name in seen or len(tools) >= 1000 or len(canonical(item).encode()) > 65536
                                        or not 1 <= len(tool.name) <= 256 or any(ord(c) < 32 for c in tool.name)):
                                    raise Conflict("MCP tool listing exceeded its bounded unique schema")
                                seen.add(tool.name)
                                tools.append({"name": tool.name, "input_schema_sha256": digest(tool.input_schema),
                                    "annotations_advisory_only": True, "invoked": False,
                                    "offline_qualified": False, "permission": "NOT_GRANTED"})
                            if not listed.next_cursor:
                                break
                            listed = await client.list_tools(params=PaginatedRequestParams(cursor=listed.next_cursor))
                        else:
                            raise Conflict("MCP pagination exceeded its bounded listing")
                        return {"protocol_version": initialized.protocol_version, "tool_count": len(tools), "tools": tools}

        receipt = {"id": uuid.uuid4().hex, "config_ref": body.config_ref, "created_at": now(),
            "mcp_sdk_version": importlib.metadata.version("mcp"),
            "scope": "PRIVATE_OFFICIAL_MCP_STDIO_INSPECTION_NOT_ORIGINAL_SESSION",
            "initialized": False, "list_tools_succeeded": False, "qualified": False,
            "tools_invoked": 0, "offline_qualified": False, "network_sandbox": "NOT_QUALIFIED",
            "loaded_in_original_session": False, "automatic_input_authorized": False}
        receipt["process_side_effects"] = "SELECTED_LOCAL_CODE_NOT_OS_SANDBOXED"
        try:
            result = await asyncio.wait_for(inspect_server(), timeout=configuration["timeout"] / 1000 + 5)
            if self._current_mcp(body.config_ref) != configuration:
                raise Conflict("MCP configuration changed during inspection")
            _command_files(configuration)
            receipt.update(result, status="INITIALIZED_AND_LISTED", initialized=True, list_tools_succeeded=True,
                cleanup="OWNED_STDIO_CONTEXT_EXITED")
        except asyncio.CancelledError:
            receipt.update(status="CANCELLED", cleanup="OWNED_STDIO_CONTEXT_UNWOUND")
            self._record_probe(receipt)
            raise
        except Exception as error:
            receipt.update(status="FAILED", error_type=type(error).__name__, cleanup="OWNED_STDIO_CONTEXT_UNWOUND")
        self._record_probe(receipt)
        return copy.deepcopy(receipt)

    def _record_probe(self, receipt):
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._write(self._path("mcp-probes", receipt["id"] + ".json"), receipt)
            self._write(self._path("mcp-probe-latest", receipt["config_ref"]["id"] + ".json"),
                {"id": receipt["id"], "sha256": digest(receipt)}, replace=True)

    def _last_probe(self, ident, ref):
        path = self._path("mcp-probe-latest", ident + ".json")
        if not path.exists():
            return None
        pointer = self._read(path)
        if not re.fullmatch(r"[a-f0-9]{32}", str(pointer.get("id"))):
            raise Conflict("MCP probe identity changed")
        receipt = self._read(self._path("mcp-probes", pointer["id"] + ".json"))
        if digest(receipt) != pointer["sha256"]:
            raise Conflict("MCP probe receipt changed")
        return {**receipt, "current_configuration": receipt["config_ref"] == ref}

    def get(self, *, network_mode="AS_CONFIGURED"):
        if network_mode not in NETWORK_MODES:
            raise InvalidInput("Unknown network mode")
        index, skills, mcps = self._index(), [], []
        from .experience_provenance import status as provenance_status
        from .experience_controls import view as control_view
        for ident, ref in sorted(index["skills"].items()):
            config = self._configuration("skills", ref)["configuration"]
            path = self._path("skill-files", ident, f'{ref["revision"]:010d}', "SKILL.md")
            intact = path.is_file() and binary_hash(path) == config["content_sha256"]
            source_policy=provenance_status(self,'skills',ref)
            visible=source_policy is None or source_policy.get('availability')=='ACTIVE'
            control=control_view(self,'skills',ident);paused=control is not None and control['effective_state']=='PAUSED'
            skills.append({"id": ident, "title": config["name"] if visible else "Unavailable experience source", "description": config["description"] if visible else "",
                "experience_source":source_policy,"capability_control":control,"effective_enabled":config['enabled'] and visible and not paused,
                "config_ref": ref, "enabled": config["enabled"], "integrity": "CURRENT" if intact else "INTEGRITY_ERROR",
                "source": "IMPORTED_ORIGINAL_SKILL_MD", "loaded_in_original_session": False,
                "qualified": False, "native_binding": "NOT_ADAPTED", "permission_granted": False})
        for ident, ref in sorted(index["mcp"].items()):
            config = self._configuration("mcp", ref)["configuration"]
            intact = True
            if config["type"] == "local":
                try:
                    _command_files(config)
                except (InvalidInput, Conflict, OSError):
                    intact = False
            last = self._last_probe(ident, ref)
            source_policy=provenance_status(self,'mcp',ref)
            visible=source_policy is None or source_policy.get('availability')=='ACTIVE'
            control=control_view(self,'mcp',ident);paused=control is not None and control['effective_state']=='PAUSED'
            mcps.append({"id": ident, "config_ref": ref, "type": config["type"], "enabled": config["enabled"],
                "experience_source":source_policy,"capability_control":control,"effective_enabled":config['enabled'] and visible and not paused,
                "integrity": "CURRENT" if intact else "INTEGRITY_ERROR", "timeout": config["timeout"],
                "loaded_in_original_session": False, "qualified": False, "native_binding": "NOT_ADAPTED",
                "probe_permitted": intact and visible and not paused and config["enabled"] and config["type"] == "local" and network_mode != "STRICT_OFFLINE",
                "last_probe": last, "offline_qualified": False})
        transport = self.transport_reader() if callable(self.transport_reader) else None
        methods = []
        if transport is not None:
            for ident, (descriptor, _) in sorted(transport.methods.items()):
                local_known = ident in {"read_message", "read_context"} and descriptor["readonly"] and not descriptor["effects"]
                methods.append({**copy.deepcopy(descriptor), "registered": True, "invocation_scope": "EXACT_PREPARED_EXECUTION_ONLY",
                    "active_session_loaded": None, "load_state": "USE_EXACT_OWNER_PROBE", "offline_qualified": local_known,
                    "display_eligible": local_known or network_mode != "STRICT_OFFLINE",
                    "permission_granted_by_management": False})
        providers = self.providers.list() if self.providers is not None else []
        return {"schema": "eeas.native-capabilities.v1", "version": VERSION, "source_commit": COMMIT,
            "network_mode": network_mode, "network_mode_scope": "DISPLAY_AND_MANAGER_PROBE_ADMISSION_ONLY",
            "os_network_sandbox": "NOT_QUALIFIED", "silent_paid_fallback": False, "automatic_input_authorized": False,
            "skills": skills, "mcp": mcps, "host_methods": methods, "providers": providers,
            "recent_executions": self._recent_executions(transport),
            "provider_management": {"list_save_revision": "/api/planning/providers", "model_access": "NOT_VALIDATED",
                "remove": "NOT_ADAPTED", "connectivity_probe": "NOT_ADAPTED",
                "subscription_oauth_in_native_companion": "CONTROL_IMPLEMENTED_ACCOUNT_NOT_VALIDATED"
                    if getattr(self.providers, "subscription_account", None) is not None else "NOT_ADAPTED",
                "subscription_account_route": "/api/subscription/account"
                    if getattr(self.providers, "subscription_account", None) is not None else None},
            "extensions": self.extensions.list() if self.extensions is not None else [],
            "extension_management": {"import_qualify_review_approve": "/api/extensions", "native_sdk_plugin_live": "NOT_VALIDATED"},
            "planning_tools": {"connected": self.toolkit is not None, "scope": "LOCAL_DETERMINISTIC_HELPERS_NOT_NATIVE_SKILLS",
                "routes": ["/api/planning/tools/validate-network", "/api/planning/tools/acceptance-coverage", "/api/planning/tools/templates"] if self.toolkit else []},
            "base_interfaces": self._base_interfaces()}

    def _recent_executions(self, transport):
        if transport is None:
            return []
        with self.store.connect() as connection:
            rows = connection.execute("SELECT id,project_id,session_id,execution_status,request_hash FROM agent_executions WHERE runtime=? ORDER BY created_at DESC,id DESC LIMIT 20", (transport.runtime,)).fetchall()
        result = []
        for row in rows:
            events = transport.stream_events(row["project_id"], row["id"])
            calls = [x["payload"] for x in events if x["payload"].get("type") in {"desktop_tool_completed", "desktop_tool_rejected"}]
            observed = False
            try:
                self._owner(row["project_id"], row["id"])
                observed = True
            except (Conflict, NotFound, ValueError):
                pass
            result.append({**dict(row), "connected_owner": observed,
                "liveness": "ACTUAL_ACTIVE_OWNER_CHECKED" if observed else "NO_LIVE_OWNER_AUTHORITY",
                "actual_tool_calls": [{k: item[k] for k in ("type", "method", "result_sha256", "error_type") if k in item} for item in calls[-50:]],
                "qualified_model_access": False, "automatic_input_authorized": False})
        return result

    def skill_content(self, ident):
        from .experience_controls import blocked
        if blocked(self,'skills',ident):raise Conflict('EXPERIENCE_PAUSED: Skill content cannot be reused while paused')
        from .experience_provenance import status
        selected=self._index()['skills'].get(ident)
        state=status(self,'skills',selected) if selected else None
        if state is not None and state.get('availability')!='ACTIVE':
            raise Conflict('EXPERIENCE_SOURCE_BLOCKED: original cached Skill content is unavailable for reuse')
        ref = self._index()["skills"].get(ident)
        if ref is None:
            raise NotFound("Imported skill does not exist")
        config = self._configuration("skills", ref)["configuration"]
        path = self._path("skill-files", ident, f'{ref["revision"]:010d}', "SKILL.md")
        if not path.is_file() or binary_hash(path) != config["content_sha256"]:
            raise Conflict("Imported skill source changed")
        return {"config_ref": ref, "content": config["content"], "source": "ORIGINAL_SKILL_MD",
            "enabled": config["enabled"], "permission_granted": False}

    def native_binding_snapshot(self, *, validate_mcp_ids=None):
        """Private future-owner handoff; does not mutate a running native config.

        Caller must separately enforce its exact permission/network policy and
        verify this snapshot immediately before starting its owned runtime.
        The optional validation scope changes executable checks, not metadata
        or its hash. An unchecked declaration is not runtime admission.
        """
        index, paths, servers = self._index(), [], {}
        source_states={'skills':{},'mcp':{}};control_states={'skills':{},'mcp':{}}
        from .experience_provenance import status as source_status
        from .experience_controls import view as control_view
        if validate_mcp_ids is not None and (type(validate_mcp_ids) is not set or
                not all(isinstance(ident, str) for ident in validate_mcp_ids) or
                not validate_mcp_ids <= index['mcp'].keys()):
            raise InvalidInput('MCP validation scope must name exact configured servers')
        for ident, ref in index["skills"].items():
            config = self._configuration("skills", ref)["configuration"]
            control=control_view(self,'skills',ident)
            if control is not None:
                control_states['skills'][ident]=control
                if control['effective_state']=='PAUSED':continue
            source=source_status(self,'skills',ref) if config['enabled'] else None
            if source is not None:
                source_states['skills'][ident]=source
                if source.get('availability')!='ACTIVE':continue
            path = self._path("skill-files", ident, f'{ref["revision"]:010d}', "SKILL.md")
            if not path.is_file() or binary_hash(path) != config["content_sha256"]:
                raise Conflict("Imported native skill bytes changed")
            if config["enabled"]:
                paths.append(str(path.parent))
        for ident, ref in index["mcp"].items():
            config = self._configuration("mcp", ref)["configuration"]
            control=control_view(self,'mcp',ident)
            if control is not None:
                control_states['mcp'][ident]=control
                if control['effective_state']=='PAUSED':continue
            if not config["enabled"]:continue
            source=source_status(self,'mcp',ref)
            if source is not None:
                source_states['mcp'][ident]=source
                if source.get('availability')!='ACTIVE':continue
            if config["type"] == "local" and (validate_mcp_ids is None or ident in validate_mcp_ids):
                _command_files(config)
            servers[ident] = {k: copy.deepcopy(v) for k, v in config.items() if k != "files_sha256"}
        value = {"schema": "eeas.native-capability-binding.v1", "version": VERSION,
            "source_commit": COMMIT, "configuration_refs": copy.deepcopy(index),
            "native_config_fragment": {"skills": {"paths": sorted(paths)}, "mcp": servers},
            "permission_granted": False, "loaded_in_original_session": False,
            "admission": "FUTURE_OWNER_MUST_ENFORCE_PERMISSIONS_AND_NETWORK_POLICY"}
        if source_states['skills'] or source_states['mcp']:
            value['experience_source_states']=source_states
        if control_states['skills'] or control_states['mcp']:
            value['experience_control_states']=control_states
        return {"value": value, "sha256": digest(value)}

    @staticmethod
    def _base_interfaces():
        return [
            {"id": "original_loop", "state": "MAPPED_PER_EXECUTION", "adapter": "OpenCodeDesktopTransport"},
            {"id": "plan_task_subagents", "state": "NOT_ADAPTED_IN_COMPANION", "native": "FIXED_BASE_PRESENT_CURRENT_EXECUTION_DENIES_NATIVE_TOOLS"},
            {"id": "questions_permissions", "state": "LIST_PROBE_MAPPED_REPLY_NOT_ADAPTED", "native": "V1_19_0_ORIGINAL_HTTP"},
            {"id": "cancel", "state": "MAPPED", "route": "/api/desktop-host/native-messages/{operation_id}/cancel"},
            {"id": "events", "state": "MAPPED_DURABLE_POLLING", "route": "/api/desktop-host/native-messages/{operation_id}", "live_sse_ui": "NOT_ADAPTED"},
            {"id": "steer_resume_fork", "state": "NOT_ADAPTED_IN_COMPANION", "native_task_review_controls": "SEPARATE_EXISTING_TASK_REVIEW_SCOPE"},
            {"id": "skills", "state": "LOCAL_IMPORT_ENABLE_DISCOVERY_MAPPED_NATIVE_BINDING_NOT_ADAPTED", "native_list": "V2_API_SKILL_NOT_MIXED_WITH_V1"},
            {"id": "mcp", "state": "LOCAL_CONFIG_STDIO_INSPECTION_MAPPED_NATIVE_MUTATIONS_NOT_ADAPTED"},
            {"id": "long_lived_owner", "state": "NOT_ADAPTED", "original_app_interface": "DISABLED_REQUIRES_LONG_LIVED_SESSION_OWNER"},
        ]

    def _owner(self, project_id, execution_id):
        transport = self.transport_reader() if callable(self.transport_reader) else None
        if transport is None or transport.closed:
            raise Conflict("Original desktop transport is not connected")
        row = transport.inspect(project_id, execution_id)
        request = _json(row["request"])
        if digest(request) != row["request_hash"]:
            raise Conflict("Original execution request changed")
        with self.store.connect() as connection:
            scope_row = connection.execute("SELECT expires_at FROM agent_scopes WHERE execution_id=?", (execution_id,)).fetchone()
        if scope_row is None or scope_row["expires_at"] <= now():
            raise Conflict("Original execution capability scope expired")
        context = transport.active.get(execution_id)
        if (row["execution_status"] not in {"RUNNING", "CANCEL_REQUESTED"} or context is None or context.get("closing")
                or context.get("session_id") != row["session_id"] or not context.get("connected") or not context["connected"].is_set()
                or context.get("process") is None or context["process"].returncode is not None
                or context.get("client") is None or context["client"].is_closed
                or not context.get("loop") or not context["loop"].is_running()):
            raise Conflict("No actual connected owner exists for this execution")
        if not isinstance(row["session_id"], str) or not re.fullmatch(r"ses_[A-Za-z0-9_-]{1,200}", row["session_id"]):
            raise Conflict("Original session identity is invalid")
        client = context["client"]
        origin = urlsplit(str(client.base_url))
        if (origin.scheme != "http" or origin.hostname != "127.0.0.1" or not origin.port or client.follow_redirects
                or str(client.params.get("directory", "")) != str(context["workspace"] / "cwd")):
            raise Conflict("Original private client directory/origin changed")
        if transport._scope(project_id, request) != transport._frozen_scope(row):
            raise Conflict("Original execution source/profile/method scope changed")
        return transport, row, request, context

    async def probe_execution(self, project_id, execution_id, *, network_mode="AS_CONFIGURED"):
        if network_mode not in NETWORK_MODES:
            raise InvalidInput("Unknown network mode")
        _, _, _, context = self._owner(project_id, execution_id)
        loop = context["loop"]
        if loop is not asyncio.get_running_loop():
            future = asyncio.run_coroutine_threadsafe(self._probe_owner(project_id, execution_id, network_mode), loop)
            return await asyncio.wrap_future(future)
        return await self._probe_owner(project_id, execution_id, network_mode)

    async def _probe_owner(self, project_id, execution_id, network_mode):
        transport, row, request, context = self._owner(project_id, execution_id)
        client = context["client"]
        probes = {}
        for name, path in [("health", "/global/health"), ("config", "/config"), ("session", "/session/" + row["session_id"]), ("agents", "/agent"),
                ("tool_ids", "/experimental/tool/ids"), ("mcp", "/mcp"), ("questions", "/question"), ("permissions", "/permission")]:
            self._owner(project_id, execution_id)
            async with client.stream("GET", path, timeout=5) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 1024 * 1024:
                        raise Conflict("Original capability response exceeds its byte boundary")
                status = response.status_code
            # Never return native config/raw exception messages or secrets to the renderer.
            if 300 <= status < 400:
                raise Conflict("Original capability redirect denied")
            probes[name] = {"http_status": status, "body_sha256": hashlib.sha256(raw).hexdigest(),
                "value": _json(raw) if status == 200 else None}
            self._owner(project_id, execution_id)
        if probes["health"]["value"] != {"healthy": True, "version": VERSION}:
            raise Conflict("Original capability server version changed")
        config = probes["config"]["value"]
        if not isinstance(config, dict) or not isinstance(config.get("permission"), dict):
            raise Conflict("Original permission configuration is unavailable")
        permissions = config["permission"]
        session = probes["session"]["value"]
        if not isinstance(session, dict) or session.get("id") != row["session_id"]:
            raise Conflict("Original exact session state is unavailable")
        session_rules = session.get("permission")
        expected_rules = {canonical({"permission": key, "pattern": "*", "action": value}) for key, value in permissions.items()}
        session_policy_intact = (isinstance(session_rules, list) and len(session_rules) == len(expected_rules)
            and {canonical(x) for x in session_rules} == expected_rules)
        ids = probes["tool_ids"]["value"] or []
        if not isinstance(ids, list) or not all(isinstance(x, str) and len(x) <= 256 for x in ids):
            raise Conflict("Original tool registry response changed")
        mcp = probes["mcp"]["value"] or {}
        if not isinstance(mcp, dict):
            raise Conflict("Original MCP status response changed")
        native_mcp = {name: {"status": item.get("status", "UNKNOWN")} for name, item in mcp.items()
            if isinstance(name, str) and isinstance(item, dict)}
        method_rows = []
        for descriptor in transport.method_descriptors(request)["methods"]:
            offline = descriptor["id"] in {"read_message", "read_context"} and descriptor["readonly"] and not descriptor["effects"]
            admitted = session_policy_intact and native_mcp.get(MCP_NAME, {}).get("status") == "connected" and all(permissions.get(tool) == "allow" for tool in MCP_TOOLS)
            method_rows.append({**descriptor, "native_mcp_connected": admitted, "execution_permitted": admitted,
                "offline_qualified": offline, "display_eligible": offline or network_mode != "STRICT_OFFLINE",
                "input_authorized": False})
        agents = probes["agents"]["value"] or []
        if not isinstance(agents, list):
            raise Conflict("Original agent registry response changed")
        agent_rows = [{"name": x["name"], "mode": x.get("mode"), "selected_for_this_execution": x["name"] == AGENT}
            for x in agents if isinstance(x, dict) and isinstance(x.get("name"), str)]
        pending = {}
        for name in ("questions", "permissions"):
            values = probes[name]["value"]
            if values is not None and not isinstance(values, list):
                raise Conflict("Original pending request response changed")
            # Counts and identities only. Native prompt/tool/secret content is not forwarded.
            pending[name] = [{"id": x["id"], "session_id": row["session_id"], "reply_adapter": "NOT_ADAPTED"}
                for x in values or [] if isinstance(x, dict) and x.get("sessionID") == row["session_id"] and isinstance(x.get("id"), str)]
        events = transport.stream_events(project_id, execution_id)
        calls = [x["payload"] for x in events if x["payload"].get("type") == "desktop_tool_completed"]
        return {"status": "ACTUAL_ORIGINAL_OWNER_OBSERVED", "version": VERSION, "project_id": project_id,
            "execution_id": execution_id, "session_id": row["session_id"], "request_sha256": row["request_hash"],
            "network_mode": network_mode, "observed_at": now(), "owner_lifetime": "ACTIVE_EXECUTION_ONLY",
            "probes": {k: {x: v[x] for x in ("http_status", "body_sha256")} for k, v in probes.items()},
            "native_registered_tool_ids": ids, "native_builtin_tools_invocable": False,
            "native_builtin_tools_reason": "CURRENT_ADAPTER_MODEL_WIRE_AND_SESSION_ALLOW_ONLY_STRUCTURED_OUTPUT_AND_BOUND_MCP",
            "session_permission_matches_config": session_policy_intact,
            "agents": agent_rows, "native_mcp": native_mcp, "host_methods": method_rows, "pending": pending,
            "actual_host_tool_receipts": [{k: x[k] for k in ("method", "result_sha256") if k in x} for x in calls],
            "base_interfaces": self._base_interfaces(), "automatic_input_authorized": False,
            "qualified_model_access": False, "native_windows_qualified": False}

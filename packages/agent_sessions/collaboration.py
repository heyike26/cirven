"""Read-only projection of already observed original-session facts, never a scheduler."""
from copy import deepcopy
import re
from packages.domain.store import Conflict
from packages.domain.tasks import digest


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,200}", value) is not None


def _text(value, limit=300):
    return value[:limit] if isinstance(value, str) else None


def _model(value, level, evidence):
    if not isinstance(value, dict): return None
    provider, model = value.get("providerID"), value.get("modelID")
    if not isinstance(provider, str) or not isinstance(model, str) or not provider or not model: return None
    result = {"provider_id": provider[:200], "model_id": model[:200], "evidence_level": level, "evidence_ref": evidence}
    if isinstance(value.get("variant"), str): result["variant"] = value["variant"][:100]
    return result


def project_collaboration(session, *, project_id=None, session_ref=None, generation=None):
    """No private RPC and no mutation: caller supplies service.get's durable snapshot."""
    value = deepcopy(session)
    ref, pid, gen = value.get("session_ref"), value.get("project_id"), value.get("generation")
    if (not isinstance(ref, dict) or not _id(ref.get("id")) or ref.get("revision") != 1 or
        not re.fullmatch(r"[a-f0-9]{64}", str(ref.get("sha256"))) or not _id(pid) or type(gen) is not int or gen < 0):
        raise Conflict("Original collaboration basis is invalid")
    if project_id is not None and project_id != pid or session_ref is not None and session_ref != ref or generation is not None and generation != gen:
        raise Conflict("Original collaboration basis changed")
    root = value.get("original_session_id")
    if root is not None and not _id(root): raise Conflict("Original native session identity is invalid")
    messages, children = value.get("messages", []), value.get("children", [])
    if not isinstance(messages, list) or not isinstance(children, list) or len(messages) > 200 or len(children) > 1000:
        raise Conflict("Original collaboration observation exceeds its bound")
    nodes, edges, gaps, known = [], [], [], {}
    native_session = value.get("original_session") or {}
    if not isinstance(native_session, dict): raise Conflict("Original session metadata is invalid")
    directory = native_session.get("directory")
    if root:
        nodes.append({"id": root, "kind": "ROOT", "title": "主会话", "observed_status": value.get("native_status", "UNKNOWN"),
                      "models": [], "pending_request_refs": [], "child_messages_observed": False})
        for child in children:
            if not isinstance(child, dict) or not _id(child.get("id")) or child.get("parentID") != root or child["id"] == root:
                raise Conflict("Observed child is outside the exact original root")
            if directory is not None and child.get("directory") != directory: raise Conflict("Observed child directory differs from its root")
            ident = child["id"]
            if ident in known:
                if known[ident] != child: raise Conflict("Conflicting duplicate child observation")
                continue
            known[ident] = child
            evidence = {"native_session_id": ident, "sha256": digest(child)}
            model = _model(child.get("model"), "SESSION_METADATA", evidence)
            nodes.append({"id": ident, "kind": "CHILD", "title": _text(child.get("title")) or ident,
                          "agent_name": _text(child.get("agent"), 100), "observed_status": "UNKNOWN",
                          "models": [model] if model else [], "pending_request_refs": [], "child_messages_observed": False})
            edges.append({"id": "lineage_" + digest([root, ident])[:24], "kind": "LINEAGE", "source": root, "target": ident,
                          "evidence_ref": evidence})
        by_id = {node["id"]: node for node in nodes}
        seen_calls, seen_messages = {}, {}
        for message in messages:
            info = message.get("info", {}) if isinstance(message, dict) else {}
            if info.get("sessionID") != root or not _id(info.get("id")):
                raise Conflict("Observed message is outside the exact original root")
            message_id = info["id"]
            message_hash = digest(message)
            if message_id in seen_messages:
                if seen_messages[message_id] != message_hash: raise Conflict("Conflicting duplicate root message observation")
                continue
            seen_messages[message_id] = message_hash
            evidence = {"native_session_id": root, "message_id": message_id, "sha256": digest(info)}
            model = _model(info, "ASSISTANT_MESSAGE", evidence) if info.get("role") == "assistant" else None
            if model and model not in nodes[0]["models"]: nodes[0]["models"].append(model)
            parts = message.get("parts", [])
            if not isinstance(parts, list) or len(parts) > 2000: raise Conflict("Observed message parts exceed their bound")
            for part in parts:
                if not isinstance(part, dict) or part.get("type") != "tool" or part.get("tool") != "task": continue
                if info.get("role") != "assistant":
                    gaps.append("UNMATCHED_TASK_ROLE")
                    continue
                if part.get("sessionID", root) != root: raise Conflict("Observed task part belongs to another session")
                state = part.get("state", {})
                metadata = state.get("metadata", {}) if isinstance(state, dict) else {}
                if not isinstance(metadata, dict): raise Conflict("Observed task metadata is invalid")
                target, call = metadata.get("sessionId"), part.get("callID")
                if metadata.get("parentSessionId") != root or target not in known or not _id(call) or not _id(part.get("id")):
                    gaps.append("UNMATCHED_TASK_METADATA")
                    continue
                attempt = (message_id, call)
                fingerprint = digest(part)
                if attempt in seen_calls:
                    if seen_calls[attempt] != fingerprint: raise Conflict("Conflicting duplicate task call observation")
                    continue
                seen_calls[attempt] = fingerprint
                evidence = {"native_session_id": root, "message_id": message_id, "part_id": part["id"], "call_id": call, "sha256": fingerprint}
                selected = _model(metadata.get("model"), "TASK_METADATA_SELECTION", evidence)
                if selected: by_id[target]["models"].append(selected)
                tool_input = state.get("input", {})
                edges.append({"id": "delegation_" + digest([message_id, call])[:24], "kind": "DELEGATION", "source": root, "target": target,
                              "call_id": call, "observed_tool_status": _text(state.get("status"), 80) or "UNKNOWN",
                              "description": _text(tool_input.get("description")) if isinstance(tool_input, dict) else None,
                              "agent_name": _text(tool_input.get("subagent_type"), 100) if isinstance(tool_input, dict) else None,
                              "evidence_ref": evidence})
        for field in ("pending_questions", "pending_permissions"):
            requests = value.get(field, [])
            if not isinstance(requests, list) or len(requests) > 1000: raise Conflict("Pending observation exceeds its bound")
            for request in requests:
                if not isinstance(request, dict) or not isinstance(request.get("native_request"), dict):
                    raise Conflict("Pending observation is invalid")
                native = request["native_request"].get("sessionID")
                if native in by_id and isinstance(request.get("request_ref"), dict):
                    by_id[native]["pending_request_refs"].append(deepcopy(request["request_ref"]))
                else: gaps.append("UNMATCHED_PENDING_REFERENCE")
        for node in nodes:
            if node["pending_request_refs"]: node["observed_status"] = "WAITING_USER"
            node["models"] = node["models"][-20:]
    elif messages or children:
        raise Conflict("Original observations have no established native root")
    body = {"project_id": pid, "session_ref": ref, "generation": gen, "root_native_session_id": root,
            "observation_at": value.get("observation_at"), "observation_is_current": value.get("observation_is_current") is True,
            "nodes": nodes, "edges": edges,
            "coverage": {"root_messages_observed": len(messages), "root_messages_limit": 200, "direct_children_observed": len(known),
                         "child_messages_observed": False, "subtree_complete": False, "gaps": sorted(set(gaps)),
                         "source_observation_coverage": value.get("observation_coverage")},
            "limitations": ["IDLE_IS_NOT_COMPLETION", "EXECUTION_RETURN_IS_NOT_ACCEPTANCE", "CHILD_MESSAGES_NOT_OBSERVED", "MODEL_ROUTING_NOT_ADAPTED"],
            "read_only": True, "model_calls": 0, "automatic_post_replay": False}
    body["source_ref"] = {"id": ref["id"], "revision": 1, "sha256": digest({"projection": body, "observations_sha256": digest({"messages": messages, "children": children})})}
    return body

"""Pure stored-final-response selection shared by supervision and chat history."""
from packages.domain.tasks import digest


def original_answer(owner, project_id, ident, session, operation, generation):
    """Read the stored final-response references, never a similar/newer answer."""
    def result(status, reason, text="", missing=0):
        return {"answer_text": text, "answer_status": status, "text_coverage": reason,
                "answer_missing_count": missing, "answer_message": {
                    "AVAILABLE": "已找回这次任务记录的最终答复；内容落实仍未独立验收。",
                    "PARTIAL": "仅找回部分原答复，缺失部分未用其他消息替代；可回原会话核查。",
                    "UNAVAILABLE": "这次任务的最终答复依据暂不可用；原操作保留，未用其他消息替代。",
                    "NOT_RETURNED": "该任务尚无已记录的最终答复；未触发新模型请求。"}[status]}

    ref = session.get("session_ref")
    native = operation.get("original_session_id")
    if (session.get("project_id") != project_id or not ref or ref.get("id") != ident
            or operation.get("kind") != "MESSAGE"
            or operation.get("session_ref") != ref or operation.get("request", {}).get("session_ref") != ref
            or operation.get("request_sha256") != digest(operation.get("request"))
            or session.get("generation") != generation
            or operation.get("generation", generation) != generation
            or not native or session.get("original_session_id") != native):
        return result("UNAVAILABLE", "ORIGINAL_OWNER_OR_GENERATION_UNAVAILABLE")

    ids = operation.get("native_assistant_ids")
    if not ids:
        status = "UNAVAILABLE" if operation.get("status") in {"RETURNED", "FAILED", "CANCELLED"} else "NOT_RETURNED"
        return result(status, "NO_STORED_FINAL_RESPONSE_REFERENCE")
    if (not isinstance(ids, list) or len(ids) > 200
            or any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids)):
        return result("UNAVAILABLE", "INVALID_STORED_FINAL_RESPONSE_REFERENCE")
    parent = operation.get("native_message_id")
    continuations = operation.get("continuation_guidance_ids")
    if continuations:
        try:
            if (not isinstance(continuations, list) or len(continuations) > 32
                    or len(set(continuations)) != len(continuations)):
                raise ValueError()
            latest = owner.operation(project_id, ident, continuations[-1])
            if (latest.get("kind") != "GUIDANCE" or latest.get("session_ref") != ref
                    or latest.get("generation") != generation or latest.get("original_session_id") != native
                    or latest.get("request", {}).get("generation") != generation
                    or latest.get("request", {}).get("session_ref") != ref
                    or latest.get("request", {}).get("expected_active_operation_id") != operation.get("id")
                    or latest.get("request_sha256") != digest(latest.get("request"))
                    or latest.get("original_active_operation_id") != operation.get("id")
                    or not latest.get("native_user_observed")
                    or latest.get("status") not in {"RETURNED", "FAILED", "CANCELLED"}
                    or set(latest.get("native_assistant_ids", [])) != set(ids)):
                raise ValueError()
            parent = latest.get("native_message_id")
        except Exception:
            return result("UNAVAILABLE", "GUIDANCE_COHORT_RESPONSE_REFERENCE_UNAVAILABLE")
    if not parent:
        return result("UNAVAILABLE", "ORIGINAL_RESPONSE_PARENT_UNAVAILABLE")

    messages = session.get("messages", [])
    counts = {}
    for message in messages:
        message_id = message.get("info", {}).get("id")
        if isinstance(message_id, str):
            counts[message_id] = counts.get(message_id, 0) + 1
    observed, texts = set(), []
    for message in messages:
        info = message.get("info", {})
        message_id = info.get("id")
        if (message_id not in ids or counts.get(message_id) != 1 or info.get("role") != "assistant"
                or info.get("sessionID") != native or info.get("parentID") != parent):
            continue
        parts = [part for part in message.get("parts", []) if part.get("type") == "text"]
        safe = [part for part in parts if isinstance(part.get("text"), str)
                and part.get("messageID", message_id) == message_id and part.get("sessionID", native) == native]
        if len(safe) == len(parts):
            observed.add(message_id)
        texts.extend(part["text"] for part in safe)
    text = "\n\n".join(texts)
    missing = len(set(ids) - observed)
    if not text:
        return result("UNAVAILABLE", "EXACT_FINAL_RESPONSE_NOT_IN_SAVED_MESSAGE_WINDOW", missing=missing)
    return result("PARTIAL" if missing else "AVAILABLE",
                  "EXACT_STORED_FINAL_ASSISTANT_IDS_SAME_OWNER_GENERATION" + ("_PARTIAL" if missing else ""), text, missing)


"""Durable user guidance into the existing native session, never a second loop.

An HTTP 204 is dispatch acknowledgement only.  Exact native user/assistant
observations establish subsequent evidence; neither proves semantic compliance.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import Field

from packages.adapters.subscription_runtime import is_subscription
from packages.domain.questions import Id
from packages.domain.store import Conflict, InvalidInput
from packages.domain.tasks import digest, now

from .models import SessionControl, OriginalWorkRef, operation_request
from packages.domain.conversation_threads import ConversationRef
from packages.domain.native_input_attachments import NativeInputAttachment
from packages.domain.conversation_file_input import ConversationFileInput


class GuidanceInput(SessionControl):
    raw_text: str = Field(min_length=1, max_length=16000)
    intent: Literal["AUTO", "STEER", "STATUS"] = "AUTO"
    expected_active_operation_id: Id | None = None
    conversation_ref: ConversationRef | None = None
    input_attachments: list[NativeInputAttachment] = Field(default_factory=list, max_length=20)
    file_input: ConversationFileInput | None = None
    original_work_ref: OriginalWorkRef | None = None


# Whole utterances only.  A requirement containing “进度” is never a status query.
_STATUS = frozenset(("进度", "进度如何", "现在进度如何", "现在进度怎么样", "当前进度", "查看进度",
                     "目前进度", "现在到哪一步了", "status", "progress", "what is the status"))
_TERMINAL = frozenset(("RETURNED", "FAILED", "CANCELLED", "COHORT_RETURNED"))


def _local_status(text):
    return text.strip().rstrip("?？。.!！").strip().casefold() in _STATUS


class SessionGuidance:
    def __init__(self, owner):
        self.owner = owner

    @staticmethod
    def _publish(context, pid, sid, record):
        scope = (pid, sid, context["generation"])
        entries = context.get("guidance_public_summaries", []) if context.get("guidance_public_scope") == scope else []
        summary = {"operation_id": record["id"], "status": record["status"], "role": record.get("role", "GUIDANCE"),
                   "generation": record.get("generation", context["generation"]),
                   "original_active_operation_id": record.get("original_active_operation_id"),
                   "native_user_observed": record.get("native_user_observed", False),
                   "individual_response_observed": record.get("individual_response_observed",
                       record["status"] in ("RETURNED", "FAILED", "CANCELLED") and bool(record.get("native_assistant_ids"))),
                   "response_evidence": record.get("response_evidence"),
                   "linked_latest_operation_id": record.get("linked_latest_operation_id"),
                   "cohort_response_status": record.get("cohort_response_status")}
        context["guidance_public_scope"] = scope
        context["guidance_public_summaries"] = ([item for item in entries if item["operation_id"] != record["id"]] + [summary])[-64:]

    def public_snapshot(self, pid, sid):
        context = self.owner.active.get(sid)
        if not context or context.get("guidance_public_scope") != (pid, sid, context["generation"]):
            return []
        return deepcopy(context.get("guidance_public_summaries", []))

    async def submit(self, pid, sid, body):
        from .service import _native_message_id

        value = GuidanceInput.model_validate(body)
        owner = self.owner
        manifest, _ = owner._manifest(pid, sid, value.session_ref.model_dump())
        if (value.conversation_ref is not None or value.input_attachments or value.file_input or value.original_work_ref is not None) and (manifest.get('native_review_admission') or manifest.get('native_message_admission')):
            raise Conflict('Private task guidance uses its existing frozen input protocol')
        if (value.original_work_ref is not None and owner.conversations is None
                and not (value.intent!='STEER' and _local_status(value.raw_text))):
            raise Conflict('Original work context authority is unavailable; input retained')
        if manifest.get('native_review_admission'):
            owner._authorize_review_before_claim(manifest, value, guidance=True)
        ordinary = bool(manifest.get('native_message_admission'))
        if ordinary:
            owner.native_messages.authorize_guidance(manifest, value)
        receipt, created = owner._claim(pid, sid, value.operation_id, "GUIDANCE", operation_request(value))
        if not created:
            return receipt  # Recovery reads this receipt; an unknown POST is never replayed.
        try:
            # Public discussion status is an observation, including after a
            # normal shutdown. It must not start an owner just to answer it.
            if (value.conversation_ref is not None and not ordinary
                    and not manifest.get('native_review_admission')
                    and value.intent != 'STEER' and _local_status(value.raw_text)):
                view = owner.get(pid, sid)
                if not view['connected_owner']:
                    if (view['generation'] != value.generation
                            or view.get('active_operation_id') != value.expected_active_operation_id):
                        raise Conflict('Saved original status differs from its exact reviewed basis')
                    parent = view.get('active_operation_id')
                    operation = owner.operation(pid, sid, parent) if parent else None
                    local = {'owner_status': view['owner_status'], 'native_status': view['native_status'],
                             'active_operation_id': parent, 'operation_status': operation['status'] if operation else None,
                             'pending_questions': len(view.get('pending_questions', [])),
                             'pending_permissions': len(view.get('pending_permissions', [])),
                             'observation_at': view.get('observation_at'), 'observation_is_current': False,
                             'coverage': 'LAST_SAVED_OWNER_AND_OPERATION_NOT_CURRENT'}
                    from .work_context import local_status as selected_work_status
                    selected=selected_work_status(owner,manifest,receipt,parent)
                    if selected is None and owner.conversations is not None:
                        from .canonical_resources import saved_status
                        canonical_work=saved_status(owner.conversations,manifest,receipt)
                        if canonical_work is not None:local['canonical_work']=canonical_work
                    if selected is not None:local.update(operation_status=selected['status'],active_operation_id=selected['active_operation_id'],owner_active_operation_id=selected['owner_active_operation_id'],original_work=selected,
                        coverage='LAST_SAVED_OWNER_AND_SELECTED_ORIGINAL_WORK_NOT_CURRENT')
                    return owner._finish(pid, sid, value.operation_id, 'RETURNED', role='LOCAL_STATUS',
                        generation=value.generation, original_active_operation_id=parent,
                        intent_resolution='EXACT_SAVED_LOCAL_STATUS', local_status=local, model_calls=0,
                        native_post_count=0, semantic_acceptance=False, independent_verification='NOT_RUN')
            observation = ordinary and value.intent != 'STEER' and _local_status(value.raw_text)
            options = {'intent': 'OBSERVE'} if observation else {}
            if not observation:
                await owner.editor_captures.broker.admit(manifest,generation=value.generation,
                    operation_id=value.operation_id,kind='GUIDANCE')
            manifest, context = owner._context(pid, sid, value.session_ref.model_dump(), value.generation, **options)
            async with context["lock"]:
                # Admission must still hold after waiting for another short dispatch.
                manifest, current = owner._context(pid, sid, value.session_ref.model_dump(), value.generation, **options)
                if current is not context:
                    raise Conflict("Original guidance owner changed while waiting")
                state = owner._state(pid, sid)
                parent = state.get("active_operation_id")
                if parent != value.expected_active_operation_id:
                    raise Conflict("Guidance active operation differs from its reviewed basis")
                exact_status = _local_status(value.raw_text)
                if value.intent == "STATUS" and not exact_status:
                    raise InvalidInput("STATUS accepts only an exact local progress question; use STEER for instructions")
                if value.intent != "STEER" and exact_status:
                    operation = owner.operation(pid, sid, parent) if parent else None
                    local = {"owner_status": state["owner_status"], "native_status": state["native_status"],
                             "active_operation_id": parent, "operation_status": operation["status"] if operation else None,
                             "pending_questions": len(state.get("pending_questions", [])),
                             "pending_permissions": len(state.get("pending_permissions", [])),
                             "observation_at": state.get("observation_at"),
                             "coverage": "CURRENT_LOCAL_OWNER_AND_OPERATION_ONLY"}
                    from .work_context import local_status as selected_work_status
                    selected=selected_work_status(owner,manifest,receipt,parent)
                    if selected is None and owner.conversations is not None:
                        from .canonical_resources import saved_status
                        canonical_work=saved_status(owner.conversations,manifest,receipt)
                        if canonical_work is not None:local['canonical_work']=canonical_work
                    if selected is not None:local.update(operation_status=selected['status'],active_operation_id=selected['active_operation_id'],owner_active_operation_id=selected['owner_active_operation_id'],original_work=selected,
                        coverage='CURRENT_LOCAL_OWNER_AND_LAST_SAVED_SELECTED_ORIGINAL_WORK')
                    result = owner._finish(pid, sid, value.operation_id, "RETURNED", role="LOCAL_STATUS",
                                         generation=value.generation, original_active_operation_id=parent,
                                         intent_resolution="EXACT_LOCAL_STATUS", local_status=local, model_calls=0,
                                         native_post_count=0, semantic_acceptance=False,
                                         independent_verification="NOT_RUN")
                    self._publish(context, pid, sid, result)
                    return result
                if not parent:
                    raise Conflict("Guidance requires an active original message; ordinary messages use the original send action")
                operation = owner.operation(pid, sid, parent)
                if (operation.get("kind") != "MESSAGE" or operation.get("status") != "ACCEPTED" or
                        not operation.get("native_message_id") or operation.get("session_ref") != value.session_ref.model_dump() or
                        operation.get("generation") != value.generation or
                        operation.get("original_session_id") != context["session_id"]):
                    raise Conflict("Guidance requires an acknowledged exact original active message")
                ids = list(state.get("guidance_operation_ids", [])) if state.get("guidance_cohort_operation_id") == parent else []
                if len(ids) >= 32:
                    raise Conflict("Original run guidance allowance is exhausted (32); existing receipts remain available")
                agent = operation.get("request", {}).get("agent", "build")
                if agent not in ("plan", "build", "workflow") or agent == 'workflow' and not context.get('workflow_mode'):
                    raise Conflict("Original active agent is not an admitted guidance route")
                if agent == 'workflow' and operation.get('work_mode_definition') != context.get('workflow_mode'):
                    raise Conflict('The frozen Workflow definition differs from this original owner')
                with owner.store.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    state = owner._state(pid, sid)
                    if state.get("active_operation_id") != parent:
                        raise Conflict("Original active message changed before guidance dispatch")
                    from .work_retests import admit_guidance
                    admit_guidance(owner,connection,pid,sid,operation,value)
                    if is_subscription(context["profile"]) or ordinary:
                        if state.get("native_turns_started", 0) >= manifest["request"]["max_calls"]:
                            raise Conflict("Original user-turn allowance is exhausted")
                        state["native_turns_started"] = state.get("native_turns_started", 0) + 1
                    if not is_subscription(context["profile"]) and state["model_calls_reserved"] >= manifest["request"]["max_calls"]:
                        raise Conflict("Original model-call allowance is exhausted")
                    ids.append(value.operation_id)
                    state.update(guidance_cohort_operation_id=parent, guidance_operation_ids=ids)
                    owner._write(owner._path(pid, sid, "state.json"), state, replace=True)
                context["guidance_operation_ids"] = ids
                context.pop("guidance_observation_cache", None)
                native_id = _native_message_id()
                timeout = operation.get("request", {}).get("timeout_seconds", 60)
                if type(timeout) is not int or not 10 <= timeout <= 300:
                    raise Conflict("Original active message timeout is not an admitted bound")
                expires = datetime.fromisoformat(context["expires_at"])
                previous = operation.get("deadline")
                next_deadline = (min(expires, datetime.fromisoformat(previous) if previous else expires).isoformat()
                    if ordinary else min(expires, max(datetime.fromisoformat(previous) if previous else datetime.now(timezone.utc),
                        datetime.now(timezone.utc) + timedelta(seconds=timeout))).isoformat())
                owner._finish(pid, sid, parent, operation["status"], deadline=next_deadline,
                              user_guidance_extended_deadline=False if ordinary else (
                                  operation.get("user_guidance_extended_deadline", False) or previous is None or next_deadline > previous),
                              last_guidance_operation_id=value.operation_id)
                owner._finish(pid, sid, value.operation_id, "DISPATCHING", generation=value.generation,
                              original_active_operation_id=parent, original_session_id=context["session_id"],
                              native_message_id=native_id,native_post_attempted=False, guidance_sequence=len(ids), dispatch_at=now(),
                              intent_resolution="EXPLICIT_STEER" if value.intent == "STEER" else "AUTO_AS_STEER",
                              parent_previous_deadline=previous, parent_deadline=next_deadline,
                              automatic_replay=False, semantic_acceptance=False, independent_verification="NOT_RUN")
                mapping = context.setdefault("guidance_native_message_ids", {})
                context["guidance_native_message_ids"] = {key: mapping[key] for key in ids if key in mapping}
                context["guidance_native_message_ids"][value.operation_id] = native_id
                from .resource_retention import dispatch_scope
                with dispatch_scope(owner,manifest,context,owner.operation(pid,sid,value.operation_id)):
                    if manifest.get('native_review_admission'):
                        owner.native_reviews.before_dispatch(manifest, context, owner.operation(pid, sid, value.operation_id))
                    if owner.conversations is not None:
                        if not (manifest.get('native_review_admission') or manifest.get('native_message_admission')):
                            owner._finish(pid, sid, value.operation_id, 'DISPATCHING', native_post_attempted=False)
                        owner.conversations.prepare(manifest, context, owner.operation(pid, sid, value.operation_id), guidance=True)
                    elif value.conversation_ref is not None or value.input_attachments or value.file_input:
                        raise Conflict('Public conversation guidance inputs are unavailable')
                    payload = {"messageID": native_id, "agent": agent,
                        "model": {"providerID": context["profile"].provider_id, "modelID": context["profile"].model_id},
                        "parts": [{"type": "text", "text": value.raw_text}]}
                    if ordinary:
                        native_format = owner.native_messages.output_format(manifest)
                        if native_format is not None:
                            payload['format'] = native_format
                        owner.native_messages.before_dispatch(manifest, context, owner.operation(pid, sid, value.operation_id))
                    if manifest.get('native_review_admission'):
                        payload = owner.native_reviews.dispatch_payload(manifest, context, owner.operation(pid, sid, value.operation_id), payload)
                        if owner.native_reviews.capability_task_admission(manifest['native_review_admission']):
                            owner._finish(pid, sid, value.operation_id, 'DISPATCHING', native_post_attempted=True)
                    if owner.conversations is not None:
                        payload = owner.conversations.dispatch_payload(manifest, context, owner.operation(pid, sid, value.operation_id), payload)
                        if not (manifest.get('native_review_admission') or manifest.get('native_message_admission')):
                            owner._finish(pid, sid, value.operation_id, 'DISPATCHING', native_post_attempted=True)
                    owner._finish(pid,sid,value.operation_id,"DISPATCHING",native_post_attempted=True)
                    await owner._http(context, "POST", "/session/" + context["session_id"] + "/prompt_async", payload, expected=204)
                    result = owner._finish(pid, sid, value.operation_id, "ACCEPTED", native_dispatch_acknowledged=True)
                    self._publish(context, pid, sid, result)
                    return result
        except BaseException as error:
            old = owner.operation(pid, sid, value.operation_id)
            v2_not_sent = bool(manifest.get('native_review_admission') and owner.native_reviews.capability_task_admission(manifest['native_review_admission'])
                               and old.get('native_post_attempted') is not True)
            result = owner._finish(pid, sid, value.operation_id,
                          "UNKNOWN_OUTCOME" if (old["status"] == "DISPATCHING" and not v2_not_sent
                              and old.get('native_post_attempted') is not False) else "FAILED",
                          error_type=type(error).__name__)
            if result['status'] == 'UNKNOWN_OUTCOME' and manifest.get('native_review_admission'):
                owner.native_reviews.dispatch_unknown(manifest, result)
            context = owner.active.get(sid)
            if context and context.get("generation") == value.generation:
                self._publish(context, pid, sid, result)
            raise

    async def observe(self, pid, sid, context, messages):
        """Project exact evidence only; do not wake, retry, abort or start a model."""
        owner = self.owner
        _manifest, session_ref = owner._manifest(pid, sid)
        state = owner._state(pid, sid)
        if (owner.active.get(sid) is not context or state.get("generation") != context.get("generation") or
                any(item.get("info", {}).get("sessionID") != context["session_id"] for item in messages)):
            raise Conflict("Guidance observation differs from the exact current native owner")
        parent = state.get("guidance_cohort_operation_id")
        result = {"cohort_operation_id": parent, "latest_guidance_native_message_id": None,
                  "pending_operation_ids": [], "unobserved_operation_ids": [], "finals": [],
                  "coverage": "EXACT_GUIDANCE_IDS_IN_SUPPLIED_ROOT_MESSAGE_WINDOW",
                  "model_calls": 0, "semantic_acceptance": False}
        ids = state.get("guidance_operation_ids", [])
        if not isinstance(ids, list) or len(ids) > 32 or len(set(ids)) != len(ids):
            raise Conflict("Guidance receipt index is invalid")
        mapping = context.get("guidance_native_message_ids", {})
        if set(mapping) != set(ids):
            mapping = {ident: owner.operation(pid, sid, ident).get("native_message_id") for ident in ids}
            context["guidance_native_message_ids"] = mapping
        native_ids = set(mapping.values()) - {None}
        # Only bounded status evidence enters this cache, never streamed bodies.
        signature = digest({"scope": [pid, sid, session_ref, context["generation"], context["session_id"]],
            "cohort": parent, "active": state.get("active_operation_id"), "ids": ids,
            "native_status": state.get("native_status"), "messages": [
                [item["info"].get("id"), item["info"].get("role"), item["info"].get("parentID"),
                 item["info"].get("finish"), item["info"].get("time", {}).get("completed"),
                 digest(item["info"].get("error")),
                 digest([part.get("text", "") for part in item.get("parts", [])
                         if part.get("type") == "text" and not part.get("synthetic")]) if item["info"].get("role") == "user" else None]
                + [digest([[part.get('mime'), digest(part.get('url'))] for part in item.get('parts', [])
                           if part.get('type') == 'file']) if item['info'].get('role') == 'user' else None]
                for item in messages if item["info"].get("id") in native_ids or item["info"].get("parentID") in native_ids]})
        cache = context.get("guidance_observation_cache")
        if cache and cache["signature"] == signature:
            return deepcopy(cache["projection"])
        records = []
        for ident in ids:
            record = owner.operation(pid, sid, ident)
            if record.get("generation") != context["generation"]:
                continue  # An older epoch is historical, never authority for this run.
            if (record.get("kind") != "GUIDANCE" or record.get("session_ref") != session_ref or
                    record.get("original_active_operation_id") != parent or
                    record.get("original_session_id") != context["session_id"]):
                raise Conflict("Guidance receipt belongs to another native cohort")
            native_id = record.get("native_message_id")
            result["latest_guidance_native_message_id"] = native_id
            user = next((item for item in messages if item["info"].get("id") == native_id and
                         item["info"].get("role") == "user"), None)
            observed = bool(user and "".join(part.get("text", "") for part in user.get("parts", [])
                                            if part.get("type") == "text" and not part.get("synthetic")) == record["request"]["raw_text"])
            if record.get('host_payload') and owner.native_reviews is not None:
                observed = owner.native_reviews.observed_payload(record, user) is True
            assistants = [item for item in messages if item["info"].get("role") == "assistant" and
                          item["info"].get("parentID") == native_id] if observed else []
            finals = [item for item in assistants if item["info"].get("time", {}).get("completed") and
                      (item["info"].get("finish") == "stop" or item["info"].get("error"))]
            if record["status"] not in _TERMINAL:
                next_status = record["status"]
                evidence = {}
                if observed:
                    evidence["native_user_observed"] = True
                    next_status = "NEXT_TURN_ASSOCIATED" if assistants else "OBSERVED"
                if assistants:
                    evidence["native_assistant_ids"] = [item["info"]["id"] for item in assistants]
                if finals:
                    errors = [item["info"]["error"] for item in finals if item["info"].get("error")]
                    next_status = ("CANCELLED" if errors and all(error.get("name") == "MessageAbortedError" for error in errors)
                                   else "FAILED" if errors else "RETURNED")
                    evidence["native_assistant_ids"] = [item["info"]["id"] for item in finals]
                    evidence["response_evidence"] = "EXACT_ASSISTANT_PARENT_ID_NOT_SEMANTIC_COMPLIANCE"
                if next_status != record["status"] or any(record.get(key) != item for key, item in evidence.items()):
                    record = owner._finish(pid, sid, ident, next_status, **evidence)
            records.append(record)
        latest = records[-1] if records else None
        if (latest and latest["status"] in ("RETURNED", "FAILED", "CANCELLED") and
                latest.get("native_user_observed") and state.get("native_status") == "idle"):
            for index, record in enumerate(records[:-1]):
                if record["status"] not in _TERMINAL and record.get("native_user_observed"):
                    records[index] = owner._finish(pid, sid, record["id"], "COHORT_RETURNED",
                        individual_response_observed=False, response_evidence="COHORT_RESPONSE_REFERENCE",
                        linked_latest_operation_id=latest["id"], cohort_response_status=latest["status"],
                        cohort_assistant_ids=latest.get("native_assistant_ids", []))
        for record in records:
            self._publish(context, pid, sid, record)
            ident, native_id = record["id"], record.get("native_message_id")
            if not record.get("native_user_observed"):
                result["unobserved_operation_ids"].append(ident)
            if record["status"] not in _TERMINAL:
                result["pending_operation_ids"].append(ident)
            elif record.get("native_user_observed"):
                result["finals"].append({"operation_id": ident, "native_message_id": native_id,
                                         "status": record["status"],
                                         "native_assistant_ids": record.get("native_assistant_ids", []),
                                         "individual_response_observed": record["status"] != "COHORT_RETURNED",
                                         **({"linked_latest_operation_id": record["linked_latest_operation_id"],
                                             "cohort_response_status": record["cohort_response_status"],
                                             "cohort_assistant_ids": record["cohort_assistant_ids"]}
                                            if record["status"] == "COHORT_RETURNED" else {})})
        context["guidance_observation_cache"] = {"signature": signature, "projection": deepcopy(result)}
        return result
